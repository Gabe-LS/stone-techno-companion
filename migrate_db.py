#!/usr/bin/env python3
"""One-time migration: old lineup.db schema -> new schema with videos table."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "lineup.db"
BACKUP_PATH = Path(__file__).resolve().parent / "lineup.db.bak"
VIDEOS_JSON = Path(__file__).resolve().parent / "output" / "videos.json"


def migrate() -> None:
    if not DB_PATH.exists():
        print("No lineup.db found — nothing to migrate.")
        return

    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Backed up to {BACKUP_PATH}")

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = OFF")

    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "schedule" in tables and "videos" in tables:
        print("Already migrated (schedule and videos tables exist). Skipping.")
        db.close()
        return

    old_has_sections = "sections" in tables
    old_has_artist_sections = "artist_sections" in tables

    section_lookup: dict[str, tuple[str, str]] = {}
    if old_has_sections:
        for row in db.execute("SELECT timestamp_key, date, period FROM sections"):
            section_lookup[row["timestamp_key"]] = (row["date"], row["period"])

    db.executescript("""
        CREATE TABLE IF NOT EXISTS artists_new (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            photo_url         TEXT,
            photo_local       TEXT,
            instagram         TEXT,
            soundcloud        TEXT,
            spotify           TEXT,
            youtube           TEXT,
            linktree          TEXT,
            ra                TEXT,
            ig_followers      INTEGER,
            sc_followers      INTEGER,
            spotify_listeners INTEGER,
            ra_followers      INTEGER,
            ra_bio            TEXT
        );
        CREATE TABLE IF NOT EXISTS locations_new (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS schedule (
            artist_id   TEXT NOT NULL REFERENCES artists_new(id),
            location_id TEXT,
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            date        TEXT NOT NULL,
            period      TEXT NOT NULL CHECK (period IN ('day', 'night')),
            PRIMARY KEY (artist_id, start_time)
        );
        CREATE TABLE IF NOT EXISTS videos (
            video_id    TEXT PRIMARY KEY,
            artist_id   TEXT NOT NULL REFERENCES artists_new(id),
            title       TEXT NOT NULL,
            url         TEXT NOT NULL,
            views       INTEGER NOT NULL DEFAULT 0,
            duration    INTEGER NOT NULL DEFAULT 0,
            upload_date INTEGER,
            position    INTEGER NOT NULL DEFAULT 0
        );
    """)

    artist_cols = {row[1] for row in db.execute("PRAGMA table_info(artists)")}
    has_ra = "ra" in artist_cols
    has_ra_bio = "ra_bio" in artist_cols
    has_ra_followers = "ra_followers" in artist_cols
    has_linktree = "linktree" in artist_cols
    has_youtube = "youtube" in artist_cols

    for row in db.execute("SELECT * FROM artists"):
        db.execute(
            "INSERT OR IGNORE INTO artists_new "
            "(id, name, photo_url, photo_local, instagram, soundcloud, spotify, "
            "youtube, linktree, ra, ig_followers, sc_followers, spotify_listeners, "
            "ra_followers, ra_bio) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["overlay_id"],
                row["name"],
                row["photo"],
                row["photo_local"] if "photo_local" in artist_cols else None,
                row["instagram"],
                row["soundcloud"],
                row["spotify"],
                row["youtube"] if has_youtube else None,
                row["linktree"] if has_linktree else None,
                row["ra"] if has_ra else None,
                row["ig_followers"],
                row["sc_followers"],
                row["spotify_listeners"]
                if "spotify_listeners" in artist_cols
                else None,
                row["ra_followers"] if has_ra_followers else None,
                row["ra_bio"] if has_ra_bio else None,
            ),
        )
    artists_count = db.execute("SELECT COUNT(*) FROM artists_new").fetchone()[0]
    print(f"Migrated {artists_count} artists")

    used_locs = set()
    if old_has_artist_sections:
        used_locs = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT location_id FROM artist_sections WHERE location_id IS NOT NULL"
            ).fetchall()
        }
    for row in db.execute("SELECT * FROM locations"):
        loc_id = row["location_id"]
        if used_locs and loc_id not in used_locs:
            print(f"  Skipping orphan location: {loc_id} ({row['name']})")
            continue
        db.execute(
            "INSERT OR IGNORE INTO locations_new (id, name, description) VALUES (?, ?, ?)",
            (loc_id, row["name"], row["description"]),
        )
    locs_count = db.execute("SELECT COUNT(*) FROM locations_new").fetchone()[0]
    print(f"Migrated {locs_count} locations (skipped orphans)")

    if old_has_artist_sections:
        as_cols = {row[1] for row in db.execute("PRAGMA table_info(artist_sections)")}
        has_start = "start_time" in as_cols
        has_end = "end_time" in as_cols
        for row in db.execute("SELECT * FROM artist_sections"):
            oid = row["overlay_id"]
            ts_key = row["timestamp_key"]
            loc_id = row["location_id"]
            start = row["start_time"] if has_start and row["start_time"] else ts_key
            end = row["end_time"] if has_end and row["end_time"] else ""
            date, period = section_lookup.get(ts_key, ("", "day"))
            db.execute(
                "INSERT OR IGNORE INTO schedule "
                "(artist_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (oid, loc_id, start, end, date, period),
            )
    sched_count = db.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
    print(f"Migrated {sched_count} schedule entries")

    vid_count = 0
    if VIDEOS_JSON.exists():
        video_data = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
        for artist_id, vids in video_data.items():
            for pos, v in enumerate(vids):
                db.execute(
                    "INSERT OR IGNORE INTO videos "
                    "(video_id, artist_id, title, url, views, duration, upload_date, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        v["id"],
                        artist_id,
                        v.get("title", ""),
                        v.get("url", ""),
                        v.get("views", 0),
                        v.get("duration", 0),
                        v.get("date"),
                        pos,
                    ),
                )
                vid_count += 1
    print(f"Migrated {vid_count} videos from videos.json")

    db.execute("DROP TABLE IF EXISTS artists")
    db.execute("ALTER TABLE artists_new RENAME TO artists")
    db.execute("DROP TABLE IF EXISTS locations")
    db.execute("ALTER TABLE locations_new RENAME TO locations")
    db.execute("DROP TABLE IF EXISTS artist_sections")
    db.execute("DROP TABLE IF EXISTS sections")

    db.execute("CREATE INDEX IF NOT EXISTS idx_schedule_date ON schedule(date, period)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_videos_artist ON videos(artist_id)")

    db.execute("PRAGMA foreign_keys = ON")
    db.commit()

    fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        print(f"WARNING: {len(fk_errors)} foreign key violations found!")
        for err in fk_errors[:5]:
            print(f"  {err}")
    else:
        print("Foreign key check passed")

    db.execute("VACUUM")
    db.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
