from __future__ import annotations

import json
import sqlite3
import uuid


def generate_timetable_json(db: sqlite3.Connection, event_id: str) -> str:
    event = db.execute(
        "SELECT timezone FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    timezone = event["timezone"] if event else "Europe/Berlin"

    rows = db.execute(
        "SELECT a.id, a.name, s.date, s.period, s.stage_id, st.name AS stage_name, "
        "s.start_time, s.end_time "
        "FROM schedule s "
        "JOIN artists a ON a.id = s.artist_id "
        "LEFT JOIN stages st ON st.id = s.stage_id "
        "WHERE s.event_id = ? AND s.start_time IS NOT NULL AND s.end_time IS NOT NULL "
        "ORDER BY s.date, CASE s.period WHEN 'day' THEN 0 WHEN 'night' THEN 1 ELSE 2 END, "
        "s.start_time, a.name",
        (event_id,),
    ).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (
            row["date"],
            row["period"],
            row["stage_id"],
            row["start_time"],
            row["end_time"],
        )
        groups.setdefault(key, []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "loc_name": row["stage_name"] or row["stage_id"] or "unknown",
            }
        )

    slots = {}
    for (date, period, fid, start_time, end_time), artists in groups.items():
        card_key = ":".join(
            [a["id"] for a in artists] + [date, period or "", fid or ""]
        )
        slot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))
        s_hhmm = start_time.split("T")[1] if "T" in start_time else start_time
        e_hhmm = end_time.split("T")[1] if "T" in end_time else end_time
        slots[slot_id] = {
            "artists": [a["name"] for a in artists],
            "floor": artists[0]["loc_name"],
            "start": start_time,
            "end": end_time,
            "start_hhmm": s_hhmm,
            "end_hhmm": e_hhmm,
        }

    return json.dumps(
        {"timezone": timezone, "slots": slots}, ensure_ascii=False, indent=2
    )
