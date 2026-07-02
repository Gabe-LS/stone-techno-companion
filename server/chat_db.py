"""Chat database schema and core operations."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHAT_DB_PATH = Path(__file__).resolve().parent / "data" / "chat.db"

DEFAULT_MESSAGE_TTL_MIN = 60
DEFAULT_MEETUP_GRACE_MIN = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def init_chat_db(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA secure_delete=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id                 TEXT PRIMARY KEY,
            provider           TEXT NOT NULL,
            provider_id        TEXT NOT NULL,
            display_name       TEXT NOT NULL DEFAULT '',
            username           TEXT NOT NULL DEFAULT '',
            username_lower     TEXT NOT NULL DEFAULT '',
            country            TEXT NOT NULL DEFAULT '',
            avatar_url         TEXT NOT NULL DEFAULT '',
            color_index        INTEGER NOT NULL DEFAULT 0,
            session_id         TEXT,
            device_fingerprint TEXT,
            muted_until        TEXT,
            mute_count         INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT NOT NULL,
            last_seen          TEXT,
            last_active        TEXT,
            UNIQUE (provider, provider_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username_lower) WHERE username_lower != '';

        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token      TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

        CREATE TABLE IF NOT EXISTS email_tokens (
            token      TEXT PRIMARY KEY,
            email      TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            fingerprint TEXT,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_providers (
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider    TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE (provider, provider_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_providers_user ON user_providers(user_id);

        CREATE TABLE IF NOT EXISTS bans (
            id                 TEXT PRIMARY KEY,
            user_id            TEXT,
            provider           TEXT NOT NULL,
            provider_id        TEXT NOT NULL,
            device_fingerprint TEXT,
            reason             TEXT NOT NULL,
            created_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bans_provider ON bans(provider, provider_id);
        CREATE INDEX IF NOT EXISTS idx_bans_fingerprint ON bans(device_fingerprint);

        CREATE TABLE IF NOT EXISTS rooms (
            id            TEXT PRIMARY KEY,
            event_id      TEXT NOT NULL,
            type          TEXT NOT NULL,
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            is_main       INTEGER NOT NULL DEFAULT 0,
            is_moderated  INTEGER NOT NULL DEFAULT 1,
            is_read_only  INTEGER NOT NULL DEFAULT 0,
            allows_media  INTEGER NOT NULL DEFAULT 1,
            ttl_minutes   INTEGER DEFAULT 60,
            position      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS room_memberships (
            user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            room_id      TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            joined_at    TEXT NOT NULL,
            last_read_at TEXT NOT NULL,
            PRIMARY KEY (user_id, room_id)
        );
        CREATE INDEX IF NOT EXISTS idx_memberships_user ON room_memberships(user_id);

        CREATE TABLE IF NOT EXISTS messages (
            id           TEXT PRIMARY KEY,
            room_id      TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type         TEXT NOT NULL,
            content      TEXT NOT NULL,
            link_preview TEXT,
            reply_to_id  TEXT REFERENCES messages(id) ON DELETE SET NULL,
            expires_at   TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_expires ON messages(expires_at);
        CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, created_at);

        CREATE TABLE IF NOT EXISTS message_reactions (
            message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            emoji      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, user_id, emoji)
        );
        CREATE INDEX IF NOT EXISTS idx_reactions_message ON message_reactions(message_id);

        CREATE TABLE IF NOT EXISTS meetups (
            id             TEXT PRIMARY KEY,
            creator_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            stage_id       TEXT,
            title          TEXT NOT NULL,
            location_lat   REAL,
            location_lng   REAL,
            location_label TEXT,
            meetup_time    TEXT NOT NULL,
            note           TEXT,
            created_at     TEXT NOT NULL,
            expires_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_meetups_expires ON meetups(expires_at);

        CREATE TABLE IF NOT EXISTS meetup_attendees (
            meetup_id TEXT NOT NULL REFERENCES meetups(id) ON DELETE CASCADE,
            user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (meetup_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS dm_participants (
            room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (room_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS blocks (
            blocker_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            blocked_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (blocker_id, blocked_id)
        );

        CREATE TABLE IF NOT EXISTS reports (
            id               TEXT PRIMARY KEY,
            reporter_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reported_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            message_snapshot TEXT NOT NULL,
            room_id          TEXT NOT NULL,
            reason           TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'pending',
            created_at       TEXT NOT NULL,
            reviewed_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS strikes (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reason     TEXT NOT NULL,
            detail     TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_strikes_user ON strikes(user_id);

        CREATE TABLE IF NOT EXISTS chat_push_subscriptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint   TEXT NOT NULL UNIQUE,
            p256dh     TEXT NOT NULL,
            auth       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_push_user ON chat_push_subscriptions(user_id);

        CREATE TABLE IF NOT EXISTS avatars (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            data    BLOB NOT NULL
        );
    """)
    db.commit()


def _migrate_chat_db(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()}
    if "link_preview" not in cols:
        db.execute("ALTER TABLE messages ADD COLUMN link_preview TEXT")
        db.commit()

    db.execute(
        "INSERT OR IGNORE INTO user_providers (user_id, provider, provider_id, created_at) "
        "SELECT id, provider, provider_id, created_at FROM users"
    )
    db.commit()

    strike_cols = {r[1] for r in db.execute("PRAGMA table_info(strikes)").fetchall()}
    if "expires_at" not in strike_cols:
        db.execute("ALTER TABLE strikes ADD COLUMN expires_at TEXT")
        db.commit()

    user_cols = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
    if "mute_count" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN mute_count INTEGER NOT NULL DEFAULT 0")
        db.commit()
    if "last_active" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
        db.commit()

    room_cols = {r[1] for r in db.execute("PRAGMA table_info(rooms)").fetchall()}
    for col, defn in [
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("is_moderated", "INTEGER NOT NULL DEFAULT 1"),
        ("is_read_only", "INTEGER NOT NULL DEFAULT 0"),
        ("allows_media", "INTEGER NOT NULL DEFAULT 1"),
        ("ttl_minutes", "INTEGER DEFAULT 60"),
        ("position", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in room_cols:
            db.execute(f"ALTER TABLE rooms ADD COLUMN {col} {defn}")
    db.commit()


_chat_db_initialized = False


def get_chat_db() -> sqlite3.Connection:
    global _chat_db_initialized
    CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(CHAT_DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA secure_delete=ON")
    if not _chat_db_initialized:
        init_chat_db(db)
        _migrate_chat_db(db)
        _chat_db_initialized = True
    return db


# --- Users ---


def create_user(
    db: sqlite3.Connection,
    provider: str,
    provider_id: str,
    display_name: str,
    device_fingerprint: str | None = None,
    session_id: str | None = None,
) -> dict:
    import random

    user_id = _uuid()
    now = _now()
    color_index = random.randint(0, 11)
    db.execute(
        "INSERT INTO users (id, provider, provider_id, display_name, "
        "device_fingerprint, session_id, color_index, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            provider,
            provider_id,
            display_name,
            device_fingerprint,
            session_id,
            color_index,
            now,
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO user_providers (user_id, provider, provider_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, provider, provider_id, now),
    )
    db.commit()
    return {
        "id": user_id,
        "provider": provider,
        "provider_id": provider_id,
        "display_name": display_name,
        "color_index": color_index,
        "created_at": now,
    }


def find_user_by_provider(
    db: sqlite3.Connection, provider: str, provider_id: str
) -> sqlite3.Row | None:
    return db.execute(
        "SELECT u.* FROM users u "
        "JOIN user_providers up ON up.user_id = u.id "
        "WHERE up.provider = ? AND up.provider_id = ?",
        (provider, provider_id),
    ).fetchone()


def add_user_provider(
    db: sqlite3.Connection, user_id: str, provider: str, provider_id: str
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO user_providers (user_id, provider, provider_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, provider, provider_id, _now()),
    )
    db.commit()


def get_user(db: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def update_display_name(db: sqlite3.Connection, user_id: str, name: str) -> None:
    db.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user_id))
    db.commit()


def update_last_seen(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (_now(), user_id))
    db.commit()


def update_last_active(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("UPDATE users SET last_active = ? WHERE id = ?", (_now(), user_id))
    db.commit()


def delete_user(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()


def is_muted(db: sqlite3.Connection, user_id: str) -> bool:
    user = get_user(db, user_id)
    if not user or not user["muted_until"]:
        return False
    return user["muted_until"] > _now()


def mute_user(db: sqlite3.Connection, user_id: str, minutes: int = 30) -> None:
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    db.execute("UPDATE users SET muted_until = ? WHERE id = ?", (until, user_id))
    db.commit()


# --- Sessions ---


def create_session(db: sqlite3.Connection, user_id: str) -> dict:
    sid = _uuid()
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    db.execute(
        "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
        (sid, user_id, token, expires),
    )
    db.commit()
    return {"id": sid, "token": token, "expires_at": expires}


def get_user_by_token(db: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT s.user_id FROM sessions s WHERE s.token = ? AND s.expires_at > ?",
        (token, _now()),
    ).fetchone()
    if not row:
        return None
    return get_user(db, row["user_id"])


# --- Bans ---


def ban_user(
    db: sqlite3.Connection,
    user_id: str | None,
    provider: str,
    provider_id: str,
    reason: str,
    device_fingerprint: str | None = None,
) -> str:
    ban_id = _uuid()
    db.execute(
        "INSERT INTO bans (id, user_id, provider, provider_id, device_fingerprint, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ban_id, user_id, provider, provider_id, device_fingerprint, reason, _now()),
    )
    if user_id:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    db.commit()
    return ban_id


def is_banned(
    db: sqlite3.Connection,
    provider: str,
    provider_id: str,
    device_fingerprint: str | None = None,
) -> sqlite3.Row | None:
    ban = db.execute(
        "SELECT * FROM bans WHERE provider = ? AND provider_id = ?",
        (provider, provider_id),
    ).fetchone()
    if ban:
        return ban
    if device_fingerprint:
        return db.execute(
            "SELECT * FROM bans WHERE device_fingerprint = ?",
            (device_fingerprint,),
        ).fetchone()
    return None


# --- Rooms ---


def create_room(
    db: sqlite3.Connection,
    room_id: str,
    event_id: str,
    room_type: str,
    name: str,
    is_main: bool = False,
    description: str = "",
    is_moderated: bool = True,
    is_read_only: bool = False,
    allows_media: bool = True,
    ttl_minutes: int | None = DEFAULT_MESSAGE_TTL_MIN,
    position: int = 0,
) -> dict:
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO rooms (id, event_id, type, name, description, is_main, "
        "is_moderated, is_read_only, allows_media, ttl_minutes, position, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            room_id,
            event_id,
            room_type,
            name,
            description,
            1 if is_main else 0,
            1 if is_moderated else 0,
            1 if is_read_only else 0,
            1 if allows_media else 0,
            ttl_minutes,
            position,
            now,
        ),
    )
    db.commit()
    return {
        "id": room_id,
        "event_id": event_id,
        "type": room_type,
        "name": name,
        "description": description,
        "is_moderated": is_moderated,
        "is_read_only": is_read_only,
        "allows_media": allows_media,
        "ttl_minutes": ttl_minutes,
        "position": position,
    }


def update_room(db: sqlite3.Connection, room_id: str, **kwargs) -> None:
    allowed = {
        "name",
        "description",
        "is_moderated",
        "is_read_only",
        "allows_media",
        "ttl_minutes",
        "position",
    }
    updates = []
    params = []
    for key, val in kwargs.items():
        if key in allowed:
            if key in ("is_moderated", "is_read_only", "allows_media"):
                val = 1 if val else 0
            updates.append(f"{key} = ?")
            params.append(val)
    if not updates:
        return
    params.append(room_id)
    db.execute(f"UPDATE rooms SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()


def delete_room(db: sqlite3.Connection, room_id: str) -> None:
    db.execute("DELETE FROM messages WHERE room_id = ?", (room_id,))
    db.execute("DELETE FROM room_memberships WHERE room_id = ?", (room_id,))
    db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    db.commit()


def get_room(db: sqlite3.Connection, room_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()


def get_main_room(db: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM rooms WHERE event_id = ? AND is_main = 1 LIMIT 1",
        (event_id,),
    ).fetchone()


def get_rooms_by_event(db: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM rooms WHERE event_id = ? AND type IN ('stage', 'general') ORDER BY is_main DESC, name",
        (event_id,),
    ).fetchall()


def join_room_membership(db: sqlite3.Connection, user_id: str, room_id: str) -> None:
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO room_memberships (user_id, room_id, joined_at, last_read_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, room_id, now, now),
    )
    db.commit()


def leave_room_membership(db: sqlite3.Connection, user_id: str, room_id: str) -> None:
    db.execute(
        "DELETE FROM room_memberships WHERE user_id = ? AND room_id = ?",
        (user_id, room_id),
    )
    db.commit()


def mark_room_read(
    db: sqlite3.Connection, user_id: str, room_id: str, timestamp: str | None = None
) -> None:
    ts = timestamp or _now()
    db.execute(
        "INSERT INTO room_memberships (user_id, room_id, joined_at, last_read_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, room_id) DO UPDATE SET last_read_at = "
        "MAX(room_memberships.last_read_at, excluded.last_read_at)",
        (user_id, room_id, ts, ts),
    )
    db.commit()


def get_user_memberships(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT room_id FROM room_memberships WHERE user_id = ?",
        (user_id,),
    ).fetchall()


def get_unread_counts(db: sqlite3.Connection, user_id: str) -> dict:
    now = _now()
    rows = db.execute(
        "SELECT src.room_id, r.type, r.name, src.last_read_at, "
        "COUNT(m.id) AS unread "
        "FROM ("
        "  SELECT room_id, last_read_at FROM room_memberships WHERE user_id = ? "
        "  UNION "
        "  SELECT dp.room_id, COALESCE(rm.last_read_at, '1970-01-01') "
        "  FROM dm_participants dp "
        "  LEFT JOIN room_memberships rm ON rm.user_id = dp.user_id AND rm.room_id = dp.room_id "
        "  WHERE dp.user_id = ? "
        ") src "
        "JOIN rooms r ON r.id = src.room_id "
        "LEFT JOIN messages m ON m.room_id = src.room_id "
        "  AND m.created_at > src.last_read_at "
        "  AND m.expires_at > ? "
        "  AND m.user_id != ? "
        "GROUP BY src.room_id",
        (user_id, user_id, now, user_id),
    ).fetchall()
    return {
        r["room_id"]: {
            "count": r["unread"],
            "type": r["type"],
            "name": r["name"],
            "last_read_at": r["last_read_at"],
        }
        for r in rows
        if r["unread"] > 0
    }


def seed_event_room(db: sqlite3.Connection, event_id: str, event_name: str) -> None:
    create_room(db, "general", event_id, "general", event_name, is_main=True)


# --- Messages ---


def create_message(
    db: sqlite3.Connection,
    room_id: str,
    user_id: str,
    msg_type: str,
    content: str,
    ttl_minutes: int | None = DEFAULT_MESSAGE_TTL_MIN,
    reply_to_id: str | None = None,
) -> dict:
    msg_id = _uuid()
    now = _now()
    expires = (
        (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        if ttl_minutes is not None
        else "9999-12-31T23:59:59+00:00"
    )
    db.execute(
        "INSERT INTO messages (id, room_id, user_id, type, content, reply_to_id, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, user_id, msg_type, content, reply_to_id, expires, now),
    )
    db.commit()
    return {
        "id": msg_id,
        "room_id": room_id,
        "user_id": user_id,
        "type": msg_type,
        "content": content,
        "reply_to_id": reply_to_id,
        "expires_at": expires,
        "created_at": now,
    }


def get_room_messages(
    db: sqlite3.Connection, room_id: str, limit: int = 100
) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT m.*, u.display_name, u.username, u.color_index, u.avatar_url, "
        "rm.content AS reply_content, rm.type AS reply_type, "
        "ru.display_name AS reply_display_name "
        "FROM messages m "
        "JOIN users u ON u.id = m.user_id "
        "LEFT JOIN messages rm ON rm.id = m.reply_to_id "
        "LEFT JOIN users ru ON ru.id = rm.user_id "
        "WHERE m.room_id = ? AND m.expires_at > ? "
        "ORDER BY m.created_at DESC LIMIT ?",
        (room_id, _now(), limit),
    ).fetchall()


# --- Reactions ---


def add_reaction(
    db: sqlite3.Connection, message_id: str, user_id: str, emoji: str
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO message_reactions (message_id, user_id, emoji, created_at) "
        "VALUES (?, ?, ?, ?)",
        (message_id, user_id, emoji, _now()),
    )
    db.commit()


def remove_reaction(
    db: sqlite3.Connection, message_id: str, user_id: str, emoji: str
) -> None:
    db.execute(
        "DELETE FROM message_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
        (message_id, user_id, emoji),
    )
    db.commit()


def get_message_reactions(db: sqlite3.Connection, message_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT emoji, COUNT(*) as count, GROUP_CONCAT(user_id) as user_ids "
        "FROM message_reactions WHERE message_id = ? GROUP BY emoji",
        (message_id,),
    ).fetchall()
    return [
        {"emoji": r["emoji"], "count": r["count"], "user_ids": r["user_ids"].split(",")}
        for r in rows
    ]


def get_reactions_for_messages(
    db: sqlite3.Connection, message_ids: list[str]
) -> dict[str, list[dict]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" * len(message_ids))
    rows = db.execute(
        f"SELECT message_id, emoji, COUNT(*) as count, GROUP_CONCAT(user_id) as user_ids "
        f"FROM message_reactions WHERE message_id IN ({placeholders}) GROUP BY message_id, emoji",
        message_ids,
    ).fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        result.setdefault(r["message_id"], []).append(
            {
                "emoji": r["emoji"],
                "count": r["count"],
                "user_ids": r["user_ids"].split(","),
            }
        )
    return result


def purge_expired_messages(db: sqlite3.Connection) -> list[dict]:
    now = _now()
    expired = db.execute(
        "SELECT id, room_id, type, content FROM messages WHERE expires_at <= ?",
        (now,),
    ).fetchall()

    by_room: dict[str, list[str]] = {}
    image_paths: list[str] = []
    for msg in expired:
        by_room.setdefault(msg["room_id"], []).append(msg["id"])
        if msg["type"] in ("image", "video"):
            import json

            try:
                content = json.loads(msg["content"])
                if "url" in content:
                    image_paths.append(content["url"])
            except (json.JSONDecodeError, TypeError):
                pass

    if expired:
        db.execute("DELETE FROM messages WHERE expires_at <= ?", (now,))
        db.commit()

    uploads_dir = Path(__file__).resolve().parent / "chat" / "uploads"
    for path_str in image_paths:
        filename = path_str.rsplit("/", 1)[-1]
        (uploads_dir / filename).unlink(missing_ok=True)
        stem = filename.rsplit(".", 1)[0]
        (uploads_dir / f"{stem}_mod.webp").unlink(missing_ok=True)
        for i in range(3):
            (uploads_dir / f"{stem}_mod{i}.webp").unlink(missing_ok=True)

    return [{"room_id": rid, "message_ids": ids} for rid, ids in by_room.items()]


# --- Meetups ---


def create_meetup(
    db: sqlite3.Connection,
    creator_id: str,
    event_id: str,
    stage_id: str | None,
    title: str,
    meetup_time: str,
    location_lat: float | None = None,
    location_lng: float | None = None,
    location_label: str | None = None,
    note: str | None = None,
) -> dict:
    meetup_id = _uuid()
    now = _now()
    mt = datetime.fromisoformat(meetup_time)
    expires = (mt + timedelta(minutes=DEFAULT_MEETUP_GRACE_MIN)).isoformat()

    db.execute(
        "INSERT INTO meetups (id, creator_id, stage_id, title, location_lat, location_lng, "
        "location_label, meetup_time, note, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            meetup_id,
            creator_id,
            stage_id,
            title,
            location_lat,
            location_lng,
            location_label,
            meetup_time,
            note,
            now,
            expires,
        ),
    )

    create_room(db, meetup_id, event_id, "meetup", title)

    db.execute(
        "INSERT INTO meetup_attendees (meetup_id, user_id, joined_at) VALUES (?, ?, ?)",
        (meetup_id, creator_id, now),
    )
    db.commit()

    return {
        "id": meetup_id,
        "title": title,
        "meetup_time": meetup_time,
        "location_lat": location_lat,
        "location_lng": location_lng,
        "location_label": location_label,
        "note": note,
        "expires_at": expires,
        "creator_id": creator_id,
    }


def join_meetup(db: sqlite3.Connection, meetup_id: str, user_id: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO meetup_attendees (meetup_id, user_id, joined_at) "
        "VALUES (?, ?, ?)",
        (meetup_id, user_id, _now()),
    )
    db.commit()


def leave_meetup(db: sqlite3.Connection, meetup_id: str, user_id: str) -> None:
    db.execute(
        "DELETE FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
        (meetup_id, user_id),
    )
    db.commit()


def get_meetup_attendees(db: sqlite3.Connection, meetup_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT u.id, u.display_name FROM meetup_attendees ma "
        "JOIN users u ON u.id = ma.user_id WHERE ma.meetup_id = ?",
        (meetup_id,),
    ).fetchall()


def get_active_meetups(db: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT m.*, r.event_id, "
        "(SELECT COUNT(*) FROM meetup_attendees ma WHERE ma.meetup_id = m.id) AS attendee_count "
        "FROM meetups m "
        "JOIN rooms r ON r.id = m.id "
        "WHERE r.event_id = ? AND m.expires_at > ? "
        "ORDER BY m.meetup_time",
        (event_id, _now()),
    ).fetchall()


def purge_expired_meetups(db: sqlite3.Connection) -> list[str]:
    now = _now()
    expired = db.execute(
        "SELECT id FROM meetups WHERE expires_at <= ?", (now,)
    ).fetchall()
    expired_ids = [m["id"] for m in expired]

    for mid in expired_ids:
        db.execute("DELETE FROM messages WHERE room_id = ?", (mid,))
        db.execute("DELETE FROM rooms WHERE id = ?", (mid,))

    if expired_ids:
        db.execute("DELETE FROM meetups WHERE expires_at <= ?", (now,))
        db.commit()

    return expired_ids


# --- DMs ---


def find_or_create_dm(
    db: sqlite3.Connection, event_id: str, user_id: str, target_user_id: str
) -> str:
    existing = db.execute(
        "SELECT dp1.room_id FROM dm_participants dp1 "
        "JOIN dm_participants dp2 ON dp1.room_id = dp2.room_id "
        "WHERE dp1.user_id = ? AND dp2.user_id = ?",
        (user_id, target_user_id),
    ).fetchone()
    if existing:
        return existing["room_id"]

    target = get_user(db, target_user_id)
    if not target:
        raise ValueError("User not found")

    room_id = _uuid()
    create_room(db, room_id, event_id, "dm", "DM")
    now = _now()
    db.execute(
        "INSERT INTO dm_participants (room_id, user_id) VALUES (?, ?), (?, ?)",
        (room_id, user_id, room_id, target_user_id),
    )
    db.commit()
    return room_id


# --- Blocks ---


def block_user(db: sqlite3.Connection, blocker_id: str, blocked_id: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id, created_at) "
        "VALUES (?, ?, ?)",
        (blocker_id, blocked_id, _now()),
    )
    db.commit()


def unblock_user(db: sqlite3.Connection, blocker_id: str, blocked_id: str) -> None:
    db.execute(
        "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
        (blocker_id, blocked_id),
    )
    db.commit()


def is_blocked(db: sqlite3.Connection, blocker_id: str, blocked_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
            (blocker_id, blocked_id),
        ).fetchone()
        is not None
    )


# --- Reports ---


def create_report(
    db: sqlite3.Connection,
    reporter_id: str,
    reported_user_id: str,
    message_snapshot: str,
    room_id: str,
    reason: str,
) -> str:
    report_id = _uuid()
    db.execute(
        "INSERT INTO reports (id, reporter_id, reported_user_id, message_snapshot, "
        "room_id, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            report_id,
            reporter_id,
            reported_user_id,
            message_snapshot,
            room_id,
            reason,
            _now(),
        ),
    )
    db.commit()
    return report_id


def get_pending_reports(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT r.*, u.display_name AS reporter_name, "
        "u2.display_name AS reported_name "
        "FROM reports r "
        "JOIN users u ON u.id = r.reporter_id "
        "JOIN users u2 ON u2.id = r.reported_user_id "
        "WHERE r.status = 'pending' ORDER BY r.created_at DESC"
    ).fetchall()


def resolve_report(db: sqlite3.Connection, report_id: str, status: str) -> None:
    db.execute(
        "UPDATE reports SET status = ?, reviewed_at = ? WHERE id = ?",
        (status, _now(), report_id),
    )
    db.commit()


def purge_old_reports(db: sqlite3.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    result = db.execute(
        "DELETE FROM reports WHERE status IN ('actioned', 'dismissed') "
        "AND reviewed_at < ?",
        (cutoff,),
    )
    db.commit()
    return result.rowcount


# --- Strikes ---


STRIKE_TTL_HOURS = 4
MAX_MUTES_BEFORE_BAN = 3


def add_strike(
    db: sqlite3.Connection, user_id: str, reason: str, detail: str | None = None
) -> int:
    now = _now()
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=STRIKE_TTL_HOURS)
    ).isoformat()
    db.execute(
        "UPDATE strikes SET expires_at = ? WHERE user_id = ? AND expires_at > ?",
        (expires, user_id, now),
    )
    db.execute(
        "INSERT INTO strikes (id, user_id, reason, detail, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_uuid(), user_id, reason, detail, now, expires),
    )
    db.commit()
    return db.execute(
        "SELECT COUNT(*) FROM strikes WHERE user_id = ? AND expires_at > ?",
        (user_id, now),
    ).fetchone()[0]


def get_strike_count(db: sqlite3.Connection, user_id: str) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM strikes WHERE user_id = ? AND expires_at > ?",
        (user_id, _now()),
    ).fetchone()[0]


def increment_mute_count(db: sqlite3.Connection, user_id: str) -> int:
    db.execute("UPDATE users SET mute_count = mute_count + 1 WHERE id = ?", (user_id,))
    db.commit()
    return db.execute(
        "SELECT mute_count FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0]


# --- Reachability ---

REACHABILITY_HOURS = 2


def get_reachable_member_count(db: sqlite3.Connection, room_id: str) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=REACHABILITY_HOURS)
    ).isoformat()
    return db.execute(
        "SELECT COUNT(DISTINCT rm.user_id) FROM room_memberships rm "
        "JOIN users u ON u.id = rm.user_id "
        "WHERE rm.room_id = ? AND ("
        "  u.last_seen > ? OR "
        "  EXISTS (SELECT 1 FROM chat_push_subscriptions cps WHERE cps.user_id = rm.user_id)"
        ")",
        (room_id, cutoff),
    ).fetchone()[0]


def get_reachable_member_counts(
    db: sqlite3.Connection, room_ids: list[str]
) -> dict[str, int]:
    if not room_ids:
        return {}
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=REACHABILITY_HOURS)
    ).isoformat()
    placeholders = ",".join("?" for _ in room_ids)
    rows = db.execute(
        f"SELECT rm.room_id, COUNT(DISTINCT rm.user_id) AS cnt "
        f"FROM room_memberships rm "
        f"JOIN users u ON u.id = rm.user_id "
        f"WHERE rm.room_id IN ({placeholders}) AND ("
        f"  u.last_seen > ? OR "
        f"  EXISTS (SELECT 1 FROM chat_push_subscriptions cps WHERE cps.user_id = rm.user_id)"
        f") GROUP BY rm.room_id",
        list(room_ids) + [cutoff],
    ).fetchall()
    return {r["room_id"]: r["cnt"] for r in rows}


def delete_user_messages(db: sqlite3.Connection, user_id: str) -> list[dict]:
    now = _now()
    msgs = db.execute(
        "SELECT id, room_id, type, content FROM messages "
        "WHERE user_id = ? AND expires_at > ?",
        (user_id, now),
    ).fetchall()

    by_room: dict[str, list[str]] = {}
    for msg in msgs:
        by_room.setdefault(msg["room_id"], []).append(msg["id"])
        if msg["type"] in ("image", "video"):
            import json

            try:
                content = json.loads(msg["content"])
                url = content.get("url", "")
                if url:
                    uploads = Path(__file__).resolve().parent / "chat" / "uploads"
                    filename = url.rsplit("/", 1)[-1]
                    (uploads / filename).unlink(missing_ok=True)
            except (json.JSONDecodeError, TypeError):
                pass

    if msgs:
        db.execute(
            "DELETE FROM messages WHERE user_id = ? AND expires_at > ?",
            (user_id, now),
        )
        db.commit()

    return [{"room_id": rid, "message_ids": ids} for rid, ids in by_room.items()]


def find_user_by_push_endpoint(
    db: sqlite3.Connection, endpoint: str
) -> sqlite3.Row | None:
    row = db.execute(
        "SELECT user_id FROM chat_push_subscriptions WHERE endpoint = ?",
        (endpoint,),
    ).fetchone()
    if not row:
        return None
    return get_user(db, row["user_id"])


# --- Admin queries ---


def get_admin_stats(db: sqlite3.Connection, online_user_ids: set[str]) -> dict:
    now = _now()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=REACHABILITY_HOURS)
    ).isoformat()
    reachable = db.execute(
        "SELECT COUNT(DISTINCT u.id) FROM users u WHERE "
        "u.last_seen > ? OR EXISTS ("
        "  SELECT 1 FROM chat_push_subscriptions cps WHERE cps.user_id = u.id"
        ")",
        (cutoff,),
    ).fetchone()[0]
    return {
        "total_users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "online_count": len(online_user_ids),
        "reachable_count": reachable,
        "total_messages_active": db.execute(
            "SELECT COUNT(*) FROM messages WHERE expires_at > ?", (now,)
        ).fetchone()[0],
        "total_rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "pending_reports": db.execute(
            "SELECT COUNT(*) FROM reports WHERE status = 'pending'"
        ).fetchone()[0],
        "active_bans": db.execute("SELECT COUNT(*) FROM bans").fetchone()[0],
        "active_strikes": db.execute(
            "SELECT COUNT(*) FROM strikes WHERE expires_at > ?", (now,)
        ).fetchone()[0],
    }


def search_users(
    db: sqlite3.Connection,
    online_ids: set[str],
    q: str = "",
    online_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    now = _now()
    if online_only and not online_ids:
        return []

    where = []
    params: list = []
    if q:
        where.append("(u.display_name LIKE ? OR u.username LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if online_only:
        placeholders = ",".join("?" for _ in online_ids)
        where.append(f"u.id IN ({placeholders})")
        params.extend(online_ids)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT u.id, u.display_name, u.username, u.country, u.avatar_url, "
        f"u.provider, u.muted_until, u.mute_count, u.created_at, u.last_seen, u.last_active, "
        f"(SELECT GROUP_CONCAT(up.provider, ',') FROM user_providers up WHERE up.user_id = u.id) AS providers, "
        f"(SELECT COUNT(*) FROM strikes s WHERE s.user_id = u.id AND s.expires_at > ?) AS strike_count, "
        f"(SELECT COUNT(*) FROM bans b WHERE b.user_id = u.id) AS ban_count, "
        f"(SELECT COUNT(*) FROM chat_push_subscriptions cps WHERE cps.user_id = u.id) AS push_count "
        f"FROM users u {where_sql} "
        f"ORDER BY u.last_seen DESC NULLS LAST "
        f"LIMIT ? OFFSET ?",
        [now] + params + [limit, offset],
    ).fetchall()

    return [
        {
            "id": r["id"],
            "display_name": r["display_name"],
            "username": r["username"],
            "country": r["country"],
            "avatar_url": r["avatar_url"],
            "providers": (r["providers"] or r["provider"]).split(","),
            "muted_until": r["muted_until"],
            "mute_count": r["mute_count"],
            "created_at": r["created_at"],
            "last_seen": r["last_seen"],
            "last_active": r["last_active"],
            "is_online": r["id"] in online_ids,
            "has_push": r["push_count"] > 0,
            "strike_count": r["strike_count"],
            "is_banned": r["ban_count"] > 0,
        }
        for r in rows
    ]


def get_user_admin_detail(db: sqlite3.Connection, user_id: str) -> dict | None:
    user = get_user(db, user_id)
    if not user:
        return None
    now = _now()

    strikes = db.execute(
        "SELECT id, reason, detail, created_at, expires_at FROM strikes "
        "WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()

    reports = db.execute(
        "SELECT r.id, u.display_name AS reporter_name, r.reason, "
        "r.message_snapshot, r.status, r.created_at "
        "FROM reports r JOIN users u ON u.id = r.reporter_id "
        "WHERE r.reported_user_id = ? ORDER BY r.created_at DESC",
        (user_id,),
    ).fetchall()

    bans = db.execute(
        "SELECT id, reason, created_at FROM bans WHERE user_id = ?",
        (user_id,),
    ).fetchall()

    msg_count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id = ? AND expires_at > ?",
        (user_id, now),
    ).fetchone()[0]

    return {
        "id": user["id"],
        "display_name": user["display_name"],
        "username": user["username"],
        "country": user["country"],
        "avatar_url": user["avatar_url"],
        "color_index": user["color_index"],
        "provider": user["provider"],
        "muted_until": user["muted_until"],
        "mute_count": user["mute_count"],
        "created_at": user["created_at"],
        "last_seen": user["last_seen"],
        "strikes": [
            {
                "id": s["id"],
                "reason": s["reason"],
                "detail": s["detail"],
                "created_at": s["created_at"],
                "expires_at": s["expires_at"],
                "is_active": s["expires_at"] and s["expires_at"] > now,
            }
            for s in strikes
        ],
        "reports_against": [dict(r) for r in reports],
        "bans": [dict(b) for b in bans],
        "message_count": msg_count,
    }


def get_all_bans(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        "SELECT b.id AS ban_id, b.user_id, u.display_name, u.username, "
        "b.provider, b.provider_id, b.device_fingerprint, b.reason, b.created_at "
        "FROM bans b LEFT JOIN users u ON u.id = b.user_id "
        "ORDER BY b.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_moderation_log(
    db: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[dict]:
    rows = db.execute(
        "SELECT type, user_id, display_name, detail, created_at FROM ("
        "  SELECT CASE WHEN s.reason = 'warnings_cleared' THEN 'cleared' ELSE 'strike' END AS type, "
        "    s.user_id, u.display_name, "
        "    s.reason || CASE WHEN s.detail IS NOT NULL THEN ': ' || s.detail ELSE '' END AS detail, "
        "    s.created_at "
        "  FROM strikes s LEFT JOIN users u ON u.id = s.user_id "
        "  UNION ALL "
        "  SELECT 'ban' AS type, b.user_id, u.display_name, b.reason AS detail, b.created_at "
        "  FROM bans b LEFT JOIN users u ON u.id = b.user_id "
        "  UNION ALL "
        "  SELECT 'report_' || r.status AS type, r.reported_user_id AS user_id, "
        "    u.display_name, r.reason AS detail, r.reviewed_at AS created_at "
        "  FROM reports r LEFT JOIN users u ON u.id = r.reported_user_id "
        "  WHERE r.status != 'pending' AND r.reviewed_at IS NOT NULL"
        ") ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_room_stats(db: sqlite3.Connection, online_counts: dict[str, int]) -> list[dict]:
    now = _now()
    rows = db.execute(
        "SELECT r.id, r.name, r.type, r.description, r.is_main, "
        "r.is_moderated, r.is_read_only, r.allows_media, r.ttl_minutes, r.position, "
        "  (SELECT COUNT(*) FROM messages m WHERE m.room_id = r.id AND m.expires_at > ?) AS message_count, "
        "  (SELECT MAX(m.created_at) FROM messages m WHERE m.room_id = r.id) AS last_message_at "
        "FROM rooms r ORDER BY r.position, last_message_at DESC NULLS LAST",
        (now,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "description": r["description"] or "",
            "is_main": bool(r["is_main"]),
            "is_moderated": bool(r["is_moderated"]),
            "is_read_only": bool(r["is_read_only"]),
            "allows_media": bool(r["allows_media"]),
            "ttl_minutes": r["ttl_minutes"],
            "online_count": online_counts.get(r["id"], 0),
            "member_count": 0,
            "message_count": r["message_count"],
            "last_message_at": r["last_message_at"],
        }
        for r in rows
    ]


# --- Purge all ---


def purge_expired_sessions(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
    db.execute("DELETE FROM email_tokens WHERE expires_at <= ?", (_now(),))
    db.commit()


def purge_expired_strikes(db: sqlite3.Connection) -> int:
    now = _now()
    result = db.execute(
        "DELETE FROM strikes WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    )
    db.commit()
    return result.rowcount


PUSH_SUBSCRIPTION_MAX_AGE_DAYS = 90


def purge_stale_push_subscriptions(db: sqlite3.Connection) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=PUSH_SUBSCRIPTION_MAX_AGE_DAYS)
    ).isoformat()
    result = db.execute(
        "DELETE FROM chat_push_subscriptions WHERE created_at < ? "
        "AND user_id NOT IN (SELECT id FROM users WHERE last_seen > ?)",
        (cutoff, cutoff),
    )
    db.commit()
    return result.rowcount


def wipe_all_chat_data(db: sqlite3.Connection) -> None:
    for table in (
        "avatars",
        "email_tokens",
        "chat_push_subscriptions",
        "strikes",
        "reports",
        "blocks",
        "dm_participants",
        "meetup_attendees",
        "meetups",
        "message_reactions",
        "messages",
        "rooms",
        "sessions",
        "bans",
        "users",
    ):
        db.execute(f"DELETE FROM {table}")
    db.commit()


# --- Email hash utility ---


def hash_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


# --- Push subscriptions ---


def save_push_subscription(
    db: sqlite3.Connection, user_id: str, endpoint: str, p256dh: str, auth: str
) -> None:
    db.execute(
        """INSERT INTO chat_push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,
               p256dh=excluded.p256dh, auth=excluded.auth, created_at=excluded.created_at""",
        (user_id, endpoint, p256dh, auth, _now()),
    )
    db.commit()


def delete_push_subscription(
    db: sqlite3.Connection, user_id: str, endpoint: str
) -> None:
    db.execute(
        "DELETE FROM chat_push_subscriptions WHERE user_id = ? AND endpoint = ?",
        (user_id, endpoint),
    )
    db.commit()


def delete_push_subscription_by_endpoint(db: sqlite3.Connection, endpoint: str) -> None:
    db.execute("DELETE FROM chat_push_subscriptions WHERE endpoint = ?", (endpoint,))
    db.commit()


def get_push_subscriptions(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT endpoint, p256dh, auth FROM chat_push_subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchall()


def get_push_subscription_count(db: sqlite3.Connection, user_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM chat_push_subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row[0]
