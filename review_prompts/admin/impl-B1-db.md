# Implementation spec — Stage B1 (chat_db.py: admins + audit schema & helpers)

You are an implementation agent. Apply EXACTLY what is below. Read `docs/admin-multiadmin.md` first — it is the authoritative contract. You may Read/Grep/Glob and Edit/Write. You CANNOT run anything; the orchestrator runs tests. No emojis. Match existing chat_db.py style (raw sqlite3, `sqlite3.Row`, `_uuid()`, `_now()`, dict-returning helpers, `hash_email`).

## File you may edit
- `server/chat_db.py` ONLY.

## Read first
- `chat_db.py` `init_db` executescript block (~lines 34-241), especially the `chat_settings` table at the END of the block (~231-239).
- `_uuid`, `_now`, `hash_email` (~1830), `get_setting`/`set_setting` for style.

## Changes

### 1. New tables in the schema
In the `init_db` `db.executescript("""...""")` block, add these two tables. Put them right BEFORE the `chat_settings` CREATE TABLE (so the INSERT OR IGNORE seed rows stay at the very end):
```sql
        CREATE TABLE IF NOT EXISTS admins (
            email_hash TEXT PRIMARY KEY,
            role       TEXT NOT NULL DEFAULT 'admin',
            label      TEXT,
            added_by   TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_actions (
            id             TEXT PRIMARY KEY,
            actor          TEXT NOT NULL,
            action         TEXT NOT NULL,
            target_user_id TEXT,
            target_room_id TEXT,
            detail         TEXT,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admin_actions_created ON admin_actions(created_at);
```
Do NOT add ALTER statements and do NOT touch `_migrate_chat_db` — these are new tables, IF NOT EXISTS handles fresh and existing DBs.

### 2. Helper functions
Add a new section near the other admin queries (after `get_room_stats`, before "Purge all", ~line 1763). Implement EXACTLY these signatures:

```python
# --- Admins (multi-admin / roles) ---

VALID_ADMIN_ROLES = ("admin", "super_admin")


def get_admin(db: sqlite3.Connection, email_hash: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM admins WHERE email_hash = ?", (email_hash,)
    ).fetchone()


def list_admins(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT email_hash, role, label, added_by, created_at FROM admins "
        "ORDER BY created_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def add_admin(
    db: sqlite3.Connection,
    email_hash: str,
    role: str,
    label: str | None,
    added_by: str | None,
) -> dict:
    if role not in VALID_ADMIN_ROLES:
        raise ValueError("invalid role")
    db.execute(
        "INSERT INTO admins (email_hash, role, label, added_by, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(email_hash) DO UPDATE SET role = excluded.role, "
        "label = excluded.label",
        (email_hash, role, label, added_by, _now()),
    )
    db.commit()
    return dict(get_admin(db, email_hash))


def remove_admin(db: sqlite3.Connection, email_hash: str) -> int:
    cur = db.execute("DELETE FROM admins WHERE email_hash = ?", (email_hash,))
    db.commit()
    return cur.rowcount


def count_super_admins(db: sqlite3.Connection) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM admins WHERE role = 'super_admin'"
    ).fetchone()[0]


# --- Admin action audit log ---


def log_admin_action(
    db: sqlite3.Connection,
    actor: str,
    action: str,
    target_user_id: str | None = None,
    target_room_id: str | None = None,
    detail: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO admin_actions (id, actor, action, target_user_id, "
        "target_room_id, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_uuid(), actor, action, target_user_id, target_room_id, detail, _now()),
    )
    db.commit()


def get_admin_actions(
    db: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = db.execute(
        "SELECT a.id, a.actor, a.action, a.target_user_id, u.display_name AS target_name, "
        "a.target_room_id, a.detail, a.created_at "
        "FROM admin_actions a LEFT JOIN users u ON u.id = a.target_user_id "
        "ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]
```

## Final report
List each change with file:line. Note any deviation from the spec (e.g. exact insertion point). Confirm you did NOT modify `_migrate_chat_db` or any existing table. Do not claim tests pass.
