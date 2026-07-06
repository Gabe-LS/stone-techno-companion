## Current state (facts, with file:line)

**Auth mechanism** — two independent, unlinked paths, both accepted by a single gate function:
- `_require_admin` (`server/chat_api.py:245-268`): first checks `X-Admin-Token` header against a single shared secret `ADMIN_TOKEN = os.environ.get("CHAT_ADMIN_TOKEN", "")` (`chat_api.py:88`) via `secrets.compare_digest`. If that fails, falls back to the `chat_session` cookie: resolves the user, looks up all rows in `user_providers` for that user, and checks whether any `provider_id` is in the in-memory set `_ADMIN_EMAIL_HASHES` (`chat_api.py:253-267`).
- `_ADMIN_EMAIL_HASHES` is populated once, at startup, from `CHAT_ADMIN_EMAILS` (`chat_api.py:181-187`, loaded at `chat_api.py:2471`). It is a `sha256` hash of each configured email (`hash_email`, `chat_db.py:1825-1826`). There is no reload path — changing the admin list requires editing the env var and restarting the container.
- Every `/admin/*` route (`chat_api.py:1902-2394`, ~24 endpoints) starts with `_require_admin(request)` and nothing else related to identity.

**`_require_admin` returns `None`** — it is a boolean gate, not an identity resolver. No endpoint has access to "which admin did this." Confirmed by reading every admin endpoint body (`chat_api.py:1902-2394`): none reference a resolved actor.

**No actor column exists anywhere in the schema.** Verified against the actual `CREATE TABLE` statements:
- `bans` (`chat_db.py:82-90`): `id, user_id, provider, provider_id, device_fingerprint, reason, created_at` — no admin/actor field.
- `strikes` (`chat_db.py:194-201`): `id, user_id, reason, detail, created_at, expires_at` — no actor field. `reason`/`detail` are free text describing the *violation* (e.g. `"admin"` / `"Manual admin action"` from `chat_api.py:2174-2175`), not who acted.
- `reports` (`chat_db.py:181-192`): `reviewed_at` is stamped but no reviewer id (`resolve_report`, `chat_db.py:1389-1394`, only sets `status` + `reviewed_at`).
- `chat_settings` (`chat_db.py:231-234`): `key, value` — no audit trail for who changed `room_sort`/`msg_char_limit`/etc.

**`get_moderation_log`** (`chat_db.py:1710-1731`) unions `strikes`, `bans`, `reports` and returns `type, user_id, display_name, detail, created_at` — `user_id`/`display_name` here are the **target's** identity, not the acting admin's. The log is a record of what happened to whom, never who did it.

**Admin ban path has no self/peer protection.** `admin_ban` (`chat_api.py:1943-1991`) does `get_user(db, user_id)` and unconditionally bans every linked provider, with zero check for whether `user_id` belongs to an entry in `_ADMIN_EMAIL_HASHES` or otherwise privileged. `admin_mute_user` (`chat_api.py:2135-2167`), `admin_strike_user` (`chat_api.py:2170-2217`), and `admin_delete_user` (`chat_api.py:2246-2276`) are the same — plain `user_id` path param, no privilege check on the target.

**In-chat admin powers: none.** `chat_ws.py` has zero references to admin/`CHAT_ADMIN_EMAILS`/token (only one unrelated comment at `chat_ws.py:1043`). Read-only room enforcement (`chat_ws.py:1552`) rejects **every** sender unconditionally — there is no bypass branch for an admin identity, meaning `docs/chat-spec.md:907`'s "admin tooling" framing and the CLAUDE.md room-property description ("`is_read_only` — only admins can post") are aspirational: nothing in the WS layer currently lets even a real admin post into a read-only room from the chat UI itself. Admin actions only exist via the separate `/chat/admin` REST surface.

**Admin UI has no identity display.** `server/chat/admin.html` never surfaces "who am I" — no `/admin/me`/whoami call exists in `chat_api.py`, and a grep of `admin.html` for "logged in / signed in as / log out" returns nothing. The login screen (`admin.html:184-198`) only collects a token; `doLogin()` stores it in `sessionStorage`. `init()` (`admin.html:200-212`) calls `api('/stats')` with `credentials: 'include'` (`admin.html:171`), so a cookie-authenticated admin with no token in `sessionStorage` still passes `_require_admin` via the cookie branch and reaches the app — the UI silently works for cookie admins, but gives them (and everyone else) no indication of which auth path succeeded or which account is acting.

**`resolve_report`** (`chat_db.py:1389-1394`) is an unconditional `UPDATE ... WHERE id = ?` with no guard on the current `status` — it will happily flip an already-`actioned`/`dismissed` report again with no error and no record of the previous resolution being overwritten.

## Findings

### F1. No actor attribution anywhere in the data model [severity: high] [kind: gap]
Evidence: `bans`/`strikes`/`reports`/`chat_settings` schemas (`chat_db.py:82-90, 194-201, 181-192, 231-234`) carry no admin-identity column; `get_moderation_log` (`chat_db.py:1710-1731`) only ever shows the *target* user, never the actor.
Proposed change: add an `actor` (or `acted_by`) column to `bans`, `strikes`, and a `reviewed_by` column to `reports`; thread it through every admin endpoint once identity resolution exists (see F2). Surface it in `get_moderation_log` and the admin log table.

### F2. `_require_admin` never resolves an actor identity [severity: high] [kind: gap]
Evidence: `_require_admin` (`chat_api.py:245-268`) returns `None` on success; every call site (`chat_api.py:1904` etc.) discards the result.
Proposed change: change `_require_admin` to return an `AdminActor` (e.g. `{"kind": "token"}` or `{"kind": "user", "user_id": ..., "email_hash": ...}`), and pass it into every mutating admin endpoint so it can be persisted alongside the action (F1). This is a prerequisite for F1, F3, and F5.

### F3. No role model — every successful auth is unconditionally full-power [severity: high] [kind: gap]
Evidence: `_require_admin` (`chat_api.py:245-268`) is binary; there is no second tier anywhere in `chat_api.py`, `chat_db.py`, or `admin.html`.
Proposed minimal split — DESIGN PROPOSAL:
- **Super-admin only**: managing the admin list itself (adding/removing DB-backed admins — see F4), deleting rooms, deleting user accounts, changing global `chat_settings` (msg limit, TTLs, room_sort), unbanning/deleting ban rows, clearing warnings.
- **Admin (moderator) tier**: mute, strike, ban (temporary/behavioral moderation), resolve reports, view mod log, view users/bans, room read/reorder/main-toggle, create/edit rooms (but not delete).
- Justification: destructive or structural actions (delete room/user/admin, change global settings) have no undo and can cripple the whole panel or event if misused by a rushed festival-night moderator; day-to-day moderation (ban/mute/strike/report) is the bulk of the work and needs to stay low-friction for several trusted people.

### F4. Admin list is env-var-only — no DB-backed management, but is also the safest bootstrap [severity: medium] [kind: gap]
Evidence: `_load_admin_emails` (`chat_api.py:181-187`) reads only `CHAT_ADMIN_EMAILS` once at startup (`chat_api.py:2471`); no code path writes to `_ADMIN_EMAIL_HASHES` or any table.
Proposed change (DESIGN PROPOSAL): add an `admins` table (`user_id` or `email_hash`, `role` ['admin'|'super_admin'], `added_by`, `created_at`) manageable via a super-admin-only endpoint. Keep `CHAT_ADMIN_EMAILS` as permanent, un-removable super-admins (a "root" set that always works even if the DB table is empty/corrupted or the panel locks everyone out) — DB rows are additive only, never able to remove or demote an env-var admin. Risks: privilege escalation if a compromised admin session can add itself as super-admin (mitigate: only super-admin role can write to `admins` table, enforced via F2/F3); lockout if the only super-admin is removed (mitigate: env-var admins can never be removed via the panel, only via redeploy).

### F5. No protection against admin-vs-admin or admin-vs-super-admin action [severity: high] [kind: gap/bug]
Evidence: `admin_ban`, `admin_mute_user`, `admin_strike_user`, `admin_delete_user` (`chat_api.py:1943-1991, 2135-2167, 2170-2217, 2246-2276`) all resolve target purely by `user_id` path param with zero check against `_ADMIN_EMAIL_HASHES` or any admin/super-admin role. `ban_user` (`chat_db.py:577-594`) deletes all of the target's sessions unconditionally, and `admin_ban` closes their live WS connections (`chat_api.py:1982-1988`) — so any holder of the shared token (or a compromised regular-admin session) can instantly ban, mute, strike, or delete the owner/super-admin's own account, including kicking them off the panel (their session cookie is deleted by the ban).
Proposed change: before executing ban/mute/strike/delete, check whether the target `user_id`'s linked providers intersect `_ADMIN_EMAIL_HASHES` (or the future `admins` table); reject with 403 unless the actor is themselves a super-admin (and even then, consider disallowing entirely, or requiring a distinct "revoke admin" flow first).

### F6. Shared token is unattributable and un-revocable per person [severity: high] [kind: design]
Evidence: `ADMIN_TOKEN` (`chat_api.py:88`) is a single value from one env var, checked via constant-time compare (`chat_api.py:247-251`) with no per-holder distinction; `admin.html:157-165` sends it as one static header for every request.
Proposed change: for genuine multi-admin (several trusted people), demote the token path to a bootstrap/emergency-only mechanism (e.g. only usable to perform super-admin actions when no cookie-admin exists yet, or only for a single designated owner), and make the cookie+role path (F2/F3) the normal way every named admin authenticates, since it is per-person and already tied to a real account.

### F7. No identity/session indication in the admin UI [severity: medium] [kind: gap]
Evidence: no `/admin/me` endpoint in `chat_api.py`; `admin.html` has no logged-in-as display and no logout affordance (grep for "logged in/log out" returns nothing); `init()` (`admin.html:200-212`) silently succeeds for a cookie-authenticated admin with no token, giving no feedback about which auth path was used.
Proposed change: add `GET /chat/api/admin/me` returning the resolved actor (email/role/auth-kind) using the F2 resolver; render it in the admin header with a logout action (for cookie admins, clear the session cookie; for token admins, clear `sessionStorage`).

### F8. `resolve_report` has no guard against double-resolution [severity: low] [kind: bug]
Evidence: `resolve_report` (`chat_db.py:1389-1394`) is an unconditional `UPDATE reports SET status = ?, reviewed_at = ? WHERE id = ?` — no `WHERE status = 'pending'` guard.
Proposed change: add `AND status = 'pending'` to the `UPDATE` and check `rowcount` to detect a race (two admins resolving the same report), returning a "already resolved" signal instead of silently overwriting. Severity is low in practice — the underlying ban/mute action is idempotent-ish (duplicate ban rows are harmless, `is_banned` just needs one match) — but the report's audit trail (who resolved it, why) can be silently clobbered by whichever admin's request lands last, which matters more once F1 adds a `reviewed_by` column.

### F9. `docs/chat-spec.md` is stale relative to the current auth model [severity: low] [kind: cross-lane, doc-only]
Evidence: `docs/chat-spec.md:735` says admin is "protected by an admin token (environment variable)" only — doesn't mention the cookie/`CHAT_ADMIN_EMAILS` path that's actually in `chat_api.py:253-267`. One-line note only; not this review's lane to fix docs generally.

## Proposed multi-admin/super-admin design (concise, implementable)

- **Data model**: new `admins` table (`user_id`/`email_hash`, `role` enum `admin`|`super_admin`, `added_by`, `created_at`). Add `actor` to `bans`, `acted_by` to `strikes`, `reviewed_by` to `reports`. Keep `CHAT_ADMIN_EMAILS` as an immutable super-admin allowlist layered on top (never removable via panel).
- **Endpoint changes**: rewrite `_require_admin` into `_resolve_admin(request) -> AdminActor | None` returning `{kind: token|cookie, role, user_id?, email_hash?}`; every admin endpoint accepts the resolved actor and (a) persists it alongside the action (F1), (b) enforces role for super-admin-only routes (F3), (c) rejects action-on-admin unless actor is super-admin (F5). Add `GET /chat/api/admin/me`, `GET/POST/DELETE /chat/api/admin/admins` (super-admin only).
- **UI changes**: header shows resolved identity + role + logout; admins management screen (super-admin only) to add/remove DB-backed admins; mod log table gains an "acted by" column.
- **Migration/bootstrap story**: `CHAT_ADMIN_EMAILS` entries become permanent super-admins on first deploy with zero migration needed; `admins` table starts empty and is populated by a super-admin through the new UI. Shared `CHAT_ADMIN_TOKEN` is kept only as an emergency/bootstrap credential (e.g. usable solely to reach the admins-management screen when no super-admin session exists), reducing its blast radius going forward.

## Uncertain / needs orchestrator verification
- Whether any other code path (not found in this review's grep) grants `CHAT_ADMIN_TOKEN` holders capabilities beyond `/chat/api/admin/*` — worth a broader repo-wide grep for `ADMIN_TOKEN`/`X-Admin-Token` outside `chat_api.py`/`admin.html`.
- Whether `docs/chat-spec.md` and `docs/e2ee-*.md` are treated as living specs that should be updated alongside any of the above (F9) — orchestrator should decide if that's in scope for this pass.
- Real-world concurrency risk of F8 (double-resolve) and room-reorder races is stated as low based on static reading only; no test run was performed to confirm no other lock/transaction exists around these paths.
