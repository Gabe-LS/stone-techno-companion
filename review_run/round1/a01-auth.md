## Auth & Session Management Review — Stone Techno Companion Chat

### Session fixation / hijacking

**[HIGH] server/chat/chat.html:1036** — The raw session token is embedded directly in the WebSocket connection URL: `new WebSocket(`${proto}://${location.host}/ws/chat/${token.split('=')[1]}`)`. This is called on every page load and every reconnect (chat.html:1027-1063, with exponential-backoff auto-reconnect). Combined with `server/Dockerfile`'s uvicorn CMD (`--proxy-headers --forwarded-allow-ips *`, no `--no-access-log`), uvicorn's default access logging captures the full WS handshake path — meaning long-lived session tokens (7-day expiry, `chat_db.py:461-470`) land in plaintext in container stdout logs on every connect/reconnect. `docker-compose.yml` only rotates these logs (`json-file`, 10MB×5), it doesn't suppress them. Anyone with read access to VPS/container logs can hijack any active chat session for up to 7 days. Recommend sending the token as a WS subprotocol header or a first-message auth handshake instead of embedding it in the URL path, or at minimum disabling uvicorn access logging for the `/ws/chat/` path.

No classic session-fixation vector found: `create_session` (`chat_db.py:461-470`) always mints a fresh server-generated `uuid4().hex*2` token; the server never accepts or reuses a client-supplied session identifier.

### Cookie attribute correctness

**[LOW] server/chat_api.py:196-206** — `_set_session_cookie` derives `secure`/`samesite` from whether `CHAT_BASE_URL` fails to start with `"http://"`, rather than an explicit `ENVIRONMENT=production` flag. This works today per the deploy checklist (which says to unset `CHAT_BASE_URL` in prod), but it's a footgun: if `CHAT_BASE_URL` is ever left set to an `https://` value in production for an unrelated reason, that's fine, but if anyone sets it to `http://` (e.g. during a debugging session against prod), `secure` and `samesite` silently downgrade cluster-wide with no warning.

`httponly=False` (chat_api.py:201) is intentional and documented in-line ("JS reads cookie for WebSocket auth URL") — not flagged as a new issue, but it does mean any future XSS in chat.html directly yields the session cookie; combined with the WS-URL logging issue above, this cookie has two independent theft vectors (JS execution, or log access) rather than one.

`path="/"` (chat_api.py:205) scopes the cookie to the whole origin (lineup pages included) rather than `/chat`, unnecessarily widening exposure — LOW, since lineup pages don't take user-controlled HTML input.

### Magic link token entropy, expiry, replay protection

Entropy and expiry are solid: `secrets.token_urlsafe(32)` (256 bits, chat_api.py:400), 15-minute TTL (chat_api.py:409), and the token row is deleted from `email_tokens` unconditionally on first lookup regardless of outcome (chat_api.py:460-461) — genuine single-use enforcement, no issues there.

**[HIGH] server/chat_api.py:428, 450-477** — `GET /chat/api/verify` (and its path-alias `/chat/v/{token}`, wired in `chat_api.py:2183-2185`) has **no rate limiting**, unlike `POST /login` (5/15min per IP, chat_api.py:369-380). By itself this is low-risk given the token's entropy. But the verify URL puts the token directly in the request path (`f"{base_url}/chat/v/{token}"`, chat_api.py:428), and — per the same default uvicorn access-log configuration noted above — every `GET /chat/v/<token>` is logged in plaintext to container stdout. Since the token is single-use and deleted on first hit, anyone with log read access during the 15-minute window can complete authentication as the target user before the legitimate recipient clicks the link, silently locking the real user out ("Invalid or expired link") with no indication their account was taken over. Recommend not logging this path (or hashing tokens before they ever appear in a URL/log) and adding a coarse rate limit for defense in depth.

Email token cleanup: **No issues found** — `purge_loop` runs every 30s (`chat_ws.py`) and calls `purge_expired_sessions` (`chat_db.py:1525-1528`), which deletes both expired `sessions` and `email_tokens` rows; confirmed wired into the FastAPI lifespan at startup (`server/api.py`, via `mount_chat`'s returned `purge_loop`).

### Google OAuth state parameter / CSRF

**[LOW] server/chat/chat.html:518-536, 556-566** — Neither the implicit `id.initialize` flow (no `nonce`) nor the code-flow `initCodeClient` (no `state`) pass a CSRF-binding parameter. In isolation this would be a login-CSRF concern, but it's mitigated here: `server/api.py` has no CORS middleware at all (confirmed — no `CORSMiddleware`, no `Access-Control-*` handling), so a cross-origin page cannot complete the required `application/json` POST to `/chat/api/auth/google` or `/auth/google/code` — the browser's CORS preflight fails closed with no `Access-Control-Allow-Origin` response, blocking the actual request. Still, adding `state`/`nonce` is worthwhile defense-in-depth in case CORS is ever loosened for another endpoint sharing the app.

### Ban bypass vectors

**[HIGH] server/chat/chat.html (all three auth call sites: lines ~527, ~570, ~598) + server/chat_api.py:217, 263, 309, 383** — The client **never computes or sends `device_fingerprint`** in any auth request body (`/auth/google`, `/auth/google/code`, `/login` all omit the key entirely). Server-side, `is_banned(db, provider, provider_id, device_fingerprint)` (chat_api.py:217, chat_db.py:506-523) always receives `fingerprint=None` from `body.get("device_fingerprint")`, so the fingerprint-based half of ban enforcement (the `bans.device_fingerprint` column, and the schema/CLAUDE.md's documented "bans stored by provider_id + device fingerprint") never engages — it's effectively dead code. A banned user can trivially evade any ban today by simply signing in with a different email address or a different Google account; the device-level mitigation that was clearly designed to catch exactly that case isn't wired up. This should either be implemented client-side (e.g. a stored random identifier + basic canvas/UA fingerprint) or the documentation/expectations should be corrected.

Ban enforcement for *existing* sessions is otherwise sound: `db_ban_user`/`ban_user` deletes all of the user's `sessions` rows immediately (chat_db.py:500-502), so REST calls via `_get_user_from_cookie` fail right after a ban regardless of provider linkage, and `admin_ban` (chat_api.py:~1780-1784) explicitly force-closes any open WebSocket connections for that user. No gap found there.

### Rate limiting on auth endpoints

**[MEDIUM] server/chat_api.py:259-367** — `POST /auth/google` and `POST /auth/google/code` have no rate limiting at all (contrast with `/login`'s 5/15min-per-IP limiter). An attacker can hammer these endpoints with garbage `id_token`/`code` values, each triggering a Google API round-trip (or local JWT verification) plus a DB connection — a resource-exhaustion vector, and it removes the one throttle that would otherwise slow credential-stuffing-style probing.

**[MEDIUM] server/chat_ws.py (`handle_chat_ws`) + server/chat_api.py:2179-2181** — The WS route `@app.websocket("/ws/chat/{token}")` takes an unconstrained `str` token with no length/format validation and no connection-attempt rate limiting; each attempt does a full DB query via `get_user_by_token`. Not exploitable for token guessing given 256-bit entropy, but it is an unthrottled resource-exhaustion surface (arbitrarily large token strings, unlimited connect attempts per client).

### Timing attacks on token comparison

**No issues found** as an exploitable vector: session tokens (`chat_db.py:473-480`) and email verify tokens (`chat_api.py:454-457`) are matched via plain SQL `WHERE token = ?` rather than a constant-time comparison, but both are 256-bit random values — timing side-channels are not practically exploitable at that entropy over a network. Good practice noted: the admin header-token check correctly uses `secrets.compare_digest` (chat_api.py:175).

### Additional finding (adjacent to auth identity, worth flagging)

**[MEDIUM] server/chat_db.py:1582-1583** — `hash_email()` uses unsalted `sha256(email.strip().lower())` to derive the `provider_id` used for the "email" auth provider and for `CHAT_ADMIN_EMAILS` matching (`chat_api.py:125-131`). This is stored directly in the `users`/`user_providers`/`bans` tables. Unsalted SHA-256 of an email address is trivially reversible via dictionary/rainbow-table attack for any common address, which undermines the "privacy-first" pseudonymization goal if `chat.db` is ever exposed (backup leak, VPS compromise) — real email addresses of every email-auth user (and the admin allowlist) could be recovered offline. Consider a keyed HMAC (with a server-side secret) instead of bare SHA-256.
