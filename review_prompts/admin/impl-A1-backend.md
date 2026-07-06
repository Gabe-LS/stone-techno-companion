# Implementation spec — Stage A backend (admin panel bug fixes)

You are an implementation agent. Apply the EXACT changes below. You may Read/Grep/Glob and Edit/Write. You CANNOT run anything — the orchestrator runs all tests. Do not run tests or claim verification. Do not refactor beyond the spec. No emojis anywhere (code, comments, logs). Preserve existing code style (raw sqlite3, sqlite3.Row, dict-returning helpers).

## Files you may edit
- `server/chat_api.py`
- `server/chat_db.py`

Do NOT touch any `.html`, `.js`, tests, or other files.

## Context you must read first
- `server/chat_api.py` admin endpoints (~lines 1900-2400) and auth helpers `_require_admin`, `_check_auth_rate` (~178-268), OAuth handlers `auth_google`/`auth_google_code` (~355-440), `_validate_display_name` (~690), `create_user` call sites.
- `server/chat_db.py`: `mute_user` (543), `ban_user`/`ban_user_all_providers`/`is_user_banned` (577-674), `update_room` (732), `delete_user_messages` (1495), `get_admin_stats` (1548), `search_users` (1578), `get_room_stats` (1734), `get_reachable_member_counts` (1473), `resolve_report` (1389), `increment_mute_count`/`MAX_MUTES_BEFORE_BAN` (1445/1412), `get_setting`.
- `server/chat_moderation.py` `process_strike` (mute escalation pattern to mirror).

## Changes

### B1 (MOD-1 + MOD-2) — `admin_ban` must delete messages AND emit a `banned` event before closing sockets
In `admin_ban` (`chat_api.py` ~1943-1991), after the ban rows are inserted and BEFORE closing sockets:
1. Call `removed = delete_user_messages(db, user_id)` and for each batch `asyncio.create_task(manager.broadcast_to_room(batch["room_id"], {"event": "messages_expired", "room_id": batch["room_id"], "message_ids": batch["message_ids"]}))`. Import `delete_user_messages` if not already imported (it is imported at top of chat_api.py — verify).
2. Before the socket-close loop, send the banned event: `await manager.send_to_user(user_id, {"event": "banned", "reason": reason})`.
3. Keep the existing socket-close loop (`code=4003`).
Mirror the ordering used in the strike-to-ban path (`admin_strike_user`, ~2196-2204): send event, then close.

### B2 (MOD-3) — new `POST /admin/unmute/{user_id}` clearing ONLY the mute
Add a new endpoint next to `admin_mute_user`:
```
@router.post("/admin/unmute/{user_id}")
async def admin_unmute_user(user_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        db.execute("UPDATE users SET muted_until = NULL WHERE id = ?", (user_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()
```
Do NOT touch `strikes` or `mute_count` here (that is what "clear-warnings" does). This is the surgical unmute.

### B3 (MOD-4a) — `admin_mute_user` must increment the lifetime mute counter and escalate to ban
In `admin_mute_user` (`chat_api.py` ~2135-2167), after `mute_user(db, user_id, minutes=minutes)`:
- Call `mute_count = increment_mute_count(db, user_id)` (import from chat_db).
- If `mute_count >= MAX_MUTES_BEFORE_BAN` (import from chat_db): call `ban_user_all_providers(db, user_id, f"Auto-ban: muted {MAX_MUTES_BEFORE_BAN} times (admin mute)")`, then send `{"event": "banned", "reason": ...}` to the user and close their sockets (mirror admin_ban's close loop). Still delete their messages (the existing `delete_user_messages` call stays). Return `{"ok": True, "action": "ban"}`.
- Otherwise keep the existing muted-event broadcast and return `{"ok": True, "action": "mute"}`.
Mirror `process_strike`'s count==3 branch semantics. Keep message deletion + `messages_expired` broadcast that already exists.
NOTE: log-visibility of mutes is deliberately deferred to Stage B — do NOT add a mutes table or strikes-row hack here.

### B4 (MOD-6) — per-ban-id unban removes ALL sibling bans of the same user; fix the stat
1. In `admin_delete_ban` (`chat_api.py` ~2006-2015): before deleting, look up the ban's `user_id`:
   `row = db.execute("SELECT user_id FROM bans WHERE id = ?", (ban_id,)).fetchone()`. If `row` and `row["user_id"]`, delete every ban for that user: `db.execute("DELETE FROM bans WHERE user_id = ?", (row["user_id"],))`. If `user_id` is NULL (legacy ban with no user link), delete just that one row by id. Commit. Keep returning `{"ok": True}`.
2. In `get_admin_stats` (`chat_db.py` ~1571): change `"active_bans"` to count distinct users: `db.execute("SELECT COUNT(DISTINCT COALESCE(user_id, id)) FROM bans").fetchone()[0]` (COALESCE so legacy NULL-user rows still count once each).

### B5 (SEC-2 + ROOM-5) — validate room create/update input
1. `admin_create_room` (`chat_api.py` ~2279): after reading `room_type = body.get("type", "general")`, validate `if room_type not in ("general", "stage"): raise HTTPException(400, "type must be 'general' or 'stage'")`.
2. `admin_update_room` (`chat_api.py` ~2317): after fetching `room`, reject editing auto-managed rooms: `if room["type"] in ("dm", "meetup"): raise HTTPException(400, "DM and meetup rooms cannot be edited")`. Then validate the body BEFORE calling `update_room`:
   - If `"ttl_minutes" in body`: value must be `None` or an int with `0 < v <= 43200` (30 days). Else `raise HTTPException(400, "ttl_minutes must be a positive integer or null")`. (Accept ints only; reject bool and non-numeric.)
   - If `"position" in body`: must be an int (reject bool). Else 400.
   - If `"name" in body`: must be a non-empty str after strip, `len <= 80`. Else 400.
   - If `"description" in body`: must be a str, `len <= 500`. Else 400.
   Use a small local helper or inline checks. Note `isinstance(True, int)` is True in Python — explicitly reject bool for ttl_minutes/position via `isinstance(v, bool)`.

### B6 (ROOM-2) — admin Rooms tab shows real reachable member counts
In `admin_rooms` (`chat_api.py` ~2117-2132): after `get_room_stats(db, online_counts)` returns `rooms`, compute `counts = get_reachable_member_counts(db, [r["id"] for r in rooms])` and set `r["member_count"] = counts.get(r["id"], 0)` for each. Import `get_reachable_member_counts` from chat_db. (Leave `get_room_stats`'s literal 0 as the default; the endpoint overrides it — this keeps get_room_stats side-effect free.)

### B7 (SEC-3) — rate-limit failed admin auth
Add a per-IP limiter to `_require_admin` so brute forcing the token is throttled. Reuse the `_auth_rate` pattern. Concretely: at the TOP of `_require_admin`, before any check, do nothing; at each FAILURE path (right before `raise HTTPException(403, ...)`), record a failure and raise 429 if too many. Simplest robust approach: create a module-level `_admin_fail_rate: dict[str, list[float]] = {}` and a helper `_check_admin_fail_rate(request)` that, using `time.monotonic()`, keeps timestamps in a 300s window and raises `HTTPException(429, "Too many attempts")` if `>= 20` failures. Call it at the start of `_require_admin`; on the FINAL failure (the `raise HTTPException(403, ...)`) append a timestamp first. Self-prune like `_check_auth_rate`. Do not throttle successful auths.

### B8 (SEC-4) — clamp pagination
- `admin_users` (`chat_api.py` ~2058): clamp `limit = max(1, min(limit, 200))`, `offset = max(0, offset)` before calling `search_users`.
- `admin_modlog` (`chat_api.py` ~2107): same clamp before `get_moderation_log`.

### B9 (SEC-5) — malformed JSON → 400
Add a module-level helper:
```
async def _admin_json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
```
Replace `await request.json()` with `await _admin_json(request)` in every admin write endpoint that reads a body: `admin_resolve_report`, `admin_ban`, `admin_update_settings`, `admin_mute_user`, `admin_strike_user`, `admin_create_room`, `admin_update_room`, `admin_reorder_rooms`. (Endpoints that ignore the body — `admin_unban`, `admin_set_main_room`, `admin_clear_warnings` — may keep as-is or adopt the helper; do not break them.)

### B10 (ROOM-10 / SEC-6) — safe room-id slug + length caps on create
In `admin_create_room` (`chat_api.py` ~2287): replace `room_id = name.lower().replace(" ", "-")` with an allowlist slug:
```
import re
slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
if not slug:
    raise HTTPException(400, "Room name must contain letters or digits")
room_id = slug
```
Also cap: `if len(name) > 80: raise HTTPException(400, "Room name too long (max 80)")` and description `> 500` → 400. Put these checks near the existing `if not name` check.

### B11 (MM-8) — guard `resolve_report` against double-resolution
In `resolve_report` (`chat_db.py` ~1389): add `AND status = 'pending'` to the UPDATE and return the rowcount:
```
def resolve_report(db, report_id, status) -> int:
    cur = db.execute("UPDATE reports SET status = ?, reviewed_at = ? WHERE id = ? AND status = 'pending'", (status, _now(), report_id))
    db.commit()
    return cur.rowcount
```
In `admin_resolve_report` (`chat_api.py` ~1928): if `resolve_report(...) == 0`, raise `HTTPException(409, "Report already resolved")`. Keep returning `{"ok": True}` on success.

### B12 (ROOM-11a) — stats: exclude pending messages
In `get_admin_stats` (`chat_db.py` ~1564): change `total_messages_active` query to `SELECT COUNT(*) FROM messages WHERE expires_at > ? AND moderation_status != 'pending'`. (Verify the column name `moderation_status` exists on `messages` by reading the messages DDL; if the exact literal differs, match it.)

### B13 (SEC-1 input layer) — never store unvalidated provider display names
Add a sanitizer used by the Google OAuth paths so a provider-supplied name can never contain XSS/control characters. Add near `_validate_display_name`:
```
def _safe_provider_display_name(raw: str, fallback: str) -> str:
    # Provider names (Google "name") are untrusted. Keep only display-safe chars,
    # collapse whitespace, cap length, and fall back to a safe base if nothing valid remains.
    name = unicodedata.normalize("NFKC", raw or "").strip()
    name = re.sub(r"\s+", " ", name)
    # allow Latin letters/digits/space and . _ - ; drop everything else (quotes, <>, control)
    name = re.sub(r"[^\w .\-]", "", name, flags=re.UNICODE).strip()
    name = name[:30]
    if len(name) < 2:
        base = re.sub(r"[^\w.\-]", "", (fallback or "user"))[:20] or "user"
        return base
    return name
```
In `auth_google` (~365) and `auth_google_code` (~434), replace `name = info.get("name") or email.split("@")[0]` with:
```
name = _safe_provider_display_name(info.get("name") or "", email.split("@")[0])
```
Make sure `re` and `unicodedata` are imported at module top (they are used elsewhere — verify).

## Final report format
Report each change B1..B13 with the file:line you edited and a one-line note. Flag ANY place the spec didn't match the real code (e.g. a column name, an already-present import, an endpoint that reads the body differently) and what you did instead. List anything you could NOT do and why. Do not claim tests pass.
