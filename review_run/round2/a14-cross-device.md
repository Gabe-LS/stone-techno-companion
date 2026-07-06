## Cross-Device Sync Review — Stone Techno Companion Chat

### Scenario traces

**1. Favorite an artist on phone → visible on desktop?**
**[CONSISTENT]** `add_pick`/`remove_pick` (`server/api.py:600-668`) mutate `picks` with an atomic `json_each`/`UNION` SQL set-union (no read-modify-write race), then `_broadcast()` (`api.py:174-206`) pushes to every WS client on that `session_id`. If desktop's WS is disconnected, it self-heals on next `/api/me`/`/api/session/{code}` load (fresh DB read). No lost-update path found.

**2. Read on phone → badge clears on desktop?**
**[CONSISTENT]**, modulo the already-known Round-1 race. `mark_read` (`chat_ws.py:1171-1194`) calls `manager.send_to_user(user_id, ...)` which iterates **all** of that user's connections (`chat_ws.py:654-661`), not just the one that issued `mark_read` — so desktop does get the `badge_update:0`. The known gap (concurrent incoming message racing the clear) still applies; not re-reported.

**3. Disable push on phone → desktop still gets push?**
**[CONSISTENT]** Both `push_unsubscribe` (`api.py:824-844`) and `chat_push_unsubscribe` (`chat_api.py:1511-1522`) delete by `(session_id/user_id, endpoint)` — scoped to the specific browser's own endpoint. Desktop's distinct endpoint row is untouched.

**4. New device registers E2EE keys → old device notified?**
**[INCONSISTENT]** — see Issue A below.

**5. Username change reflected on all connected sessions immediately?**
**[INCONSISTENT]** — see Issue B below.

**6. Muted user blocked from sending on all devices?**
**[CONSISTENT]** `is_muted(db, user_id)` is re-checked from the DB inside `moderate_message` for *every single message* (`chat_moderation.py:428-433`), not cached per-connection — so all devices are blocked the instant `muted_until` is set, no propagation delay.

**7. Push dies on one device, repair runs → affects others?**
**[CONSISTENT]** Repair re-subscribes using that device's own endpoint/session/user id only; no cross-device write path found.

**8. Delete account → all sessions/devices invalidated?**
**[INCONSISTENT]** — see Issue C below.

---

### New issues

**[HIGH] — E2EE device-key cache trusts staleness indefinitely; offline peers silently locked out of future messages.**
`E2EE.getDeviceList()` (`chat.html:4335-4347`) returns `this._deviceLists[userId]` if present, with no TTL — the only invalidation triggers are the `key_rotated` WS event, logout, or a same-tab device-identity change (`chat.html:1520, 4088, 4421`). `key_rotated` itself (`chat_api.py:1618-1667`) is sent only via `manager.send_to_user`, which is a no-op if the peer has no live connection — there's no persisted/replayed notification. Opening the DM (`_verifyDmEncryptionState`, `chat.html:1949-1966`) only checks *whether a key exists* (for the lock-icon banner), it never refreshes the cached device list. Net effect: if User A registers a new device while their DM peer B is offline, B never learns of it; B's next several messages continue encrypting only to A's *old* device set, so A's new device silently can never decrypt them, with no error shown to either side, until B happens to log out (full cache reset). Fix: invalidate/refresh `_deviceLists[userId]` whenever a DM room is opened, not only on `key_rotated`.

**[HIGH] — Profile changes (name/username/avatar/color) are frozen inside already-open WebSocket connections.**
`handle_chat_ws` reads `display_name`, `username`, `color_index`, `avatar_url` once at handshake into local closure variables (`chat_ws.py:1024-1030`), and every subsequent `join_room` presence broadcast (`chat_ws.py:590-621`) and `send_message` broadcast (`chat_ws.py:862-873`) reuses those frozen values. `PUT /chat/api/profile` (`chat_api.py:602-665`) only writes to the `users` table — it never notifies `chat_ws.manager` or pushes any event to the connection. Client-side, the WS reconnect handler never refetches `/chat/api/me` either — it only re-sends `join_room`. Result: after a user renames/re-avatars themselves, anyone watching a room they're active in keeps seeing the *old* identity in real-time broadcasts and the online-members list for the rest of that WebSocket's lifetime — correctable only by that user's browser tab actually disconnecting and reconnecting (reload/network blip), not by leaving and rejoining a room. Fix: on profile update, look up and refresh (or close-and-invite-reconnect) the user's live connection state in `ConnectionManager`.

**[HIGH] — Self-service account deletion never closes the user's live WebSocket connections (unlike admin delete/ban).**
`auth_delete_account` (`chat_api.py:494-516`) deletes messages then calls `delete_user` (`chat_db.py:440-442`, `DELETE FROM users` relying on `ON DELETE CASCADE` for sessions/messages/memberships), but — unlike `admin_delete_user` (`chat_api.py:2008-2038`) and `admin_ban` (`chat_api.py:1739-1787`), which both explicitly `ws.close(code=4003, ...)` for every connection in `manager.user_conns[user_id]` — it never touches `manager.user_conns`. Any already-open socket for that user (this device, or any other device logged into the same account) stays fully live: it keeps receiving room/DM broadcasts and presence, and can still browse rooms, after the account is gone from the DB. The socket only dies the moment it attempts a *write* (`send_message`, `mark_read`, etc.), which now raises an unhandled `sqlite3.IntegrityError` (FK violation against the cascade-deleted `users` row via `messages.user_id`/`room_memberships.user_id`, `chat_db.py:112,123`) that propagates to the generic `except Exception` in `handle_chat_ws` and silently drops just that one connection. Fix: mirror the admin path — close all of the deleting user's live connections as part of `auth_delete_account`.
