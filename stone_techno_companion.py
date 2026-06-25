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
    init_db,
    load_assignments_from_db,
    load_locations_from_db,
    load_sections_from_db,
    upsert_lineup,
)
from scraper.images import process_artist_photos
from scraper.render import render_output_html
from scraper.timetable_json import generate_timetable_json
from scraper.scrape import (
    fetch_all_ig,
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
        timetable_src = output_dir / "timetable.json"
        if timetable_src.exists():
            shutil.copy2(timetable_src, staging_path / "timetable.json")
        photos_src = output_dir / "photos"
        if photos_src.is_dir():
            shutil.copytree(photos_src, staging_path / "photos")
        subprocess.run(
            [
                "rsync",
                "-avz",
                "--delete",
                f"{staging}/",
                f"{VPS_HOST}:{VPS_STATIC_DIR}",
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
    parser.add_argument("--title", default="Stone Techno 2026 Line-up")
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

    db = sqlite3.connect(str(DB_PATH))
    try:
        init_db(db)

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
                    upsert_lineup(db, parsed)
                    apply_overrides(db, OVERRIDES_PATH)

                    if args.refresh_followers:
                        db.execute(
                            "UPDATE artists SET ig_followers = NULL, sc_followers = NULL, spotify_listeners = NULL"
                        )
                        db.commit()

                    if not args.no_followers:
                        fetch_all_sc(ctx, db)
                        fetch_all_ig(ctx, db)
                        fetch_all_spotify(ctx, db)
                finally:
                    browser.close()
        else:
            print("Rendering from database (no scraping) ...")
            apply_overrides(db, OVERRIDES_PATH)

        if args.refresh_photos:
            db.execute("UPDATE artists SET photo_local = NULL")
            db.commit()

        if not args.no_photos:
            process_artist_photos(db, output_dir / "photos")

        ordered_sections = load_sections_from_db(db)
        all_locations = load_locations_from_db(db)
        all_assignments = load_assignments_from_db(db)

        # Enable timetable view when any artist has start_time
        has_timetable = any(
            a.get("start_time") for artists in all_assignments.values() for a in artists
        )

        output_html = render_output_html(
            args.title,
            ordered_sections,
            all_assignments,
            all_locations,
            has_timetable=has_timetable,
        )

        if has_timetable:
            timetable_json = generate_timetable_json(db)
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
