from __future__ import annotations

import json
import sqlite3
import uuid


def generate_timetable_json(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT a.overlay_id, a.name, s.date, s.period, sa.location_id, l.name, "
        "sa.start_time, sa.end_time "
        "FROM artist_sections sa "
        "JOIN artists a ON a.overlay_id = sa.overlay_id "
        "JOIN sections s ON s.timestamp_key = sa.timestamp_key "
        "LEFT JOIN locations l ON l.location_id = sa.location_id "
        "WHERE sa.start_time IS NOT NULL AND sa.end_time IS NOT NULL "
        "ORDER BY s.position, sa.start_time, a.name"
    ).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for oid, name, date, period, loc_id, loc_name, start_time, end_time in rows:
        key = (date, period, loc_id, start_time, end_time)
        groups.setdefault(key, []).append(
            {
                "overlay_id": oid,
                "name": name,
                "loc_name": loc_name or loc_id or "unknown",
            }
        )

    slots = {}
    for (date, period, fid, start_time, end_time), artists in groups.items():
        card_key = ":".join([a["overlay_id"] for a in artists] + [date, period, fid])
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
        {"timezone": "Europe/Berlin", "slots": slots}, ensure_ascii=False, indent=2
    )
