from __future__ import annotations

import json as _json
import shutil
import sqlite3
import uuid
from pathlib import Path

from .db import get_event
from .render import _parse_time, _render_markdown, _slot_group_times, _strip_booking
from .timetable_json import slot_uuid

__doc__ = """Emits the static JSON files served by the read-only lineup data
API (docs/api/lineup-data-api.md, hybrid serving strategy: files generated
here, fronted by explicit services/companion/api.py routes with
Cache-Control: no-cache).

This module is purely additive: it reads the same already-loaded structures
`stone_techno_companion.py` builds for `render_output_html`/
`generate_timetable_json` (ordered_sections, all_assignments, all_locations,
stage_curators, stage_colors, all_videos) and never mutates them, so the
existing lineup.html/bios.json/timetable.json outputs are unaffected.

Id rules (INV-1, docs/invariants.md; section 5 of the design doc) are
reproduced exactly, not re-derived: the per-artist card/heart id uses the
same `uuid.uuid5(uuid.NAMESPACE_URL, f"{artist_id}:{date}:{period}:
{floor_id or ''}")` formula as `render.py`'s `card_key`/`a_card_key`, and
every slot id is computed by calling `timetable_json.slot_uuid()` directly
(never reimplemented).
"""


def _dump(obj) -> str:
    return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _card_id(
    artist_id: str, date: str, period: str | None, floor_id: str | None
) -> str:
    card_key = f"{artist_id}:{date}:{period}:{floor_id or ''}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, card_key))


def _photo_path(photo_file: str | None, photos_prefix: str) -> str:
    return f"{photos_prefix}{photo_file}" if photo_file else ""


def build_event_payload(event_row: sqlite3.Row | None) -> dict | None:
    if event_row is None:
        return None
    keys = event_row.keys()

    def g(col: str):
        return event_row[col] if col in keys else None

    return {
        "id": g("id"),
        "name": g("name"),
        "short_name": g("short_name"),
        "edition": g("edition"),
        "timezone": g("timezone"),
        "start_date": g("start_date"),
        "end_date": g("end_date"),
        "address": g("address"),
        "website": g("website"),
        "source_url": g("source_url"),
        "latitude": g("latitude"),
        "longitude": g("longitude"),
    }


def _lineup_link_entry(link: dict) -> dict:
    return {
        "platform": link.get("platform"),
        "url": link.get("url"),
        "follower_count": link.get("follower_count"),
    }


def _lineup_all_slot_entry(slot: dict) -> dict:
    return {
        "date": slot.get("date"),
        "period": slot.get("period"),
        "floor_id": slot.get("location_id"),
        "floor_name": slot.get("location_name"),
        "start_time": slot.get("start_time"),
        "end_time": slot.get("end_time"),
    }


def _lineup_artist_entry(
    a: dict, date: str, period: str | None, floor_id: str | None, photos_prefix: str
) -> dict:
    oid = a.get("id", "")
    return {
        "id": oid,
        "card_id": _card_id(oid, date, period, floor_id),
        "name": a.get("name", ""),
        "photo": _photo_path(a.get("photo_file"), photos_prefix),
        "links": [_lineup_link_entry(lnk) for lnk in a.get("links", [])],
        "slot": {
            "start_time": a.get("start_time"),
            "end_time": a.get("end_time"),
        },
        "all_slots": [_lineup_all_slot_entry(s) for s in a.get("all_slots", [])],
    }


def _dates_and_sections(
    ordered_sections: list[dict],
) -> tuple[list[str], dict[str, list[dict]]]:
    dates_seen: list[str] = []
    sections_by_date: dict[str, list[dict]] = {}
    for sec in ordered_sections:
        sections_by_date.setdefault(sec["date"], []).append(sec)
        if sec["date"] not in dates_seen:
            dates_seen.append(sec["date"])
    return dates_seen, sections_by_date


def build_lineup_payload(
    event_id: str,
    ordered_sections: list[dict],
    all_assignments: dict[str, list[dict]],
    all_locations: dict[str, dict],
    photos_prefix: str = "photos/",
) -> dict:
    dates_seen, sections_by_date = _dates_and_sections(ordered_sections)

    days = []
    for date_str in dates_seen:
        periods = []
        for sec in sections_by_date[date_str]:
            period = sec["period"]
            is_night = period == "night"
            artists = all_assignments.get(sec["key"], [])

            if is_night:
                by_floor: dict[str | None, list[dict]] = {}
                floor_order: list[str | None] = []
                for a in artists:
                    fid = a.get("location_id")
                    if fid not in by_floor:
                        by_floor[fid] = []
                        floor_order.append(fid)
                    by_floor[fid].append(a)
                floors = []
                for fid in floor_order:
                    loc = all_locations.get(fid) if fid else None
                    floors.append(
                        {
                            "floor_id": fid,
                            "floor_name": loc["name"] if loc else fid,
                            "artists": [
                                _lineup_artist_entry(
                                    a, date_str, period, fid, photos_prefix
                                )
                                for a in by_floor[fid]
                            ],
                        }
                    )
                periods.append({"period": period, "floors": floors})
            else:
                periods.append(
                    {
                        "period": period,
                        "artists": [
                            _lineup_artist_entry(
                                a, date_str, period, None, photos_prefix
                            )
                            for a in artists
                        ],
                    }
                )
        days.append({"date": date_str, "periods": periods})

    return {"event_id": event_id, "days": days}


def build_timetable_payload(
    event_id: str,
    ordered_sections: list[dict],
    all_assignments: dict[str, list[dict]],
    all_locations: dict[str, dict],
    stage_colors: dict[str, str],
    stage_curators: dict[str, str],
    photos_prefix: str = "photos/",
) -> dict:
    # Global floor order/colors: sourced from event_stages.position (the
    # order `load_stages_from_db` already returns), NOT render.py's
    # hardcoded `canonical_floor_order` list -- the design doc (section 2.3)
    # flags that hardcoded list as a legacy workaround to retire, not a
    # convention for this new endpoint to copy forward.
    floor_order = list(all_locations.keys())
    floors = [
        {
            "id": fid,
            "name": all_locations[fid]["name"],
            "color": stage_colors.get(fid),
        }
        for fid in floor_order
    ]

    dates_seen, sections_by_date = _dates_and_sections(ordered_sections)

    days = []
    for date_str in dates_seen:
        periods = []
        for sec in sections_by_date[date_str]:
            period = sec["period"]
            artists = all_assignments.get(sec["key"], [])
            timed = [a for a in artists if a.get("start_time") and a.get("end_time")]
            if not timed:
                continue

            by_floor: dict[str, list[dict]] = {}
            for a in timed:
                fid = a.get("location_id") or "unknown"
                by_floor.setdefault(fid, []).append(a)
            floor_ids = [f for f in floor_order if f in by_floor] + [
                f for f in by_floor if f not in floor_order
            ]

            is_night = period == "night"
            all_starts = [_parse_time(a["start_time"]) for a in timed]
            all_ends = [_parse_time(a["end_time"]) for a in timed]
            if is_night:
                adjusted_ends = [e + 1440 if e < 12 * 60 else e for e in all_ends]
                adjusted_starts = [s + 1440 if s < 12 * 60 else s for s in all_starts]
                grid_start = min(adjusted_starts)
                grid_end = max(adjusted_ends)
            else:
                grid_start = min(all_starts)
                grid_end = max(all_ends)
            grid_start = (grid_start // 60) * 60

            notes = []
            for key, note in stage_curators.items():
                note_date, _, note_fid = key.partition(".")
                if note_date == date_str and note_fid in floor_ids:
                    notes.append({"floor_id": note_fid, "note": note})

            slots = []
            for fid in floor_ids:
                floor_artists = by_floor.get(fid, [])
                grouped: dict[tuple[str, str], list[dict]] = {}
                for a in floor_artists:
                    key = (a["start_time"], a["end_time"])
                    grouped.setdefault(key, []).append(a)
                group_times = _slot_group_times(grouped)

                for (st, et), group in grouped.items():
                    aids = [a.get("id", "") for a in group]
                    slot_id = slot_uuid(
                        aids, date_str, period, fid, st, et, group_times[tuple(aids)]
                    )
                    slot_artists = []
                    for a in group:
                        loc_for_id = fid if is_night else ""
                        slot_artists.append(
                            {
                                "id": a.get("id", ""),
                                "card_id": _card_id(
                                    a.get("id", ""), date_str, period, loc_for_id
                                ),
                                "name": a.get("name", ""),
                                "photo": _photo_path(
                                    a.get("photo_file"), photos_prefix
                                ),
                            }
                        )
                    slots.append(
                        {
                            "slot_id": slot_id,
                            "floor_id": fid,
                            "start_time": st,
                            "end_time": et,
                            "is_b2b": len(group) > 1,
                            "artists": slot_artists,
                        }
                    )
            slots.sort(key=lambda s: (floor_ids.index(s["floor_id"]), s["start_time"]))

            periods.append(
                {
                    "period": period,
                    "is_night": is_night,
                    "grid_start_min": grid_start,
                    "grid_end_min": grid_end,
                    "floor_ids": floor_ids,
                    "notes": notes,
                    "slots": slots,
                }
            )
        if periods:
            days.append({"date": date_str, "periods": periods})

    return {"event_id": event_id, "floors": floors, "days": days}


def build_artist_detail_payloads(
    all_assignments: dict[str, list[dict]],
    all_videos: dict[str, list[dict]],
    photos_prefix: str = "photos/",
    thumbs_prefix: str = "thumbs/",
) -> dict[str, dict]:
    # Same scope as today's bios.json: only artists actually scheduled in
    # this event's assignments (deduped by id), not every artist row ever
    # seen in the (global, multi-event-shared) artists table.
    seen: dict[str, dict] = {}
    for artists_list in all_assignments.values():
        for a in artists_list:
            oid = a.get("id", "")
            if oid and oid not in seen:
                seen[oid] = a

    payloads: dict[str, dict] = {}
    for oid, a in seen.items():
        raw_bio = a.get("bio") or ""
        bio_html = _render_markdown(_strip_booking(raw_bio)) if raw_bio else ""
        sets = []
        for v in all_videos.get(oid, []):
            sets.append(
                {
                    "id": v["id"],
                    "platform": v["platform"],
                    "url": v["url"],
                    "title": v["title"],
                    "view_count": v["views"],
                    "duration_min": v["duration"],
                    "upload_date": v["date"],
                    "thumb": f"{thumbs_prefix}{v['id']}.avif",
                }
            )
        payloads[oid] = {
            "id": oid,
            "name": a.get("name", ""),
            "photo": _photo_path(a.get("photo_file"), photos_prefix),
            "bio_html": bio_html,
            "links": [_lineup_link_entry(lnk) for lnk in a.get("links", [])],
            "sets": sets,
        }
    return payloads


def generate_lineup_api_json(
    db: sqlite3.Connection,
    event_id: str,
    output_dir: str | Path,
    ordered_sections: list[dict],
    all_assignments: dict[str, list[dict]],
    all_locations: dict[str, dict],
    stage_curators: dict[str, str],
    stage_colors: dict[str, str],
    all_videos: dict[str, list[dict]],
    has_timetable: bool = False,
    photos_prefix: str = "photos/",
    thumbs_prefix: str = "thumbs/",
) -> None:
    """Write the static JSON files served by GET /api/v1/events/{event_id},
    .../lineup, .../timetable and .../artists/{artist_id} (services/companion/api.py).
    Regenerates the whole api/v1/ tree on every run so an artist or event
    that disappears from the DB does not leave a stale file behind."""
    api_dir = Path(output_dir) / "api" / "v1"
    if api_dir.exists():
        shutil.rmtree(api_dir)

    events_dir = api_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    event_payload = build_event_payload(get_event(db, event_id))
    (events_dir / f"{event_id}.json").write_text(_dump(event_payload), encoding="utf-8")

    event_subdir = events_dir / event_id
    event_subdir.mkdir(parents=True, exist_ok=True)

    lineup_payload = build_lineup_payload(
        event_id, ordered_sections, all_assignments, all_locations, photos_prefix
    )
    (event_subdir / "lineup.json").write_text(_dump(lineup_payload), encoding="utf-8")

    if has_timetable:
        timetable_payload = build_timetable_payload(
            event_id,
            ordered_sections,
            all_assignments,
            all_locations,
            stage_colors,
            stage_curators,
            photos_prefix,
        )
        (event_subdir / "timetable.json").write_text(
            _dump(timetable_payload), encoding="utf-8"
        )

    artists_dir = api_dir / "artists"
    artists_dir.mkdir(parents=True, exist_ok=True)
    artist_payloads = build_artist_detail_payloads(
        all_assignments, all_videos, photos_prefix, thumbs_prefix
    )
    for oid, payload in artist_payloads.items():
        (artists_dir / f"{oid}.json").write_text(_dump(payload), encoding="utf-8")
