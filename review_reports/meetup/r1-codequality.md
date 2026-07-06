- ID: codequality-1
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1232-1264 (`create_meetup_endpoint`) vs server/chat_ws.py:1748-1832 (`elif event == "create_meetup"`)
- Finding: Meetup creation is implemented twice with diverging behavior. The WS handler posts a `meetup_invite` card message into the origin room and broadcasts `message`/`meetup_created` events so other connected clients see the new meetup live. The REST endpoint calls the same `create_meetup()` DB function but does neither — no invite card, no WS broadcast to anyone. The shipped frontend (`chat.html:3291`, `submitMeetup`) only ever calls `wsSend('create_meetup', ...)`, so the REST creation path is effectively unused/untested-in-practice yet remains a fully public POST endpoint. Any future caller (mobile client, integration, admin tool) that uses the documented REST API will silently create a "invisible" meetup that nobody else is notified about.
- Recommendation: Make one of the two call `create_meetup()` and then run a single shared "post-create" routine (invite card + broadcast) so REST and WS produce identical side effects, or explicitly deprecate/remove the REST creation endpoint if WS-only creation is intended.
- Effort: M
- Risk of change: low

- ID: codequality-2
- Severity: high
- Confidence: certain
- Location: server/chat_ws.py:1834-1867 (`join_meetup`/`leave_meetup` WS handlers) vs server/chat/chat.html:3199-3213 (`toggleMeetupJoin`), and chat.html:1494-1505/1506-1509 (`meetup_updated`/`meetup_expired` handlers) vs chat.html:2223/3192 (actual button markup)
- Finding: Two independent bugs compound into a fully broken real-time attendee-count feature. (1) The frontend join/leave flow exclusively calls the REST endpoints (`api('/meetups/{id}/join')`), never the WS `join_meetup`/`leave_meetup` events — making the WS handlers in chat_ws.py dead code from the shipped client's perspective, and the REST endpoints never broadcast the `meetup_updated` event the WS path produces. (2) Even disregarding that, the client's `meetup_updated`/`meetup_expired` handlers look up `document.querySelector('[data-meetup-id]…] .meetup-join-btn')`, but the actual rendered button class is `.meetup-join`/`.meetup-join-wide` (chat.html:2223) — `.meetup-join-btn` does not exist anywhere in the DOM (confirmed via grep). So even if the WS broadcast path were reachable, the handler would silently no-op (`mBtn` is always null). Net effect: no user ever sees another attendee's join/leave, or a meetup's expiry, update live — only on next `loadMeetups()`/`loadMeetupJoinState()` fetch.
- Recommendation: Pick one transport for join/leave (REST, since that's what ships) and have it also broadcast `meetup_updated` to the room; fix the two selector references to `.meetup-join`; then either wire the WS `join_meetup`/`leave_meetup` handlers to that same REST path or delete them as dead code.
- Effort: S
- Risk of change: low

- ID: codequality-3
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1094-1117 (`get_message_context`) vs chat_api.py:1059-1091 (`room_messages`) and 1132-1151 (`room_online`)
- Finding: The meetup-room access check (`SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?`) is duplicated inline at every gated endpoint except this one. `get_message_context` (backing the `/chat/msg/{id}` permalink feature) only guards `room["type"] == "dm"`; it has no `elif room["type"] == "meetup"` branch. Any authenticated user can hit `GET /chat/api/messages/{message_id}` for a message inside a meetup room they never joined and learn the room_id/room_name/room_type — a case the sibling endpoints explicitly deny with a 403/404. This is a direct symptom of the access check being copy-pasted per-endpoint instead of centralized: whoever added the meetup branch to `room_messages`/`room_online` didn't update this one.
- Recommendation: Extract a shared `_check_room_access(db, room, user_id) -> bool` (or raise) helper covering both `dm` and `meetup` types, and use it at every gated endpoint including `get_message_context`.
- Effort: S
- Risk of change: low

- ID: codequality-4
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1267-1275 (`join_meetup_endpoint`) / server/chat_db.py:1266-1272 (`join_meetup`)
- Finding: `join_meetup_endpoint` never checks that `meetup_id` refers to an existing meetup before calling `join_meetup()`, which does `INSERT OR IGNORE INTO meetup_attendees (meetup_id, ...)`. `meetup_attendees.meetup_id` has `REFERENCES meetups(id)` and `foreign_keys=ON` is set on every connection (chat_db.py:32), so inserting a row for a non-existent meetup_id raises `sqlite3.IntegrityError: FOREIGN KEY constraint failed`. There is no try/except here (contrast with the one other `except sqlite3.IntegrityError` in the file at chat_api.py:912) and no global exception handler, so this becomes an unhandled 500 instead of a clean 404. No test exercises this path (`test_chat_api.py::TestMeetups` only covers valid meetup ids).
- Recommendation: Look up the meetup first and raise `HTTPException(404, "Meetup not found")` if missing, mirroring the pattern already used in `get_meetup`/`delete_meetup`.
- Effort: S
- Risk of change: low

- ID: codequality-5
- Severity: medium
- Confidence: certain
- Location: server/chat_db.py:1245 (`create_meetup` → `create_room` call) vs chat_db.py:1388-1390 (`find_or_create_dm` → `create_room(..., is_moderated=False)`) and chat_db.py:698-712 (`create_room` default `is_moderated: bool = True`)
- Finding: `create_meetup()`'s call to `create_room()` passes no `is_moderated` argument, so meetup rooms silently inherit the default `True` (moderated, like group rooms). This is never asserted by any test (`TestMeetups::test_create_meetup` in both test_chat_db.py and test_chat_api.py never reads `room["is_moderated"]`), and the manifest recon itself flagged this as an open question ("meetup rooms are NOT moderated the same as group rooms? check is_moderated"). By contrast, the DM sibling makes the equivalent decision explicit at the call site (`is_moderated=False`). Given meetups carry real GPS coordinates and are ad-hoc, festival-goer-created spaces, whether they get word-filter + AI moderation is a safety-relevant behavior that is currently just an accidental consequence of a default parameter rather than a documented, tested decision.
- Recommendation: Make the choice explicit at the `create_meetup` call site (`is_moderated=True` or `False`, whichever is intended) and add a test asserting it, the same way DM's `is_moderated=False` is explicit and easy to audit.
- Effort: S
- Risk of change: low

- ID: codequality-6
- Severity: medium
- Confidence: certain
- Location: server/chat_ws.py:1366-1371, 1598-1601, 1934-1936, 1969-1971, 2068-2070; server/chat_api.py:1072-1077, 1143-1148 (and missing at 1094-1117, see codequality-3)
- Finding: The raw SQL snippet `"SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?"` (paired with an identical `dm_participants` check) is hand-copied at at least 7 separate call sites across chat_ws.py and chat_api.py to gate room-join, send, presence, report, and message-history operations for meetup rooms. This is the root cause that let codequality-3 happen — one of ~8 near-identical blocks was simply never written. Every future change to meetup access semantics (e.g., allowing spectators, revoking access on ban) requires updating all of these in lockstep, with no compiler/test signal if one is missed.
- Recommendation: Centralize into one helper (e.g. `_user_can_access_room(db, room, user_id)`) used everywhere instead of re-inlining the SQL.
- Effort: M
- Risk of change: low

- ID: codequality-7
- Severity: medium
- Confidence: certain
- Location: server/chat_db.py:532-548 (`delete_user`), 1314-1322 (`delete_meetup`), 1325-1361 (`purge_expired_meetups`); server/chat_ws.py:2195-2205 (purge loop manager cleanup)
- Finding: There are three independent implementations of "tear down a meetup and its room," none of which share code, and they've already diverged: `delete_user()` re-implements `delete_meetup()`'s exact body (`delete_room()` + explicit `DELETE FROM meetup_attendees` + `DELETE FROM meetups`) inline instead of calling `delete_meetup(db, meetup_id)`; `purge_expired_meetups()` reimplements room/message deletion again without calling `delete_room()`, relying instead on FK cascade for `meetup_attendees` and `room_memberships`. Only the purge-loop caller (chat_ws.py:2204-2205) remembers to also pop the in-memory `manager.rooms`/`manager._room_meta` entries for the deleted room id; the admin-delete path (`admin_delete_meetup` → `delete_meetup`) and the `delete_user` teardown do not, leaving stale in-memory WS-manager state for meetups deleted via those two paths. Because there's no single "delete a meetup" entry point, a future requirement (e.g., also unlink media, also close live sockets in that room, also clean manager state) is likely to be added to only one of the three copies, as already happened here.
- Recommendation: Make `delete_meetup(db, meetup_id)` the single source of truth and have `delete_user`'s loop call it directly; give `purge_expired_meetups` a batched variant of the same function (or refactor `delete_meetup` to accept a list of ids) instead of a third hand-rolled implementation; move the `manager.rooms`/`manager._room_meta` cleanup into whatever async layer wraps every meetup-deletion caller (or a synchronous callback list) so it can't be forgotten again.
- Effort: M
- Risk of change: medium (touches deletion paths used by user-delete, admin-delete, and the purge loop — needs test coverage around each before consolidating)

- ID: codequality-8
- Severity: medium
- Confidence: certain
- Location: server/chat_ws.py:115-116 and 1140-1141 (push/badge preview text) vs chat_ws.py:1789 (`create_message(db, stage_id, user_id, "meetup_invite", ...)`) vs chat.html:2212 (`m.type === 'meetup_invite'`)
- Finding: Both places that generate a human-readable preview for a meetup message (push notification body and unread-badge preview) check `msg_type == "meetup_card"`. But the message type actually created for a meetup invite everywhere else in the codebase is `"meetup_invite"` — confirmed by grep, `"meetup_card"` appears only in these two dead branches and nowhere is a message ever created with that type. The condition can never match, so both fall through to `return ""` / `text_preview = ""`: a meetup invite's push notification and badge preview text render as blank instead of "Shared a meetup". This looks like a rename (`meetup_card` → `meetup_invite`) that updated the message-type creation and the frontend renderer but missed these two preview-text helpers — which, being duplicated rather than shared, both needed (and both missed) the same fix.
- Recommendation: Change both conditions to `msg_type == "meetup_invite"`; add a regression test asserting the push/badge preview text for a meetup_invite message; consider a single shared constant/dict for message-type → preview-text instead of duplicating the if/elif chain (ties into the same "message-type string has no single source of truth" root cause as codequality-6).
- Effort: S
- Risk of change: low

- ID: codequality-9
- Severity: medium
- Confidence: certain
- Location: tests/test_chat_ws.py (meetup coverage: only line 945-968, which calls `create_meetup`/`join_meetup` DB functions directly, never sends the WS `create_meetup`/`join_meetup`/`leave_meetup` events through the socket); tests/test_chat_api.py `TestMeetups` (no test for joining a non-existent meetup, no assertion on `is_moderated`, no assertion that create posts an invite message/broadcasts)
- Finding: None of the bugs in codequality-2, -4, -5, or -8 would have been caught by the existing suite, because the WS meetup event handlers are never exercised end-to-end (only their underlying DB helpers are called directly in tests), and the REST meetup tests only cover the happy path. This is a coverage gap specific to the meetup feature's cross-layer (REST/WS/frontend) wiring — the kind of bug that unit tests of individual DB functions structurally cannot catch.
- Recommendation: Add a WS-level test that sends `{"event": "create_meetup", ...}` / `join_meetup` / `leave_meetup` over an actual test socket and asserts the resulting broadcast payloads (would have caught codequality-2 and codequality-8); add a REST test posting to `/meetups/{bogus_id}/join` expecting 404 (codequality-4); add an assertion on `room["is_moderated"]` after `create_meetup` (codequality-5).
- Effort: M
- Risk of change: low
