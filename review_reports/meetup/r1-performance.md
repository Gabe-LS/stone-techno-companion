This is sufficient depth for the performance/concurrency perspective. Compiling findings.

## Findings

- ID: performance-1
- Severity: medium
- Confidence: certain
- Location: `server/chat_api.py:1159-1202` (`list_meetups`), specifically the `last_msgs` query at 1167-1172
- Finding: `GET /meetups` runs `SELECT room_id, MAX(created_at) FROM messages WHERE expires_at > ? GROUP BY room_id` with no `room_id` scoping. This aggregates over **every non-expired message in the entire chat system** (all group rooms + all DMs, not just meetup rooms) just to sort a handful of meetups by last activity. The existing indexes (`idx_messages_expires` on `expires_at`, `idx_messages_room` on `(room_id, created_at)`) don't cover this access pattern — SQLite has to range-scan by `expires_at` then build a temp group-by on `room_id`, touching the full live-message working set of the app. This runs on every meetups-tab open, independent of how many meetups exist, and gets more expensive as overall chat volume grows.
- Recommendation: Scope the query to only the active meetup room ids, e.g. `WHERE room_id IN (<meetup ids from get_active_meetups>) AND expires_at > ?`, or add a covering index and compute this from `rooms.last_message_at` (already maintained) instead of re-aggregating `messages`.
- Effort: S
- Risk of change: low

- ID: performance-2
- Severity: medium
- Confidence: certain
- Location: `server/chat_api.py:1173-1198` (`list_meetups` loop), `server/chat_db.py:1283-1289` (`get_meetup_attendees`)
- Finding: N+1 pattern — `get_active_meetups` returns all active meetups in one query (with a `COUNT(*)` subquery per row for `attendee_count`, itself fine), but the endpoint then calls `get_meetup_attendees(db, m["id"])` (a separate `JOIN` query) **once per meetup** just to compute `is_going` and populate a full attendee list the primary caller (`loadMeetups` in chat.html:1844-1868) never uses (it only reads `attendee_count`/`is_going`). For M active meetups this is 1 + M round-trip queries per list load.
- Recommendation: Replace the per-meetup `get_meetup_attendees` call with a single batched query, e.g. `SELECT meetup_id, user_id FROM meetup_attendees WHERE meetup_id IN (...)` to build an `is_going`/count map in one pass; only fetch the full attendee roster (with display names) for the single-meetup detail endpoint (`GET /meetups/{id}`) where it's actually rendered.
- Effort: S
- Risk of change: low

- ID: performance-3
- Severity: low
- Confidence: certain
- Location: `server/chat/chat.html:2212-2224` (meetup_invite card render) calling `loadMeetupJoinState` (chat.html:3183-3197 → `GET /meetups/{meetup_id}`)
- Finding: Every rendered `meetup_invite` message card fires its own `GET /meetups/{id}` (2 DB queries: fetch meetup + `get_meetup_attendees`) via `setTimeout(() => loadMeetupJoinState(mid), 0)`. On any room-history load or re-render, each visible invite card issues an independent HTTP round trip with no caching/dedup/batching — for a room with several outstanding meetup invites this is M parallel requests where one `GET /meetups` list call could satisfy all of them.
- Recommendation: Either cache the `GET /meetups/{id}` result client-side (keyed by meetup id, invalidated on `meetup_updated`/`join`/`leave`), or fetch the active-meetups list once per room-open and hydrate all visible cards from it instead of a per-card fetch.
- Effort: S
- Risk of change: low

- ID: performance-4
- Severity: high
- Confidence: certain
- Location: `server/chat_ws.py:1834-1849` (`join_meetup` WS handler) and `server/chat_db.py:1266-1272` (`join_meetup`) vs `server/chat_db.py:1325-1361` (`purge_expired_meetups`, runs every 30s in `purge_loop`)
- Finding: Real check-then-act race. `GET /meetups` only lists meetups where `expires_at > now` (chat_db.py:1297), but between that read and the user tapping "Join", the 30s purge loop can delete the `meetups` row (and its room) out from under them. `join_meetup`/the WS `join_meetup` handler perform `INSERT INTO meetup_attendees ... VALUES (?,?,?)` with **no existence/expiry check**, and `meetup_attendees.meetup_id` has an `ON DELETE CASCADE` FK to `meetups(id)` under `foreign_keys=ON`. If the meetup row is gone, this INSERT raises `sqlite3.IntegrityError`, which is unhandled inside the WS event loop. The entire `while True` receive loop for that connection is wrapped in one outer `except Exception: logger.exception(...)` (chat_ws.py:2125) that falls through to `finally: disconnect(...)` — so a single mistimed join **drops the user's whole WebSocket connection**, not just that one action. The REST path (`chat_api.py:1267-1275`, `db_join_meetup`) has the same unguarded INSERT and no visible global exception handler wrapping it either, so it likely surfaces as a raw 500 to the client.
- Recommendation: Make `join_meetup`/`leave_meetup` tolerant of a concurrently-purged meetup: check `SELECT 1 FROM meetups WHERE id=? AND expires_at > ?` before inserting (or catch `sqlite3.IntegrityError` around the insert) and return a clean "meetup no longer active" response instead of letting it propagate. This should not disconnect the whole socket for what is a normal, expected race under a 30s purge cadence.
- Effort: S
- Risk of change: low

- ID: performance-5
- Severity: medium
- Confidence: certain
- Location: `server/chat/chat.html:3199-3213` (`toggleMeetupJoin`, REST-only) vs `server/chat_ws.py:1834-1866` (`join_meetup`/`leave_meetup` WS handlers, which broadcast `meetup_updated`) vs `server/chat/chat.html:1494-1505` (`meetup_updated` handler)
- Finding: The actual join/leave UI path (`toggleMeetupJoin`) calls only the REST endpoints (`POST`/`DELETE /meetups/{id}/join`), which never broadcast anything over WS. The WS `join_meetup`/`leave_meetup` events that *do* broadcast `meetup_updated` (attendee list + count) to the whole room are never sent by the frontend (`grep` for `wsSend('join_meetup'` / `'leave_meetup'` in chat.html returns nothing) — they appear to be dead server-side code paths for the current client. Compounding this, the `meetup_updated` client handler queries `.meetup-join-btn` (chat.html:1495), a class that doesn't exist anywhere in the actual rendered markup (`.meetup-join`/`.meetup-join-wide`, chat.html:2223), so even a stray broadcast would silently no-op. Net effect: there is no real-time attendee-count sync for meetups at all; the system instead relies on the per-card polling described in performance-3, and other viewers only see updated counts on their next manual list/card refresh.
- Recommendation: Either wire `toggleMeetupJoin` to also emit the WS `join_meetup`/`leave_meetup` events (or have the REST handlers themselves broadcast `meetup_updated` via `manager.broadcast_to_room`), and fix the `.meetup-join-btn` selector to match the real `.meetup-join` class — this removes the need for the poll-per-card workaround in performance-3 and gives attendees live counts instead of stale/polled ones.
- Effort: M
- Risk of change: medium (touches both join/leave call sites and the broadcast payload consumers)

- ID: performance-6
- Severity: low
- Confidence: likely
- Location: `server/chat_ws.py:1834-1849` / `1851-1866` (`join_meetup`/`leave_meetup` broadcast of full attendee roster)
- Finding: *If* performance-5 is fixed and these handlers become live, note that both rebuild and broadcast the **entire** attendee list (`get_meetup_attendees` → `JOIN users`) to every connected member of the meetup room on every single join/leave. For a popular meetup with N attendees, each join/leave event costs one JOIN query plus O(N) payload fan-out to O(N) sockets — O(N²) total work across a meetup's lifetime if attendance churns. This is speculative impact since the path is currently unreachable from the client (see performance-5), but should be considered before re-enabling it.
- Recommendation: Broadcast only the delta (joined/left user id + new count) instead of the full roster; let clients increment/decrement locally and only fetch the full roster on demand (e.g. opening a "who's going" view).
- Effort: S
- Risk of change: low

- ID: performance-7
- Severity: low
- Confidence: certain
- Location: `server/chat_db.py:1325-1361` (`purge_expired_meetups`)
- Finding: Non-batched per-meetup work inside a single DB connection: for each expired meetup, a separate `SELECT ... FROM messages WHERE room_id = ?` is executed, followed by separate `DELETE FROM messages WHERE room_id = ?` and `DELETE FROM rooms WHERE id = ?` — i.e., 3 round-trip statements per expired meetup rather than one batched `IN (...)` query/delete, before a single commit at the end. Since this runs embedded (no network RTT) the absolute cost is small, but it scales linearly with however many meetups expire in the same 30s purge tick (e.g., many meetups all timed for a set-change moment), and it's holding the connection/transaction open for the whole loop.
- Recommendation: Batch with `WHERE room_id IN (...)` for the messages/rooms deletes; keep the per-row loop only where genuinely needed (media URL extraction), or bulk-fetch media URLs in one query with an `IN` clause too.
- Effort: S
- Risk of change: low

- ID: performance-8
- Severity: low
- Confidence: likely
- Location: `server/chat_ws.py:2157-2262` (`purge_loop`) — sleeps `await asyncio.sleep(30)` at line 2262
- Finding: The manifest and general docs describe a "60s purge loop," but the actual interval is 30s (`asyncio.sleep(30)`). This isn't a bug per se, but it means `purge_expired_meetups`, `purge_expired_messages`, the empty-DM sweep, and the WAL checkpoint/stale-subscription sweeps all run twice as often as documented — worth confirming this is intentional, since it doubles the steady-state query/lock overhead of the whole purge pipeline (including the meetup N+1 in performance-7) relative to what's documented elsewhere in the codebase.
- Recommendation: Reconcile the interval with documentation, or explicitly justify 30s (e.g., tighter TTL granularity needs) so future changes to this loop don't assume 60s.
- Effort: S
- Risk of change: low

- ID: performance-9
- Severity: low
- Confidence: speculative
- Location: `server/chat_db.py:94` (`rooms` table def, no index on `event_id`) used by `get_active_meetups` (chat_db.py:1291-1301, `JOIN rooms r ON r.id = m.id WHERE r.event_id = ?`) and `delete_user`'s `SELECT id FROM meetups WHERE creator_id = ?` (chat_db.py:539-541, no index on `meetups.creator_id`)
- Finding: Both queries filter on unindexed columns, forcing a full table scan of `rooms`/`meetups` respectively. Given this app is currently single-event (`DEFAULT_EVENT_ID`) and meetups are bounded by TTL + 30s purging, table sizes stay small in practice, so real-world impact is likely negligible today — flagging as speculative in case multi-event usage or a much larger festival changes that assumption.
- Recommendation: If `get_active_meetups`/`delete_user` show up in profiling, add `idx_rooms_event` on `rooms(event_id)` and `idx_meetups_creator` on `meetups(creator_id)`.
- Effort: S
- Risk of change: low
