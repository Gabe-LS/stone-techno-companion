#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

import pyvips
from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, sync_playwright

STONE_TECHNO_URL = "https://www.stone-techno.com/"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DB_PATH = Path(__file__).resolve().parent / "lineup.db"
PHOTOS_DIR = DEFAULT_OUTPUT_DIR / "photos"
OVERRIDES_PATH = Path(__file__).resolve().parent / "overrides.toml"
SSIMULACRA2_TARGET = 78.0


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


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
    # Migrations for existing DBs
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
    # Migrate old sections table (label -> date+period)
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


def upsert_lineup(
    db: sqlite3.Connection,
    parsed: dict,
) -> None:
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


OVERRIDE_FIELDS = {"instagram", "soundcloud", "spotify", "linktree", "youtube", "photo"}


def apply_overrides(db: sqlite3.Connection) -> None:
    if not OVERRIDES_PATH.exists():
        return
    import tomllib

    with open(OVERRIDES_PATH, "rb") as f:
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
                # Clear associated follower count when a link changes
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
        print(f"Applied {applied} override(s) from overrides.json")


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split()) if text else ""


def is_valid_url(url: str | None) -> bool:
    if not url:
        return False
    url = url.strip()
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_youtube_channel(url: str) -> bool:
    """True for YouTube channel/profile URLs, false for individual videos."""
    if not url:
        return False
    lower = url.lower()
    if "youtube.com" not in lower:
        return False
    # youtu.be is always a video short link — reject
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    # Accept: /c/name, /channel/id, /user/name, /@handle
    if any(path.startswith(p) for p in ("/c/", "/channel/", "/user/", "/@")):
        return True
    return False


def is_sc_profile(url: str) -> bool:
    """True for SoundCloud profile URLs, false for individual tracks/sets/redirects."""
    if not url:
        return False
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc in ("on.soundcloud.com",):
        return False
    if "soundcloud.com" not in netloc:
        return False
    segments = [s for s in parsed.path.strip("/").split("/") if s]
    return len(segments) == 1


def parse_follower_count(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip().replace(",", "")
    if raw.isdigit():
        return int(raw)
    m = re.match(r"^([\d.]+)([KMB])$", raw, re.IGNORECASE)
    if m:
        num = float(m.group(1))
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
        return int(num * mult)
    return None


def format_followers(count: int | None) -> str | None:
    if count is None:
        return None
    return f"{count:,}"


# ---------------------------------------------------------------------------
# Scraping — lineup page
# ---------------------------------------------------------------------------


def _parse_timestamp_key(ts_key: str) -> tuple[str, str] | None:
    """Parse '1783641600d' -> ('2026-07-10', 'day') or None."""
    if not ts_key or ts_key == "0":
        return None
    suffix = ts_key[-1]
    if suffix not in {"d", "n"}:
        return None
    raw = ts_key[:-1]
    if not raw.isdigit():
        return None
    dt = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d"), "day" if suffix == "d" else "night"


def scrape_lineup(ctx: BrowserContext, url: str) -> dict:
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("li.tab-list__list-item.lineup-name", timeout=30000)
    source_html = page.content()
    page.close()

    soup = BeautifulSoup(source_html, "html.parser")

    sections: list[dict] = []
    for li in soup.select("ul.tab-list__nav li.tab-list__nav-item.day-filter"):
        ts_key = (li.get("data-timestamp") or "").strip()
        parsed = _parse_timestamp_key(ts_key)
        if not parsed:
            continue
        sections.append({"key": ts_key, "date": parsed[0], "period": parsed[1]})

    locations: dict[str, dict] = {}
    for li in soup.select("li.tab-list__nav-item.location-filter"):
        loc_id = (li.get("data-location") or "").strip()
        if not loc_id:
            continue
        name = li.get_text(strip=True)
        desc = (li.get("data-locationdescription") or "").strip() or None
        locations[loc_id] = {"name": name, "description": desc}

    overlay_details: dict[str, dict] = {}
    for overlay in soup.select("div.overlay.line-up-overlay[data-overlay-identifier]"):
        overlay_id = overlay.get("data-overlay-identifier", "").strip()
        if not overlay_id:
            continue
        name_el = overlay.select_one(".headline__text")
        name = (
            normalize_whitespace(name_el.get_text(" ", strip=True)) if name_el else ""
        )
        instagram = soundcloud = spotify = youtube = None
        for a in overlay.select("ul.social-list a.social-list__link[href]"):
            href = (a.get("href") or "").strip()
            hl = href.lower()
            if "instagram.com" in hl and not instagram and is_valid_url(href):
                instagram = href
            elif (
                "soundcloud.com" in hl
                and not soundcloud
                and is_valid_url(href)
                and is_sc_profile(href)
            ):
                soundcloud = href
            elif "spotify.com" in hl and not spotify and is_valid_url(href):
                spotify = href
            elif not youtube and is_valid_url(href) and is_youtube_channel(href):
                youtube = href
        photo = None
        picture = overlay.select_one("picture")
        if picture:
            img = picture.select_one("img[src]")
            if img:
                src = (img.get("src") or "").strip()
                if is_valid_url(src):
                    photo = src
        overlay_details[overlay_id] = {
            "name": name,
            "instagram": instagram,
            "soundcloud": soundcloud,
            "spotify": spotify,
            "youtube": youtube,
            "photo": photo,
        }

    assignments: list[dict] = []
    for li in soup.select("li.tab-list__list-item.lineup-name[data-timestamp]"):
        overlay_id = (li.get("data-overlay-identifier") or "").strip()
        ts_list = [
            t.strip() for t in (li.get("data-timestamp") or "").split(",") if t.strip()
        ]
        loc_list = [l.strip() for l in (li.get("data-location") or "").split(",")]
        for i, ts in enumerate(ts_list):
            loc_id = loc_list[i] if i < len(loc_list) and loc_list[i] else None
            assignments.append(
                {"overlay_id": overlay_id, "timestamp_key": ts, "location_id": loc_id}
            )

    return {
        "sections": sections,
        "locations": locations,
        "artists": overlay_details,
        "assignments": assignments,
    }


# ---------------------------------------------------------------------------
# Scraping — SoundCloud (followers + artist links)
# ---------------------------------------------------------------------------


def _extract_gate_url(href: str) -> str | None:
    try:
        qs = parse_qs(urlparse(href).query)
        real = unquote(qs.get("url", [""])[0])
        return real if real and is_valid_url(real) else None
    except Exception:
        return None


def fetch_sc_profile(ctx: BrowserContext, url: str) -> dict:
    result: dict = {
        "followers": None,
        "instagram": None,
        "spotify": None,
        "linktree": None,
        "youtube": None,
    }
    try:
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        soup = BeautifulSoup(page.content(), "html.parser")
        page.close()

        link = soup.select_one('a[title*="followers"]')
        if link:
            m = re.match(r"([\d,.]+)", link.get("title", ""))
            if m:
                result["followers"] = parse_follower_count(m.group(1))

        for a in soup.select('a[href*="gate.sc"]'):
            real_url = _extract_gate_url(a.get("href", ""))
            if not real_url:
                continue
            lower = real_url.lower()
            if "instagram.com" in lower and not result["instagram"]:
                result["instagram"] = real_url
            elif "spotify.com" in lower and not result["spotify"]:
                result["spotify"] = real_url
            elif "linktr.ee" in lower and not result["linktree"]:
                result["linktree"] = real_url
            elif not result["youtube"] and is_youtube_channel(real_url):
                result["youtube"] = real_url
    except Exception:
        try:
            page.close()
        except Exception:
            pass
    return result


def fetch_all_sc(ctx: BrowserContext, db: sqlite3.Connection) -> None:
    missing = get_missing(db, "soundcloud", "sc_followers")
    if not missing:
        return

    total = len(missing)
    print(f"Fetching {total} SoundCloud profiles ...")
    ig_corrections: list[tuple[str, str]] = []

    for i, (oid, sc_url) in enumerate(missing, 1):
        artist = get_artist(db, oid)
        print(f"  [{i}/{total}] SC: {artist['name']}", end="", flush=True)

        profile = fetch_sc_profile(ctx, sc_url)

        if profile["followers"] is not None:
            update_artist_field(db, oid, "sc_followers", profile["followers"])

        # Fill missing links from SC profile
        if profile["instagram"] and not artist["instagram"]:
            update_artist_field(db, oid, "instagram", profile["instagram"])
        if profile["spotify"] and not artist["spotify"]:
            update_artist_field(db, oid, "spotify", profile["spotify"])
        if profile["linktree"] and not artist["linktree"]:
            update_artist_field(db, oid, "linktree", profile["linktree"])
        if profile["youtube"] and not artist["youtube"]:
            update_artist_field(db, oid, "youtube", profile["youtube"])

        # Track IG corrections: SC has a different IG than the main site
        if profile["instagram"] and artist["instagram"]:
            sc_ig = profile["instagram"].rstrip("/").lower()
            site_ig = artist["instagram"].rstrip("/").lower()
            if sc_ig != site_ig:
                ig_corrections.append((oid, profile["instagram"]))

        fc = f"{profile['followers']:,}" if profile["followers"] else "?"
        extras = [
            k for k in ("instagram", "spotify", "linktree", "youtube") if profile[k]
        ]
        extra_str = f" +{','.join(extras)}" if extras else ""
        print(f" -> {fc}{extra_str}")

    # Fix wrong IG links: if the main site's IG never returned followers, use SC's IG
    if ig_corrections:
        print(f"\nFound {len(ig_corrections)} different IG links from SoundCloud:")
        for oid, new_ig in ig_corrections:
            artist = get_artist(db, oid)
            if artist["ig_followers"] is None:
                db.execute(
                    "UPDATE artists SET instagram = ?, ig_followers = NULL WHERE overlay_id = ?",
                    (new_ig, oid),
                )
                db.commit()
                print(
                    f"  {artist['name']}: {artist['instagram']} -> {new_ig} (replaced)"
                )
            else:
                print(
                    f"  {artist['name']}: kept {artist['instagram']} (has followers), SC says {new_ig}"
                )


# ---------------------------------------------------------------------------
# Scraping — Instagram (followers + artist links)
# ---------------------------------------------------------------------------


def _find_in_json(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_in_json(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_in_json(v, key)
            if found is not None:
                return found
    return None


def _extract_ig_links(data: dict) -> dict:
    """Extract links from IG GraphQL user data."""
    links: dict = {
        "soundcloud": None,
        "spotify": None,
        "linktree": None,
        "youtube": None,
    }

    external_url = _find_in_json(data, "external_url") or ""
    bio_links = _find_in_json(data, "bio_links") or []

    all_urls = [external_url] if external_url else []
    for bl in bio_links:
        url = bl.get("url", "") or ""
        if not url:
            lynx = bl.get("lynx_url", "") or ""
            if "l.instagram.com" in lynx:
                try:
                    qs = parse_qs(urlparse(lynx).query)
                    url = unquote(qs.get("u", [""])[0])
                except Exception:
                    pass
        if url:
            all_urls.append(url)

    for url in all_urls:
        lower = url.lower()
        if (
            "soundcloud.com" in lower
            and not links["soundcloud"]
            and is_valid_url(url)
            and is_sc_profile(url)
        ):
            links["soundcloud"] = url
        elif "spotify.com" in lower and not links["spotify"] and is_valid_url(url):
            links["spotify"] = url
        elif "linktr.ee" in lower and not links["linktree"] and is_valid_url(url):
            links["linktree"] = url
        elif not links["youtube"] and is_valid_url(url) and is_youtube_channel(url):
            links["youtube"] = url

    return links


def fetch_ig_profile(ctx: BrowserContext, url: str) -> dict:
    result: dict = {
        "followers": None,
        "soundcloud": None,
        "spotify": None,
        "linktree": None,
        "youtube": None,
    }
    try:
        page = ctx.new_page()
        captured = {"data": None, "count": None}

        def on_response(response):
            if captured["count"] is not None and captured["data"] is not None:
                return
            if "graphql" not in response.url:
                return
            try:
                data = response.json()
                fc = _find_in_json(data, "follower_count")
                if isinstance(fc, int):
                    captured["count"] = fc
                    captured["data"] = data
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(url, wait_until="networkidle", timeout=20000)

        for _ in range(10):
            if captured["count"] is not None:
                break
            page.wait_for_timeout(500)

        if captured["count"] is not None:
            result["followers"] = captured["count"]
            links = _extract_ig_links(captured["data"])
            result.update(links)
        else:
            # Fallback: meta tag (abbreviated)
            soup = BeautifulSoup(page.content(), "html.parser")
            meta = soup.find("meta", attrs={"property": "og:description"})
            if meta:
                m = re.match(r"([\d,.]+[KMB]?)\s+Follower", meta.get("content", ""))
                if m:
                    result["followers"] = parse_follower_count(m.group(1))

        page.close()
    except Exception:
        try:
            page.close()
        except Exception:
            pass
    return result


def fetch_all_ig(ctx: BrowserContext, db: sqlite3.Connection) -> None:
    missing = get_missing(db, "instagram", "ig_followers")
    if not missing:
        return

    url_to_oids: dict[str, list[str]] = {}
    for oid, ig_url in missing:
        url_to_oids.setdefault(ig_url, []).append(oid)

    unique_urls = list(url_to_oids.keys())
    total = len(unique_urls)
    print(f"Fetching {total} Instagram profiles ...")

    for i, ig_url in enumerate(unique_urls, 1):
        oid = url_to_oids[ig_url][0]
        artist = get_artist(db, oid)
        print(f"  [{i}/{total}] IG: {artist['name']}", end="", flush=True)

        profile = fetch_ig_profile(ctx, ig_url)

        if profile["followers"] is not None:
            for o in url_to_oids[ig_url]:
                update_artist_field(db, o, "ig_followers", profile["followers"])

        # Fill missing links from IG bio
        for o in url_to_oids[ig_url]:
            a = get_artist(db, o)
            if profile["soundcloud"] and not a["soundcloud"]:
                update_artist_field(db, o, "soundcloud", profile["soundcloud"])
            if profile["spotify"] and not a["spotify"]:
                update_artist_field(db, o, "spotify", profile["spotify"])
            if profile["linktree"] and not a["linktree"]:
                update_artist_field(db, o, "linktree", profile["linktree"])
            if profile["youtube"] and not a["youtube"]:
                update_artist_field(db, o, "youtube", profile["youtube"])

        fc_str = f"{profile['followers']:,}" if profile["followers"] else "?"
        extras = [
            k for k in ("soundcloud", "spotify", "linktree", "youtube") if profile[k]
        ]
        extra_str = f" +{','.join(extras)}" if extras else ""
        print(f" -> {fc_str}{extra_str}")


# ---------------------------------------------------------------------------
# Scraping — Spotify (monthly listeners)
# ---------------------------------------------------------------------------


def fetch_spotify_listeners(ctx: BrowserContext, url: str) -> int | None:
    try:
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
        page.close()
        m = re.search(r"([\d,]+)\s+monthly listener", text)
        if m:
            return parse_follower_count(m.group(1))
    except Exception:
        try:
            page.close()
        except Exception:
            pass
    return None


def fetch_all_spotify(ctx: BrowserContext, db: sqlite3.Connection) -> None:
    missing = get_missing(db, "spotify", "spotify_listeners")
    if not missing:
        return

    url_to_oids: dict[str, list[str]] = {}
    for oid, sp_url in missing:
        url_to_oids.setdefault(sp_url, []).append(oid)

    unique_urls = list(url_to_oids.keys())
    total = len(unique_urls)
    print(f"Fetching {total} Spotify profiles ...")

    for i, sp_url in enumerate(unique_urls, 1):
        oid = url_to_oids[sp_url][0]
        artist = get_artist(db, oid)
        print(f"  [{i}/{total}] Spotify: {artist['name']}", end="", flush=True)

        listeners = fetch_spotify_listeners(ctx, sp_url)
        if listeners is not None:
            for o in url_to_oids[sp_url]:
                update_artist_field(db, o, "spotify_listeners", listeners)

        print(f" -> {listeners:,}" if listeners else " -> ?")


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------


def post_downscale_sharpen(im: pyvips.Image, scale: float) -> pyvips.Image:
    if scale >= 1.0:
        return im
    t = min(1.0 - scale, 1.0)
    sigma = 0.5 + 0.3 * t
    m1 = 0.1
    m2 = 0.15 + 0.25 * t
    return im.sharpen(sigma=sigma, x1=1.0, y2=10, y3=20, m1=m1, m2=m2)


def resize_and_crop(raw_path: str, size: int) -> pyvips.Image:
    im = pyvips.Image.new_from_file(raw_path)
    im = im.autorot()
    if im.hasalpha():
        im = im.flatten(background=[255, 255, 255])
    src_w, src_h = im.width, im.height
    scale = size / min(src_w, src_h)
    resized = im.resize(scale, kernel="lanczos3")
    if resized.width > size or resized.height > size:
        left = (resized.width - size) // 2
        top = (resized.height - size) // 2
        resized = resized.crop(left, top, size, size)
    return post_downscale_sharpen(resized, scale)


def encode_avif_to_target(ref_im: pyvips.Image, out_path: str, target: float) -> float:
    with tempfile.TemporaryDirectory() as tmp_dir:
        ref_png = f"{tmp_dir}/ref.png"
        ref_im.pngsave(ref_png, compression=1)
        lo, hi = 20, 80
        best_q = 60
        while hi - lo > 1:
            mid = (lo + hi) // 2
            candidate = f"{tmp_dir}/candidate.avif"
            ref_im.heifsave(
                candidate, Q=mid, compression=pyvips.enums.ForeignHeifCompression.AV1
            )
            decoded = f"{tmp_dir}/decoded.png"
            subprocess.run(
                ["vips", "copy", candidate, decoded], check=True, capture_output=True
            )
            result = subprocess.run(
                ["ssimulacra2", ref_png, decoded],
                check=True,
                capture_output=True,
                text=True,
            )
            score = float(result.stdout.strip())
            if score < target:
                lo = mid
            else:
                hi = mid
                best_q = mid
        ref_im.heifsave(
            out_path, Q=best_q, compression=pyvips.enums.ForeignHeifCompression.AV1
        )
        decoded = f"{tmp_dir}/final_decoded.png"
        subprocess.run(
            ["vips", "copy", out_path, decoded], check=True, capture_output=True
        )
        result = subprocess.run(
            ["ssimulacra2", ref_png, decoded],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())


def process_artist_photos(db: sqlite3.Connection) -> None:
    missing = get_artists_missing_photos(db)
    if not missing:
        print("All photos already processed.")
        return
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    total = len(missing)
    print(f"Processing {total} artist photos ...")
    for i, (overlay_id, photo_url) in enumerate(missing, 1):
        name = (
            db.execute(
                "SELECT name FROM artists WHERE overlay_id = ?", (overlay_id,)
            ).fetchone()
            or [overlay_id]
        )[0]
        print(f"  [{i}/{total}] {name}", end="", flush=True)
        filename = f"{overlay_id}.avif"
        out_path = PHOTOS_DIR / filename
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                raw_path = f"{tmp_dir}/original"
                from urllib.request import Request

                req = Request(photo_url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=30) as resp:
                    with open(raw_path, "wb") as f:
                        f.write(resp.read())
                ref_im = resize_and_crop(raw_path, 240)
                score = encode_avif_to_target(ref_im, str(out_path), SSIMULACRA2_TARGET)
            save_photo_local(db, overlay_id, filename)
            print(f" -> {out_path.stat().st_size / 1024:.1f}KB ssim2={score:.1f}")
        except Exception as e:
            print(f" -> ERROR: {e}")


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------


def _format_date_heading(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _format_short_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}"


def _format_other_slots(
    all_slots: list[dict], current_date: str, current_period: str
) -> str | None:
    others = []
    for slot in all_slots:
        if slot["date"] == current_date and slot["period"] == current_period:
            continue
        same_day = slot["date"] == current_date
        slot_name = (
            "daytime (12:00–23:59)"
            if slot["period"] == "day"
            else "nighttime (23:00–07:00)"
        )
        if same_day:
            label = slot_name
        else:
            label = f"{_format_short_date(slot['date'])} {slot_name}"
        if slot.get("location_name"):
            label += f" @ {slot['location_name']}"
        others.append(label)
    if not others:
        return None
    return "Also playing " + " · ".join(others)


def render_output_html(
    title: str,
    ordered_sections: list[dict],
    assignments: dict[str, list[dict]],
    locations: dict[str, dict],
) -> str:
    def esc(text: str | None) -> str:
        return html.escape(text or "")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('  <meta charset="UTF-8">')
    parts.append(
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">'
    )
    parts.append(f"  <title>{esc(title)}</title>")
    parts.append("  <style>")
    parts.append("""
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; line-height: 1.5; max-width: 960px; margin: 40px auto; padding: 0 24px; color: #111; background: #fff; }
    h1 { margin-bottom: 32px; font-size: 2em; position: sticky; top: 0; background: #fff; z-index: 30; padding: 12px 0 8px; border-bottom: 2px solid #222; }
    section.date-section { margin-bottom: 48px; }
    h2 { position: sticky; top: 68px; background: #fff; z-index: 20; padding: 10px 0 8px; margin-bottom: 8px; font-size: 1.5em; border-bottom: 1px solid #ccc; }
    h3.period-heading { position: sticky; top: 122px; background: #fff; z-index: 10; padding: 8px 0 6px; margin: 24px 0 12px; font-size: 1.15em; color: #333; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: none; }
    .fade-after::after { content: ''; position: absolute; left: 0; right: 0; top: 100%; height: 36px; background: linear-gradient(to bottom, rgba(255,255,255,1) 0%, rgba(255,255,255,0.9) 20%, rgba(255,255,255,0.75) 35%, rgba(255,255,255,0.5) 55%, rgba(255,255,255,0.15) 78%, rgba(255,255,255,0) 100%); pointer-events: none; opacity: 0; transition: opacity 0.15s; }
    .fade-after.stuck::after { opacity: 1; }
    h4.location-heading { position: sticky; top: 152px; background: #fff; z-index: 10; font-size: 1em; padding: 6px 0 4px; margin: 16px 0 8px; color: #555; border-bottom: 1px solid #eee; }
    h4.location-heading small { font-weight: normal; color: #999; }
    ul.artist-list { list-style: none; padding: 0; margin: 0; }
    li.artist-item { display: flex; align-items: center; gap: 16px; padding: 12px; margin-bottom: 8px; background: #f9f9f9; border-radius: 8px; border: 1px solid #eee; }
    .artist-photo { width: 120px; height: 120px; object-fit: cover; border-radius: 6px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .photo-placeholder { width: 120px; height: 120px; flex-shrink: 0; }
    .artist-info { flex: 1; min-width: 0; }
    .artist-name { font-weight: 700; font-size: 1.15em; display: block; margin-bottom: 3px; }
    .artist-schedule { color: #888; font-size: 0.85em; display: block; margin-bottom: 6px; }
    .links { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; }
    .links a { display: inline-flex; align-items: center; gap: 5px; text-decoration: none; color: #555; font-size: 0.72em; padding: 3px 0; min-width: 72px; font-variant-numeric: tabular-nums; }
    .links a:hover { color: #111; }
    .links a svg { flex-shrink: 0; }
    .missing { color: #aaa; font-size: 0.8em; }
    """)
    parts.append("  </style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append(f"  <h1>{esc(title)}</h1>")

    SVG_IG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16.677 8.4615C16.5682 8.18175 16.4385 7.9815 16.2285 7.7715C16.0185 7.5615 15.819 7.43175 15.5385 7.323C15.327 7.24125 15.0097 7.143 14.4247 7.11675C13.7917 7.08825 13.602 7.0815 12 7.0815C10.398 7.0815 10.2083 7.0875 9.57525 7.11675C8.99025 7.14375 8.67225 7.24125 8.4615 7.323C8.18175 7.43175 7.9815 7.56225 7.7715 7.77225C7.5615 7.98225 7.43175 8.18175 7.323 8.46225C7.2405 8.67375 7.143 8.99175 7.11675 9.576C7.0875 10.209 7.0815 10.3988 7.0815 12.0008C7.0815 13.6028 7.0875 13.7925 7.11675 14.4255C7.14375 15.0105 7.24125 15.3285 7.323 15.5393C7.43175 15.819 7.5615 16.0193 7.7715 16.2293C7.9815 16.4393 8.181 16.569 8.4615 16.6778C8.673 16.7603 8.99025 16.8578 9.57525 16.884C10.2083 16.9125 10.3973 16.9193 12 16.9193C13.6027 16.9193 13.7917 16.9133 14.4247 16.884C15.0097 16.857 15.3278 16.7595 15.5385 16.6778C15.8183 16.569 16.0185 16.4393 16.2285 16.2293C16.4385 16.0193 16.5682 15.8198 16.677 15.5393C16.7595 15.3278 16.857 15.0105 16.8832 14.4255C16.9125 13.7925 16.9185 13.6028 16.9185 12.0008C16.9185 10.3988 16.9125 10.209 16.8832 9.576C16.8562 8.991 16.7587 8.673 16.677 8.46225V8.4615ZM12 15.081C10.2983 15.081 8.919 13.7018 8.919 12C8.919 10.2983 10.2983 8.919 12 8.919C13.7017 8.919 15.081 10.2983 15.081 12C15.081 13.7018 13.7017 15.081 12 15.081ZM15.2025 9.51675C14.805 9.51675 14.4825 9.19425 14.4825 8.79675C14.4825 8.39925 14.805 8.07675 15.2025 8.07675C15.6 8.07675 15.9225 8.39925 15.9225 8.79675C15.9225 9.19425 15.6 9.51675 15.2025 9.51675ZM16.5 1.49925H7.5C4.1865 1.5 1.5 4.1865 1.5 7.5V16.5C1.5 19.8135 4.1865 22.5 7.5 22.5H16.5C19.8135 22.5 22.5 19.8135 22.5 16.5V7.5C22.5 4.1865 19.8135 1.5 16.5 1.5V1.49925ZM17.964 14.4728C17.9347 15.1118 17.8335 15.5475 17.685 15.9293C17.532 16.3238 17.3265 16.6583 16.9928 16.992C16.659 17.3258 16.3245 17.5305 15.93 17.6843C15.5483 17.8328 15.1125 17.934 14.4735 17.9633C13.8338 17.9925 13.629 17.9993 12 17.9993C10.371 17.9993 10.1662 17.9925 9.5265 17.9633C8.8875 17.934 8.45175 17.8328 8.07 17.6843C7.6755 17.5313 7.341 17.3258 7.00725 16.992C6.6735 16.6583 6.46875 16.3238 6.315 15.9293C6.1665 15.5475 6.06525 15.1118 6.036 14.4728C6.00675 13.833 6 13.6283 6 11.9993C6 10.3703 6.00675 10.1655 6.036 9.52575C6.06525 8.88675 6.1665 8.451 6.315 8.06925C6.468 7.67475 6.6735 7.34025 7.00725 7.0065C7.341 6.67275 7.6755 6.468 8.07 6.31425C8.45175 6.16575 8.8875 6.0645 9.5265 6.03525C10.1662 6.006 10.371 5.99925 12 5.99925C13.629 5.99925 13.8338 6.006 14.4735 6.03525C15.1125 6.0645 15.5483 6.16575 15.93 6.31425C16.3245 6.46725 16.659 6.67275 16.9928 7.0065C17.3265 7.34025 17.5312 7.67475 17.685 8.06925C17.8335 8.451 17.9347 8.88675 17.964 9.52575C17.9932 10.1655 18 10.3703 18 11.9993C18 13.6283 17.9932 13.833 17.964 14.4728ZM14.0002 11.9993C14.0002 13.104 13.1047 13.9995 12 13.9995C10.8953 13.9995 9.99975 13.104 9.99975 11.9993C9.99975 10.8945 10.8953 9.999 12 9.999C13.1047 9.999 14.0002 10.8945 14.0002 11.9993Z"/></svg>'
    SVG_SC = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 1.5H7.5C4.1865 1.5 1.5 4.1865 1.5 7.5V16.5C1.5 19.8135 4.1865 22.5 7.5 22.5H16.5C19.8135 22.5 22.5 19.8135 22.5 16.5V7.5C22.5 4.1865 19.8135 1.5 16.5 1.5ZM5.66775 13.497C5.66775 13.5765 5.60325 13.6417 5.523 13.6417C5.44275 13.6417 5.37825 13.5773 5.37825 13.497C5.2425 12.786 5.17875 12.195 5.3655 11.4847C5.373 11.403 5.442 11.3407 5.52375 11.3407C5.6055 11.3407 5.6745 11.403 5.682 11.4847C5.90775 12.1987 5.82675 12.7755 5.66775 13.497ZM6.61425 14.0205C6.6 14.178 6.3255 14.1795 6.3105 14.0205C6.17325 12.9622 6.1155 12.0255 6.3045 10.9718C6.315 10.893 6.3825 10.8345 6.462 10.8345C6.5415 10.8345 6.609 10.893 6.6195 10.9718C6.83775 12.0398 6.76575 12.9473 6.6135 14.0205H6.61425ZM7.53525 14.1608C7.53525 14.241 7.47075 14.3055 7.3905 14.3055C7.31025 14.3055 7.24575 14.241 7.24575 14.1608C7.12725 13.1145 7.0215 12.162 7.24575 11.1255C7.25325 11.0468 7.31925 10.9875 7.398 10.9875C7.47675 10.9875 7.54275 11.0475 7.55025 11.1255C7.78725 12.1762 7.67925 13.0972 7.5345 14.1608H7.53525ZM8.48625 14.1547C8.47875 14.2335 8.41275 14.2935 8.334 14.2935C8.25525 14.2935 8.18925 14.2335 8.18175 14.1547C8.01 12.8182 8.01 11.4653 8.18175 10.1295C8.18175 10.044 8.25075 9.975 8.33625 9.975C8.42175 9.975 8.49075 10.044 8.49075 10.1295C8.67375 11.4653 8.67225 12.8197 8.48625 14.1547ZM9.417 14.1473C9.417 14.2298 9.35025 14.2965 9.26775 14.2965C9.18525 14.2965 9.1185 14.2298 9.1185 14.1473C8.90475 12.6323 8.97375 11.2133 9.1185 9.69525C9.1185 9.61275 9.18525 9.54525 9.26775 9.54525C9.35025 9.54525 9.417 9.612 9.417 9.69525C9.5685 11.2305 9.63675 12.6075 9.417 14.1473ZM10.3492 14.1517C10.3387 14.229 10.2727 14.2867 10.1947 14.2867C10.1168 14.2867 10.0507 14.229 10.0402 14.1517C9.8535 12.6705 9.8955 11.3243 10.0402 9.84C10.0402 9.75375 10.11 9.684 10.1962 9.684C10.2825 9.684 10.3523 9.75375 10.3523 9.84C10.5128 11.3377 10.5608 12.6487 10.3492 14.1517ZM11.28 14.1458C11.28 14.2283 11.2133 14.2943 11.1315 14.2943C11.0498 14.2943 10.9823 14.2275 10.9823 14.1458C10.7595 12.4867 10.8577 10.9253 10.9823 9.26175C10.9823 9.17925 11.049 9.1125 11.1315 9.1125C11.214 9.1125 11.28 9.17925 11.28 9.26175C11.4187 10.9395 11.52 12.4703 11.28 14.1458ZM16.836 14.2815H12.2355C12.0247 14.28 11.8545 14.1082 11.8553 13.8967V8.946C11.8463 8.7705 11.9482 8.60775 12.1095 8.53875C12.1095 8.53875 12.5317 8.25 13.422 8.25C13.9688 8.24775 14.5058 8.39475 14.976 8.67525C15.7163 9.108 16.2405 9.83175 16.4213 10.6702C16.5803 10.6245 16.7445 10.6012 16.9095 10.602C17.4067 10.602 17.883 10.8038 18.2295 11.1607C18.576 11.5177 18.7628 12 18.7477 12.4972C18.7185 13.509 17.8418 14.2822 16.8353 14.2822L16.836 14.2815Z"/></svg>'
    SVG_SP = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16.122 10.3538C16.389 10.512 16.4767 10.8562 16.3185 11.1225C16.161 11.3895 15.8152 11.4773 15.5497 11.319C13.6163 10.1707 10.4257 10.065 8.57925 10.6252C8.283 10.7153 7.9695 10.548 7.8795 10.251C7.7895 9.95475 7.95675 9.64125 8.25375 9.55125C10.3733 8.90775 13.8967 9.03225 16.1227 10.3538H16.122ZM14.628 14.1398C13.0462 13.173 11.0858 12.948 8.8005 13.4708C8.59875 13.5165 8.4735 13.7175 8.51925 13.9185C8.565 14.1195 8.766 14.2463 8.967 14.1998C11.0557 13.7228 12.8287 13.9178 14.238 14.778C14.4142 14.886 14.6453 14.8305 14.7525 14.6542C14.8605 14.478 14.805 14.2478 14.6287 14.1398H14.628ZM15.3323 12.3773C13.4805 11.2388 10.7715 10.9185 8.592 11.58C8.34525 11.655 8.20575 11.916 8.28 12.1635C8.355 12.4103 8.616 12.5498 8.8635 12.4755C10.7708 11.8965 13.2293 12.1838 14.8425 13.1745C15.0623 13.3105 15.3503 13.2405 15.486 13.02C15.6218 12.8003 15.552 12.5122 15.3323 12.3773ZM22.5007 7.5V16.5C22.5007 19.8135 19.8142 22.5 16.5007 22.5H7.5C4.1865 22.5 1.5 19.8135 1.5 16.5V7.5C1.5 4.1865 4.1865 1.5 7.5 1.5H16.5C19.8135 1.5 22.5 4.1865 22.5 7.5H22.5007ZM18.0007 12C18.0007 8.6865 15.315 6 12.0007 6C8.68725 6 6.00075 8.68575 6.00075 12C6.00075 15.3142 8.68725 18 12.0007 18C15.3142 18 18.0007 15.3135 18.0007 12Z"/></svg>'
    SVG_LT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5,1.5H7.5C4.187,1.5,1.5,4.187,1.5,7.5v9c0,3.313,2.687,6,6,6h9c3.313,0,6-2.687,6-6V7.5c0-3.313-2.687-6-6-6Z"/><path d="M6.5,9.82h3.326l-2.364-2.254,1.308-1.345,2.254,2.317v-3.294h1.954v3.294l2.254-2.312,1.307,1.34-2.363,2.249h3.325v1.86h-3.343l2.379,2.312-1.304,1.313-3.231-3.247-3.231,3.247-1.308-1.308,2.38-2.312h-3.341v-1.86ZM11.018,14.343h1.954v4.413h-1.954v-4.413Z" fill="#fff"/></svg>'
    SVG_YT = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M10.8 10.2L13.9177 12 10.8 13.8V10.2ZM22.5 7.5V16.5C22.5 19.8135 19.8135 22.5 16.5 22.5H7.5C4.1865 22.5 1.5 19.8135 1.5 16.5V7.5C1.5 4.1865 4.1865 1.5 7.5 1.5H16.5C19.8135 1.5 22.5 4.1865 22.5 7.5ZM18 12C18 12 18 10.0478 17.7495 9.11175C17.6115 8.59575 17.205 8.1885 16.6882 8.0505C15.7522 7.8 12 7.8 12 7.8C12 7.8 8.24775 7.8 7.31175 8.0505C6.79575 8.1885 6.3885 8.595 6.2505 9.11175C6 10.0478 6 12 6 12C6 12 6 13.9522 6.2505 14.8883C6.3885 15.4043 6.795 15.8115 7.31175 15.9495C8.24775 16.2 12 16.2 12 16.2C12 16.2 15.7522 16.2 16.6882 15.9495C17.2042 15.8115 17.6115 15.405 17.7495 14.8883C18 13.9522 18 12 18 12Z"/></svg>'

    def _link(href: str, svg: str, label: str = "") -> str:
        txt = f"{svg} {esc(label)}" if label else svg
        return f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer" title="{esc(label)}">{txt}</a>'

    def render_artist_card(a: dict, cur_date: str, cur_period: str) -> None:
        name = a.get("name") or ""
        photo_local = a.get("photo_local")
        ig = a.get("instagram")
        sc = a.get("soundcloud")
        sp = a.get("spotify")
        lt = a.get("linktree")
        yt = a.get("youtube")
        ig_f = format_followers(a.get("ig_followers"))
        sc_f = format_followers(a.get("sc_followers"))
        sp_l = format_followers(a.get("spotify_listeners"))
        schedule = _format_other_slots(a.get("all_slots", []), cur_date, cur_period)

        parts.append('      <li class="artist-item">')
        if photo_local:
            parts.append(
                f'        <img class="artist-photo" src="photos/{esc(photo_local)}" alt="{esc(name)}" width="120" height="120" loading="lazy">'
            )
        else:
            parts.append('        <div class="photo-placeholder"></div>')
        parts.append('        <div class="artist-info">')
        parts.append(f'        <span class="artist-name">{esc(name)}</span>')
        if schedule:
            parts.append(
                f'        <span class="artist-schedule">{esc(schedule)}</span>'
            )
        parts.append('        <div class="links">')
        if ig:
            parts.append(f"          {_link(ig, SVG_IG, ig_f or '')}")
        if sc:
            parts.append(f"          {_link(sc, SVG_SC, sc_f or '')}")
        if sp:
            parts.append(f"          {_link(sp, SVG_SP, sp_l or '')}")
        if lt:
            parts.append(f"          {_link(lt, SVG_LT)}")
        if yt:
            parts.append(f"          {_link(yt, SVG_YT)}")
        if not ig and not sc and not sp and not lt and not yt:
            parts.append('          <span class="missing">No links</span>')
        parts.append("        </div>")
        parts.append("        </div>")
        parts.append("      </li>")

    dates_seen: list[str] = []
    sections_by_date: dict[str, list[dict]] = {}
    for sec in ordered_sections:
        sections_by_date.setdefault(sec["date"], []).append(sec)
        if sec["date"] not in dates_seen:
            dates_seen.append(sec["date"])

    for date_str in dates_seen:
        date_heading = _format_date_heading(date_str)
        parts.append('  <section class="date-section">')
        parts.append(f"    <h2>{esc(date_heading)}</h2>")

        for sec in sections_by_date[date_str]:
            timestamp = sec["key"]
            is_night = sec["period"] == "night"
            time_range = "23:00 – 07:00" if is_night else "12:00 – 23:59"
            period_label = f"{sec['period'].capitalize()} ({time_range})"
            artists = assignments.get(timestamp, [])

            h3_cls = "period-heading" if is_night else "period-heading fade-after"
            parts.append(f'    <h3 class="{h3_cls}">{esc(period_label)}</h3>')

            if not artists:
                parts.append("    <p>No artists found.</p>")
                continue

            if is_night:
                by_loc: dict[str | None, list[dict]] = {}
                for a in artists:
                    by_loc.setdefault(a.get("location_id"), []).append(a)

                for loc_id, loc_artists in by_loc.items():
                    loc = locations.get(loc_id) if loc_id else None
                    if loc:
                        desc = (
                            f" <small>{esc(loc['description'])}</small>"
                            if loc.get("description")
                            else ""
                        )
                        parts.append(
                            f'    <h4 class="location-heading fade-after">{esc(loc["name"])}{desc}</h4>'
                        )
                    parts.append('    <ul class="artist-list">')
                    for a in loc_artists:
                        render_artist_card(a, sec["date"], sec["period"])
                    parts.append("    </ul>")
            else:
                parts.append('    <ul class="artist-list">')
                for a in artists:
                    render_artist_card(a, sec["date"], sec["period"])
                parts.append("    </ul>")

        parts.append("  </section>")

    parts.append("  <script>")
    parts.append("""document.querySelectorAll('.fade-after').forEach(el => {
      const top = parseFloat(getComputedStyle(el).top) || 0;
      const s = document.createElement('div');
      s.style.cssText = 'height:1px;width:0;pointer-events:none;visibility:hidden;margin-bottom:-1px;position:relative;top:-' + top + 'px';
      el.parentNode.insertBefore(s, el);
      new IntersectionObserver(([e]) => {
        el.classList.toggle('stuck', e.intersectionRatio === 0);
      }, {threshold: 0}).observe(s);
    });""")
    parts.append("  </script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape the Stone Techno lineup and produce a clean HTML page. "
        "Data is cached in lineup.db — images and follower counts are only fetched once."
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
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "lineup.html"

    db = sqlite3.connect(str(DB_PATH))
    init_db(db)

    if not args.render_only:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()

            print(f"Fetching {args.url} ...")
            parsed = scrape_lineup(ctx, args.url)
            print(
                f"Parsed {len(parsed['artists'])} artists across {len(parsed['sections'])} sections, {len(parsed['locations'])} locations."
            )
            upsert_lineup(db, parsed)
            apply_overrides(db)

            if args.refresh_followers:
                db.execute(
                    "UPDATE artists SET ig_followers = NULL, sc_followers = NULL, spotify_listeners = NULL"
                )
                db.commit()

            if not args.no_followers:
                fetch_all_sc(ctx, db)
                fetch_all_ig(ctx, db)
                fetch_all_spotify(ctx, db)

            browser.close()
    else:
        print("Rendering from database (no scraping) ...")
        apply_overrides(db)

    if args.refresh_photos:
        db.execute("UPDATE artists SET photo_local = NULL")
        db.commit()

    if not args.no_photos:
        process_artist_photos(db)

    ordered_sections = load_sections_from_db(db)
    all_locations = load_locations_from_db(db)
    all_assignments = load_assignments_from_db(db)
    output_html = render_output_html(
        args.title, ordered_sections, all_assignments, all_locations
    )

    db.close()

    output_path.write_text(output_html, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
