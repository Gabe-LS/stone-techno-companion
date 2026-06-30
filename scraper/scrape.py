from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from .db import get_artist, get_artists_without_ra, get_missing, update_artist_field

logger = logging.getLogger(__name__)


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
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host != "youtube.com" and not host.endswith(".youtube.com"):
        return False
    path = parsed.path.rstrip("/").lower()
    if any(path.startswith(p) for p in ("/c/", "/channel/", "/user/", "/@")):
        return True
    return False


def is_sc_profile(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host == "on.soundcloud.com":
        return False
    if host != "soundcloud.com" and not host.endswith(".soundcloud.com"):
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
# Lineup page
# ---------------------------------------------------------------------------


def _parse_timestamp_key(ts_key: str) -> tuple[str, str] | None:
    if not ts_key or ts_key == "0":
        return None
    suffix = ts_key[-1]
    if suffix not in {"d", "n"}:
        return None
    raw = ts_key[:-1]
    if not raw.isdigit():
        return None
    try:
        dt = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
    return dt.strftime("%Y-%m-%d"), "day" if suffix == "d" else "night"


def scrape_lineup(ctx: BrowserContext, url: str) -> dict:
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("li.tab-list__list-item.lineup-name", timeout=30000)
        source_html = page.content()
    finally:
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
# SoundCloud
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
    page = None
    try:
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        soup = BeautifulSoup(page.content(), "html.parser")
        page.close()

        link = soup.select_one('a[title*="followers"]')
        if link:
            m = re.match(r"([\d,.]+[KMB]?)", link.get("title", ""), re.IGNORECASE)
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
        logger.warning("Failed to fetch SoundCloud profile %s", url, exc_info=True)
        if page:
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

        if profile["instagram"] and not artist["instagram"]:
            update_artist_field(db, oid, "instagram", profile["instagram"])
        if profile["spotify"] and not artist["spotify"]:
            update_artist_field(db, oid, "spotify", profile["spotify"])
        if profile["linktree"] and not artist["linktree"]:
            update_artist_field(db, oid, "linktree", profile["linktree"])
        if profile["youtube"] and not artist["youtube"]:
            update_artist_field(db, oid, "youtube", profile["youtube"])

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

    if ig_corrections:
        print(f"\nFound {len(ig_corrections)} different IG links from SoundCloud:")
        for oid, new_ig in ig_corrections:
            artist = get_artist(db, oid)
            if artist["ig_followers"] is None:
                db.execute(
                    "UPDATE artists SET instagram = ?, ig_followers = NULL WHERE id = ?",
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
# Instagram
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
    page = None
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
            soup = BeautifulSoup(page.content(), "html.parser")
            meta = soup.find("meta", attrs={"property": "og:description"})
            if meta:
                m = re.match(
                    r"([\d,.]+[KMB]?)\s+Follower",
                    meta.get("content", ""),
                    re.IGNORECASE,
                )
                if m:
                    result["followers"] = parse_follower_count(m.group(1))

        page.close()
    except Exception:
        logger.warning("Failed to fetch Instagram profile %s", url, exc_info=True)
        if page:
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
# Spotify
# ---------------------------------------------------------------------------


def fetch_spotify_listeners(ctx: BrowserContext, url: str) -> int | None:
    page = None
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
        logger.warning("Failed to fetch Spotify profile %s", url, exc_info=True)
        if page:
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
# Resident Advisor
# ---------------------------------------------------------------------------

_RA_SEARCH_QUERY = """
query SearchArtists($term: String!) {
    search(searchTerm: $term, limit: 5, indices: [ARTIST]) {
        id
        value
        contentUrl
        searchType
        score
    }
}
"""

_RA_ARTIST_QUERY = """
query GetArtist($slug: String!) {
    artist(slug: $slug) {
        id
        name
        followerCount
        contentUrl
        facebook
        instagram
        twitter
        soundcloud
        discogs
        bandcamp
        website
        biography { blurb content }
    }
}
"""


def _ra_gql(page, query: str, variables: dict) -> dict:
    return page.evaluate(
        """async ([query, variables]) => {
        const resp = await fetch('https://ra.co/graphql', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Referer': 'https://ra.co/',
            },
            body: JSON.stringify({ query, variables })
        });
        return await resp.json();
    }""",
        [query, variables],
    )


def _normalize_social_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().rstrip("/").lower()
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "").replace("m.", "")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def _ra_match_score(ra_profile: dict, db_artist) -> int:
    score = 0
    for field in ("soundcloud", "instagram"):
        ra_val = _normalize_social_url(ra_profile.get(field))
        db_val = _normalize_social_url(db_artist[field])
        if ra_val and db_val:
            if ra_val == db_val:
                score += 3
            else:
                score -= 2
    ra_name = (ra_profile.get("name") or "").strip().lower()
    db_name = (db_artist["name"] or "").strip().lower()
    if ra_name == db_name:
        score += 2
    elif ra_name and db_name and (ra_name in db_name or db_name in ra_name):
        score += 1
    return score


def _clean_ra_bio(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n\n+", text)
    return "\n\n".join(
        " ".join(line.strip() for line in p.strip().splitlines()) for p in paragraphs
    ).strip()


def fetch_ra_profile(page, artist_name: str, db_artist) -> dict | None:
    """Search RA for an artist, validate via social link matching, return profile."""
    result = _ra_gql(page, _RA_SEARCH_QUERY, {"term": artist_name})
    hits = (result.get("data") or {}).get("search") or []
    if not hits:
        return None

    for hit in hits:
        content_url = hit.get("contentUrl", "")
        if not content_url.startswith("/dj/"):
            continue
        slug = content_url.replace("/dj/", "").strip("/")

        profile_result = _ra_gql(page, _RA_ARTIST_QUERY, {"slug": slug})
        profile = (profile_result.get("data") or {}).get("artist")
        if not profile:
            continue

        score = _ra_match_score(profile, db_artist)

        has_social_overlap = any(
            _normalize_social_url(profile.get(f))
            and _normalize_social_url(db_artist[f])
            for f in ("soundcloud", "instagram")
        )
        if has_social_overlap and score >= 3:
            bio = profile.get("biography") or {}
            return {
                "ra_url": f"https://ra.co{profile['contentUrl']}",
                "ra_followers": profile.get("followerCount"),
                "ra_bio": _clean_ra_bio(bio.get("content") or ""),
            }
        if not has_social_overlap and score >= 2:
            bio = profile.get("biography") or {}
            return {
                "ra_url": f"https://ra.co{profile['contentUrl']}",
                "ra_followers": profile.get("followerCount"),
                "ra_bio": _clean_ra_bio(bio.get("content") or ""),
            }

    return None


def fetch_all_ra(ctx: BrowserContext, db: sqlite3.Connection) -> None:
    missing = get_artists_without_ra(db)
    if not missing:
        return

    total = len(missing)
    print(f"Fetching {total} Resident Advisor profiles ...")

    page = ctx.new_page()
    try:
        page.goto("https://ra.co", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        matched = 0
        for i, artist in enumerate(missing, 1):
            print(f"  [{i}/{total}] RA: {artist['name']}", end="", flush=True)

            profile = fetch_ra_profile(page, artist["name"], artist)

            if profile:
                matched += 1
                update_artist_field(db, artist["id"], "ra", profile["ra_url"])
                update_artist_field(
                    db, artist["id"], "ra_followers", profile["ra_followers"]
                )
                bio = profile["ra_bio"].strip()
                if bio:
                    update_artist_field(db, artist["id"], "ra_bio", bio)
                fc = f"{profile['ra_followers']:,}" if profile["ra_followers"] else "?"
                print(f" -> {fc} followers ({profile['ra_url']})")
            else:
                update_artist_field(db, artist["id"], "ra", "")
                update_artist_field(db, artist["id"], "ra_followers", 0)
                print(" -> not found")
    finally:
        page.close()

    print(f"Matched {matched}/{total} artists on Resident Advisor")
