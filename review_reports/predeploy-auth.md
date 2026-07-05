# Findings: auth

## [SEVERITY: HIGH] Banned/muted users can keep sending DM messages indefinitely
- Where: `server/chat_ws.py:789-793`, `server/chat_moderation.py:410-434`, `server/chat_api.py:1688-1708`
- Evidence:
  ```
  # chat_ws.py:789
  if is_moderated:
      mod_result = await moderate_message(db, user_id, text, image_url)
  else:
      mod_result = {"allowed": True}
  ```
  `moderate_message()` is the *only* place `is_banned()`/`is_muted()` are checked (`chat_moderation.py:416,428`). DM rooms are created with `is_moderated=False` (`chat_db.py:1039`), so `moderate_message` — and therefore the ban/mute check — is never invoked for DMs. Separately, `admin_ban` (`chat_api.py:1688-1708`) deletes the user's session rows (via `db_ban_user` → `chat_db.py:485-486`) but never closes already-open WebSocket connections (contrast with `admin_delete_user` at `chat_api.py:1952-1956`, which explicitly does `ws.close(...)`). `handle_chat_ws` (`chat_ws.py:994-1002`) also validates the token only once, at connect time.
- Impact: A user who is already connected when an admin bans them keeps a live, authenticated WS session. Because DMs never call the ban-check code path at all, that user can continue sending direct messages to any peer indefinitely (until they close the tab), fully bypassing the ban. Since DMs are the one place server-side moderation is intentionally absent (E2EE), this is the exact channel where ban enforcement matters most for abuse/harassment cases.
- Fix: Close all of a banned user's live WebSocket connections from `admin_ban` (same pattern as `admin_delete_user`), and additionally check `is_banned`/`is_muted` unconditionally in the DM send path (not just inside the moderation-gated branch).

## [SEVERITY: HIGH] Bans are trivially evadable — no cross-identity propagation, and device fingerprinting is dead in production
- Where: `server/chat_api.py:1688-1705`, `server/chat_db.py:384-392`, `server/chat_db.py:491-508`, `server/chat/chat.html` (no matches for `fingerprint`)
- Evidence:
  ```
  # chat_api.py:1695-1705 (admin_ban)
  user = get_user(db, user_id)
  db_ban_user(db, user_id, user["provider"], user["provider_id"], reason, user["device_fingerprint"])
  ```
  `user["provider"]`/`user["provider_id"]` are the single values frozen on the `users` row at `create_user` time (`chat_db.py:353-367`); they are never updated when a second provider is linked (`add_user_provider`, `chat_db.py:395-403`), even though `find_user_by_provider` resolves the *same* account through any of its linked identities in `user_providers` (`chat_db.py:384-392`). `is_banned` (`chat_db.py:491-508`) only matches the exact provider/provider_id pair passed in, falling back to `device_fingerprint` only if one was supplied. `device_fingerprint` is accepted as an optional body field on every auth endpoint (`chat_api.py:213,263,306,377`), but the shipped client (`server/chat/chat.html`) never computes or sends one — a full-text search for `fingerprint` in that file returns zero matches.
- Impact: (1) A banned account that had previously linked both Google and Email logins can simply sign back in via the *other* already-linked provider and evade the ban entirely, since `is_banned` never checks the account's other `user_providers` rows. (2) Because no device fingerprint is ever collected, the documented fallback ("Bans stored by provider_id + device fingerprint") never fires in production — a banned user can re-register with a new email address (or a `+tag` alias of the same mailbox, which `email_validator` does not normalize away) from the same browser/device with zero correlation to the prior ban.
- Fix: Ban all rows in `user_providers` for the target `user_id` (not just the frozen `users.provider/provider_id`), and have the client actually generate and send a stable device fingerprint so the fallback match is functional.

## [SEVERITY: MEDIUM] Google login auto-links to an existing account without checking `email_verified`
- Where: `server/chat_api.py:278-296` (`/auth/google`), `server/chat_api.py:339-357` (`/auth/google/code`)
- Evidence:
  ```
  info = google_id_token.verify_oauth2_token(id_token, google_requests.Request(), client_id)
  provider_id = info["sub"]
  email = info.get("email", "")
  ...
  if not user and email:
      email_hash = hash_email(email)
      user = find_user_by_provider(db, "email", email_hash)
      if user:
          add_user_provider(db, user["id"], "google", provider_id)
  ```
  The code reads `info.get("email")` and uses it to silently merge the Google login into a pre-existing account (created via the email magic-link flow) purely on hash match, without ever inspecting `info.get("email_verified")`.
- Impact: Google's own integration guidance (and common OAuth account-linking guidance) is to treat `email` as untrustworthy for linking unless `email_verified` is `true`. If the claim is unverified for any Google-backed identity (e.g., certain Workspace configurations, freshly-changed addresses), signing in with Google auto-attaches that identity to — and grants full session access to — whatever existing chat account was registered under that email, including its DM history and message-signing keys.
- Fix: Require `info.get("email_verified") is True` before using the email to look up/link an existing account; otherwise treat it as a fresh registration.

## [SEVERITY: MEDIUM] Login CSRF on the Google auth endpoints
- Where: `server/chat_api.py:259-299` (`/chat/api/auth/google`), `server/chat_api.py:302-360` (`/chat/api/auth/google/code`)
- Evidence: Both handlers do `body = await request.json()` with no `state`/anti-CSRF token check anywhere in the function, and no CORS middleware is registered anywhere in the server (`server/` has zero matches for `CORSMiddleware`). Starlette's `Request.json()` parses the raw body regardless of `Content-Type`, so a cross-origin `<form enctype="text/plain">` POST (a "simple request" that browsers send without a CORS preflight) reaches these endpoints with attacker-controlled JSON.
- Impact: An attacker who obtains a valid `id_token`/authorization `code` for **their own** Google account can embed it in a cross-site form and trick a victim into submitting it. The victim's browser gets a `chat_session` cookie for the attacker's account (classic login CSRF) — the victim may then unknowingly send sensitive chat content into an account the attacker controls and can read at any time.
- Fix: Bind a per-session anti-CSRF token (or verify `sec-fetch-site`/`Origin` header) before accepting the credential exchange on both endpoints.

## [SEVERITY: LOW] Session token exposure surface: non-`httpOnly` cookie + token in WebSocket URL
- Where: `server/chat_api.py:196-206` (`_set_session_cookie`), `server/chat_api.py:2100-2102` (`/ws/chat/{token}`)
- Evidence:
  ```
  response.set_cookie("chat_session", token, httponly=False,  # JS reads cookie for WebSocket auth URL
      secure=is_prod, samesite="lax" if not is_prod else "strict", max_age=7*24*3600, path="/")
  ...
  @app.websocket("/ws/chat/{token}")
  ```
- Impact: The full-privilege 7-day session token is (a) readable by any JS on the page via `document.cookie`, and (b) transmitted as a path segment on every WebSocket connection, which typically lands in plaintext in reverse-proxy/access logs (Caddy, per `CLAUDE.md`). This is a deliberate tradeoff for WS auth, but it means any future stored-XSS in message rendering — or log access by a lower-trust party — is a full account takeover rather than a contained bug.
- Fix: Not urgent given the documented tradeoff, but consider a short-lived, single-use WS handshake token minted server-side (via an authenticated REST call) instead of reusing the long-lived session cookie value directly in the WS path.

## [SEVERITY: LOW] Cross-device sync PIN brute-force budget is coarse relative to its blast radius
- Where: `server/api.py:38-43`, `server/api.py:509-522`
- Evidence: `RATE_LIMITS = {"create": (10,3600), "pick": (600,3600), "schedule": (600,3600), "load": (600,3600)}`; `exchange_sync_pin` calls `_check_rate(ip, "load")`, i.e. it shares the generic 600-req/hour bucket. The PIN itself is `f"{secrets.randbelow(1000000):06d}"` (`api.py:500`), 5-minute TTL (`SYNC_PIN_TTL = 300`), and a correct guess grants **full write access** (not read-only) to another user's session (`readonly: False`, `api.py:531`).
- Impact: 600 requests/hour per IP is high relative to a 5-minute-lived, 6-digit (1,000,000-value) secret — a modest number of distributed source IPs can put a meaningful fraction of the keyspace at risk of colliding with any of the currently-outstanding PINs within their validity window, gaining edit access to a stranger's picks/schedule.
- Fix: Add a dedicated, much stricter rate-limit bucket for `/api/sync/{pin}` (e.g., single-digit attempts per IP per minute) independent of the `load` bucket.

## Verified clean
- Email magic-link token: `secrets.token_urlsafe(32)` (256-bit), single-use — row is deleted (`chat_api.py:453`) before the expiry check runs, so a token cannot be replayed even if already expired.
- Session token entropy: `uuid.uuid4().hex + uuid.uuid4().hex` (`chat_db.py:448`) — ample entropy, 7-day expiry enforced in `get_user_by_token` (`chat_db.py:458-465`).
- Admin endpoint gating: every route under `/chat/api/admin/*` calls `_require_admin(request)` as its first statement (checked across `chat_api.py:1647-2077`); the header-token path uses `secrets.compare_digest` (`chat_api.py:175`).
- No endpoint accepts a client-supplied `user_id`/`device_id`-as-self; all mutating routes resolve the acting identity via `_get_user_from_cookie` (grepped across `chat_api.py` and `chat_ws.py`).
- Google ID token verification itself (signature/audience/issuer/expiry) is delegated to `google.oauth2.id_token.verify_oauth2_token`, correctly scoped to `GOOGLE_CLIENT_ID` (`chat_api.py:275-277`, `336-338`).
- Lineup session cookie (`server/api.py:54-63`) is `httponly=True, secure=True, samesite=lax` — correctly hardened, unlike the chat cookie (which has a documented reason for the tradeoff, see LOW finding above).
- Dev-vs-prod cookie branching (`_set_session_cookie`, `chat_api.py:197`) fails safe: if `CHAT_BASE_URL` is unset, `is_prod` defaults to `True` (secure/strict), not `False`.
