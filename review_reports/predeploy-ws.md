# Findings: websocket-realtime

## [SEVERITY: CRITICAL] Purge loop destroys DM rooms whose messages have merely expired, contradicting the "DM persists after expiry" invariant
- Where: `server/chat_ws.py:1748` and `server/chat_ws.py:1775-1788`
- Evidence:
  ```python
  expired_msgs = purge_expired_messages(db)   # line 1748 — deletes all expired messages, ALL rooms
  ...
  empty_dms = db.execute(
      "SELECT r.id FROM rooms r "
      "WHERE r.type = 'dm' AND NOT EXISTS ("
      "  SELECT 1 FROM messages m WHERE m.room_id = r.id"
      ")"
  ).fetchall()
  for row in empty_dms:
      db.execute("DELETE FROM dm_participants WHERE room_id = ?", (row["id"],))
      db.execute("DELETE FROM room_memberships WHERE room_id = ?", (row["id"],))
      db.execute("DELETE FROM rooms WHERE id = ?", (row["id"],))
  ```
- Impact: `purge_expired_messages` (called immediately before, same cycle) deletes every message past `expires_at`. The very next block treats "no message rows exist for this DM room" as "abandoned DM shell, safe to delete." It does not distinguish "never had a message" from "had messages that just expired in this same cycle." With `dm_ttl_minutes=1440` (24h), **every** DM conversation will eventually have all its messages expire together, and on that exact purge tick (every 30s) the DM's `rooms` row and `dm_participants` rows are permanently deleted. This directly contradicts the documented behavior in CLAUDE.md ("DM rooms persist after messages expire (conversation thread stays)"): the DM disappears entirely from both users' DM lists, and a fresh `find_or_create_dm` call would silently create a brand-new room/thread. For ~200 users doing normal DM usage over a multi-day festival, this will fire constantly and is effectively silent data loss of conversation state.
- Fix: Only delete DM rooms that never had a message and are old enough to be considered abandoned, e.g. join against a "message ever existed" marker independent of expiry (track `has_ever_messaged` on the room, or check `rooms.created_at < cutoff AND` no row ever inserted into messages for that room via a separate flag), rather than deriving "empty" from the post-purge `messages` table state.

## [SEVERITY: CRITICAL] `reply_to_id` is not scoped to the target room, leaking message content/sender across rooms and DMs
- Where: `server/chat_ws.py:708-726` (`_build_reply_snippet`), called at `server/chat_ws.py:859-861`; also `server/chat_db.py:782-796` (`get_room_messages`, used for `room_history`)
- Evidence:
  ```python
  def _build_reply_snippet(db, reply_to_id: str | None) -> dict | None:
      if not reply_to_id:
          return None
      orig = db.execute(
          "SELECT m.content, m.type, u.display_name FROM messages m "
          "JOIN users u ON u.id = m.user_id WHERE m.id = ?",
          (reply_to_id,),
      ).fetchone()
  ```
  and in `send_message` handling: `reply_to_id = data.get("reply_to_id")` (line 1188) flows unchecked into `create_message(...)` (line 1340) and then into `_build_reply_snippet(db, reply_to_id)` (line 859) with **no verification that the referenced message belongs to `room_id`** or that the sender ever had access to it.
- Impact: Any authenticated user can post `{"event":"send_message", "room_id": <any room they can post to>, "content": ..., "reply_to_id": <arbitrary message id>}`. If that message id belongs to a different room — including a private DM the attacker is not a participant of, or a group room they were never a member of — the server fetches the original sender's `display_name` and (for non-E2EE content) up to 80 characters of the message text, and embeds it in the `reply_to` field of the broadcast `message` event to everyone in the attacker's room, and again on every subsequent `room_history` fetch (`get_room_messages` LEFT JOINs the same way with no room check). Message ids are UUIDv4 but are routinely exposed to clients via the shareable permalink feature (`/chat/msg/{id}`) and via `data-msg-id` DOM attributes, so obtaining one from a room you're no longer in (or a DM whose peer forwarded/shared a link) is realistic. For keyless-fallback DMs (plaintext per the E2EE design doc), this leaks the DM's actual plaintext content cross-room. Even for E2EE DMs, the sender's `display_name` still leaks.
- Fix: Before accepting `reply_to_id`, verify the referenced message's `room_id` equals the target `room_id` (and reject/ignore otherwise) — do this both when creating the message (`send_message` handler) and when building the snippet for broadcast/history.

## [SEVERITY: HIGH] `message_acked` omits `room_id`; client mis-applies the ack, causing already-delivered messages to be shown as failed
- Where: `server/chat_ws.py:1343-1355` (server ack payload); `server/chat/chat.html:1313-1332` (client handler) and `server/chat/chat.html:2524-2557` (optimistic send + timeout)
- Evidence — server ack has no room identifier:
  ```python
  await ws.send_text(
      json.dumps(
          {
              "event": "message_acked",
              "temp_id": temp_id,
              "id": msg["id"],
              "created_at": msg["created_at"],
          }
      )
  )
  ```
  Client looks it up in whatever room happens to be open *now*, not the room the message was sent to:
  ```js
  case 'message_acked': {
    const msgs = messagesByRoom[currentRoom] || [];
    const idx = msgs.findIndex(m => m.temp_id === data.temp_id);
    ...
    } else {
      dbg('[WS] message_acked ignored (sent from another device)');
    }
  ```
  and the optimistic-send timeout that then fires:
  ```js
  setTimeout(() => {
    const msgs = messagesByRoom[roomId] || [];
    const still = msgs.find(m => m.id === tempId && m.pending);
    if (still) {
      msgs.splice(msgs.indexOf(still), 1);
      document.querySelector(`[data-msg-id="${tempId}"]`)?.remove();
      showToast('Message not sent. Check your connection.');
    }
  }, 10000);
  ```
- Impact: If a user sends a message in room A and switches to room B within 10 seconds (a very ordinary interaction — e.g. checking another room while waiting), `currentRoom` at ack-arrival time is B, so the ack's `temp_id` lookup against `messagesByRoom[B]` fails (`idx === -1`), the ack is silently dropped, and 10s later the original optimistic message in room A is spliced out of the DOM/state with a **"Message not sent. Check your connection."** toast — even though the message was successfully saved and broadcast to everyone else. The message only reappears if the user reopens room A (triggering a fresh `room_history` fetch). This is a concrete, easily-reproduced correctness bug in the optimistic-messaging protocol.
- Fix: Include `room_id` in the `message_acked` payload server-side, and have the client look up the pending message in `messagesByRoom[data.room_id]` instead of `messagesByRoom[currentRoom]`.

## [SEVERITY: MEDIUM] Unvalidated WS payload fields crash the per-connection task
- Where: `server/chat_ws.py:1183-1191` (content/room_id types unchecked), `server/chat_ws.py:1188` and `:1340` (`reply_to_id` existence unchecked); enforced by `server/chat_db.py:32` (`PRAGMA foreign_keys=ON`) and the FK on `server/chat_db.py:119-129` (`reply_to_id TEXT REFERENCES messages(id) ON DELETE SET NULL`)
- Evidence:
  ```python
  room_id = data.get("room_id")
  msg_type = data.get("type", "text")
  content = data.get("content", "")
  temp_id = data.get("temp_id")
  reply_to_id = data.get("reply_to_id")
  if not room_id or not content:
      continue
  ```
  No type check on `content` (could be a JSON object/array rather than a string) and no existence/ownership check on `reply_to_id` before it reaches:
  ```python
  msg = create_message(db, room_id, user_id, msg_type, content, ttl_minutes=room_ttl, reply_to_id=reply_to_id)
  ```
- Impact: A client sending a bogus/nonexistent `reply_to_id` (typo, stale/deleted message id, or a value referencing a message in a room the FK doesn't care about) causes `create_message`'s `INSERT` to raise `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — this is unhandled at the call site and propagates to the outer `try/except Exception` in `handle_chat_ws` (line 1721), which logs and tears down that connection (`finally` disconnects the user, broadcasts presence-offline). Similarly, `content` as a non-string (e.g. `{"text":"hi"}` sent as a JSON object instead of a JSON-encoded string) passes the `if not room_id or not content` check but fails at `sqlite3` parameter binding (`sqlite3.InterfaceError`) inside `create_message`, again killing the connection. Same class of issue applies to `message_id`/`room_id` used as raw SQL parameters and dict keys elsewhere (e.g. `add_reaction`, `mark_read`) — a non-hashable type (list/dict) as `room_id` would raise `TypeError` on `mgr._room_meta.get(room_id, ...)`. None of this crashes the server (each connection is an isolated task), but it lets any client trivially force their own connection into a disconnect/reconnect loop, and is evidence of a systemic lack of input-shape validation on WS payloads.
- Fix: Validate `content` is a `str` and `room_id`/`message_id`/`reply_to_id` are strings of the expected shape before use; when `reply_to_id` is present, verify it references an existing message (ideally in the same room, per the CRITICAL finding above) and reject with `message_rejected` instead of letting the DB layer raise.

## [SEVERITY: MEDIUM] Per-user message moderation is not serialized — a message can broadcast after/alongside a ban triggered by a near-simultaneous message
- Where: `server/chat_ws.py:1381-1400` (moderation spawned as an independent task per message) and `server/chat_ws.py:807-817` (`delete_user_messages` on ban/mute)
- Evidence:
  ```python
  asyncio.create_task(
      _moderate_and_broadcast(
          manager, room_id, user_id, conn_id, display_name, username, color_index,
          avatar_url, msg, msg_type, content, text_for_moderation, image_url,
          reply_to_id, ws, is_moderated=bool(send_room["is_moderated"]),
      )
  )
  ```
  There is no per-user lock/queue around this; each `send_message` event spawns its own independent moderation task.
- Impact: If a user sends two messages in quick succession, their moderation tasks run concurrently. If the first is flagged and results in a ban (`mod_result["action"] == "ban"` → `delete_user_messages` deletes all of that user's currently-non-expired messages and the connection is closed), the second message's moderation task — already past the word-filter/AI calls and independently judged "allowed" — will still execute `await mgr.broadcast_to_room(...)` and persist/show that message, since nothing links the two tasks. The result is a banned user's message still reaching the room after the ban decision.
- Fix: Serialize moderation per user (e.g. an `asyncio.Lock` keyed by `user_id`, or a single-consumer queue per user) so a ban/mute from an earlier message aborts moderation for messages still in flight from the same user.

## [SEVERITY: MEDIUM] Synchronous sqlite3 calls block the single shared event loop for all ~200 connections
- Where: `server/chat_ws.py:373-408` (`_do_send_push` DB reads), `server/chat_ws.py:1775-1780` (empty-DM correlated subquery), `server/chat_ws.py:1796-1797` (`PRAGMA wal_checkpoint(TRUNCATE)`)
- Evidence:
  ```python
  async def _do_send_push(...):
      db = get_chat_db()
      try:
          subs = get_push_subscriptions(db, user_id)     # blocking sqlite3 call, no to_thread
          ...
          counts = get_unread_counts(db, user_id)          # blocking, multi-join query
  ```
  ```python
  empty_dms = db.execute(
      "SELECT r.id FROM rooms r WHERE r.type = 'dm' AND NOT EXISTS ("
      "  SELECT 1 FROM messages m WHERE m.room_id = r.id)"
  ).fetchall()
  ```
  ```python
  if _purge_cycle % 120 == 0:
      db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
  ```
  Contrast with the webpush send itself, which correctly avoids blocking: `await asyncio.to_thread(webpush, ...)` (line 465).
- Impact: Every one of these `sqlite3` calls executes synchronously on the asyncio event loop thread. Since this is a single-process asyncio server handling all ~200 concurrent WebSocket connections, any one of these calls (a slow query under lock contention, a `wal_checkpoint(TRUNCATE)` that has to wait out any open reader) stalls message delivery, typing indicators, and reconnects for every other user simultaneously. `wal_checkpoint(TRUNCATE)` in particular runs once per purge cycle roughly every hour (`_purge_cycle % 120`, purge loop sleeps 30s) directly inline with no `to_thread` wrapping and no timeout handling beyond the connection's `busy_timeout=5000` (which bounds contention waits but does not make the checkpoint itself non-blocking to the caller).
- Fix: Wrap DB-heavy operations (especially the WAL checkpoint and the empty-DM scan) in `asyncio.to_thread`, or move maintenance-style queries to a dedicated executor/connection so the main event loop isn't blocked by them.

## [SEVERITY: LOW] Authorization check for meetup-type rooms is inconsistent between send_message and reaction/report handlers
- Where: `server/chat_ws.py:1295-1300` (send_message correctly checks meetup membership) vs `server/chat_ws.py:1568-1574`, `:1595-1601`, `:1663-1669` (add_reaction/remove_reaction/report_message only check `dm`)
- Evidence — send_message enforces meetup membership:
  ```python
  if send_room["type"] == "meetup":
      if not db.execute(
          "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
          (room_id, user_id),
      ).fetchone():
          continue
  ```
  but add_reaction only checks `dm`:
  ```python
  if msg_row:
      r_room = get_room(db, msg_row["room_id"])
      if r_room and r_room["type"] == "dm":
          if not db.execute(
              "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
              (msg_row["room_id"], user_id),
          ).fetchone():
              continue
  ```
  (same pattern repeated for `remove_reaction` and `report_message`).
- Impact: A user who is not an attendee of a meetup can react to or report a message in that meetup's room if they obtain a `message_id` (e.g. via a leaked/observed id, or having previously been an attendee and then leaving). Low likelihood since meetup room history/message ids aren't normally exposed to non-attendees (blocked at `join_room`), but the inconsistency is real and cheap to fix.
- Fix: Add the same `meetup_attendees` membership check used in `send_message` to `add_reaction`, `remove_reaction`, and `report_message`.

## Verified clean
- **Connection lifecycle / registry cleanup**: `ConnectionManager.disconnect` (chat_ws.py:553-578) and `ChatRoom.broadcast`'s defensive cleanup (chat_ws.py:502-517) correctly remove dead sockets from every registry (`connections`, `conn_users`, `user_names`, `user_info`, `user_conns`, `conn_user`, `user_rooms`, `user_badge_rooms`, `user_unread`, `_rate_buckets`, `_recent_msgs`, `_last_active_ts`) on both normal and exception exit paths (`finally` block at chat_ws.py:1723-1736 always runs `manager.disconnect`). `_last_ws_activity` lingers slightly longer per-user but is harmless (checked only via `uid not in connected_uids` short-circuit) and is swept every ~2h by the purge loop (chat_ws.py:1811-1819).
- **Broadcast isolation**: `ChatRoom.broadcast` iterates a `list()` snapshot and wraps each `send_text` in try/except, so one client's failed/slow socket cannot raise into or block the loop for other recipients (chat_ws.py:502-517).
- **sendBeacon idle + 30s fallback**: instant idle marking via `/chat/api/push/idle` (chat_api.py:1484-1491, chat_ws.py `_last_ws_activity[user_id] = 0`) combined with the `now - last_ws_activity > 30` fallback and `not in connected_uids` short-circuit (chat_ws.py:925-932) correctly avoids both missed and duplicate pushes for genuinely offline users; the debounce keyed by `user_id:room_id` (chat_ws.py:303-346) prevents double sends.
- **Unread badge / mark_read cross-device correctness**: `mark_room_read` + `send_to_user` (which fans out to all of a user's connections) correctly synchronizes badge clears across devices (chat_ws.py:1155-1179); the "ignore badge_update for the foreground open room" behavior on the client is intentional and documented, not a bug.
- **Purge loop resilience**: the loop's outer `try/except Exception`/`finally` (chat_ws.py:1742-1826) ensures a single failed purge cycle logs and retries after `sleep(30)` rather than killing the background task.
- **DM/meetup message authorization on send/join**: `join_room` and `send_message` correctly gate `dm` and `meetup` room types on `dm_participants`/`meetup_attendees` membership (chat_ws.py:1094-1106, 1272-1300); general/stage rooms are intentionally open to any authenticated user (consistent with the app's public-room model, no bug).
