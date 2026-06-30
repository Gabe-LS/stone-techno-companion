#!/usr/bin/env python3
"""Fetch top YouTube sets for all artists and save to the videos table."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlretrieve

import pyvips

DB_PATH = Path(__file__).resolve().parent / "lineup.db"
THUMBS_DIR = Path(__file__).resolve().parent / "output" / "thumbs"
OVERRIDES_PATH = Path(__file__).resolve().parent / "scraper" / "overrides.toml"

MIN_DURATION = 2700  # 45 min
TARGET = 5
MAX_THUMB = 240
SEARCH_COUNT = 50
MAX_YEARS = 15


def fetch_video_metadata(video_ids: list[str]) -> list[dict]:
    results = []
    for vid_id in video_ids:
        url = f"https://www.youtube.com/watch?v={vid_id}"
        try:
            proc = subprocess.run(
                ["yt-dlp", url, "-j", "--skip-download"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue
        for line in proc.stdout.strip().split("\n"):
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            results.append(
                {
                    "id": d.get("id", vid_id),
                    "title": d.get("title", ""),
                    "url": url,
                    "views": d.get("view_count") or 0,
                    "duration": round((d.get("duration") or 0) / 60),
                    "date": int(d.get("upload_date") or "0"),
                    "_channel": d.get("channel_id") or "",
                }
            )
    return results


def search_artist_videos(name: str, search_name: str | None = None) -> list[dict]:
    yt_name = search_name or name
    query = f"ytsearch{SEARCH_COUNT}:{yt_name} DJ set mix live"
    try:
        proc = subprocess.run(
            ["yt-dlp", query, "-j", "--skip-download"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return []

    match_name = search_name or name
    results = []
    for line in proc.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue

        title = d.get("title", "")
        duration = d.get("duration") or 0
        views = d.get("view_count") or 0
        vid_id = d.get("id", "")
        upload = int(d.get("upload_date") or "0")
        channel = d.get("channel_id") or d.get("uploader_id") or ""

        if not re.search(re.escape(match_name), title, re.IGNORECASE):
            continue
        if duration < MIN_DURATION:
            continue

        results.append(
            {
                "id": vid_id,
                "title": title,
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "views": views,
                "duration": round(duration / 60),
                "date": upload,
                "_channel": channel,
            }
        )

    return results


def _cap_per_channel(videos: list[dict], max_per: int = 2) -> list[dict]:
    sorted_vids = sorted(videos, key=lambda x: x["views"], reverse=True)
    counts: dict[str, int] = {}
    kept = []
    for v in sorted_vids:
        ch = v.get("_channel") or ""
        if ch and counts.get(ch, 0) >= max_per:
            continue
        kept.append(v)
        if ch:
            counts[ch] = counts.get(ch, 0) + 1
    return kept


def select_videos(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []

    candidates = _cap_per_channel(candidates)

    cutoff_5y = int((datetime.now() - timedelta(days=5 * 365)).strftime("%Y%m%d"))
    cutoff_max = int(
        (datetime.now() - timedelta(days=MAX_YEARS * 365)).strftime("%Y%m%d")
    )

    recent = [r for r in candidates if r["date"] >= cutoff_5y and r["views"] >= 5000]
    if len(recent) >= TARGET:
        recent.sort(key=lambda x: x["views"], reverse=True)
        return recent

    pool = [r for r in candidates if r["date"] >= cutoff_max]

    big = [r for r in pool if r["views"] >= 50000]
    big.sort(key=lambda x: x["views"], reverse=True)

    if len(big) >= TARGET:
        return big[:TARGET]

    selected = list(big)
    remaining = [r for r in pool if r not in selected]
    remaining.sort(key=lambda x: x["date"], reverse=True)

    for threshold in range(40000, 4000, -10000):
        candidates_at = [
            r for r in remaining if r["views"] >= threshold and r not in selected
        ]
        if len(selected) + len(candidates_at) >= TARGET:
            selected.extend(candidates_at[: TARGET - len(selected)])
            break
    else:
        candidates_at = [
            r for r in remaining if r["views"] >= 5000 and r not in selected
        ]
        selected.extend(candidates_at[: TARGET - len(selected)])

    selected.sort(key=lambda x: x["views"], reverse=True)
    return selected


def download_thumb(vid_id: str) -> bool:
    out_path = THUMBS_DIR / f"{vid_id}.avif"
    if out_path.exists():
        return True

    thumb_url = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(tmp_fd)

    try:
        urlretrieve(thumb_url, tmp_path)
        img = pyvips.Image.new_from_file(tmp_path)
        scale = min(MAX_THUMB / img.width, MAX_THUMB / img.height)
        if scale < 1:
            img = img.resize(scale, kernel=pyvips.enums.Kernel.LANCZOS3)
        img.heifsave(
            str(out_path), compression=pyvips.enums.ForeignHeifCompression.AV1, Q=50
        )
        return True
    except Exception:
        return False
    finally:
        os.unlink(tmp_path)


def main():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    from scraper.db import init_db

    init_db(db)

    artists = db.execute("SELECT id, name FROM artists ORDER BY name").fetchall()

    yt_names: dict[str, str] = {}
    yt_forced: dict[str, list[str]] = {}
    yt_add: dict[str, list[str]] = {}
    if OVERRIDES_PATH.exists():
        import tomllib

        with open(OVERRIDES_PATH, "rb") as f:
            overrides = tomllib.load(f)
        yt_names = overrides.get("youtube_names", {})
        yt_forced = overrides.get("youtube_videos", {})
        yt_add = overrides.get("youtube_videos_add", {})

    cached_ids = {
        row[0] for row in db.execute("SELECT DISTINCT artist_id FROM videos").fetchall()
    }

    THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    total = len(artists)
    fetched = 0
    skipped = 0

    for i, artist in enumerate(artists, 1):
        aid = artist["id"]
        name = artist["name"]
        search_name = yt_names.get(name)

        if aid in cached_ids:
            count = db.execute(
                "SELECT COUNT(*) FROM videos WHERE artist_id = ?", (aid,)
            ).fetchone()[0]
            skipped += 1
            print(f"  [{i}/{total}] {name}: cached ({count} videos)")
            continue

        print(f"  [{i}/{total}] {name}", end="", flush=True)

        if name in yt_forced:
            selected = fetch_video_metadata(yt_forced[name])
        else:
            candidates = search_artist_videos(name, search_name)
            selected = select_videos(candidates)
            if name in yt_add:
                extra = fetch_video_metadata(yt_add[name])
                seen_ids = {v["id"] for v in selected}
                selected.extend(e for e in extra if e["id"] not in seen_ids)
                selected.sort(key=lambda x: x["views"], reverse=True)

        for v in selected:
            download_thumb(v["id"])

        for pos, v in enumerate(selected):
            db.execute(
                "INSERT OR REPLACE INTO videos "
                "(video_id, artist_id, title, url, views, duration, upload_date, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    v["id"],
                    aid,
                    v["title"],
                    v["url"],
                    v["views"],
                    v["duration"],
                    v.get("date"),
                    pos,
                ),
            )
        db.commit()

        fetched += 1

        if selected:
            print(f" -> {len(selected)} videos (top: {selected[0]['views']:,} views)")
        else:
            print(" -> no videos found")

    db.close()

    with_videos = len(cached_ids) + sum(1 for a in artists if a["id"] not in cached_ids)
    print(f"\nDone: fetched {fetched}, cached {skipped}")


if __name__ == "__main__":
    main()
