from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict


def slot_uuid(
    artist_ids: list[str],
    date: str,
    period: str | None,
    floor_id: str | None,
    start_time: str,
    end_time: str,
    group_times: list[tuple[str, str]],
) -> str:
    """The single source of truth for a timetable slot's stable id.

    Historically the id was uuid5(artists:date:period:floor) with NO time
    component, so an artist playing two sets on the same floor within one
    date+period collapsed to one id (the second set vanished from the slot map
    and never fired a push).

    This preserves the historical id for EVERY existing slot: the id is
    unchanged whenever a (artists, date, period, floor) group has a single slot,
    and for a genuine collision the earliest-starting slot keeps the base id.
    Only the ADDITIONAL colliding slot(s) get a disambiguated id folding in
    start/end time. Because a collision was previously an invisible merge, no
    saved schedule could ever have pointed at the extra slot -- so no existing
    id changes and no user's saved likes/schedule is reset.

    `group_times` is every (start, end) sharing this (artists, date, period,
    floor). render.py and this module both call this with identically-derived
    inputs, so the id is defined in exactly one place and cannot drift.
    """
    base_key = ":".join(list(artist_ids) + [date, period or "", floor_id or ""])
    base_id = str(uuid.uuid5(uuid.NAMESPACE_URL, base_key))
    if len(group_times) <= 1 or (start_time, end_time) == min(group_times):
        return base_id
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{base_key}:{start_time}:{end_time}"))


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

    # Map each (artists, date, period, floor) group to all its (start, end)
    # slots, so slot_uuid can keep the historical id for the canonical slot and
    # only disambiguate genuine same-artist/floor/period collisions.
    group_times: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for (date, period, fid, start_time, end_time), artists in groups.items():
        gkey = (tuple(a["id"] for a in artists), date, period, fid)
        group_times[gkey].append((start_time, end_time))

    slots = {}
    for (date, period, fid, start_time, end_time), artists in groups.items():
        aids = [a["id"] for a in artists]
        gkey = (tuple(aids), date, period, fid)
        slot_id = slot_uuid(
            aids, date, period, fid, start_time, end_time, group_times[gkey]
        )
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
