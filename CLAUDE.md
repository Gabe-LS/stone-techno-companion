# Stone Techno Companion

Festival lineup scraper + enrichment pipeline + static site generator with a real-time favorites API and push notifications.

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

# Preview locally (required for fetch/API features — file:// won't work)
cd output && python3 -m http.server 8321
# Then open http://localhost:8321/lineup.html

# Run for a specific event
python stone_techno_companion.py --event-id stone-techno-2026 --event-name "Stone Techno 2026"

# Migrate old DB to new schema (one-time, creates backup)
python migrate_db.py
```

## Local Development

**Always preview via HTTP, never `file://`.** The page uses `fetch()` for lazy-loaded bios and API calls. Browsers block fetch from `file://` origins (CORS). Use `python3 -m http.server` from the `output/` directory, or any local server. The bio overlay, favorites sync, and push notifications all require HTTP.

## System Dependencies

These are not pip-installable and must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — used for perceptual quality targeting during AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server); `yt-dlp` (video discovery); `markdown` (bio rendering).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json + bios.json
2. `lineup.db` (SQLite, WAL mode, FK enforcement) is the single source of truth for all artist, schedule, location, and video data
3. `scraper/overrides.toml` provides manual corrections (artist links), editorial data (floor curators), and YouTube video overrides — applied as patches to the DB
4. `fetch_videos.py` discovers YouTube sets via yt-dlp and writes to the `videos` table in the DB
5. Output: `lineup.html` (~645 KB) + `bios.json` (~200 KB, lazy-loaded) + `timetable.json` + `photos/*.avif` + `thumbs/*.avif`

### Database schema

```
events          — id, name, url, start_date, end_date, timezone
artists         — id, name, photo_url, photo_local, socials, followers, ra_bio
locations       — id, event_id, name, color (RGB), description, about (markdown)
location_notes  — location_id, date, note (daily annotations like curators)
location_details — location_id, label, value, position (static key-value facts)
schedule        — artist_id, event_id, location_id, start_time, end_time, date, period, set_type
videos          — video_id, artist_id, title, url, views, duration, upload_date, position
```

Key design decisions:
- **Artists and videos are global** (shared across events). Schedule and locations are per-event.
- **`period` is a free-text tag** (day, night, afterhours, etc.), nullable for events without period splits
- **`set_type`** supports dj, live, hybrid, b2b, talk, or NULL
- **Floor colors** stored as RGB channels in `locations.color` (e.g. `"198, 249, 197"`), CSS generated dynamically at build time
- **SQLite pragmas**: `journal_mode=WAL` (concurrent reads), `foreign_keys=ON` (referential integrity)
- **All queries use `sqlite3.Row`** — dict-like access by column name, no positional indexing

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify/Resident Advisor scrapers |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries — all event-scoped |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. Markdown rendering for bios. SVG icons deduplicated via `<symbol>`/`<use>` sprite |
| `scraper/timetable_json.py` | Generates `timetable.json` mapping schedule slot UUIDs to set times (used by push notification scheduler and ICS endpoint) |
| `fetch_videos.py` | YouTube set discovery via yt-dlp — searches, selects top sets, downloads AVIF thumbnails. Writes to `videos` table in DB |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `migrate_db.py` | One-time migration script for old schema → new schema. Creates backup, migrates data, imports videos.json and floor curators |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push notification scheduler + ICS calendar export + `/bios.json` route |
| `server/static/sw.js` | Service worker — handles push events and notification click navigation |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |

### Two deploy paths

- **Content** (HTML + photos + thumbs + timetable.json + bios.json + sw.js + manifest.json): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build --force-recreate`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite database (artists, schedule, locations, videos, events)
- `lineup.db.bak` — backup created by migrate_db.py
- `output/lineup.html` — generated page (~645 KB)
- `output/bios.json` — artist bios + videos, lazy-loaded on first artist tap (~200 KB)
- `output/photos/*.avif` — processed artist photos (~100 files)
- `output/timetable.json` — slot UUID → set time mapping for push notifications
- `output/thumbs/*.avif` — YouTube video thumbnails (240px max, AVIF)

These are regenerable. The source of truth is the live website + `overrides.toml` + DB enrichment data.

## Timetable View

The page includes both a line-up list and a timetable grid, toggled via the command bar. The timetable appears automatically when artists have `start_time`/`end_time` data in `schedule`.

- **Desktop**: CSS grid with sticky floor headers and time labels
- **Mobile**: HTML `<table>` with native scroll (`overflow: auto` on single `tt-v-scroll` container); sticky `<thead>` for floor headers (no JS sync needed); `table-layout: fixed` with `--row-h` CSS variable for row height; grid lines via CSS `repeating-linear-gradient`; dynamic `--row-h` (10px or 14px) based on artist density per slot
- **B2B sets**: Multiple artists in the same time slot render as one card with per-artist hearts
- **Schedule**: Calendar icon on each card, server-synced via `/api/session/{code}/schedule/{slot_id}`
- **ICS export**: Button on each card — server endpoint `GET /ics/{slot_id}` serves `.ics` file with `Content-Type: text/calendar` for native iOS/Android calendar integration
- **Fake data**: `python seed_timetable.py` populates 5 day floors + 2 night floors with time slots
- **Hamburger menu**: mobile-only, shows/hides based on current view, preserves view in localStorage across reloads
- **Artist schedule notes**: every list-view card shows floor + time; artists playing multiple slots get an "Also" line with cross-references
- **Floor curators**: "curated by" / "hosted by" annotations below floor name pills, per-day per-floor. Data stored in `location_notes` table, populated from `[floor_curators]` section of `overrides.toml` (keyed as `"YYYY-MM-DD.location_id"`). Desktop uses `<span>` inside `.floor-header`; mobile uses `<span class="floor-curator">` inside `<th>`. The `.floor-header` div has `background: none !important` to prevent the floor color from bleeding onto the container — floor color is on `> span:first-child` only

### Design system

- **Colors**: CSS variables in `:root` — `--color-text`, `--color-bg`, `--color-surface`, `--color-surface-hover`, `--color-muted` (4.88:1 AA), `--color-muted-icon` (3.54:1), `--color-accent`, `--color-schedule`, `--color-border`, `--color-line-hour`, `--color-line-half`
- **Floor colors**: stored as RGB channels in `locations.color` in the DB. CSS generated dynamically at build time — cards use `rgba(R,G,B, 0.88)`, header pills use `rgb(R,G,B)`. Floors without a color fall back to `.floor-unknown` (gray)
- **Font scale**: 6 steps via variables — `--font-2xl` (2em) through `--font-xs` (0.75em/12px minimum). No text below 12px for accessibility
- **Shared tokens**: `--shadow-modal`, `--radius-card`, `--radius-modal`, `--transition-fast`, `--fade-gradient` — used consistently across components
- **Hover states**: all hover effects guarded with `@media (hover: hover)` to prevent sticky hover on touch devices
- **Contrast**: all text/icon colors pass WCAG 2.1 AA

Floor order is defined in `canonical_floor_order` in `render.py` (alphabetical).

## Resident Advisor Integration

RA profiles are discovered via GraphQL API (`ra.co/graphql`) — no HTML scraping. The pipeline searches by artist name, fetches the profile with social links, and validates matches by comparing SoundCloud/Instagram handles against the DB. Stored fields: `ra` (URL), `ra_followers` (integer), `ra_bio` (biography text). Bio text is cleaned at scrape time: `\r\n` normalized, hard wraps joined, booking/contact info stripped.

## YouTube Sets

`fetch_videos.py` discovers DJ sets on YouTube via yt-dlp and writes results to the `videos` table in the DB. Run separately from the main pipeline:

```bash
python fetch_videos.py
```

Selection algorithm: if 5+ videos with >= 5K views exist in the last 5 years, keep all. Otherwise expand to 15 years, starting at 50K view threshold and lowering by 10K until 5 videos found. Max 2 videos per channel. Videos are sorted by views descending.

Overrides in `scraper/overrides.toml`:
- `[youtube_names]` — search name aliases (e.g. `"Serge" = "Serge Clone"`)
- `[youtube_videos]` — forced video IDs, skips search entirely
- `[youtube_videos_add]` — extra video IDs appended after search (bypass all filters)

Thumbnails: `output/thumbs/*.avif` (240px max, pyvips lanczos3).

## Artist Bio Overlay

Clicking an artist's name or photo in the lineup opens a modal overlay with photo (128px desktop, 96px mobile), name, RA biography (rendered as markdown → HTML at build time, booking info stripped), and YouTube sets with thumbnails. Body scroll is locked via `position: fixed` on body while modal is open (works on iOS Safari). Bios are lazy-loaded from `bios.json` on first tap — fetched once and cached in memory. Falls back to name-only overlay if fetch fails (e.g. `file://` origin).

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. There is no separate HTML/CSS/JS file to edit. After changes:

```bash
python stone_techno_companion.py --render-only --no-photos
cd output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

Do not open via `file://` — fetch-based features (bios, API) will fail silently.

## HTML Standards

- All buttons have `type="button"`, `<nav>` wraps the command bar, `<main>` wraps content
- Interactive elements (artist names, photos, timetable blocks) have `tabindex="0" role="button"` and keyboard handlers
- Modals have `role="dialog"`, `aria-modal`, `aria-labelledby`; focus returns to trigger on close
- SVG sprite has `aria-hidden="true"`; images have meaningful `alt` text
- PWA meta tags: `apple-mobile-web-app-capable`, `theme-color`, `apple-mobile-web-app-title`

## Server

The FastAPI server (`server/api.py`) serves static files and provides the favorites + schedule API. Sessions are identified by 128-bit URL-safe tokens (`secrets.token_urlsafe(16)`): `session_id` for read-write, `share_token` for read-only. Cross-device sync uses ephemeral 6-digit PINs (5-min TTL, single-use, one active per session). Picks and schedule are stored as JSON arrays in SQLite with atomic add/remove via `json_each`/`json_group_array`. Real-time sync uses WebSocket at `/ws/{code}`. Schedule endpoints mirror picks: `POST/DELETE /api/session/{code}/schedule/{slot_id}`.

Static file routes: `/bios.json`, `/timetable.json`, `/manifest.json`, `/sw.js`, `/favicon.svg`, `/favicon.png` are served explicitly before the catch-all `/{path:path}` route (which serves `index.html`). New static JSON files must have an explicit route added to `api.py` or they'll be intercepted by the catch-all.

Production: Docker container on a DigitalOcean VPS behind Caddy (auto-TLS). Database at `server/data/hearts.db` is volume-mounted for persistence. VAPID keys for push stored in `.env` on the VPS.

## Push Notifications

See README for full push documentation (platform support, VAPID setup, API endpoints).

Implementation notes:

- **Scheduler**: background task in `api.py` runs every 60s, matches `timetable.json` slots against sessions' schedule arrays, sends via `pywebpush`
- **Dedup**: `sent_notifications` table tracks `(session_id, slot_id)` pairs. Pruned after 7 days. Dead subscriptions (HTTP 404/410) auto-removed on failed send
- **Re-sync on load**: client re-sends its push subscription on every page load to recover from DB purges or PWA reinstalls
- **iOS workaround**: notification click uses a Cache Storage flag to open on the timetable view (service worker can't access localStorage)

## Multi-Event Support

The DB supports multiple events via the `events` table. Artists and videos are global (shared across events). Schedule, locations, and location_notes are scoped per `event_id`. The CLI accepts `--event-id` and `--event-name` flags. Each event needs its own scraper module in `scraper/` — the scraper output format (`parsed` dict with `artists`, `sections`, `locations`, `assignments`) is the interface between event-specific scrapers and the generic DB/render pipeline.
