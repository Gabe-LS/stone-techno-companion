#!/usr/bin/env python3
"""Stone Techno Companion — enriched festival line-up with artist data."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.db import (
    apply_overrides,
    ensure_event,
    get_event,
    init_db,
    load_all_sets,
    load_assignments_from_db,
    load_stage_curators,
    load_stage_colors,
    load_stages_from_db,
    load_sections_from_db,
    upsert_lineup,
)
from scraper.images import process_artist_photos
from scraper.render import render_output_html
from scraper.timetable_json import generate_timetable_json
from scraper.scrape import (
    fetch_all_ig,
    fetch_all_ra,
    fetch_all_sc,
    fetch_all_spotify,
    scrape_lineup,
)

STONE_TECHNO_URL = "https://www.stone-techno.com/"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DB_PATH = PROJECT_ROOT / "lineup.db"
PHOTOS_DIR = DEFAULT_OUTPUT_DIR / "photos"
OVERRIDES_PATH = PROJECT_ROOT / "scraper" / "overrides.toml"
VPS_HOST = "root@209.38.244.136"
VPS_STATIC_DIR = "/root/services/stone-techno/server/static/"

DEFAULT_EVENT_ID = "stone-techno-2026"
DEFAULT_EVENT_NAME = "Stone Techno"
DEFAULT_EVENT_EDITION = "2026"


def deploy_to_vps(output_dir: Path, output_path: Path) -> None:
    import tempfile

    print("Deploying to VPS ...")
    with tempfile.TemporaryDirectory() as staging:
        staging_path = Path(staging)
        shutil.copy2(output_path, staging_path / "index.html")
        icons_dir = Path(__file__).resolve().parent / "scraper" / "icons"
        for fname in ("favicon.svg", "favicon.png"):
            src = icons_dir / fname
            if src.exists():
                shutil.copy2(src, staging_path / fname)
        for json_name in ("timetable.json", "bios.json"):
            json_src = output_dir / json_name
            if json_src.exists():
                shutil.copy2(json_src, staging_path / json_name)
        server_static = Path(__file__).resolve().parent.parent / "server" / "static"
        for fname in ("manifest.json", "sw.js", "shared.css", "shared.js"):
            src = server_static / fname
            if src.exists():
                shutil.copy2(src, staging_path / fname)
        photos_src = output_dir / "photos"
        if photos_src.is_dir():
            shutil.copytree(photos_src, staging_path / "photos")
        thumbs_src = output_dir / "thumbs"
        if thumbs_src.is_dir():
            shutil.copytree(thumbs_src, staging_path / "thumbs")
        # Normalize staging permissions before syncing: the staging dir is a
        # mkdtemp (mode 700), and rsync -a copies that mode onto the VPS
        # static dir itself, locking out the container's non-root appuser
        # (every static route 500s). Dirs must be world-traversable (755) and
        # files world-readable (644). Done here in Python because macOS ships
        # openrsync, which does not support --chmod=D755,F644.
        staging_path.chmod(0o755)
        for p in staging_path.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
        # No global --delete: the VPS static dir is the git worktree's
        # server/static, which also holds tracked assets not staged here —
        # a mirror sync would delete them and break the next git pull.
        subprocess.run(
            [
                "rsync",
                "-avz",
                f"{staging}/",
                f"{VPS_HOST}:{VPS_STATIC_DIR}",
            ],
            check=True,
        )
        # Prune stale files only inside the fully regenerated directories.
        for subdir in ("photos", "thumbs"):
            if (staging_path / subdir).is_dir():
                subprocess.run(
                    [
                        "rsync",
                        "-avz",
                        "--delete",
                        f"{staging}/{subdir}/",
                        f"{VPS_HOST}:{VPS_STATIC_DIR}{subdir}/",
                    ],
                    check=True,
                )
    print("Deployed to https://stonetechno.deftlab.dev/")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="stone_techno_companion",
        description="Stone Techno Companion — scrape the festival lineup, enrich with "
        "social data, and generate an interactive line-up page.",
    )
    parser.add_argument("--url", default=STONE_TECHNO_URL)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--event-id", default=DEFAULT_EVENT_ID)
    parser.add_argument("--event-name", default=DEFAULT_EVENT_NAME)
    parser.add_argument("--event-edition", default=DEFAULT_EVENT_EDITION)
    parser.add_argument(
        "--no-followers", action="store_true", help="Skip fetching follower counts"
    )
    parser.add_argument(
        "--no-photos", action="store_true", help="Skip processing photos"
    )
    parser.add_argument(
        "--render-only", action="store_true", help="Regenerate HTML from DB only"
    )
    parser.add_argument(
        "--refresh-followers", action="store_true", help="Re-fetch all follower counts"
    )
    parser.add_argument(
        "--refresh-photos", action="store_true", help="Re-process all photos"
    )
    parser.add_argument(
        "--deploy", action="store_true", help="Deploy to VPS after generating"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "lineup.html"

    event_id = args.event_id

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    try:
        init_db(db)
        ensure_event(
            db,
            event_id,
            args.event_name,
            edition=args.event_edition,
            source_url=args.url,
        )

        if not args.render_only:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(
                        locale="en-US",
                        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    )

                    print(f"Fetching {args.url} ...")
                    parsed = scrape_lineup(ctx, args.url)
                    print(
                        f"Parsed {len(parsed['artists'])} artists across "
                        f"{len(parsed['sections'])} sections, {len(parsed['locations'])} locations."
                    )
                    upsert_lineup(db, parsed, event_id)
                    apply_overrides(db, OVERRIDES_PATH, event_id)

                    if args.refresh_followers:
                        db.execute("UPDATE artist_links SET follower_count = NULL")
                        db.commit()

                    if not args.no_followers:
                        fetch_all_sc(ctx, db)
                        fetch_all_ig(ctx, db)
                        fetch_all_spotify(ctx, db)
                        fetch_all_ra(ctx, db)
                finally:
                    browser.close()
        else:
            print("Rendering from database (no scraping) ...")
            apply_overrides(db, OVERRIDES_PATH, event_id)

        if args.refresh_photos:
            db.execute("UPDATE artists SET photo_file = NULL")
            db.commit()

        if not args.no_photos:
            process_artist_photos(db, output_dir / "photos")

        ordered_sections = load_sections_from_db(db, event_id)
        all_locations = load_stages_from_db(db, event_id)
        all_assignments = load_assignments_from_db(db, event_id)
        all_videos = load_all_sets(db)
        stage_curators = load_stage_curators(db, event_id)
        stage_colors = load_stage_colors(db, event_id)

        has_timetable = any(
            a.get("start_time") and a.get("end_time")
            for artists in all_assignments.values()
            for a in artists
        )

        event = get_event(db, event_id)
        event_title = event["name"] if event else args.event_name
        if event and event["edition"]:
            event_title = f"{event['name']} {event['edition']}"
        page_title = f"{event_title} Companion"
        site_short = (
            event["short_name"]
            if event and "short_name" in event.keys() and event["short_name"]
            else event_title
        )

        output_html = render_output_html(
            page_title,
            ordered_sections,
            all_assignments,
            all_locations,
            has_timetable=has_timetable,
            stage_curators=stage_curators,
            stage_colors=stage_colors,
            output_dir=str(output_dir),
            site_short=site_short,
            videos=all_videos,
        )

        if has_timetable:
            timetable_json = generate_timetable_json(db, event_id)
            timetable_path = output_dir / "timetable.json"
            timetable_path.write_text(timetable_json, encoding="utf-8")
            print(f"Wrote {timetable_path}")
    finally:
        db.close()

    output_path.write_text(output_html, encoding="utf-8")
    print(f"Wrote {output_path}")

    if args.deploy:
        deploy_to_vps(output_dir, output_path)


if __name__ == "__main__":
    main()
