# Stone Techno Companion

Festival lineup scraper + enrichment pipeline + static site generator with a real-time favorites API.

## Quick Reference

```bash
# Full pipeline (scrape + enrich + photos + generate HTML)
python stone_techno_companion.py

# Regenerate HTML only (fast — no network, no scraping)
python stone_techno_companion.py --render-only --no-photos

# Deploy content to production (rsync, no container restart needed)
python stone_techno_companion.py --render-only --deploy
```

## System Dependencies

These are not pip-installable and must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — used for perceptual quality targeting during AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]` (server).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML
2. `lineup.db` (SQLite) caches all scraped data — follower counts and photos are only fetched once unless `--refresh-*` flags are used
3. `scraper/overrides.toml` provides manual corrections applied after scraping, before enrichment
4. Output is a single HTML file (`output/lineup.html`) + AVIF photos (`output/photos/`)

### Key files

| File | Role |
|---|---|
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify scrapers |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — includes all CSS, JS, modals, hearts logic |
| `server/api.py` | FastAPI app — favorites API + WebSocket real-time sync |

### Two deploy paths

- **Content** (HTML + photos): `--deploy` flag rsyncs to VPS static dir. No container restart — files are volume-mounted.
- **Server code**: push to `main` with changes in `server/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build`.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite cache of artists, sections, follower counts
- `output/lineup.html` — generated page (~2800 lines)
- `output/photos/*.avif` — processed artist photos (~100 files)

These are regenerable. The source of truth is the live website + `overrides.toml`.

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. There is no separate HTML/CSS/JS file to edit. After changes, regenerate with `--render-only --no-photos` and open `output/lineup.html`.

## Server

The FastAPI server (`server/api.py`) serves static files and provides the favorites API. Sessions are identified by 128-bit URL-safe tokens (`secrets.token_urlsafe(16)`): `session_id` for read-write, `share_token` for read-only. Cross-device sync uses ephemeral 6-digit PINs (5-min TTL, single-use, one active per session). Picks are stored as JSON arrays in SQLite with atomic add/remove via `json_each`/`json_group_array`. Real-time sync uses WebSocket at `/ws/{code}`.

Production: Docker container on a DigitalOcean VPS behind Caddy (auto-TLS). Database at `server/data/hearts.db` is volume-mounted for persistence.
