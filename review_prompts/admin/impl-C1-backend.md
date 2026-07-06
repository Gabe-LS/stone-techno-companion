# Implementation spec — Stage C backend (admin completeness endpoints)

You are an implementation agent. Apply EXACTLY what is below. You may Read/Grep/Glob and Edit/Write.
You CANNOT run anything; the orchestrator runs tests. No emojis. Match existing style (FastAPI router
with `actor = _require_admin(request)` / `_require_super_admin`, `_get_db()` try/finally, chat_db raw
sqlite3 helpers, `log_admin_action(db, actor["label"], ...)` after each mutation).

## Files you may edit
- `server/chat_api.py`
- `server/chat_db.py`

Do NOT touch chat_ws.py, chat_moderation.py, or any .html/.js. Two features are intentionally OUT OF
SCOPE (they touch the live WS pipeline): admin posting into read-only rooms, and meetup-title
moderation at creation. Do not implement those.

## Context
- `get_room_messages(db, room_id, limit)` (chat_db.py:1005) filters out pending — for admins we want
  to SEE pending too, so add a separate admin variant.
- `_unlink_media_if_orphaned(db, uploads_dir, url)` (chat_db.py:1082) is the SAFE unlink (only removes
  a file when no other live message references it). Reuse it — never unlink a raw url directly.
- `delete_room` (chat_db.py:758) shows the media-collection + unlink pattern.
- `get_pending_reports` (chat_db.py:1396); `resolve_report` returns rowcount now.
- Admin endpoints live ~lines 1970-2760; `_admin_json`, `log_admin_action`, `get_reachable_member_counts`.
- Settings keys in chat_settings: room_sort, msg_char_limit, dm_ttl_minutes, room_ttl_minutes, meetup_ttl_minutes.

## chat_db.py additions

### D1. Admin room-message view (includes pending)
```python
def get_room_messages_admin(
    db: sqlite3.Connection, room_id: str, limit: int = 100
) -> list[dict]:
    rows = db.execute(
        "SELECT m.id, m.user_id, m.type, m.content, m.media_url, m.moderation_status, "
        "m.created_at, u.display_name, u.username "
        "FROM messages m LEFT JOIN users u ON u.id = m.user_id "
        "WHERE m.room_id = ? AND m.expires_at > ? "
        "ORDER BY m.created_at DESC LIMIT ?",
        (room_id, _now(), limit),
    ).fetchall()
    return [dict(r) for r in rows]
```

### D2. Delete a single message (safe media unlink)
```python
def delete_message_by_id(db: sqlite3.Connection, message_id: str) -> dict | None:
    row = db.execute(
        "SELECT id, room_id, user_id, type, content, media_url FROM messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    if not row:
        return None
    url = ""
    if row["type"] in ("image", "video"):
        url = row["media_url"] or ""
        if not url:
            import json
            try:
                url = json.loads(row["content"]).get("url", "")
            except (json.JSONDecodeError, TypeError):
                url = ""
    db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    db.commit()
    if url:
        uploads_dir = Path(__file__).resolve().parent / "chat" / "uploads"
        _unlink_media_if_orphaned(db, uploads_dir, url)
    return {"room_id": row["room_id"], "message_id": message_id, "user_id": row["user_id"]}
```

### D3. Reports by status
```python
def get_reports_by_status(db: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    base = (
        "SELECT r.*, u.display_name AS reporter_name, u2.display_name AS reported_name, "
        "rm.name AS room_name "
        "FROM reports r "
        "LEFT JOIN users u ON u.id = r.reporter_id "
        "LEFT JOIN users u2 ON u2.id = r.reported_user_id "
        "LEFT JOIN rooms rm ON rm.id = r.room_id "
    )
    if status == "all":
        return db.execute(base + "ORDER BY r.created_at DESC LIMIT 200").fetchall()
    return db.execute(
        base + "WHERE r.status = ? ORDER BY r.created_at DESC LIMIT 200", (status,)
    ).fetchall()
```
(Leave `get_pending_reports` as-is; the endpoint will route to whichever it needs. Note this adds
`room_name` — the pending path can keep using get_pending_reports, but for consistency ALSO add
`rm.name AS room_name` join to `get_pending_reports`.)

### D4. DM participant names
```python
def get_dm_participant_names(db: sqlite3.Connection, room_id: str) -> list[str]:
    rows = db.execute(
        "SELECT u.display_name, u.username FROM dm_participants dp "
        "JOIN users u ON u.id = dp.user_id WHERE dp.room_id = ?",
        (room_id,),
    ).fetchall()
    return [(r["display_name"] or r["username"] or "?") for r in rows]
```

### D5. Meetup listing + delete helpers
```python
def get_all_meetups(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT m.id, m.title, m.creator_id, u.display_name AS creator_name, "
        "m.meetup_time, m.location_label, m.created_at, m.expires_at, "
        "(SELECT COUNT(*) FROM meetup_attendees ma WHERE ma.meetup_id = m.id) AS attendees "
        "FROM meetups m LEFT JOIN users u ON u.id = m.creator_id "
        "ORDER BY m.meetup_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_meetup(db: sqlite3.Connection, meetup_id: str) -> bool:
    row = db.execute("SELECT id FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
    if not row:
        return False
    delete_room(db, meetup_id)  # meetup room shares the meetup id; unlinks media
    db.execute("DELETE FROM meetup_attendees WHERE meetup_id = ?", (meetup_id,))
    db.execute("DELETE FROM meetups WHERE id = ?", (meetup_id,))
    db.commit()
    return True
```
Verify the meetup room id equals the meetup id in this codebase (grep create_meetup / how the meetup
room is created). If the room id differs, adjust `delete_room(db, <room id>)` accordingly and note it.

## chat_api.py additions / changes

### E1. Room messages + delete message
```python
@router.get("/admin/rooms/{room_id}/messages")
async def admin_room_messages(room_id: str, request: Request, limit: int = 100):
    _require_admin(request)
    limit = max(1, min(limit, 200))
    db = _get_db()
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["type"] == "dm":
            raise HTTPException(400, "DM messages are end-to-end encrypted")
        return get_room_messages_admin(db, room_id, limit)
    finally:
        db.close()


@router.delete("/admin/messages/{message_id}")
async def admin_delete_message(message_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        result = delete_message_by_id(db, message_id)
        if not result:
            raise HTTPException(404, "Message not found")
        log_admin_action(db, actor["label"], "delete_message",
                         target_user_id=result["user_id"],
                         target_room_id=result["room_id"], detail=message_id[:8])
        from chat_ws import manager
        asyncio.create_task(manager.broadcast_to_room(
            result["room_id"],
            {"event": "messages_expired", "room_id": result["room_id"],
             "message_ids": [message_id]},
        ))
        return {"ok": True}
    finally:
        db.close()
```

### E2. Reports: status filter + room name
Change `admin_reports` to route by status:
```python
@router.get("/admin/reports")
async def admin_reports(request: Request, status: str = "pending"):
    _require_admin(request)
    db = _get_db()
    try:
        if status == "pending":
            reports = get_pending_reports(db)
        elif status in ("actioned", "dismissed", "all"):
            reports = get_reports_by_status(db, status)
        else:
            raise HTTPException(400, "invalid status")
        return [
            { ...existing fields...,
              "room_name": (r["room_name"] if "room_name" in r.keys() else None),
            }
            for r in reports
        ]
    finally:
        db.close()
```
Add `"room_name"` to the returned dict (guard with `r.keys()` since get_pending_reports now also
selects it). Keep all existing fields.

### E3. admin_rooms: DM participant names
In `admin_rooms`, after computing member counts, for each room whose `type == "dm"` set
`r["participants"] = get_dm_participant_names(db, r["id"])` (import the helper). Leave other rooms.

### E4. Settings GET/PATCH extension
- `admin_get_settings` (currently returns only room_sort): return all of:
  `room_sort`, and int-parsed `msg_char_limit`, `dm_ttl_minutes`, `room_ttl_minutes`,
  `meetup_ttl_minutes` (use get_setting with the documented defaults 1000/1440/1440/60).
- `admin_update_settings` (already `_require_super_admin`): accept those keys. Validate:
  room_sort in {auto, manual}; the four ttl/limit keys must be positive ints (reject bool/non-int),
  with a sane cap (e.g. msg_char_limit 1..5000; ttls 1..43200). Use set_setting(db, key, str(value)).
  Log `update_settings` with detail=str(body) (already logged — keep).

### E5. Meetups list + delete
```python
@router.get("/admin/meetups")
async def admin_list_meetups(request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        return get_all_meetups(db)
    finally:
        db.close()


@router.delete("/admin/meetups/{meetup_id}")
async def admin_delete_meetup(meetup_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        if not delete_meetup(db, meetup_id):
            raise HTTPException(404, "Meetup not found")
        log_admin_action(db, actor["label"], "delete_room", target_room_id=meetup_id,
                         detail="meetup")
        from chat_ws import manager
        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        return {"ok": True}
    finally:
        db.close()
```

### E6. Reorder validation
In `admin_reorder_rooms`, before the loop, fetch the set of valid general/stage room ids:
`valid = {r["id"] for r in db.execute("SELECT id FROM rooms WHERE type IN ('general','stage')").fetchall()}`
and skip any `room_id` in `order` not in `valid` (do not error the whole request — just ignore
invalid ids). Keep the rest.

## Imports
Add to the chat_db import block in chat_api.py: `get_room_messages_admin, delete_message_by_id,
get_reports_by_status, get_dm_participant_names, get_all_meetups, delete_meetup`. `get_room`,
`get_pending_reports`, `resolve_report`, `Path` (if needed) — verify already imported.

## Final report
List each helper (D1-D5) + endpoint (E1-E6) with file:line. Report the meetup-room-id verification
result (E5/D5). Flag any spec/reality mismatch. Do not claim tests pass.
