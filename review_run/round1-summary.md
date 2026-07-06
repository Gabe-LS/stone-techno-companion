# Round 1 Findings Summary (CRITICAL + HIGH)

## CRITICAL
- C1 chat_ws.py:1361-1370,1683-1702 — Arbitrary file deletion: E2EE message `media_url` is unvalidated (validation gated on `not is_e2ee_msg`); delete_message unlinks it verbatim. Any user can delete another user's uploaded file.
- C2 chat_ws.py:141-269 — SSRF via DNS rebinding in link-preview: `_is_safe_preview_url` resolves once, `client.get` resolves again at connect (TOCTOU). Any pasted URL can hit internal/metadata IPs.
- C3 chat.html:2493-2523,4335-4400 — E2EE fail-open: transient `GET /chat/api/keys/{peer}` failure (timeout/5xx) is treated like "no key" 404 and sends DM as plaintext.
- C4 render.py:1518,94-113 — `</script>` breakout XSS: artist name/URLs json.dumps'd into inline `<script>var TT_ARTISTS=...`; `</script>` in a name breaks out. No-click XSS on timetable.

## HIGH
- H1 chat.html:1036 — Session token embedded in WS URL path → logged in uvicorn access log (7-day token theft via log read).
- H2 chat_api.py:428,450-477 — Magic verify token in URL path (`/chat/v/{token}`) logged; no rate limit on verify.
- H3 chat.html auth sites + chat_api.py:217 — Client never sends `device_fingerprint`; fingerprint ban half is dead code → ban evasion via new email/Google.
- H5 chat_db.py:886-920,1256-1287 — E2EE media files never cleaned by TTL purge or ban/mute mass-delete (content is opaque envelope, `.get("url")` always absent; only WS delete_message reads media_url column). Unbounded disk growth.
- H6 chat_ws.py:1444-1457,1601-1665,580-643 — Broadcast storm: typing/add_reaction/remove_reaction/join_room/leave_room have no rate limiting (only send_message/create_meetup do).
- H7 api.py:335-402 — Push scheduler dedup skipped by partial-loop exception: non-WebPushException network error aborts before sent_notifications INSERT → duplicate lineup pushes.
- H8 sw.js:95-112 — pushsubscriptionchange repairs only chat subscription; lineup record left stale (SW has no lineup session code).
- H9 chat_moderation.py:116-132 — Word filter check() has no substring match on message content (whole-word only); "buymdmanow" bypasses. check_username has substring fallback, check() does not.
- H10 chat_moderation.py:460-475 — Partial AI failure not fail-closed: block only when BOTH layers raise. Single-layer 5xx falls through to allowed:True.
- H11 chat_db.py:763-807 + chat_ws.py:1148-1164 — Optimistic delivery: create_message persists before moderation; room_history (join_room) returns not-yet-cleared messages; message_removed only to sender → rejected content visible to re-joiners.
- H12 chat_moderation.py:354-377,482-489 — Auto-bans (strike/mute/AI) ban only frozen users.provider identity; admin_ban fans out to all user_providers. Linked 2nd provider evades auto-ban.
- H13 chat_api.py:1136,1233,1330 — `await file.read()` buffers entire body before size check; no ASGI/Caddy body cap → disk/CPU exhaustion.
- H14 chat_db.py:181-182 — reports.reporter_id/reported_user_id ON DELETE CASCADE; delete_user wipes moderation evidence (bans deliberately have no FK).
- H15 chat_ws.py:1423,328,959 — Untracked asyncio.create_task(_moderate_and_broadcast); SIGTERM on deploy kills mid-flight → message left un-moderated in DB, served via room_history = moderation bypass on every deploy.
- H16 docker-compose.yml — No mem_limit/cpus on app container; upload burst exhausts host.
- H17 deploy.sh:109-143 — Health-check failure only warns, no exit 1, no rollback.
- H18 render.py:2111,2457-2466 — Client JS builds innerHTML with unescaped artist name/photo/link URL in timetable popup + bio modal (esc() used two lines away for other fields). DOM XSS.
- H19 scrape.py:187 — assignment loop missing `if not overlay_id: continue` guard; TBA slot → FK IntegrityError aborts whole scrape (INSERT OR IGNORE doesn't suppress FK violations).
