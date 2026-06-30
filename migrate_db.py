#!/usr/bin/env python3
"""One-time migration: old lineup.db schema -> new normalized schema."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "lineup.db"
BACKUP_PATH = Path(__file__).resolve().parent / "lineup.db.bak"
VIDEOS_JSON = Path(__file__).resolve().parent / "output" / "videos.json"
OVERRIDES_PATH = Path(__file__).resolve().parent / "scraper" / "overrides.toml"

DEFAULT_EVENT_ID = "stone-techno-2026"
DEFAULT_EVENT_NAME = "Stone Techno 2026"
DEFAULT_EVENT_URL = "https://www.stone-techno.com/"
DEFAULT_TIMEZONE = "Europe/Berlin"

FLOOR_COLORS = {
    "eisbahn": "198, 249, 197",
    "grand-hall": "197, 249, 241",
    "koksofenbatterie": "197, 213, 249",
    "listening-floor": "226, 197, 249",
    "mischanlage": "249, 197, 228",
    "salzlager": "249, 211, 197",
    "werksschwimmbad": "243, 249, 197",
}

PLATFORM_POSITIONS = {
    "instagram": 0,
    "soundcloud": 1,
    "spotify": 2,
    "youtube": 3,
    "ra": 4,
    "linktree": 5,
}


def _get_cols(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _get_tables(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def migrate() -> None:
    if not DB_PATH.exists():
        print("No lineup.db found — nothing to migrate.")
        return

    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backed up to {BACKUP_PATH}")

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = OFF")

    tables = _get_tables(db)

    if "artist_links" in tables and "artist_sets" in tables:
        print("Already fully migrated. Skipping.")
        db.close()
        return

    # Detect old schema variants
    has_old_overlay = "artists" in tables and "overlay_id" in _get_cols(db, "artists")
    has_old_sections = "sections" in tables
    has_old_artist_sections = "artist_sections" in tables

    # Build date lookup from old sections table
    section_lookup: dict[str, tuple[str, str]] = {}
    if has_old_sections:
        for row in db.execute("SELECT timestamp_key, date, period FROM sections"):
            section_lookup[row["timestamp_key"]] = (row["date"], row["period"])

    # Create new tables
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            source_url TEXT,
            website    TEXT,
            start_date TEXT,
            end_date   TEXT,
            timezone   TEXT NOT NULL DEFAULT 'Europe/Berlin',
            address    TEXT,
            latitude   REAL,
            longitude  REAL
        );
        CREATE TABLE IF NOT EXISTS artists_new (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            photo_url  TEXT,
            photo_file TEXT,
            bio        TEXT
        );
        CREATE TABLE IF NOT EXISTS artist_links (
            artist_id      TEXT NOT NULL REFERENCES artists_new(id),
            platform       TEXT NOT NULL,
            url            TEXT NOT NULL,
            follower_count INTEGER,
            position       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (artist_id, platform)
        );
        CREATE TABLE IF NOT EXISTS locations_new (
            id        TEXT PRIMARY KEY,
            event_id  TEXT NOT NULL REFERENCES events(id),
            name      TEXT NOT NULL,
            color     TEXT,
            about     TEXT,
            address   TEXT,
            latitude  REAL,
            longitude REAL
        );
        CREATE TABLE IF NOT EXISTS location_notes (
            location_id TEXT NOT NULL REFERENCES locations_new(id),
            date        TEXT NOT NULL,
            note        TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, date, position)
        );
        CREATE TABLE IF NOT EXISTS location_details (
            location_id TEXT NOT NULL REFERENCES locations_new(id),
            label       TEXT NOT NULL,
            value       TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, label)
        );
        CREATE TABLE IF NOT EXISTS schedule_new (
            artist_id   TEXT NOT NULL REFERENCES artists_new(id),
            event_id    TEXT NOT NULL REFERENCES events(id),
            location_id TEXT REFERENCES locations_new(id),
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            date        TEXT NOT NULL,
            period      TEXT,
            set_type    TEXT,
            PRIMARY KEY (artist_id, event_id, start_time)
        );
        CREATE TABLE IF NOT EXISTS artist_sets (
            id           TEXT PRIMARY KEY,
            artist_id    TEXT NOT NULL REFERENCES artists_new(id),
            platform     TEXT NOT NULL DEFAULT 'youtube',
            url          TEXT NOT NULL,
            title        TEXT NOT NULL,
            view_count   INTEGER NOT NULL DEFAULT 0,
            duration_min INTEGER NOT NULL DEFAULT 0,
            upload_date  INTEGER,
            position     INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Event
    db.execute(
        "INSERT OR IGNORE INTO events (id, name, source_url, timezone) VALUES (?, ?, ?, ?)",
        (DEFAULT_EVENT_ID, DEFAULT_EVENT_NAME, DEFAULT_EVENT_URL, DEFAULT_TIMEZONE),
    )
    print(f"Created event: {DEFAULT_EVENT_ID}")

    # Artists
    artist_cols = _get_cols(db, "artists")
    id_col = "overlay_id" if has_old_overlay else "id"
    photo_col = "photo" if "photo" in artist_cols else "photo_url"
    photo_file_col = "photo_local" if "photo_local" in artist_cols else "photo_file"
    bio_col = "ra_bio" if "ra_bio" in artist_cols else "bio"

    for row in db.execute("SELECT * FROM artists"):
        aid = row[id_col]
        db.execute(
            "INSERT OR IGNORE INTO artists_new (id, name, photo_url, photo_file, bio) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                aid,
                row["name"],
                row[photo_col] if photo_col in artist_cols else None,
                row[photo_file_col] if photo_file_col in artist_cols else None,
                row[bio_col] if bio_col in artist_cols else None,
            ),
        )
        # Migrate social links
        link_map = {
            "instagram": (
                "instagram",
                "ig_followers" if "ig_followers" in artist_cols else None,
            ),
            "soundcloud": (
                "soundcloud",
                "sc_followers" if "sc_followers" in artist_cols else None,
            ),
            "spotify": (
                "spotify",
                "spotify_listeners" if "spotify_listeners" in artist_cols else None,
            ),
            "youtube": ("youtube", None),
            "linktree": ("linktree", None),
            "ra": ("ra", "ra_followers" if "ra_followers" in artist_cols else None),
        }
        for platform, (url_col, count_col) in link_map.items():
            if url_col not in artist_cols:
                continue
            url = row[url_col]
            if url:
                count = row[count_col] if count_col else None
                pos = PLATFORM_POSITIONS.get(platform, 99)
                db.execute(
                    "INSERT OR IGNORE INTO artist_links (artist_id, platform, url, follower_count, position) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (aid, platform, url, count, pos),
                )
    artists_count = db.execute("SELECT COUNT(*) FROM artists_new").fetchone()[0]
    links_count = db.execute("SELECT COUNT(*) FROM artist_links").fetchone()[0]
    print(f"Migrated {artists_count} artists with {links_count} links")

    # Locations
    loc_table = "locations"
    loc_cols = _get_cols(db, loc_table)
    loc_id_col = "location_id" if "location_id" in loc_cols else "id"

    used_locs = set()
    source_schedule = None
    if has_old_artist_sections:
        source_schedule = "artist_sections"
        used_locs = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT location_id FROM artist_sections WHERE location_id IS NOT NULL"
            ).fetchall()
        }
    elif "schedule" in tables:
        source_schedule = "schedule"
        used_locs = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT location_id FROM schedule WHERE location_id IS NOT NULL"
            ).fetchall()
        }

    # Get dates for location notes
    all_dates: list[str] = []
    if section_lookup:
        all_dates = sorted({d for d, _ in section_lookup.values()})
    elif source_schedule == "schedule":
        all_dates = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT date FROM schedule ORDER BY date"
            ).fetchall()
        ]

    for row in db.execute(f"SELECT * FROM {loc_table}"):
        loc_id = row[loc_id_col]
        if used_locs and loc_id not in used_locs:
            print(f"  Skipping orphan location: {loc_id} ({row['name']})")
            continue
        color = FLOOR_COLORS.get(loc_id)
        db.execute(
            "INSERT OR IGNORE INTO locations_new (id, event_id, name, color) "
            "VALUES (?, ?, ?, ?)",
            (loc_id, DEFAULT_EVENT_ID, row["name"], color),
        )
        desc = row["description"] if "description" in loc_cols else None
        if desc:
            for date in all_dates:
                db.execute(
                    "INSERT OR IGNORE INTO location_notes (location_id, date, note, position) "
                    "VALUES (?, ?, ?, 0)",
                    (loc_id, date, desc),
                )
    locs_count = db.execute("SELECT COUNT(*) FROM locations_new").fetchone()[0]
    print(f"Migrated {locs_count} locations with colors")

    # Schedule
    sched_count = 0
    if source_schedule == "artist_sections":
        as_cols = _get_cols(db, "artist_sections")
        has_start = "start_time" in as_cols
        has_end = "end_time" in as_cols
        for row in db.execute("SELECT * FROM artist_sections"):
            oid = row["overlay_id"]
            ts_key = row["timestamp_key"]
            start = row["start_time"] if has_start and row["start_time"] else ts_key
            end = row["end_time"] if has_end and row["end_time"] else ""
            date, period = section_lookup.get(ts_key, ("", None))
            db.execute(
                "INSERT OR IGNORE INTO schedule_new "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (oid, DEFAULT_EVENT_ID, row["location_id"], start, end, date, period),
            )
            sched_count += 1
    elif source_schedule == "schedule":
        sched_cols = _get_cols(db, "schedule")
        for row in db.execute("SELECT * FROM schedule"):
            eid = row["event_id"] if "event_id" in sched_cols else DEFAULT_EVENT_ID
            db.execute(
                "INSERT OR IGNORE INTO schedule_new "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["artist_id"],
                    eid,
                    row["location_id"],
                    row["start_time"],
                    row["end_time"],
                    row["date"],
                    row["period"],
                ),
            )
            sched_count += 1
    print(f"Migrated {sched_count} schedule entries")

    # Videos/Sets
    set_count = 0
    if "videos" in tables:
        vid_cols = _get_cols(db, "videos")
        vid_id_col = "video_id" if "video_id" in vid_cols else "id"
        views_col = "views" if "views" in vid_cols else "view_count"
        dur_col = "duration" if "duration" in vid_cols else "duration_min"
        for row in db.execute("SELECT * FROM videos"):
            db.execute(
                "INSERT OR IGNORE INTO artist_sets "
                "(id, artist_id, platform, url, title, view_count, duration_min, upload_date, position) "
                "VALUES (?, ?, 'youtube', ?, ?, ?, ?, ?, ?)",
                (
                    row[vid_id_col],
                    row["artist_id"],
                    row["url"],
                    row["title"],
                    row[views_col],
                    row[dur_col],
                    row["upload_date"] if "upload_date" in vid_cols else None,
                    row["position"],
                ),
            )
            set_count += 1
    elif VIDEOS_JSON.exists():
        video_data = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
        for artist_id, vids in video_data.items():
            for pos, v in enumerate(vids):
                db.execute(
                    "INSERT OR IGNORE INTO artist_sets "
                    "(id, artist_id, platform, url, title, view_count, duration_min, upload_date, position) "
                    "VALUES (?, ?, 'youtube', ?, ?, ?, ?, ?, ?)",
                    (
                        v["id"],
                        artist_id,
                        v.get("url", ""),
                        v.get("title", ""),
                        v.get("views", 0),
                        v.get("duration", 0),
                        v.get("date"),
                        pos,
                    ),
                )
                set_count += 1
    print(f"Migrated {set_count} artist sets")

    # Floor curator notes from overrides
    if OVERRIDES_PATH.exists():
        import tomllib

        with open(OVERRIDES_PATH, "rb") as f:
            overrides = tomllib.load(f)
        curators = overrides.get("floor_curators", {})
        note_count = 0
        for key, note in curators.items():
            date, loc_id = key.split(".", 1)
            existing = db.execute(
                "SELECT MAX(position) FROM location_notes WHERE location_id = ? AND date = ?",
                (loc_id, date),
            ).fetchone()[0]
            pos = (existing or 0) + 1
            db.execute(
                "INSERT OR IGNORE INTO location_notes (location_id, date, note, position) "
                "VALUES (?, ?, ?, ?)",
                (loc_id, date, note, pos),
            )
            note_count += 1
        if note_count:
            print(f"Migrated {note_count} floor curator notes")

    # Drop old tables, rename new ones
    for t in (
        "artists",
        "locations",
        "schedule",
        "videos",
        "artist_sections",
        "sections",
    ):
        db.execute(f"DROP TABLE IF EXISTS {t}")
    db.execute("ALTER TABLE artists_new RENAME TO artists")
    db.execute("ALTER TABLE locations_new RENAME TO locations")
    db.execute("ALTER TABLE schedule_new RENAME TO schedule")

    # Indexes
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_artist_links_artist ON artist_links(artist_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_schedule_event ON schedule(event_id, date, period)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_artist_sets_artist ON artist_sets(artist_id)"
    )

    db.execute("PRAGMA foreign_keys = ON")
    db.commit()

    fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        print(f"WARNING: {len(fk_errors)} foreign key violations!")
        for err in fk_errors[:5]:
            print(f"  {err}")
    else:
        print("Foreign key check passed")

    db.execute("VACUUM")
    db.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
