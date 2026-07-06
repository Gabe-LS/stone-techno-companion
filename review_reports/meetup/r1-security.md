Good, `GET /meetups/{id}` (line 3186, `loadMeetupJoinState`) is actively used by the client and does leak full attendee identities + GPS to any authenticated caller, confirming the finding is live/exploitable, not dead code. I have enough to write up the findings now.

- ID: security-1
- Severity: critical
- Confidence: certain
- Location: server/chat_ws.py:1748-1832 (`create_meetup` WS handler), compare with `send_message` at server/chat_ws.py:1467-1605 and `join_room` at 1354-1371
- Finding: The `create_meetup` WS event handler takes `stage_id` directly from the client payload and uses it as the target room for injecting a `meetup_invite` message (`create_message(db, stage_id, user_id, "meetup_invite", ...)`) and a room broadcast — with **zero authorization check** that the sender is a member of, or otherwise permitted to post in, that room. Every other write path that touches a room (`send_message`, `join_room`) explicitly checks `dm_participants`/`meetup_attendees`/`room_memberships` and rejects on `is_read_only`/`allows_media`. This one doesn't. Consequences: (1) any authenticated user can inject an unmoderated (`moderation_status` defaults to `"approved"` in `create_message`, so the word filter + OpenAI/GPT layers never run) message into **any** enumerable stage/general room, including read-only announcement rooms, bypassing `is_read_only`; (2) a currently-**muted** user (mute only blocks `moderate_message`/`check_ban_mute` in the normal send path, which `create_meetup` never calls) can use this to keep effectively "posting" while muted; (3) if `stage_id` is a room the attacker isn't a member of (e.g., a group room they never joined, or — if the UUID is known/leaked — someone else's DM or meetup room), a message is written into it and broadcast to its real members under the attacker's own identity, with no membership check at all. This is a full authorization-bypass on the room-write boundary that the rest of the codebase carefully enforces everywhere else.
- Recommendation: Before building/broadcasting the invite, validate `stage_id` the same way `send_message` does: fetch the room, reject if missing, reject if `is_read_only`, and require the sender to already be a member (`room_memberships` for group rooms, `dm_participants`/`meetup_attendees` for dm/meetup types) or restrict `stage_id` to `type in ('stage','general')` rooms the user has joined. Also run `check_ban_mute` (or full `moderate_message`) on the invite the same as any other message before persisting/broadcasting it.
- Effort: S
- Risk of change: low

- ID: security-2
- Severity: high
- Confidence: certain
- Location: server/chat_api.py:1159-1230 (`GET /meetups`, `GET /meetups/{meetup_id}`)
- Finding: Both endpoints require only a valid session (any authenticated chat user), with no check that the caller is an attendee of the meetup or a member of the originating stage room. `GET /meetups` returns exact `location_lat`/`location_lng`, `note`, and the full attendee list (`{id, display_name}`) for **every active meetup event-wide**, and `GET /meetups/{id}` (used by the client's `loadMeetupJoinState`, chat.html:3186) returns the same for a single meetup regardless of membership. The manifest/design intent (per code comments/context) is that GPS + attendee identity are "shared to all attendees" — but the implementation shares them with every logged-in user, attendee or not, before they ever tap Join. In a real-world festival app this is a precise-location + identity disclosure to any registered stranger, which is the kind of privacy/safety surface explicitly called out as sensitive elsewhere in this codebase (E2EE DM design docs, etc.).
- Recommendation: Decide the intended exposure model and enforce it server-side: if meetups are meant to be a public discovery board, strip `location_lat`/`location_lng`/`note`/full attendee identities from the unauthenticated-of-membership view and only reveal them (or the precise ones) once `user_id` is in `meetup_attendees`; if meetups are meant to be attendee/room-scoped, gate both endpoints on `meetup_attendees` membership (or stage-room membership) the same way `/rooms/{room_id}/messages` and `/rooms/{room_id}/online` already gate on it for meetup rooms.
- Effort: M
- Risk of change: medium

- ID: security-3
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1267-1287 (`POST/DELETE /meetups/{meetup_id}/join`), server/chat_ws.py:1834-1866 (`join_meetup`/`leave_meetup` WS handlers)
- Finding: Unlike `POST /meetups` (which calls `manager.check_rate_limit(user["id"])`) and `join_room`/`leave_room` over WS (which call `manager.check_broadcast_rate`), none of the join/leave-meetup paths — REST or WS — apply any rate limit. Each call triggers a DB write plus (on WS) a `get_meetup_attendees` query and a `broadcast_to_room` fan-out to every member of that meetup room. A single authenticated user can loop join/leave against one or many `meetup_id`s to generate unbounded DB writes and broadcast storms at negligible cost.
- Recommendation: Apply the same `check_rate_limit`/`check_broadcast_rate` gate used elsewhere to the REST join/leave endpoints and the WS `join_meetup`/`leave_meetup` handlers.
- Effort: S
- Risk of change: low

- ID: security-4
- Severity: low
- Confidence: likely
- Location: server/chat_db.py:1266-1281 (`join_meetup`, `leave_meetup`); server/chat_api.py:1267-1287
- Finding: `join_meetup`/`leave_meetup` never verify the `meetup_id` exists before writing. `meetup_attendees.meetup_id` has an FK to `meetups(id)`, and SQLite's `INSERT OR IGNORE` silently swallows FK-constraint failures, so joining a bogus/expired/deleted `meetup_id` returns HTTP 200 with an empty attendee array instead of a 404 — the same "success" shape used for a real join. This lets a client distinguish valid vs. invalid meetup IDs (does the returned attendee list contain my own id?) as a minor enumeration side-channel, and papers over otherwise-legitimate 404s for deleted/expired meetups.
- Recommendation: Look up the meetup first and return 404 if absent (mirroring `delete_meetup`'s existence check), before calling `join_meetup`/`leave_meetup`.
- Effort: S
- Risk of change: low

- ID: security-5
- Severity: low
- Confidence: certain
- Location: server/chat/chat.html:2212-2225 (`meetup_invite` render), :1858 (`loadMeetups` room-item render)
- Finding: No XSS found in the current invite/list rendering: `title` is passed through `escapeHtml`/`esc`/`jss` everywhere it's interpolated, and `meetup_id` (server-generated `uuid.uuid4()`, chat_db.py:26) is the only unescaped value placed into HTML attributes / inline `onclick` strings, which is safe since it's never attacker-controlled. However, `note` and `location_label` — both user-supplied, capped at 200/100 chars server-side but not otherwise sanitized — are round-tripped through `GET /meetups`/`GET /meetups/{id}` and are not currently rendered anywhere in chat.html. If a future UI change renders `note`/`location_label` (e.g., in a meetup detail view), it must go through `escapeHtml`/`esc` like `title` does, since nothing in the API response marks them as pre-sanitized.
- Recommendation: No immediate fix required; flagging so any future meetup-detail UI that surfaces `note`/`location_label` uses the existing `esc()`/`escapeHtml()` helpers, consistent with `title`.
- Effort: S
- Risk of change: low
