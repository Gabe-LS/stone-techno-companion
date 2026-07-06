## Database Schema & Query Review

### HIGH

**[HIGH] server/chat_db.py:181-182 — moderation audit trail destroyed on user deletion**
`reports.reporter_id` and `reports.reported_user_id` are both `REFERENCES users(id) ON DELETE CASCADE`. Calling `delete_user()` (chat_db.py:440-442) — e.g. from an admin "delete account" action, or a self-service deletion — cascades and wipes every report that mentions that user, whether they were the *reporter* or the *reported* party. This is inconsistent with `bans`, whose `user_id` column deliberately has **no** FK so bans "survive user deletion" (per the existing comment at chat_db.py:82-90). The effect: deleting a repeatedly-reported user erases the exact evidence trail (`reports`, and `strikes` at chat_db.py:194 similarly) that justified banning them, and `get_user_admin_detail`/`get_moderation_log` lose that history permanently. Fix: drop the CASCADE on `reports.reporter_id`/`reported_user_id` (and arguably `strikes.user_id`) the same way `bans.user_id` was left unconstrained, or snapshot reporter/reported identifiers as plain TEXT (no FK) so history survives account deletion.

### MEDIUM

**[MEDIUM] server/chat_db.py:607-611, 1024-1039 — room/meetup deletion leaks uploaded media files**
`purge_expired_messages` (chat_db.py:886-920) is the *only* place that parses message `content` for `image`/`video` types and unlinks the file (plus `_mod*.webp` moderation copies) from `chat/uploads/`. Both other deletion paths skip this entirely:
- `delete_room()` (chat_db.py:607-611) does `DELETE FROM messages WHERE room_id = ?` directly with zero media cleanup.
- `purge_expired_meetups()` (chat_db.py:1024-1039) does the same at line 1032 before dropping the room at 1033.

Any photo/video posted in a meetup or in a room an admin deletes is orphaned on disk forever — nothing else ever scans for it once the message row is gone. Fix: route these deletions through the same content-parsing/unlink logic `purge_expired_messages` uses, scoped to the affected `room_id`.

**[MEDIUM] server/chat_db.py:1045-1075 — `find_or_create_dm` has a TOCTOU race that can create duplicate DM rooms**
The function SELECTs for an existing DM between two users, and if none is found, creates a new `rooms` + `dm_participants` pair — with no `BEGIN IMMEDIATE`/transaction wrapping the check-then-insert, and no unique constraint on `dm_participants` that would prevent two different `room_id`s from holding the same participant pair. FastAPI runs sync route handlers in a thread pool, so two near-simultaneous calls (two tabs, or both users opening the DM at once) can each see "no existing room" and each create one, splitting the conversation across two rooms. Fix: wrap in `BEGIN IMMEDIATE`, or add a canonical unique key (e.g. a `pair_key` column = sorted user id pair) with `INSERT OR IGNORE` to make creation idempotent.

**[MEDIUM] server/chat_db.py:94-109 (schema) + 1045-1068 — meetup rooms become permanently orphaned when the creator is deleted**
`meetups.creator_id REFERENCES users(id) ON DELETE CASCADE` (chat_db.py:146), but the `rooms` row created alongside it in `create_meetup` (chat_db.py:966, same id as the meetup) has no FK relationship to `meetups` at all. Deleting a user who created a meetup cascades away the `meetups` row and its `meetup_attendees`, but the matching `rooms` row is never touched — it survives indefinitely with no meetup record pointing at it, and `purge_expired_meetups` (which is the only code that ever cleans up a meetup's room) can't find it because it iterates `meetups`, not `rooms`. Fix: when deleting a user (or in `delete_user` itself), explicitly delete rooms for any meetups the user created before/instead of relying on the CASCADE, e.g. `DELETE FROM rooms WHERE id IN (SELECT id FROM meetups WHERE creator_id = ?)` prior to the user delete.

**[MEDIUM] server/chat_db.py:1491-1500 — `get_room_stats` has no `type`/`event_id` filter**
Unlike `get_rooms_by_event` (chat_db.py:625-629), which correctly restricts to `type IN ('stage', 'general')`, `get_room_stats` selects `FROM rooms r` unconditionally. The admin "Rooms" tab will therefore list every DM room (always named literally "DM") and every meetup room across all events, alongside real configurable rooms — and, combined with the orphan bug above, stale ghost rooms accumulate there forever. Fix: add `WHERE r.type IN ('general', 'stage')` (matching `get_rooms_by_event`), and filter by `event_id` if the admin dashboard is meant to be per-event.

### LOW

**[LOW] server/chat_db.py:333, 253-330 — multi-worker migration race is unguarded (currently inert)**
`_chat_db_initialized` (chat_db.py:333) is a process-local global gating `init_chat_db`/`_migrate_chat_db`. The column-existence-check-then-`ALTER TABLE` pattern in `_migrate_chat_db` (e.g. chat_db.py:254-260, 281-293) isn't safe if two processes start concurrently — the second could hit "duplicate column name" mid-migration. Not exploitable today since `server/Dockerfile:10` runs a single `uvicorn` process with no `--workers`, but flag before ever scaling to multiple workers.

**[LOW] server/api.py:310-315 — unindexed full-table scan every 60s**
The push scheduler's `SELECT DISTINCT s.session_id, je.value AS slot_id FROM sessions s, json_each(s.schedule) je WHERE je.value IN (...)` does a lateral `json_each` scan over every row in `sessions` on each 60-second tick, with no index possible on JSON array contents in SQLite. With 90-day session retention (`_prune_expired_sessions`, api.py:240-258), this scales linearly with total historical sessions rather than active ones. Not a correctness bug, but worth normalizing `schedule` into a child table with an index if the session count grows large.

### Not a bug (verified, ruling out false leads)
- WAL mode and `PRAGMA foreign_keys=ON` are correctly re-applied on every new connection in both `chat_db.get_chat_db()` (chat_db.py:341-344) and `api._get_db()` (api.py:93-95) — not just at creation time.
- ISO-8601 `expires_at`/`created_at` TEXT comparisons (`<=` in purge, `>` in reads) are consistent at the boundary and remain correctly ordered despite Python's `isoformat()` omitting microseconds when zero — verified no ordering inversion since the timezone is always fixed UTC (`+00:00`).
- The pick/schedule read-modify-write endpoints in `api.py` (600-739) are single atomic `UPDATE ... (SELECT ... FROM json_each(...))` statements, not separate read-then-write round trips — no TOCTOU there despite the shape suggesting it.
- All SQL across the three files is parameterized; the few f-string-built queries (`update_room`, `apply_overrides`, `update_artist_field`) interpolate only column names drawn from fixed allowlists, never request-controlled values — no injection risk found.
