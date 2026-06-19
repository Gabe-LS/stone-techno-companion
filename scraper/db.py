from __future__ import annotations

import sqlite3
from pathlib import Path


OVERRIDE_FIELDS = {"instagram", "soundcloud", "spotify", "linktree", "youtube", "photo"}


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
    ]:
        if col not in artist_cols:
            db.execute(f"ALTER TABLE artists ADD COLUMN {col} {typ}")
    as_cols = {row[1] for row in db.execute("PRAGMA table_info(artist_sections)")}
    if "location_id" not in as_cols:
        db.execute("ALTER TABLE artist_sections ADD COLUMN location_id TEXT")
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
    for loc_id, loc in parsed["locations"].items():
        db.execute(
            "INSERT INTO locations (location_id, name, description) VALUES (?, ?, ?) "
            "ON CONFLICT(location_id) DO UPDATE SET name=excluded.name, description=excluded.description",
            (loc_id, loc["name"], loc.get("description")),
        )
    for oid, d in parsed["artists"].items():
        db.execute(
            "INSERT INTO artists (overlay_id, name, instagram, soundcloud, spotify, youtube, photo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(overlay_id) DO UPDATE SET "
            "name=excluded.name, instagram=excluded.instagram, soundcloud=excluded.soundcloud, "
            "spotify=excluded.spotify, youtube=excluded.youtube, photo=excluded.photo",
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
    db.commit()


def apply_overrides(db: sqlite3.Connection, overrides_path: Path) -> None:
    if not overrides_path.exists():
        return
    import tomllib

    with open(overrides_path, "rb") as f:
        overrides = tomllib.load(f)
    if not overrides:
        return

    applied = 0
    for artist_name, fields in overrides.items():
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
            current = db.execute(
                f"SELECT {field} FROM artists WHERE overlay_id = ?", (oid,)
            ).fetchone()[0]
            if current != value:
                dependent_col = {
                    "instagram": "ig_followers",
                    "soundcloud": "sc_followers",
                    "spotify": "spotify_listeners",
                    "photo": "photo_local",
                }.get(field)
                if dependent_col:
                    db.execute(
                        f"UPDATE artists SET {field} = ?, {dependent_col} = NULL WHERE overlay_id = ?",
                        (value, oid),
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


def get_missing(
    db: sqlite3.Connection, url_col: str, count_col: str
) -> list[tuple[str, str]]:
    return db.execute(
        f"SELECT overlay_id, {url_col} FROM artists WHERE {url_col} IS NOT NULL AND {count_col} IS NULL"
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
    for oid, date, period, loc_id, loc_name in db.execute(
        "SELECT sa.overlay_id, s.date, s.period, sa.location_id, l.name "
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
            }
        )
    return slots


def load_assignments_from_db(db: sqlite3.Connection) -> dict[str, list[dict]]:
    all_slots = _load_artist_all_slots(db)
    assignments: dict[str, list[dict]] = {}
    for row in db.execute(
        "SELECT a.name, a.instagram, a.soundcloud, a.spotify, a.linktree, a.youtube, "
        "a.photo_local, a.ig_followers, a.sc_followers, a.spotify_listeners, "
        "s.timestamp_key, sa.location_id, a.overlay_id "
        "FROM artist_sections sa "
        "JOIN artists a ON a.overlay_id = sa.overlay_id "
        "JOIN sections s ON s.timestamp_key = sa.timestamp_key "
        "ORDER BY s.position, a.name"
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
            }
        )
    return assignments


def update_artist_field(db: sqlite3.Connection, oid: str, field: str, value) -> None:
    db.execute(f"UPDATE artists SET {field} = ? WHERE overlay_id = ?", (value, oid))
    db.commit()


def get_artist(db: sqlite3.Connection, oid: str) -> sqlite3.Row | tuple:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM artists WHERE overlay_id = ?", (oid,)).fetchone()
    db.row_factory = None
    return row
