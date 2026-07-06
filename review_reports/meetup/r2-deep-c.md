Now I have enough evidence to compile the full set of findings.

- ID: DEEP-C-1
- Severity: high
- Confidence: certain
- Location: server/chat_ws.py:1834-1849 (`join_meetup` WS handler), server/chat_db.py:1266-1273 (`join_meetup`), server/chat_db.py:161-166 (`meetup_attendees` FK `ON DELETE CASCADE` to `meetups`)
- Finding: The `join_meetup` WS handler calls `join_meetup(db, meetup_id, user_id)` (an `INSERT OR IGNORE INTO meetup_attendees`) with no check that the meetup still exists. If the 30s purge loop (`purge_expired_meetups`) has already deleted the `meetups` row concurrently, this INSERT violates the `meetup_attendees.meetup_id` foreign key (`foreign_keys=ON` is set on every connection via `get_chat_db`) and raises `sqlite3.IntegrityError`. The WS message loop only wraps `json.loads` in its own try/except (chat_ws.py:1335-1338); every event branch, including `join_meetup`, runs unguarded inside the outer `try` at line 1332, whose `except Exception` (line 2125) logs and falls through to `finally: disconnect(conn_id)`. So a single stale join click doesn't just fail the join — it silently kills the user's entire chat WebSocket connection.
- Recommendation: In `join_meetup` (chat_db.py) check the meetup exists (and hasn't expired) before inserting, returning a boolean/None on failure; in the WS handler, wrap the call and emit a `message_rejected`/`meetup_gone`-style response instead of letting the exception propagate. Apply the same guard to `create_meetup`'s wider handling isn't needed, but at minimum catch `sqlite3.IntegrityError` around this specific insert path.
- Effort: S
- Risk of change: low

- ID: DEEP-C-2
- Severity: high
- Confidence: certain
- Location: server/chat_api.py:1267-1275 (`join_meetup_endpoint`), server/chat/chat.html:3199-3213 (`toggleMeetupJoin`), server/chat/chat.html:2212-2224 (meetup_invite card render)
- Finding: Same unguarded-insert issue as DEEP-C-1 but over REST — `db_join_meetup(db, meetup_id, user["id"])` has no existence/expiry check and no try/except, so joining an already-purged meetup raises an unhandled `sqlite3.IntegrityError` → generic 500. This is trivially reachable, not just a tight race: a `meetup_invite` card is a normal chat message that lives for the room's message TTL (default 1440 min), while the meetup it references typically expires after `meetup_ttl_minutes` (default 60 min). So for the majority of a room's session, any historic invite card's "Join" button points at a meetup_id that's long gone, and clicking it throws a 500 surfaced to the user as a raw error toast.
- Recommendation: Have `join_meetup_endpoint` (and `leave_meetup_endpoint` for symmetry) fetch the meetup first and return `404`/a clean "This meetup has ended" error if missing; render the invite card's Join button as disabled/removed once `loadMeetupJoinState`'s underlying `GET /meetups/{id}` 404s (currently the catch just `dbg()`s and leaves the button saying "Join").
- Effort: S
- Risk of change: low

- ID: DEEP-C-3
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:1424-1443 (`rooms_changed` handler), server/chat_db.py:816-820 (`get_rooms_by_event`, filters `type IN ('stage','general')`), server/chat_api.py:2915 (`admin_delete_meetup` broadcasts `rooms_changed` to all clients)
- Finding: `get_rooms_by_event` — the source of the client's `rooms` array via `loadRooms()` — only ever returns `stage`/`general` type rooms; meetup and DM rooms are never included by design (they're loaded separately via `/meetups` and `/dms`). The `rooms_changed` WS handler, however, does `rooms.find(r => r.id === currentRoom)` and force-navigates the user to the main room whenever that lookup fails. Since a meetup (or DM) room id can never be found in `rooms`, ANY currently-open meetup or DM is unconditionally kicked back to the main room every time `rooms_changed` fires for ANY reason — including admin deleting a completely unrelated meetup (chat_api.py:2915), editing/reordering/creating/deleting any stage room (2588/2658/2688/2717/2739). This is a broad, easily-triggered regression, not limited to the meetup being acted on.
- Recommendation: Guard the redirect branch to only apply when `currentRoomType` is a type actually present in `rooms` (i.e. skip the "still not found → redirect" logic for `dm`/`meetup` room types), or have the server scope `rooms_changed` to affected users only.
- Effort: S
- Risk of change: low

- ID: DEEP-C-4
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:2903-2918 (`admin_delete_meetup`) vs server/chat_ws.py:2195-2205 (purge loop's expiry cleanup)
- Finding: The purge-loop path for an expired meetup broadcasts `meetup_expired` directly to the meetup's room (`manager.broadcast_to_room(meetup_id, ...)`) and evicts it from in-memory state (`manager.rooms.pop(meetup_id, None)`, `manager._room_meta.pop(meetup_id, None)`). The admin-delete path (`admin_delete_meetup`) does neither: it only broadcasts a generic `rooms_changed` to all clients and never touches `manager.rooms`/`manager._room_meta`. Members actively inside an admin-deleted meetup room get no direct `meetup_expired` signal (their only symptom is the disruptive, unrelated `rooms_changed` redirect from DEEP-C-3), and the manager retains a stale `ChatRoom` entry/`_room_meta` entry for that room id indefinitely (only cleared implicitly as members individually disconnect, never as a room-level cleanup) — a divergence from the "normal" expiry path and a small unbounded-growth surface for repeated admin deletions.
- Recommendation: Have `admin_delete_meetup` mirror the purge loop: broadcast `meetup_expired` to `meetup_id`'s room, then pop it from `manager.rooms`/`manager._room_meta`, in addition to the existing `rooms_changed` broadcast.
- Effort: S
- Risk of change: low

- ID: DEEP-C-5
- Severity: medium
- Confidence: likely
- Location: server/chat_db.py:1205-1224 (`create_meetup`, `datetime.fromisoformat(meetup_time)` / expiry math), server/chat_db.py:21-22 (`_now()`), server/chat_ws.py:1756-1759, server/chat_api.py:1245-1248 (validation is parse-only)
- Finding: Neither the WS `create_meetup` handler nor `POST /meetups` validates that `meetup_time` carries a UTC offset or that it's a sane (future) instant — only that `datetime.fromisoformat` doesn't throw. `expires_at` is then stored via `.isoformat()` and later compared against `_now()` (`datetime.now(timezone.utc).isoformat()`) as a raw TEXT/lexicographic comparison in SQL (`purge_expired_meetups`, `get_active_meetups`). If any client (a non-browser API caller, or a future frontend change) supplies a naive datetime, the stored `expires_at` will lack the `+00:00` suffix `_now()` always has, and — more subtly — `datetime.isoformat()` omits the fractional-seconds field entirely when microseconds are exactly 0 (as happens for the browser's `toISOString()`-derived, millisecond-precision timestamps once parsed), while `_now()` almost always includes microseconds. The two ends of the `expires_at <= now` comparison are therefore not guaranteed to have the same string shape, and correctness currently depends on incidental ASCII-ordering behavior rather than an explicit, enforced invariant.
- Recommendation: Reject `meetup_time` values lacking tzinfo (or normalize to UTC server-side) and reject values in the past at creation time; compare expiry using parsed `datetime` objects (or a normalized/zero-padded timestamp format) rather than raw ISO-string lexicographic comparison in SQL.
- Effort: S
- Risk of change: low

- ID: DEEP-C-6
- Severity: medium
- Confidence: likely
- Location: server/chat_db.py:532-551 (`delete_user`), compare server/chat_ws.py:1834-1866 (`join_meetup`/`leave_meetup` broadcast `meetup_updated`)
- Finding: `delete_user` only tears down (and explicitly notifies no one about) meetups the deleted user *created*; for meetups where the user was merely an attendee, the `meetup_attendees` row is removed silently via the `ON DELETE CASCADE` on `users(id)` when the final `DELETE FROM users` runs. No `meetup_updated` broadcast is sent for those meetups, unlike the explicit join/leave WS handlers which always re-broadcast the attendee list. Other users currently viewing that meetup's join button/"N going" count will see a stale, too-high count until something else (a join/leave, or a page reload via `GET /meetups/{id}`) refreshes it.
- Recommendation: In `delete_user`, before the cascade-triggering `DELETE FROM users`, look up which meetups this user was attending (`SELECT meetup_id FROM meetup_attendees WHERE user_id = ?`, excluding ones already torn down as creator) and have the caller (`chat_api.py` account-delete / admin-delete-user) broadcast `meetup_updated` with the refreshed attendee list to each.
- Effort: S
- Risk of change: low

- ID: DEEP-C-7
- Severity: medium
- Confidence: likely
- Location: server/chat_db.py:776-798 (`delete_room`, commits internally), server/chat_db.py:1314-1322 (`delete_meetup`)
- Finding: `delete_meetup` is not atomic: it calls `delete_room(db, meetup_id)`, which performs its own `db.commit()` partway through (after deleting messages/memberships/room), and only afterward does `delete_meetup` issue `DELETE FROM meetup_attendees` / `DELETE FROM meetups` followed by a second, separate `db.commit()`. A crash or unhandled exception between these two commits (e.g. during the admin-panel request) leaves a `meetups` row (and its `meetup_attendees`) referencing a `room_id` that has already been deleted — a "ghost" meetup with no chat room. `get_active_meetups`'s `INNER JOIN rooms` (chat_db.py:1291-1301) would exclude it from the public listing, but `get_all_meetups` (admin, chat_db.py:1303-1312) has no such join and would still list it as if live. It self-heals only once `purge_expired_meetups` naturally reaches its `expires_at` (that function doesn't depend on the room existing), which could be arbitrarily far in the future if the meetup hadn't expired yet when the crash occurred.
- Recommendation: Wrap `delete_meetup` (and ideally `delete_room`) in a single explicit transaction (`BEGIN ... COMMIT`, or defer commits to the outermost caller) so the room and meetup rows are removed atomically.
- Effort: S
- Risk of change: low

- ID: DEEP-C-8
- Severity: medium
- Confidence: certain
- Location: server/chat_ws.py:1549-1551 (`send_message` silently `continue`s when `get_room` returns `None`), server/chat/chat.html:1334-1352 / 1445-1456 (`message_acked`/`message_rejected` are the only paths that resolve a pending bubble; no timeout fallback found)
- Finding: If a user is composing/sends into a meetup room that gets purged (or admin-deleted) in the same window, the server drops the message entirely with no `message_rejected` (the `send_message` handler just does `if not send_room: continue`). The client's optimistic-send UI has no client-side timeout for a pending message — it only transitions out of "pending" on `message_acked` or `message_rejected`. The result is a message bubble stuck showing a pending/"sending" state indefinitely with no error ever surfaced to the user.
- Recommendation: Have `send_message` emit a `message_rejected` (e.g. reason "This room no longer exists") when `send_room` is `None`, and/or add a client-side timeout (e.g. 10-15s) that marks a still-pending message as failed with a retry affordance.
- Effort: S
- Risk of change: low

- ID: DEEP-C-9
- Severity: low
- Confidence: certain
- Location: server/chat_ws.py:1748-1832 (`create_meetup` WS handler), server/chat_api.py:1232-1264 (`POST /meetups`), server/chat_db.py:1205-1224 (`create_meetup` expiry math)
- Finding: Neither creation path rejects a `meetup_time` in the past — only `datetime.fromisoformat` parse success is checked. A meetup created with a past `meetup_time` gets an `expires_at` that can already be `<= now`, making it immediately excluded from `get_active_meetups`'s listing (filtered on `expires_at > now`) while a `meetup_invite` card is still posted into the stage room (chat_ws.py:1772-1824) advertising it, and the row/attendees/room persist for up to ~30s until the next purge tick. Any user who taps "Join" on that card in the interim races the purge (DEEP-C-1/2); once purged, the card is a permanently dead invite with no visual indication it expired on arrival.
- Recommendation: Reject (400/ignore) `meetup_time` values that are not sufficiently in the future (e.g. `<= now`) at both creation entry points.
- Effort: S
- Risk of change: low

- ID: DEEP-C-10
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:1059-1064 (WS `onopen` re-sends `join_room` for `currentRoom`), server/chat_ws.py:1354-1359 (`join_room` handler no-ops when `get_room` returns `None`), server/chat/chat.html:1976 (`historyReady` 3s timeout with no error path)
- Finding: If a client disconnects (network blip, tab backgrounded, etc.) while a meetup room is open, and that meetup is purged during the disconnect gap, reconnection sends `join_room` for the now-gone room id. The server's `join_room` handler requires `room_row` to exist (`if room_id and room_row:`) and silently does nothing otherwise — no `room_history`, no explicit "room gone"/error event. The client has no fallback for a `join_room` that never produces `room_history`: the only related timer (`historyReady`, a 3s `setTimeout`) just unblocks scroll-position logic without showing any error or navigating away. The user is left staring at the stale pre-disconnect message list, attendee count, and an enabled composer indefinitely, with no signal the meetup ended, until they navigate away manually or an unrelated `rooms_changed` event happens to fire (DEEP-C-3's redirect side effect).
- Recommendation: Add an explicit server response (e.g. `room_not_found` / reuse `meetup_expired`) when `join_room` targets a nonexistent room, and have the client handle it by showing a banner and/or navigating back to the main room.
- Effort: S
- Risk of change: low
