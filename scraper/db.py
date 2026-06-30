from __future__ import annotations

import sqlite3
from pathlib import Path


PLATFORM_LINK_FIELDS = {
    "instagram",
    "soundcloud",
    "spotify",
    "linktree",
    "youtube",
    "ra",
}

OVERRIDE_ARTIST_FIELDS = {"photo_url"}
OVERRIDE_ALIASES = {"photo": "photo_url"}


def init_db(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            edition    TEXT,
            source_url TEXT,
            website    TEXT,
            start_date TEXT,
            end_date   TEXT,
            timezone   TEXT NOT NULL DEFAULT 'Europe/Berlin',
            address    TEXT,
            latitude   REAL,
            longitude  REAL
        );
        CREATE TABLE IF NOT EXISTS artists (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            photo_url  TEXT,
            photo_file TEXT,
            bio        TEXT
        );
        CREATE TABLE IF NOT EXISTS artist_links (
            artist_id      TEXT NOT NULL REFERENCES artists(id),
            platform       TEXT NOT NULL,
            url            TEXT NOT NULL,
            follower_count INTEGER,
            position       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (artist_id, platform)
        );
        CREATE INDEX IF NOT EXISTS idx_artist_links_artist ON artist_links(artist_id);
        CREATE TABLE IF NOT EXISTS locations (
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
            location_id TEXT NOT NULL REFERENCES locations(id),
            date        TEXT NOT NULL,
            note        TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, date, position)
        );
        CREATE TABLE IF NOT EXISTS location_details (
            location_id TEXT NOT NULL REFERENCES locations(id),
            label       TEXT NOT NULL,
            value       TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (location_id, label)
        );
        CREATE TABLE IF NOT EXISTS schedule (
            artist_id   TEXT NOT NULL REFERENCES artists(id),
            event_id    TEXT NOT NULL REFERENCES events(id),
            location_id TEXT REFERENCES locations(id),
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            date        TEXT NOT NULL,
            period      TEXT,
            set_type    TEXT,
            PRIMARY KEY (artist_id, event_id, start_time)
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_event ON schedule(event_id, date, period);
        CREATE TABLE IF NOT EXISTS artist_sets (
            id           TEXT PRIMARY KEY,
            artist_id    TEXT NOT NULL REFERENCES artists(id),
            platform     TEXT NOT NULL DEFAULT 'youtube',
            url          TEXT NOT NULL,
            title        TEXT NOT NULL,
            view_count   INTEGER NOT NULL DEFAULT 0,
            duration_min INTEGER NOT NULL DEFAULT 0,
            upload_date  INTEGER,
            position     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_artist_sets_artist ON artist_sets(artist_id);
    """)
    db.commit()


PLATFORM_POSITIONS = {
    "instagram": 0,
    "soundcloud": 1,
    "spotify": 2,
    "youtube": 3,
    "ra": 4,
    "linktree": 5,
}


def ensure_event(db: sqlite3.Connection, event_id: str, name: str, **kwargs) -> None:
    db.execute(
        "INSERT INTO events (id, name, edition, source_url, website, start_date, end_date, "
        "timezone, address, latitude, longitude) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, edition=excluded.edition, "
        "source_url=excluded.source_url, "
        "website=excluded.website, start_date=excluded.start_date, end_date=excluded.end_date, "
        "timezone=excluded.timezone, address=excluded.address, "
        "latitude=excluded.latitude, longitude=excluded.longitude",
        (
            event_id,
            name,
            kwargs.get("edition"),
            kwargs.get("source_url"),
            kwargs.get("website"),
            kwargs.get("start_date"),
            kwargs.get("end_date"),
            kwargs.get("timezone", "Europe/Berlin"),
            kwargs.get("address"),
            kwargs.get("latitude"),
            kwargs.get("longitude"),
        ),
    )
    db.commit()


def upsert_lineup(db: sqlite3.Connection, parsed: dict, event_id: str) -> None:
    section_lookup = {
        sec["key"]: (sec["date"], sec["period"]) for sec in parsed["sections"]
    }

    all_dates = sorted({date for date, _ in section_lookup.values()})
    for loc_id, loc in parsed["locations"].items():
        db.execute(
            "INSERT INTO locations (id, event_id, name) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (loc_id, event_id, loc["name"]),
        )
        loc_desc = loc.get("description")
        if loc_desc:
            for date in all_dates:
                db.execute(
                    "INSERT OR IGNORE INTO location_notes (location_id, date, note, position) "
                    "VALUES (?, ?, ?, 0)",
                    (loc_id, date, loc_desc),
                )
    if parsed["locations"]:
        current_locs = list(parsed["locations"].keys())
        placeholders = ",".join("?" * len(current_locs))
        db.execute(
            f"DELETE FROM locations WHERE event_id = ? AND id NOT IN ({placeholders})",
            [event_id, *current_locs],
        )

    for oid, d in parsed["artists"].items():
        db.execute(
            "INSERT INTO artists (id, name, photo_url) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "photo_url=excluded.photo_url, "
            "photo_file = CASE WHEN photo_url IS NOT excluded.photo_url THEN NULL ELSE photo_file END",
            (oid, d["name"], d.get("photo")),
        )
        for platform in ("instagram", "soundcloud", "spotify", "youtube"):
            url = d.get(platform)
            if url:
                pos = PLATFORM_POSITIONS.get(platform, 99)
                db.execute(
                    "INSERT INTO artist_links (artist_id, platform, url, position) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(artist_id, platform) DO UPDATE SET url=excluded.url, "
                    "follower_count = CASE WHEN url IS NOT excluded.url THEN NULL ELSE follower_count END",
                    (oid, platform, url, pos),
                )

    if parsed["assignments"]:
        db.execute("DELETE FROM schedule WHERE event_id = ?", (event_id,))
        for assignment in parsed["assignments"]:
            ts_key = assignment["timestamp_key"]
            date, period = section_lookup.get(ts_key, ("", None))
            db.execute(
                "INSERT OR IGNORE INTO schedule "
                "(artist_id, event_id, location_id, start_time, end_time, date, period) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment["overlay_id"],
                    event_id,
                    assignment.get("location_id"),
                    ts_key,
                    "",
                    date,
                    period,
                ),
            )
    elif parsed["artists"]:
        print(
            "WARNING: Scrape returned artists but no assignments - keeping existing lineup data"
        )
    db.commit()


def apply_overrides(
    db: sqlite3.Connection, overrides_path: Path, event_id: str | None = None
) -> None:
    if not overrides_path.exists():
        return
    import tomllib

    with open(overrides_path, "rb") as f:
        overrides = tomllib.load(f)
    if not overrides:
        return

    NON_ARTIST_SECTIONS = {
        "floor_curators",
        "youtube_names",
        "youtube_videos",
        "youtube_videos_add",
    }

    applied = 0
    for artist_name, fields in overrides.items():
        if artist_name in NON_ARTIST_SECTIONS:
            continue
        row = db.execute(
            "SELECT id FROM artists WHERE name = ?", (artist_name,)
        ).fetchone()
        if not row:
            print(f"  Override skipped: artist '{artist_name}' not found in DB")
            continue
        aid = row["id"]
        for field, value in fields.items():
            col = OVERRIDE_ALIASES.get(field, field)

            if col in OVERRIDE_ARTIST_FIELDS:
                if value is False:
                    value = ""
                dependent = {"photo_url": "photo_file"}.get(col)
                current = db.execute(
                    f"SELECT {col} FROM artists WHERE id = ?", (aid,)
                ).fetchone()[col]
                if current != value:
                    if dependent:
                        dep_val = None if value else None
                        db.execute(
                            f"UPDATE artists SET {col} = ?, {dependent} = ? WHERE id = ?",
                            (value, dep_val, aid),
                        )
                    else:
                        db.execute(
                            f"UPDATE artists SET {col} = ? WHERE id = ?", (value, aid)
                        )
                    applied += 1

            elif col in PLATFORM_LINK_FIELDS:
                if value is False:
                    value = ""
                if value:
                    pos = PLATFORM_POSITIONS.get(col, 99)
                    db.execute(
                        "INSERT INTO artist_links (artist_id, platform, url, position) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(artist_id, platform) DO UPDATE SET "
                        "url=excluded.url, follower_count=NULL",
                        (aid, col, value, pos),
                    )
                else:
                    db.execute(
                        "UPDATE artist_links SET url = '', follower_count = 0 "
                        "WHERE artist_id = ? AND platform = ?",
                        (aid, col),
                    )
                applied += 1
            else:
                print(f"  Override skipped: unknown field '{field}' for {artist_name}")

    if event_id:
        curators = overrides.get("floor_curators", {})
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
        if curators:
            applied += len(curators)

    if applied:
        db.commit()
        print(f"Applied {applied} override(s) from overrides.toml")


def load_floor_curators(db: sqlite3.Connection, event_id: str) -> dict[str, str]:
    return {
        f"{row['date']}.{row['location_id']}": row["note"]
        for row in db.execute(
            "SELECT ln.location_id, ln.date, ln.note FROM location_notes ln "
            "JOIN locations l ON l.id = ln.location_id "
            "WHERE l.event_id = ? "
            "ORDER BY ln.position",
            (event_id,),
        )
    }


def load_location_colors(db: sqlite3.Connection, event_id: str) -> dict[str, str]:
    return {
        row["id"]: row["color"]
        for row in db.execute(
            "SELECT id, color FROM locations WHERE event_id = ? AND color IS NOT NULL",
            (event_id,),
        )
    }


def get_event(db: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_missing_links(db: sqlite3.Connection, platform: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT artist_id, url FROM artist_links "
        "WHERE platform = ? AND url IS NOT NULL AND url != '' AND follower_count IS NULL",
        (platform,),
    ).fetchall()


def get_artists_without_platform(
    db: sqlite3.Connection, platform: str
) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT a.* FROM artists a "
        "WHERE NOT EXISTS (SELECT 1 FROM artist_links al WHERE al.artist_id = a.id AND al.platform = ?)",
        (platform,),
    ).fetchall()


def upsert_artist_link(
    db: sqlite3.Connection,
    artist_id: str,
    platform: str,
    url: str,
    follower_count: int | None = None,
) -> None:
    pos = PLATFORM_POSITIONS.get(platform, 99)
    db.execute(
        "INSERT INTO artist_links (artist_id, platform, url, follower_count, position) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(artist_id, platform) DO UPDATE SET "
        "url=excluded.url, follower_count=excluded.follower_count",
        (artist_id, platform, url, follower_count, pos),
    )
    db.commit()


def update_link_follower_count(
    db: sqlite3.Connection, artist_id: str, platform: str, count: int
) -> None:
    db.execute(
        "UPDATE artist_links SET follower_count = ? WHERE artist_id = ? AND platform = ?",
        (count, artist_id, platform),
    )
    db.commit()


def get_artists_missing_photos(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, photo_url FROM artists "
        "WHERE photo_url IS NOT NULL AND photo_file IS NULL"
    ).fetchall()


def save_photo_file(db: sqlite3.Connection, artist_id: str, filename: str) -> None:
    db.execute(
        "UPDATE artists SET photo_file = ? WHERE id = ?",
        (filename, artist_id),
    )
    db.commit()


def load_sections_from_db(db: sqlite3.Connection, event_id: str) -> list[dict]:
    return [
        {
            "key": f"{row['date']}:{row['period'] or 'all'}",
            "date": row["date"],
            "period": row["period"],
        }
        for row in db.execute(
            "SELECT DISTINCT date, period FROM schedule "
            "WHERE event_id = ? "
            "ORDER BY date, CASE period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END",
            (event_id,),
        )
    ]


def load_locations_from_db(db: sqlite3.Connection, event_id: str) -> dict[str, dict]:
    return {
        row["id"]: {
            "name": row["name"],
            "color": row["color"],
            "about": row["about"],
        }
        for row in db.execute(
            "SELECT id, name, color, about FROM locations WHERE event_id = ?",
            (event_id,),
        )
    }


def _load_artist_all_slots(
    db: sqlite3.Connection, event_id: str
) -> dict[str, list[dict]]:
    slots: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT s.artist_id, s.date, s.period, s.location_id, l.name AS location_name, "
        "s.start_time, s.end_time "
        "FROM schedule s "
        "LEFT JOIN locations l ON l.id = s.location_id "
        "WHERE s.event_id = ? "
        "ORDER BY s.date, CASE s.period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END, "
        "s.start_time",
        (event_id,),
    ):
        slots.setdefault(row["artist_id"], []).append(
            {
                "date": row["date"],
                "period": row["period"],
                "location_id": row["location_id"],
                "location_name": row["location_name"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
            }
        )
    return slots


def _load_all_artist_links(db: sqlite3.Connection) -> dict[str, list[dict]]:
    links: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT artist_id, platform, url, follower_count "
        "FROM artist_links WHERE url IS NOT NULL AND url != '' "
        "ORDER BY artist_id, position"
    ):
        links.setdefault(row["artist_id"], []).append(
            {
                "platform": row["platform"],
                "url": row["url"],
                "follower_count": row["follower_count"],
            }
        )
    return links


def load_assignments_from_db(
    db: sqlite3.Connection, event_id: str
) -> dict[str, list[dict]]:
    all_slots = _load_artist_all_slots(db, event_id)
    all_links = _load_all_artist_links(db)
    assignments: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT a.id, a.name, a.photo_file, a.bio, "
        "s.date, s.period, s.location_id, s.start_time, s.end_time, s.set_type "
        "FROM schedule s "
        "JOIN artists a ON a.id = s.artist_id "
        "WHERE s.event_id = ? "
        "ORDER BY s.date, CASE s.period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END, "
        "s.start_time, a.name",
        (event_id,),
    ):
        section_key = f"{row['date']}:{row['period'] or 'all'}"
        assignments.setdefault(section_key, []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "photo_file": row["photo_file"],
                "bio": row["bio"],
                "links": all_links.get(row["id"], []),
                "location_id": row["location_id"],
                "all_slots": all_slots.get(row["id"], []),
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "set_type": row["set_type"],
            }
        )
    return assignments


def load_all_sets(db: sqlite3.Connection) -> dict[str, list[dict]]:
    sets: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT id, artist_id, platform, url, title, view_count, duration_min, "
        "upload_date, position "
        "FROM artist_sets ORDER BY artist_id, position"
    ):
        sets.setdefault(row["artist_id"], []).append(
            {
                "id": row["id"],
                "platform": row["platform"],
                "url": row["url"],
                "title": row["title"],
                "views": row["view_count"],
                "duration": row["duration_min"],
                "date": row["upload_date"],
            }
        )
    return sets


def update_artist_field(
    db: sqlite3.Connection, artist_id: str, field: str, value
) -> None:
    valid = {"photo_url", "photo_file", "bio"}
    if field not in valid:
        raise ValueError(f"Invalid field: {field}")
    db.execute(f"UPDATE artists SET {field} = ? WHERE id = ?", (value, artist_id))
    db.commit()


def get_artist(db: sqlite3.Connection, artist_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
