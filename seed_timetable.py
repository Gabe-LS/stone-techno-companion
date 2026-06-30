#!/usr/bin/env python3
"""Seed fake timetable data: 5 floors with realistic time slots for all artists."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "lineup.db"

DEFAULT_EVENT_ID = "stone-techno-2026"

DAY_FLOORS = [
    ("eisbahn", "Eisbahn", None),
    ("koksofenbatterie", "Koksofenbatterie", None),
    ("listening-floor", "Listening Floor", None),
    ("salzlager", "Salzlager", None),
    ("werksschwimmbad", "Werksschwimmbad", None),
]

NIGHT_FLOORS = [
    ("grand-hall", "Grand Hall", "Hosted by FOLD London"),
    ("mischanlage", "Mischanlage", "Hosted by Delirium & Gitter"),
]

ALL_FLOORS = DAY_FLOORS + NIGHT_FLOORS

DAY_START_HOUR = 12
DAY_END_HOUR = 24
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 31  # 07:00 next day

SET_LENGTHS = [60, 75, 90, 105, 120]


def seed(db: sqlite3.Connection, event_id: str = DEFAULT_EVENT_ID) -> None:
    for loc_id, name, desc in ALL_FLOORS:
        db.execute(
            "INSERT INTO stages (id, name) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (loc_id, name),
        )
        db.execute(
            "INSERT INTO event_stages (event_id, stage_id) VALUES (?, ?) "
            "ON CONFLICT(event_id, stage_id) DO NOTHING",
            (event_id, loc_id),
        )

    date_periods = db.execute(
        "SELECT DISTINCT date, period FROM schedule "
        "WHERE event_id = ? "
        "ORDER BY date, CASE period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END",
        (event_id,),
    ).fetchall()

    for row in date_periods:
        date, period = row["date"], row["period"]
        artists = db.execute(
            "SELECT artist_id FROM schedule WHERE event_id = ? AND date = ? AND period IS ?",
            (event_id, date, period),
        ).fetchall()
        if not artists:
            continue

        artist_ids = [r["artist_id"] for r in artists]
        random.shuffle(artist_ids)

        is_night = period == "night"
        if is_night:
            floor_ids = [f[0] for f in NIGHT_FLOORS]
            start_hour = NIGHT_START_HOUR
            end_hour = NIGHT_END_HOUR
        else:
            floor_ids = [f[0] for f in DAY_FLOORS]
            start_hour = DAY_START_HOUR
            end_hour = DAY_END_HOUR

        per_floor = len(artist_ids) // len(floor_ids)
        remainder = len(artist_ids) % len(floor_ids)
        chunks: list[list[str]] = []
        idx = 0
        for i in range(len(floor_ids)):
            n = per_floor + (1 if i < remainder else 0)
            chunks.append(artist_ids[idx : idx + n])
            idx += n

        db.execute(
            "DELETE FROM schedule WHERE event_id = ? AND date = ? AND period IS ?",
            (event_id, date, period),
        )

        for floor_id, chunk in zip(floor_ids, chunks):
            if not chunk:
                continue
            total_minutes = (end_hour - start_hour) * 60

            slots: list[list[str]] = []
            i = 0
            while i < len(chunk):
                if i + 1 < len(chunk) and random.random() < 0.15:
                    slots.append([chunk[i], chunk[i + 1]])
                    i += 2
                else:
                    slots.append([chunk[i]])
                    i += 1

            slot_minutes = total_minutes // len(slots)
            slot_minutes = max(60, min(slot_minutes, 120))

            cursor = start_hour * 60
            for group in slots:
                length = (
                    random.choice([m for m in SET_LENGTHS if m <= slot_minutes + 15])
                    if slot_minutes >= 60
                    else slot_minutes
                )
                s_h, s_m = divmod(cursor, 60)
                e_h, e_m = divmod(cursor + length, 60)
                start_time = f"{date}T{s_h % 24:02d}:{s_m:02d}"
                end_time = f"{date}T{e_h % 24:02d}:{e_m:02d}"

                for aid in group:
                    db.execute(
                        "INSERT INTO schedule "
                        "(artist_id, event_id, stage_id, start_time, end_time, date, period) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (aid, event_id, floor_id, start_time, end_time, date, period),
                    )
                cursor += length

    db.commit()
    print("Seeded fake timetable data.")


if __name__ == "__main__":
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    try:
        from scraper.db import init_db

        init_db(db)
        seed(db)
    finally:
        db.close()
