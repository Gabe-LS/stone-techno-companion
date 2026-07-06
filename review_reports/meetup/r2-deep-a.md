- ID: DEEP-A-1
- Severity: critical
- Confidence: certain
- Location: server/chat_ws.py:1748-1832 (`create_meetup` handler, invite-card insert at 1772-1793), contrasted with the guards on `send_message` at server/chat_ws.py:1549-1603
- Finding: The `create_meetup` WS handler, when `stage_id` is supplied, calls `create_message(db, stage_id, user_id, "meetup_invite", ...)` and broadcasts it with **none** of the checks `send_message` applies to the same target room: no `get_room(...)` existence/read-only check (`is_read_only` is never consulted), no `is_moderated` content scan, no ban/mute re-check, and no room-type gate (DM/meetup) before writing. Every `stage`/`general` room's id is fully enumerable via `GET /chat/api/rooms` regardless of membership or read-only status (server/chat_api.py:959-978, `get_rooms_by_event` filters only by event, not membership). So any authenticated, non-banned user can post an approved, completely unmoderated message into ANY group room in the event — including read-only "announcements"-style rooms meant to be admin-only — with a single `create_meetup` call.
- Recommendation: Before creating/broadcasting the invite message, apply the same authorization pipeline `send_message` uses: `send_room = get_room(db, stage_id)`; reject if missing; reject if `send_room["is_read_only"]`; for `type == "dm"` require `dm_participants` membership and no block; for `type == "meetup"` require `meetup_attendees` membership; re-check `check_ban_mute` immediately before the insert; and route the title through content moderation (see DEEP-A-3) before marking it `approved`.
- Effort: M
- Risk of change: low

- ID: DEEP-A-2
- Severity: high
- Confidence: certain
- Location: server/chat_ws.py:1748-1832; server/chat_api.py:1232-1264; server/chat_db.py:1205-1264
- Finding: Neither meetup-creation path (WS `create_meetup` or `POST /chat/api/meetups`) calls `check_ban_mute`/`is_muted` anywhere. A muted user — who is explicitly blocked from sending text/image/video/location messages via the `moderate_message`/`check_ban_mute` gate — can still create a brand-new public meetup (room + attendee row + optional invite broadcast) via WS, or silently via REST, entirely bypassing their mute.
- Recommendation: Call `await check_ban_mute(db, user_id)` at the top of both `create_meetup` handlers and reject with the standard `muted`/`banned` events (mirroring the pattern in `_moderate_and_broadcast`) before touching the DB.
- Effort: S
- Risk of change: low

- ID: DEEP-A-3
- Severity: high
- Confidence: certain
- Location: server/chat_ws.py:1748-1771; server/chat_api.py:1232-1264; server/chat_db.py:1205-1264
- Finding: `title`, `note`, and `location_label` never pass through the word filter, OpenAI omni-moderation, or GPT-nano content pipeline that every other message type goes through — they are only length-truncated (60/200/100 chars). `title` becomes the meetup room's display name, is embedded verbatim (only HTML-escaped, not moderated) in the `meetup_invite` card broadcast into a group room, and `title`/`note`/`location_label` are all returned verbatim to every authenticated user via `GET /chat/api/meetups`. This is a clean, unmonitored channel for slurs/drug terms/spam/scam text that the rest of the app is specifically designed to catch and strike for.
- Recommendation: Run `title`/`note`/`location_label` (at minimum the free word-filter layer, ideally the full pipeline given they're user-supplied free text shown across the app) before `create_meetup`/`create_room` execute, and strike/reject on failure the same way `_moderate_and_broadcast` does for messages.
- Effort: M
- Risk of change: low-medium

- ID: DEEP-A-4
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1232-1264 vs server/chat_ws.py:1748-1832
- Finding: `POST /chat/api/meetups` (REST) never creates the invite-card message and never broadcasts `meetup_created` — only the WS handler does both. The same nominal action has materially different, less visible/auditable side effects depending on which API surface is used. This also means the REST path is the "quieter" way to pollute the public active-meetups list or plant a persistent room (see DEEP-A-5), since it leaves no message trace in any room for members/admins to notice.
- Recommendation: Factor the invite-card + broadcast logic into a function shared by both endpoints (after adding the authz/moderation checks from DEEP-A-1/2/3), so REST and WS produce identical, equally-guarded side effects.
- Effort: M
- Risk of change: low

- ID: DEEP-A-5
- Severity: medium
- Confidence: certain
- Location: server/chat_db.py:1205-1264 (`create_meetup`), server/chat_db.py:1325-1355 (`purge_expired_meetups`), server/chat_ws.py:1756-1759, server/chat_api.py:1245-1248
- Finding: `meetup_time` is validated only for ISO-parseability (`datetime.fromisoformat`), with no bound on how far in the future it can be, and no cap on how many concurrently-active meetups a user/event can have. Since `expires_at = meetup_time + meetup_ttl_minutes` and purge only removes rows once `expires_at <= now`, a crafted far-future `meetup_time` (e.g. year 9999) creates a `meetups` row + a full `rooms` row that is never purged, stays permanently visible in the public "active meetups" list, and can only be removed by manual admin deletion. Combined with the shared 10-req/10s rate limit, this allows fast unbounded growth of persistent rooms.
- Recommendation: Reject `meetup_time` outside a sane window (not too far in the past, not beyond the event's end date + some margin), and/or cap concurrently-active meetups per creator.
- Effort: S
- Risk of change: low

- ID: DEEP-A-6
- Severity: medium
- Confidence: likely
- Location: server/chat_ws.py:1772-1793
- Finding: The invite-message insert in `create_meetup` has no room-type gate. If `stage_id` happens to reference a `dm`-type room, the code will insert a plaintext `meetup_invite` message directly into that DM's message history and broadcast it live to connected participants — bypassing the E2EE design entirely (the server is meant to never author unencrypted content inside a DM) as well as the block check `send_message` enforces for DMs (`is_blocked` at chat_ws.py:1584-1596). Exploitability depends on the attacker knowing/guessing a target DM's room_id, which isn't exposed via `GET /rooms`, but could leak via `/chat/msg/{id}` permalinks, client-side state, or a former DM participant whose access was never revoked — hence "likely" rather than "certain".
- Recommendation: Explicitly reject `stage_id` when `get_room(db, stage_id)["type"] in ("dm", "meetup")` before using it for the invite insert.
- Effort: S
- Risk of change: low

- ID: DEEP-A-7
- Severity: low
- Confidence: certain
- Location: server/chat_ws.py:1780-1793 vs the FK definition at server/chat_db.py:121-133 (`messages.room_id REFERENCES rooms(id)`, `foreign_keys=ON`) and the catch-all at server/chat_ws.py:2125-2126
- Finding: The code never verifies `stage_id` refers to an existing room before calling `create_message`; it only conditionally reads `get_room(...)` for TTL and falls back to a default when `None`. A bogus/non-existent `stage_id` therefore raises an uncaught `sqlite3.IntegrityError` (FK violation) inside the WS receive loop, caught only by the generic `except Exception` at the bottom of `handle_chat_ws`, which terminates that user's entire WebSocket connection. Self-inflicted only, but indicates this path was never exercised against invalid input.
- Recommendation: `if stage_id and not get_room(db, stage_id): stage_id = None` (or reject) before use, instead of relying on the DB layer to fail.
- Effort: S
- Risk of change: low

- ID: DEEP-A-8
- Severity: low
- Confidence: certain
- Location: server/chat_ws.py:1767-1768; server/chat_api.py:1257-1258; server/chat_db.py:1212-1213
- Finding: `lat`/`lng` are passed through with no type or range validation (not cast to float, no -90..90/-180..180 bounds check) on either creation path. SQLite's dynamic typing stores whatever is sent. Currently unused by any renderer in chat.html, so today's impact is limited to data hygiene, but it's an unvalidated field waiting for a future consumer (map/ICS export) to mishandle.
- Recommendation: Validate lat/lng are finite numbers within valid geographic range before storing; drop/reject otherwise.
- Effort: S
- Risk of change: low

- ID: DEEP-A-9
- Severity: low
- Confidence: likely
- Location: server/chat_db.py:1219-1224 (`mt = datetime.fromisoformat(meetup_time)`); server/chat_ws.py:1756-1759; server/chat_api.py:1245-1248
- Finding: `meetup_time` validation only checks that the string parses via `datetime.fromisoformat`, which accepts timezone-naive strings. `expires_at` is then computed and stored without a UTC offset while purge/list queries compare against `_now()` (timezone-aware). A client submitting a naive local time can cause the meetup's actual purge time to silently drift by the client's UTC offset from what was intended, and mixes naive/aware ISO strings in the same column.
- Recommendation: Require or assume a UTC offset consistently (reject naive input, or normalize it to UTC) before computing `expires_at`.
- Effort: S
- Risk of change: low

- ID: DEEP-A-10
- Severity: low
- Confidence: certain
- Location: server/chat/chat.html (no references to `location_lat`/`location_lng`/`location_label`/`note`; no `case 'meetup_created'` handler, vs. broadcast at server/chat_ws.py:1829-1832)
- Finding: `note`, `location_label`, `location_lat`, `location_lng` are collected, length-capped, stored, and returned by both `GET /chat/api/meetups` and `GET /chat/api/meetups/{id}`, yet chat.html never renders them anywhere — only `title`/`meetup_time`/attendee counts show up. Separately, the `meetup_created` WS event has no client-side handler at all, so real-time propagation of a newly created meetup to other users' room lists appears to depend on an unrelated refresh trigger. Not a vulnerability by itself, but it's exactly the kind of "already exposed via API, not yet rendered" surface where a future UI change could introduce an XSS/moderation regression unnoticed (see DEEP-A-3).
- Recommendation: Either wire up real note/location display + a `meetup_created` handler, or explicitly scope down what's collected/returned; don't treat these fields as low-priority for moderation (DEEP-A-3) just because they're currently unrendered.
- Effort: S/M
- Risk of change: low
