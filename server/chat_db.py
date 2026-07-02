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
            created_at         TEXT NOT NULL,
            last_seen          TEXT,
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
            id         TEXT PRIMARY KEY,
            event_id   TEXT NOT NULL,
            type       TEXT NOT NULL,
            name       TEXT NOT NULL,
            is_main    INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
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
            created_at TEXT NOT NULL
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


_chat_db_initialized = False


def get_chat_db() -> sqlite3.Connection:
    global _chat_db_initialized
    CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(CHAT_DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
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
        "SELECT * FROM users WHERE provider = ? AND provider_id = ?",
        (provider, provider_id),
    ).fetchone()


def get_user(db: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def update_display_name(db: sqlite3.Connection, user_id: str, name: str) -> None:
    db.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user_id))
    db.commit()


def update_last_seen(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (_now(), user_id))
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
) -> dict:
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO rooms (id, event_id, type, name, is_main, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (room_id, event_id, room_type, name, 1 if is_main else 0, now),
    )
    db.commit()
    return {"id": room_id, "event_id": event_id, "type": room_type, "name": name}


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
    ttl_minutes: int = DEFAULT_MESSAGE_TTL_MIN,
    reply_to_id: str | None = None,
) -> dict:
    msg_id = _uuid()
    now = _now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
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
    result = db.execute(
        "DELETE FROM reports WHERE status IN ('actioned', 'dismissed') "
        "AND reviewed_at < datetime('now', '-30 days')"
    )
    db.commit()
    return result.rowcount


# --- Strikes ---


def add_strike(
    db: sqlite3.Connection, user_id: str, reason: str, detail: str | None = None
) -> int:
    db.execute(
        "INSERT INTO strikes (id, user_id, reason, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (_uuid(), user_id, reason, detail, _now()),
    )
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) FROM strikes WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    return count


def get_strike_count(db: sqlite3.Connection, user_id: str) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM strikes WHERE user_id = ?", (user_id,)
    ).fetchone()[0]


# --- Purge all ---


def purge_expired_sessions(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
    db.execute("DELETE FROM email_tokens WHERE expires_at <= ?", (_now(),))
    db.commit()


def wipe_all_chat_data(db: sqlite3.Connection) -> None:
    for table in (
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
