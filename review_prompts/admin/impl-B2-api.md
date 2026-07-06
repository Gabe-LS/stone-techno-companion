# Implementation spec — Stage B2 (chat_api.py: roles, actor, audit, admin mgmt)

You are an implementation agent. Apply EXACTLY what is below. Read `docs/admin-multiadmin.md` FIRST (authoritative contract) — this spec implements it. You may Read/Grep/Glob and Edit/Write. You CANNOT run anything; the orchestrator runs tests. No emojis. Match existing chat_api.py style (FastAPI router, `_get_db()` + try/finally, `HTTPException`, `from chat_ws import manager`).

## File you may edit
- `server/chat_api.py` ONLY.

## DB helpers already implemented in chat_db.py (import from `chat_db`, do NOT reimplement)
`get_admin(db, email_hash) -> Row|None`, `list_admins(db) -> list[dict]`,
`add_admin(db, email_hash, role, label, added_by) -> dict`, `remove_admin(db, email_hash) -> int`,
`count_super_admins(db) -> int`, `log_admin_action(db, actor, action, target_user_id=None,
target_room_id=None, detail=None)`, `get_admin_actions(db, limit, offset) -> list[dict]`,
`VALID_ADMIN_ROLES`. Also existing: `hash_email(email)`, `get_user(db, id)`, and the module set
`_ADMIN_EMAIL_HASHES`.

## Read first
Current `_require_admin` (~263-289, includes the Stage-A `_check_admin_fail_rate` call and the
fail-append before the 403), `_ADMIN_EMAIL_HASHES`/`_load_admin_emails` (~181-190), `ADMIN_TOKEN`
(~88), every `/admin/*` endpoint (~1936-2560), the `_admin_json` helper, and the existing chat
`logout` endpoint if any (grep `logout`).

## Changes

### 1. Actor resolution — replace `_require_admin`
Rewrite the admin gate into an actor-returning resolver plus two guards. Keep the fail-rate limiter.

```python
def _resolve_admin(request: Request) -> dict | None:
    header_token = request.headers.get("X-Admin-Token") or ""
    if ADMIN_TOKEN and header_token and secrets.compare_digest(header_token, ADMIN_TOKEN):
        return {"kind": "token", "role": "super_admin", "user_id": None,
                "email_hash": None, "label": "token"}
    session_token = request.cookies.get("chat_session")
    if session_token:
        db = _get_db()
        try:
            user = get_user_by_token(db, session_token)
            if user:
                providers = db.execute(
                    "SELECT provider_id FROM user_providers WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
                pid_list = [p["provider_id"] for p in providers]
                if user["provider_id"] not in pid_list:
                    pid_list.append(user["provider_id"])
                # env emails = permanent super-admins
                for pid in pid_list:
                    if pid in _ADMIN_EMAIL_HASHES:
                        row = get_admin(db, pid)
                        label = (row["label"] if row and row["label"] else pid[:12])
                        return {"kind": "cookie", "role": "super_admin",
                                "user_id": user["id"], "email_hash": pid, "label": label}
                # DB-backed admins
                for pid in pid_list:
                    row = get_admin(db, pid)
                    if row:
                        return {"kind": "cookie", "role": row["role"],
                                "user_id": user["id"], "email_hash": pid,
                                "label": (row["label"] or pid[:12])}
        finally:
            db.close()
    return None


def _require_admin(request: Request) -> dict:
    _check_admin_fail_rate(request)
    actor = _resolve_admin(request)
    if actor is None:
        ip = request.client.host if request.client else "unknown"
        _admin_fail_rate.setdefault(ip, []).append(time.monotonic())
        raise HTTPException(403, "Admin access required")
    return actor


def _require_super_admin(request: Request) -> dict:
    actor = _require_admin(request)
    if actor["role"] != "super_admin":
        raise HTTPException(403, "Super-admin access required")
    return actor
```
Ensure `get_admin`, `get_user_by_token` are imported from chat_db (get_user_by_token is already used elsewhere — verify).

### 2. Protection helper
```python
def _protected_role(db, target_user_id: str) -> str | None:
    user = get_user(db, target_user_id)
    if not user:
        return None
    pids = [
        p["provider_id"]
        for p in db.execute(
            "SELECT provider_id FROM user_providers WHERE user_id = ?",
            (target_user_id,),
        ).fetchall()
    ]
    if user["provider_id"] not in pids:
        pids.append(user["provider_id"])
    if any(p in _ADMIN_EMAIL_HASHES for p in pids):
        return "super_admin"
    best = None
    for p in pids:
        row = get_admin(db, p)
        if row:
            if row["role"] == "super_admin":
                return "super_admin"
            best = "admin"
    return best


def _guard_target(db, actor: dict, target_user_id: str) -> None:
    role = _protected_role(db, target_user_id)
    if role == "super_admin":
        raise HTTPException(403, "Cannot moderate a super-admin")
    if role == "admin" and actor["role"] != "super_admin":
        raise HTTPException(403, "Only a super-admin can moderate another admin")
```

### 3. Thread actor through EVERY admin endpoint
- Change each `_require_admin(request)` call to `actor = _require_admin(request)`.
- For super-admin-only endpoints, use `actor = _require_super_admin(request)`:
  `admin_delete_room`, `admin_delete_user`, `admin_update_settings`, `admin_unban`,
  `admin_delete_ban`, `admin_clear_warnings`.
- In `admin_ban`, `admin_mute_user`, `admin_strike_user`, `admin_delete_user`: call
  `_guard_target(db, actor, user_id)` immediately AFTER the `user = get_user(...)` / not-found check
  and BEFORE any mutation.
- After each mutating action succeeds, add a `log_admin_action(db, actor["label"], "<action>", ...)`
  call with the right action constant and target ids/detail:
  - admin_ban → action "ban", target_user_id=user_id, detail=reason
  - admin_unban → "unban", target_user_id=user_id
  - admin_delete_ban → "delete_ban", target_user_id=(the resolved row user_id if any)
  - admin_mute_user → "mute" (or "ban" if it escalated), target_user_id=user_id, detail minutes
  - admin_unmute_user → "unmute", target_user_id=user_id
  - admin_strike_user → "strike" (or the escalated action from result), target_user_id=user_id, detail=detail
  - admin_clear_warnings → "clear_warnings", target_user_id=user_id
  - admin_delete_user → "delete_user", target_user_id=user_id
  - admin_create_room → "create_room", target_room_id=room_id, detail=name
  - admin_update_room → "update_room", target_room_id=room_id
  - admin_delete_room → "delete_room", target_room_id=room_id
  - admin_set_main_room → "set_main", target_room_id=room_id
  - admin_reorder_rooms → "reorder", detail=str(len(order))
  - admin_resolve_report → "resolve_report", detail=status (log only on the success path, rowcount>0)
  - admin_update_settings → "update_settings", detail=str(body)
  Keep each endpoint's existing behavior/return unchanged otherwise. Log AFTER the DB mutation and
  before `return`. Reuse the already-open `db` handle (the log helper commits).

### 4. New endpoints (add in the Admin section)

```python
@router.get("/admin/me")
async def admin_me(request: Request):
    actor = _require_admin(request)
    return {"role": actor["role"], "kind": actor["kind"],
            "label": actor["label"], "email_hash": actor["email_hash"]}


@router.get("/admin/audit")
async def admin_audit(request: Request, limit: int = 50, offset: int = 0):
    _require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db = _get_db()
    try:
        return get_admin_actions(db, limit, offset)
    finally:
        db.close()


@router.get("/admin/admins")
async def admin_list_admins(request: Request):
    _require_super_admin(request)
    db = _get_db()
    try:
        rows = list_admins(db)
        by_hash = {r["email_hash"]: r for r in rows}
        out = []
        # env permanent super-admins first
        for h in sorted(_ADMIN_EMAIL_HASHES):
            db_row = by_hash.pop(h, None)
            out.append({
                "email_hash": h, "role": "super_admin",
                "label": (db_row["label"] if db_row and db_row["label"] else "env"),
                "permanent": True, "added_by": "env",
                "created_at": (db_row["created_at"] if db_row else ""),
            })
        for r in by_hash.values():
            out.append({**r, "permanent": False})
        return out
    finally:
        db.close()


@router.post("/admin/admins")
async def admin_add_admin(request: Request):
    actor = _require_super_admin(request)
    body = await _admin_json(request)
    email = (body.get("email") or "").strip()
    role = body.get("role", "admin")
    label = (body.get("label") or "").strip() or None
    if not email:
        raise HTTPException(400, "email required")
    if role not in VALID_ADMIN_ROLES:
        raise HTTPException(400, "role must be 'admin' or 'super_admin'")
    h = hash_email(email)
    if h in _ADMIN_EMAIL_HASHES:
        raise HTTPException(409, "Already a permanent super-admin")
    db = _get_db()
    try:
        row = add_admin(db, h, role, label, actor["label"])
        log_admin_action(db, actor["label"], "add_admin", detail=f"{role}:{h[:12]}")
        return {**row, "permanent": False}
    finally:
        db.close()


@router.delete("/admin/admins/{email_hash}")
async def admin_remove_admin(email_hash: str, request: Request):
    actor = _require_super_admin(request)
    if email_hash in _ADMIN_EMAIL_HASHES:
        raise HTTPException(400, "Cannot remove a permanent super-admin")
    db = _get_db()
    try:
        row = get_admin(db, email_hash)
        if not row:
            raise HTTPException(404, "Admin not found")
        # never leave zero super-admins when there are no env super-admins
        if (row["role"] == "super_admin" and not _ADMIN_EMAIL_HASHES
                and count_super_admins(db) <= 1):
            raise HTTPException(400, "Would remove the last super-admin")
        remove_admin(db, email_hash)
        log_admin_action(db, actor["label"], "remove_admin", detail=email_hash[:12])
        return {"ok": True}
    finally:
        db.close()
```
Add imports: `get_admin, list_admins, add_admin, remove_admin, count_super_admins,
log_admin_action, get_admin_actions, VALID_ADMIN_ROLES` from chat_db. `hash_email` — verify it's
already imported (it is used in `_load_admin_emails`... actually `hash_email` is in chat_db; ensure imported).

### 5. Logout endpoint (only if none exists)
Grep for an existing `/logout` route. If a chat-session logout endpoint already exists, DO NOTHING here
(the UI will reuse it). If none exists, add:
```python
@router.post("/logout")
async def chat_logout(request: Request, response: Response):
    token = request.cookies.get("chat_session")
    if token:
        db = _get_db()
        try:
            db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            db.commit()
        finally:
            db.close()
    response.delete_cookie("chat_session", path="/")
    return {"ok": True}
```
Report which branch you took.

## Final report
List every endpoint you touched and the action constant logged. List new endpoints. Note the logout
branch. Flag ANY spec/reality mismatch (imports already present, endpoint body differences, an
existing logout route) and what you did. Do not claim tests pass.
