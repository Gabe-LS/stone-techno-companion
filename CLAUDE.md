# Stone Techno Companion

Multi-event festival companion tool: scraper + enrichment pipeline + static site generator with real-time favorites, push notifications, and cross-device sync.

## Quick Reference

```bash
# Full pipeline (scrape + enrich + photos + generate HTML)
python stone_techno_companion.py

# Regenerate HTML only (fast — no network, no scraping)
python stone_techno_companion.py --render-only --no-photos

# Fetch YouTube sets for all artists (separate step, ~50 min)
python fetch_videos.py

# Deploy content to production (rsync, no container restart needed)
python stone_techno_companion.py --render-only --deploy

# Preview locally (required — file:// won't work)
cd output && python3 -m http.server 8321
# Then open http://localhost:8321/lineup.html

# Run for a specific event
python stone_techno_companion.py --event-id stone-techno-2026 --event-name "Stone Techno" --event-edition "2026"

# Migrate old DB to new schema (one-time, creates backup)
python migrate_db.py
```

## Local Development

**Always preview via HTTP, never `file://`.** The page uses `fetch()` for lazy-loaded bios and API calls. Browsers block fetch from `file://` origins (CORS). Use `python3 -m http.server` from the `output/` directory.

Expected 404s when serving locally: `/manifest.json`, `/sw.js`, `/api/me` — these only exist on the production server (FastAPI). The page works fine without them locally.

## System Dependencies

Not pip-installable, must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — perceptual quality targeting for AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server); `yt-dlp` (video discovery); `markdown` (bio rendering).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json + bios.json
2. `lineup.db` (SQLite, WAL mode, FK enforcement) is the single source of truth — artists, links, sets, schedule, locations, events
3. `scraper/overrides.toml` provides manual corrections (artist links), editorial data (floor curators), and YouTube video overrides — applied as patches to the DB
4. `fetch_videos.py` discovers YouTube sets via yt-dlp and writes to the `artist_sets` table
5. Output: `lineup.html` (~650 KB) + `bios.json` (~200 KB, lazy-loaded) + `timetable.json` + `photos/*.avif` + `thumbs/*.avif`

### Database schema

```
events            — id, name, edition, source_url, website, start/end_date, timezone, address, lat/lng
artists           — id, name, photo_url, photo_file, bio (markdown)
artist_links      — artist_id, platform, url, follower_count, position
artist_sets       — id, artist_id, platform, url, title, view_count, duration_min, upload_date, position
locations         — id, event_id, name, color (RGB), about (markdown), address, lat/lng
location_notes    — location_id, date, note, position (daily annotations: curators, hosts)
location_details  — location_id, label, value, position (static key-value facts for popup)
schedule          — artist_id, event_id, location_id, start_time, end_time, date, period, set_type
```

Key design decisions:
- **Artists, artist_links, and artist_sets are global** — shared across events. Schedule and locations are per-event.
- **artist_links** normalizes all social platforms — adding a new platform (Mixcloud, Bandcamp) is just an INSERT, no schema change
- **artist_sets** normalizes all media sources — `platform` column distinguishes YouTube, SoundCloud, etc.
- **`period`** is a free-text tag (day, night, afterhours, etc.), nullable for events without period splits
- **`set_type`** supports dj, live, hybrid, b2b, talk, or NULL
- **`edition`** on events separates the event name ("Stone Techno") from the instance ("2026", "XV"). Page title is derived as `"{name} {edition} Companion"`
- **Floor colors** stored as RGB channels in `locations.color` (e.g. `"198, 249, 197"`), CSS generated dynamically at build time
- **Location notes** hold per-day annotations (curators, hosts) shown below floor pills. Static info like "Hosted by FOLD London" is stored as notes on all event dates, not in a description field
- **SQLite pragmas**: `journal_mode=WAL` (concurrent reads), `foreign_keys=ON` (referential integrity)
- **All queries use `sqlite3.Row`** — dict-like access by column name, no positional indexing
- **Schedule PK** is `(artist_id, event_id, start_time)` — safe for multi-event
- **Geo** on both events (single-venue) and locations (multi-venue like Dekmantel)

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify/Resident Advisor scrapers. Each event needs its own scraper module. |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries — all event-scoped |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. Markdown bio rendering. Dynamic floor color CSS. SVG icons via `<symbol>`/`<use>` sprite |
| `scraper/timetable_json.py` | Generates `timetable.json` — slot UUID → set time mapping for push scheduler and ICS endpoint. Reads timezone from events table. |
| `fetch_videos.py` | YouTube set discovery via yt-dlp. Writes to `artist_sets` table with `platform='youtube'`. |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `migrate_db.py` | One-time migration from any old schema version to current. Creates backup, migrates artists + links + sets + locations + notes. |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push scheduler + ICS export + static file routes (`/bios.json`, `/timetable.json`, etc.) |
| `server/static/sw.js` | Service worker — handles push events and notification click navigation |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |

### Two deploy paths

- **Content** (HTML + photos + thumbs + timetable.json + bios.json + sw.js + manifest.json): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build --force-recreate`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite database (all tables)
- `lineup.db.bak` — backup created by migrate_db.py
- `output/lineup.html` — generated page (~650 KB)
- `output/bios.json` — artist bios + sets, lazy-loaded on first artist tap (~200 KB)
- `output/photos/*.avif` — processed artist photos
- `output/timetable.json` — slot UUID → set time mapping for push notifications
- `output/thumbs/*.avif` — YouTube video thumbnails (240px max, AVIF)

These are regenerable. Source of truth is the live website + `overrides.toml` + DB enrichment data.

## Overrides

`scraper/overrides.toml` provides manual corrections. Applied after scraping, before follower fetching.

```toml
# Artist link overrides — field names match platform names in artist_links
[Amoral]
ra = "https://ra.co/dj/amoral"

[ROD]
soundcloud = "https://soundcloud.com/bennyrodrigues"
photo = "https://cdn.example.com/photo.webp"  # "photo" is aliased to photo_url

# YouTube search name aliases
[youtube_names]
"Serge" = "Serge Clone"

# Force specific video IDs (skips search)
[youtube_videos]
"Function" = ["abc123", "def456"]

# Append extra videos after search
[youtube_videos_add]
"Rødhåd" = ["ghi789"]

# Per-day per-floor annotations (shown below floor pill)
[floor_curators]
"2026-07-11.koksofenbatterie" = "curated by Freddy K"
"2026-07-12.werksschwimmbad" = "hosted by Clone Records"
```

Supported link fields: `instagram`, `soundcloud`, `spotify`, `linktree`, `youtube`, `ra`. Setting a field to `false` clears the URL and marks the count as fetched (0).

## Timetable View

Toggled via the command bar. Appears automatically when artists have `start_time`/`end_time` in `schedule`.

- **Desktop**: CSS grid with sticky floor headers and time labels
- **Mobile**: HTML `<table>` with native scroll, sticky `<thead>`, `table-layout: fixed`, dynamic `--row-h` (10px or 14px based on artist density)
- **Scroll position**: saved per view — switching between lineup and timetable restores where you were
- **Popup → Bio**: clicking artist name/photo in the timetable popup closes it and opens the bio modal
- **B2B sets**: multiple artists in same time slot render as one card with per-artist hearts
- **Schedule**: calendar icon on each card, server-synced via API
- **ICS export**: button on each card → server endpoint serves `.ics` file
- **Floor annotations**: "curated by" / "hosted by" from `location_notes` table, shown below floor pills per day
- **Artist schedule notes**: floor + time on every card, "Also" cross-references for multi-slot artists
- **Hamburger menu**: mobile-only, preserves view in localStorage across reloads

### Design system

- **Colors**: CSS variables in `:root` — `--color-text`, `--color-bg`, `--color-surface`, `--color-surface-hover`, `--color-muted`, `--color-muted-icon`, `--color-accent`, `--color-schedule`, `--color-border`
- **Floor colors**: from `locations.color` in DB (RGB channels). CSS generated at build time — cards `rgba(R,G,B, 0.88)`, pills `rgb(R,G,B)`. Unknown floors fall back to gray.
- **Font scale**: `--font-2xl` (2em) → `--font-xs` (0.75em/12px min). No text below 12px.
- **Shared tokens**: `--shadow-modal`, `--radius-card`, `--radius-modal`, `--transition-fast`, `--fade-gradient`
- **Hover**: all guarded with `@media (hover: hover)` — no sticky hover on touch
- **Contrast**: all text/icon colors pass WCAG 2.1 AA

## Artist Bio Overlay

Clicking artist name/photo opens modal with photo, name, biography (markdown → HTML at build time, booking info stripped), and sets with thumbnails. Bios lazy-loaded from `bios.json` on first tap — fetched once, cached in memory. Falls back to name-only overlay if fetch fails. Body scroll locked via `position: fixed` (iOS Safari compatible).

## HTML Standards

- `<nav>` wraps command bar, `<main>` wraps content
- All buttons have `type="button"`
- Interactive elements have `tabindex="0" role="button"` + keyboard handlers
- Modals: `role="dialog"`, `aria-modal`, `aria-labelledby`; focus returns to trigger on close; tab trapping; Escape closes
- SVG sprite: `aria-hidden="true"`; images have meaningful `alt` text
- PWA meta tags: `apple-mobile-web-app-capable`, `theme-color`, `apple-mobile-web-app-title`
- Social links rendered as a loop from `artist_links` — adding a platform requires only a new SVG icon + a mapping entry in `PLATFORM_ICONS`

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. No separate HTML/CSS/JS files.

```bash
python stone_techno_companion.py --render-only --no-photos
cd output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

## Server

FastAPI (`server/api.py`). Sessions via 128-bit URL-safe tokens. Cross-device sync via ephemeral 6-digit PINs (5-min TTL). Real-time sync via WebSocket. Atomic pick/schedule operations via `json_group_array`/`json_each`.

Static file routes (`/bios.json`, `/timetable.json`, `/manifest.json`, `/sw.js`, `/favicon.*`) are explicit endpoints before the catch-all `/{path:path}` (which serves `index.html`). New static files need an explicit route in `api.py`.

Production: Docker on DigitalOcean VPS behind Caddy (auto-TLS). DB at `server/data/hearts.db` volume-mounted. VAPID keys in `.env`.

## Push Notifications

- **Scheduler**: background task runs every 60s, matches `timetable.json` slots against sessions' schedule, sends via `pywebpush`
- **Dedup**: `sent_notifications` table, pruned after 7 days. Dead subscriptions auto-removed.
- **Re-sync on load**: client re-sends push subscription to recover from DB purges
- **iOS**: Cache Storage flag for notification click navigation (service worker can't access localStorage)

## Multi-Event Support

The DB supports multiple events via the `events` table. Artists and artist_links/artist_sets are global (shared). Schedule, locations, location_notes, and location_details are scoped per `event_id`. CLI flags: `--event-id`, `--event-name`, `--event-edition`. Each event needs its own scraper module — the scraper output format (`parsed` dict with `artists`, `sections`, `locations`, `assignments`) is the interface.
