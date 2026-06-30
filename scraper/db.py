from __future__ import annotations

import sqlite3
from pathlib import Path


OVERRIDE_FIELDS = {
    "instagram",
    "soundcloud",
    "spotify",
    "linktree",
    "youtube",
    "photo",
    "ra",
}


def init_db(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS artists (
            overlay_id        TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            instagram         TEXT,
            soundcloud        TEXT,
            spotify           TEXT,
            linktree          TEXT,
            youtube           TEXT,
            photo             TEXT,
            ig_followers      INTEGER,
            sc_followers      INTEGER,
            spotify_listeners INTEGER,
            photo_local       TEXT
        );
        CREATE TABLE IF NOT EXISTS sections (
            timestamp_key TEXT PRIMARY KEY,
            date          TEXT NOT NULL,
            period        TEXT NOT NULL,
            position      INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS locations (
            location_id   TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            description   TEXT
        );
        CREATE TABLE IF NOT EXISTS artist_sections (
            overlay_id    TEXT NOT NULL,
            timestamp_key TEXT NOT NULL,
            location_id   TEXT,
            PRIMARY KEY (overlay_id, timestamp_key),
            FOREIGN KEY (overlay_id) REFERENCES artists(overlay_id),
            FOREIGN KEY (timestamp_key) REFERENCES sections(timestamp_key),
            FOREIGN KEY (location_id) REFERENCES locations(location_id)
        );
    """)
    artist_cols = {row[1] for row in db.execute("PRAGMA table_info(artists)")}
    for col, typ in [
        ("photo_local", "TEXT"),
        ("linktree", "TEXT"),
        ("youtube", "TEXT"),
        ("spotify_listeners", "INTEGER"),
        ("ra", "TEXT"),
        ("ra_followers", "INTEGER"),
        ("ra_bio", "TEXT"),
    ]:
        if col not in artist_cols:
            db.execute(f"ALTER TABLE artists ADD COLUMN {col} {typ}")
    as_cols = {row[1] for row in db.execute("PRAGMA table_info(artist_sections)")}
    if "location_id" not in as_cols:
        db.execute("ALTER TABLE artist_sections ADD COLUMN location_id TEXT")
    if "start_time" not in as_cols:
        db.execute("ALTER TABLE artist_sections ADD COLUMN start_time TEXT")
    if "end_time" not in as_cols:
        db.execute("ALTER TABLE artist_sections ADD COLUMN end_time TEXT")
    sec_cols = {row[1] for row in db.execute("PRAGMA table_info(sections)")}
    if "label" in sec_cols and "date" not in sec_cols:
        db.execute("DROP TABLE sections")
        db.execute("""
            CREATE TABLE sections (
                timestamp_key TEXT PRIMARY KEY,
                date          TEXT NOT NULL,
                period        TEXT NOT NULL,
                position      INTEGER NOT NULL
            )
        """)
    db.commit()


def upsert_lineup(db: sqlite3.Connection, parsed: dict) -> None:
    for pos, sec in enumerate(parsed["sections"]):
        db.execute(
            "INSERT INTO sections (timestamp_key, date, period, position) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(timestamp_key) DO UPDATE SET date=excluded.date, period=excluded.period, position=excluded.position",
            (sec["key"], sec["date"], sec["period"], pos),
        )
    if parsed["sections"]:
        current_keys = [sec["key"] for sec in parsed["sections"]]
        db.execute(
            f"DELETE FROM sections WHERE timestamp_key NOT IN ({','.join('?' * len(current_keys))})",
            current_keys,
        )
    for loc_id, loc in parsed["locations"].items():
        db.execute(
            "INSERT INTO locations (location_id, name, description) VALUES (?, ?, ?) "
            "ON CONFLICT(location_id) DO UPDATE SET name=excluded.name, description=excluded.description",
            (loc_id, loc["name"], loc.get("description")),
        )
    if parsed["locations"]:
        current_locs = list(parsed["locations"].keys())
        db.execute(
            f"DELETE FROM locations WHERE location_id NOT IN ({','.join('?' * len(current_locs))})",
            current_locs,
        )
    for oid, d in parsed["artists"].items():
        db.execute(
            "INSERT INTO artists (overlay_id, name, instagram, soundcloud, spotify, youtube, photo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(overlay_id) DO UPDATE SET "
            "name=excluded.name, instagram=excluded.instagram, soundcloud=excluded.soundcloud, "
            "spotify=excluded.spotify, youtube=excluded.youtube, photo=excluded.photo, "
            "ig_followers = CASE WHEN instagram IS NOT excluded.instagram THEN NULL ELSE ig_followers END, "
            "sc_followers = CASE WHEN soundcloud IS NOT excluded.soundcloud THEN NULL ELSE sc_followers END, "
            "spotify_listeners = CASE WHEN spotify IS NOT excluded.spotify THEN NULL ELSE spotify_listeners END, "
            "photo_local = CASE WHEN photo IS NOT excluded.photo THEN NULL ELSE photo_local END",
            (
                oid,
                d["name"],
                d.get("instagram"),
                d.get("soundcloud"),
                d.get("spotify"),
                d.get("youtube"),
                d.get("photo"),
            ),
        )
    if parsed["assignments"]:
        db.execute("DELETE FROM artist_sections")
        for assignment in parsed["assignments"]:
            db.execute(
                "INSERT OR IGNORE INTO artist_sections (overlay_id, timestamp_key, location_id) VALUES (?, ?, ?)",
                (
                    assignment["overlay_id"],
                    assignment["timestamp_key"],
                    assignment.get("location_id"),
                ),
            )
    elif parsed["artists"]:
        print(
            "WARNING: Scrape returned artists but no assignments - keeping existing lineup data"
        )
    db.commit()


def apply_overrides(db: sqlite3.Connection, overrides_path: Path) -> None:
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
            "SELECT overlay_id FROM artists WHERE name = ?", (artist_name,)
        ).fetchone()
        if not row:
            print(f"  Override skipped: artist '{artist_name}' not found in DB")
            continue
        oid = row[0]
        for field, value in fields.items():
            if field not in OVERRIDE_FIELDS:
                print(f"  Override skipped: unknown field '{field}' for {artist_name}")
                continue
            # false means "no profile" — clear the URL and mark count as fetched
            if value is False:
                value = ""
            dependent_col = {
                "instagram": "ig_followers",
                "soundcloud": "sc_followers",
                "spotify": "spotify_listeners",
                "photo": "photo_local",
                "ra": "ra_followers",
            }.get(field)
            current = db.execute(
                f"SELECT {field} FROM artists WHERE overlay_id = ?", (oid,)
            ).fetchone()[0]
            if current != value:
                if field == "ra":
                    count_val = 0 if value == "" else None
                    db.execute(
                        "UPDATE artists SET ra = ?, ra_followers = ?, ra_bio = ? WHERE overlay_id = ?",
                        (value, count_val, None if count_val is None else "", oid),
                    )
                elif dependent_col:
                    count_val = 0 if value == "" else None
                    db.execute(
                        f"UPDATE artists SET {field} = ?, {dependent_col} = ? WHERE overlay_id = ?",
                        (value, count_val, oid),
                    )
                else:
                    db.execute(
                        f"UPDATE artists SET {field} = ? WHERE overlay_id = ?",
                        (value, oid),
                    )
                applied += 1
    if applied:
        db.commit()
        print(f"Applied {applied} override(s) from overrides.toml")


def load_floor_curators(overrides_path: Path) -> dict[str, str]:
    if not overrides_path.exists():
        return {}
    import tomllib

    with open(overrides_path, "rb") as f:
        overrides = tomllib.load(f)
    return dict(overrides.get("floor_curators", {}))


def get_missing(
    db: sqlite3.Connection, url_col: str, count_col: str
) -> list[tuple[str, str]]:
    return db.execute(
        f"SELECT overlay_id, {url_col} FROM artists WHERE {url_col} IS NOT NULL AND {url_col} != '' AND {count_col} IS NULL"
    ).fetchall()


def get_artists_without_ra(db: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(
        "SELECT * FROM artists WHERE (ra IS NULL OR ra = '') AND ra_followers IS NULL"
    ).fetchall()


def get_artists_missing_photos(db: sqlite3.Connection) -> list[tuple[str, str]]:
    return db.execute(
        "SELECT overlay_id, photo FROM artists WHERE photo IS NOT NULL AND photo_local IS NULL"
    ).fetchall()


def save_photo_local(db: sqlite3.Connection, overlay_id: str, filename: str) -> None:
    db.execute(
        "UPDATE artists SET photo_local = ? WHERE overlay_id = ?",
        (filename, overlay_id),
    )
    db.commit()


def load_sections_from_db(db: sqlite3.Connection) -> list[dict]:
    return [
        {"key": row[0], "date": row[1], "period": row[2]}
        for row in db.execute(
            "SELECT timestamp_key, date, period FROM sections ORDER BY position"
        )
    ]


def load_locations_from_db(db: sqlite3.Connection) -> dict[str, dict]:
    return {
        row[0]: {"name": row[1], "description": row[2]}
        for row in db.execute("SELECT location_id, name, description FROM locations")
    }


def _load_artist_all_slots(db: sqlite3.Connection) -> dict[str, list[dict]]:
    slots: dict[str, list[dict]] = {}
    for oid, date, period, loc_id, loc_name, start_time, end_time in db.execute(
        "SELECT sa.overlay_id, s.date, s.period, sa.location_id, l.name, "
        "sa.start_time, sa.end_time "
        "FROM artist_sections sa "
        "JOIN sections s ON s.timestamp_key = sa.timestamp_key "
        "LEFT JOIN locations l ON l.location_id = sa.location_id "
        "ORDER BY s.position"
    ):
        slots.setdefault(oid, []).append(
            {
                "date": date,
                "period": period,
                "location_id": loc_id,
                "location_name": loc_name,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
    return slots


def load_assignments_from_db(db: sqlite3.Connection) -> dict[str, list[dict]]:
    all_slots = _load_artist_all_slots(db)
    assignments: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT a.name, a.instagram, a.soundcloud, a.spotify, a.linktree, a.youtube, "
        "a.photo_local, a.ig_followers, a.sc_followers, a.spotify_listeners, "
        "s.timestamp_key, sa.location_id, a.overlay_id, sa.start_time, sa.end_time, "
        "a.ra, a.ra_followers, a.ra_bio "
        "FROM artist_sections sa "
        "JOIN artists a ON a.overlay_id = sa.overlay_id "
        "JOIN sections s ON s.timestamp_key = sa.timestamp_key "
        "ORDER BY s.position, sa.start_time, a.name"
    ):
        assignments.setdefault(row[10], []).append(
            {
                "name": row[0],
                "instagram": row[1],
                "soundcloud": row[2],
                "spotify": row[3],
                "linktree": row[4],
                "youtube": row[5],
                "photo_local": row[6],
                "ig_followers": row[7],
                "sc_followers": row[8],
                "spotify_listeners": row[9],
                "location_id": row[11],
                "overlay_id": row[12],
                "all_slots": all_slots.get(row[12], []),
                "start_time": row[13],
                "end_time": row[14],
                "ra": row[15],
                "ra_followers": row[16],
                "ra_bio": row[17],
            }
        )
    return assignments


_VALID_FIELDS = {
    "ig_followers",
    "sc_followers",
    "spotify_listeners",
    "ra_followers",
    "ra_bio",
    "instagram",
    "soundcloud",
    "spotify",
    "linktree",
    "youtube",
    "ra",
    "photo_local",
}


def update_artist_field(db: sqlite3.Connection, oid: str, field: str, value) -> None:
    if field not in _VALID_FIELDS:
        raise ValueError(f"Invalid field: {field}")
    db.execute(f"UPDATE artists SET {field} = ? WHERE overlay_id = ?", (value, oid))
    db.commit()


def get_artist(db: sqlite3.Connection, oid: str) -> sqlite3.Row | tuple:
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute("SELECT * FROM artists WHERE overlay_id = ?", (oid,)).fetchone()
