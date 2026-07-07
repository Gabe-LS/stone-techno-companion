# Stone Techno Companion

Multi-event festival companion tool: scraper + enrichment pipeline + static site generator with real-time favorites, push notifications, and cross-device sync.

## Quick Reference

```bash
# Full pipeline (scrape + enrich + photos + generate HTML)
python pipeline/stone_techno_companion.py

# Regenerate HTML only (fast — no network, no scraping)
python pipeline/stone_techno_companion.py --render-only --no-photos

# Fetch YouTube sets for all artists (separate step, ~50 min)
python pipeline/fetch_videos.py

# Deploy content to production (rsync, no container restart needed)
python pipeline/stone_techno_companion.py --render-only --deploy

# Preview locally (required — file:// won't work)
cd pipeline/output && python3 -m http.server 8321
# Then open http://localhost:8321/lineup.html

# Run for a specific event
python pipeline/stone_techno_companion.py --event-id stone-techno-2026 --event-name "Stone Techno" --event-edition "2026"

# Run full server locally (lineup + chat)
cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile certs/localhost+1-key.pem --ssl-certfile certs/localhost+1.pem
# Open https://localhost:64728/line-up and https://localhost:64728/chat

# Run tests
python -m pytest tests/ -v
```

## Conventions

- **No emojis** — anywhere: code, logs, comments, commit messages, generated files.
- **Every action logs via `dbg()`** — this is a hard rule for new frontend code, not just a description of the existing calls. Any user-triggered or automated action must emit a timecoded `dbg()` line so behavior is diagnosable from the console. Output is OFF in production by default: `dbg()`/`verify()` success lines print only when `localStorage.stc_debug === '1'` (set it and reload to diagnose in the field); `verify()` failures always print.
- **Check the port before starting a server** — `lsof -ti :<port>` first, never bind blindly. A dev server may already be running (local dev uses 64728).

## Local Development

**Always preview via HTTP, never `file://`.** The page uses `fetch()` for lazy-loaded bios and API calls. Browsers block fetch from `file://` origins (CORS).

**For lineup only**: `cd pipeline/output && python3 -m http.server 8321` — expected 404s for `/manifest.json`, `/sw.js`, `/api/me`.

**For lineup + chat**: run the full FastAPI server: `cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile certs/localhost+1-key.pem --ssl-certfile certs/localhost+1.pem`. Symlinks in `server/static/` point to `pipeline/output/` files so lineup reflects latest build.

**Chat requires auth**: sign in via email magic link at `/chat`. For local dev, set `CHAT_BASE_URL=https://localhost:64728` in `.env` so the magic link points to localhost.

**Testing on iPhone (LAN, no proxy)**: the mkcert root CA is installed and trusted on the phone, so it connects directly. Start uvicorn with `--host 0.0.0.0` added to the command above and open `https://<Mac-LAN-IP>:64728/chat` on the phone. The cert (`server/certs/localhost+1.pem`) must have a SAN for the current LAN IP — on a new network, regenerate keeping the same filenames: `cd server && mkcert -cert-file certs/localhost+1.pem -key-file certs/localhost+1-key.pem localhost 127.0.0.1 <lan-ip>`. If magic-link emails must open on the phone, point `CHAT_BASE_URL` at the LAN IP (restore to localhost afterwards).

## System Dependencies

Not pip-installable, must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — perceptual quality targeting for AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server); `yt-dlp` (video discovery); `markdown` (bio rendering); `email-validator` (auth); `maileroo` (magic link emails).

System: `ffmpeg` + `ffprobe` must be in PATH for video upload (frame extraction for moderation).

## Architecture

### Data flow

1. `pipeline/stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json + bios.json
2. `pipeline/lineup.db` (SQLite, WAL mode, FK enforcement) is the single source of truth — artists, links, sets, schedule, locations, events
3. `pipeline/scraper/overrides.toml` provides manual corrections (artist links), editorial data (floor curators), and YouTube video overrides — applied as patches to the DB
4. `pipeline/fetch_videos.py` discovers YouTube sets via yt-dlp and writes to the `artist_sets` table
5. Output: `lineup.html` (~650 KB) + `bios.json` (~200 KB, lazy-loaded) + `timetable.json` + `photos/*.avif` + `thumbs/*.avif`

### Database schema

```
events            — id, name, short_name, edition, source_url, website, start/end_date, timezone, address, lat/lng
venues            — id, name, about, address, lat/lng
stages            — id, name, about, venue_id (FK → venues)
event_stages      — event_id + stage_id (PK), color (RGB), position
stage_notes       — stage_id, date, note, position (daily annotations: curators, hosts)
stage_details     — stage_id, label, value, position (static key-value facts for popup)
artists           — id, name, photo_url, photo_file, bio (markdown)
artist_links      — artist_id + platform (PK), url, follower_count, position
artist_sets       — id, artist_id, platform, url, title, view_count, duration_min, upload_date, position
schedule          — artist_id + event_id + start_time (PK), stage_id, end_time, date, period, set_type
```

Key design decisions:
- **Artists, artist_links, and artist_sets are global** — shared across events
- **Stages are global, reusable across events** — the same physical stage can appear at multiple events. Event-specific config (color, display order) lives in `event_stages` junction
- **Venues** hold physical addresses/coordinates — stages reference their venue via `venue_id`. Single-venue events: one venue, all stages point to it (or NULL, address on events table). Multi-venue events: multiple venues, each stage references its venue
- **artist_links** normalizes all social platforms — adding a new platform is just an INSERT, no schema change
- **artist_sets** normalizes all media sources — `platform` column distinguishes YouTube, SoundCloud, etc.
- **`period`** is a free-text tag (day, night, afterhours, etc.), nullable for events without period splits
- **`set_type`** supports dj, live, hybrid, b2b, talk, or NULL — reserved, no writer populates it yet
- **`edition`** on events separates the event name ("Stone Techno") from the instance ("2026", "XV"). Page title derived as `"{name} {edition} Companion"`
- **Stage colors** stored as RGB channels in `event_stages.color` (e.g. `"198, 249, 197"`), CSS generated dynamically at build time. Per-event — same stage can be green at one festival, blue at another
- **Stage notes** hold per-day annotations (curators, hosts) shown below floor pills
- **SQLite pragmas**: `journal_mode=WAL` (concurrent reads), `foreign_keys=ON` (referential integrity)
- **All queries use `sqlite3.Row`** — dict-like access by column name, no positional indexing
- **Schedule PK** is `(artist_id, event_id, start_time)` — safe for multi-event

### Key files

| File | Role |
|---|---|
| `pipeline/scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify/Resident Advisor scrapers. Each event needs its own scraper module. |
| `pipeline/scraper/db.py` | SQLite schema, upserts, overrides, queries — all event-scoped |
| `pipeline/scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `pipeline/scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. Markdown bio rendering. Dynamic floor color CSS. SVG icons via `<symbol>`/`<use>` sprite |
| `pipeline/scraper/timetable_json.py` | Generates `timetable.json` — slot UUID → set time mapping for push scheduler and ICS endpoint. Reads timezone from events table. Owns `slot_uuid()` (also imported by `render.py`) — the single source of truth for a slot's id, collision-aware: an artist playing two sets on the same floor within one date+period no longer collapses to one id, and existing ids are preserved (the earliest slot keeps the historical id, only the extra one is disambiguated) so no saved schedule is ever reset. |
| `pipeline/fetch_videos.py` | YouTube set discovery via yt-dlp. Writes to `artist_sets` table with `platform='youtube'`. |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push scheduler + ICS export + static file routes. Mounts chat module at startup. |
| `server/chat_db.py` | Chat SQLite schema (chat.db) — users, sessions, bans, rooms, messages, meetups, reactions, blocks, reports, strikes, E2EE device key store |
| `server/chat_moderation.py` | Three-layer moderation: word filter + OpenAI omni-moderation + GPT-5.4-nano drug detection. All via raw httpx. |
| `server/chat_ws.py` | Chat WebSocket server — rooms, optimistic messaging, presence, typing, reactions, replies, meetups, DMs, purge loop, E2EE content gating (DM-only envelopes, generic previews, snippet redaction) |
| `server/chat_api.py` | Chat REST API — auth (Google/Email), rooms, meetups, DMs, media upload, admin page, E2EE key endpoints. Mounts routes + WS into FastAPI. |
| `server/chat/chat.html` | Chat frontend — single HTML file with inline CSS/JS. WhatsApp-style bubbles, reactions, replies, action menus, client-side E2EE (per-device keys, encrypt/decrypt, key rotation). |
| `server/chat/admin.html` | Admin dashboard — dark-themed SPA with tabs: Rooms, Users, Reports, Banned, Logs, Audit, and (super-admin only) Admins + Settings. Room management (create/edit/delete/reorder/set-main; "..." per-row menu), user moderation ("..." menu: strike/mute/unmute/ban/unban/clear/delete), per-room message viewer + single delete, meetup delete, admin-action audit log, admin-list management, live app settings. Role-gated UI (see "Admin Page"). |
| `server/chat/blocklist.txt` | Word filter blocklist (drug terms, slurs). Editable without deploy. |
| `server/static/shared.css` | Unified design tokens and shared CSS components (loaded by all pages) |
| `server/static/shared.js` | Shared JS utilities: escapeHtml, dbg, showToast, storageGet/Set, icon constants (loaded by all pages) |
| `server/static/sw.js` | Service worker — push ack (delivered/clicked/dismissed), notification click, pushsubscriptionchange auto-resubscribe, plus a scoped **cache-first `fetch` handler** (cache `stc-map-v1`) that offline-caches the meetup map assets (`/dop` aerial tiles, `/vendor/maplibre/*`, `tiles.openfreemap.org`); all other requests pass through untouched |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |
| `tests/test_chat_db.py` | 59 tests — users, sessions, bans, rooms, messages, meetups, DMs, blocks, reports, strikes |
| `tests/test_chat_moderation.py` | 39 tests — word filter, strike system (expiry, reset, mute cycling), AI moderation pipeline |
| `tests/test_chat_ws.py` | 44 tests — WebSocket rooms, messaging, presence, moderation flow |
| `tests/test_chat_api.py` | 63 tests — REST endpoints, auth, rooms, meetups (incl. cancel-deletes-invite), DMs, admin |
| `tests/test_chat_admin_roles.py` | 22 tests — multi-admin role resolution, super-admin gating, admin-account protection, admins CRUD, audit log, message/meetup/settings/reports admin endpoints (through the ASGI stack) |
| `tests/test_notifications.py` | 54 tests — push debounce, payload, badge, clearing. Requires Playwright infra. |
| `tests/e2ee_browser_check.py` | Standalone Playwright verification (not part of the pytest suite) — 21 checks across 5 browser contexts, see "End-to-End Encryption (DMs)" below |
| `tests/verify_push_both.py` | Standalone push check — after enabling notifications on both lineup and chat in one Chromium profile, fires a real WebPush at each stored subscription and asserts both surfaces share one LIVE endpoint (see push "Hard-won invariants") |
| `tests/stress_test/run.py` | Chat stress test — 200 concurrent WS users, multi-room + DMs, burst testing, media uploads, latency/throughput/resource metrics, moderation cost estimation |

### Deploy

```bash
# Server code deploy (backup + pull + rebuild + health check)
./deploy.sh

# Content deploy (lineup HTML + photos — no server restart needed)
python pipeline/stone_techno_companion.py --render-only --deploy
```

**Incidents during the live event**: see `docs/runbook.md` — health checks, push debugging, restart/rollback/DB restore, moderation outage behavior (fails closed), admin lockout break-glass, POI fallback.

`deploy.sh` does: sync prod env vars to the VPS `.env` (back up the previous `.env` first, then an atomic temp-file + byte-count check + `mv` + `chmod 600` on the new file, so a dropped connection can't leave a truncated `.env` and secrets aren't left world-readable); **VAPID preflight** — derive the public key from the VPS `vapid_private.pem` and abort if the local `VAPID_PUBLIC_KEY` being synced doesn't match (a mismatch silently breaks every push); snapshot the VPS SQLite DBs via `VACUUM INTO` (transactionally consistent even under live writes — no torn `.db`/`-wal` copies) and download the snapshots (+ best-effort `server/chat-uploads/`) to `backups/{timestamp}/` locally; verify each downloaded `.db` with `PRAGMA quick_check` and abort before any change if a backup is corrupt; keep the same snapshot on the VPS as `data.bak.{timestamp}` (both backups are the same verified bytes); `git pull`; **seed `chat.db`** — on the FIRST chat deploy only (skipped whenever the VPS already has a `chat.db`), build a clean production DB from the local one via `server/seed_chat_db.py` (keeps curated group rooms + `chat_settings`, strips all messages/users/test data, pre-creates the owner account `gabriele` / gabrielelosurdo@gmail.com so first login only asks for the avatar + confirmation); `docker compose up -d --build --force-recreate`; health check (container + chat API), exiting non-zero on failure; prune old VPS backups (keeps 5). Local backups survive VPS disk failure. `./deploy.sh --rollback <commit>` resets the VPS code to a known-good commit and rebuilds (code only, container-only health check — see `docs/runbook.md`).

## Generated Artifacts (gitignored)

- `pipeline/lineup.db` — SQLite database (all tables)
- `pipeline/output/lineup.html` — generated page (~650 KB)
- `pipeline/output/bios.json` — artist bios + sets, lazy-loaded on first artist tap (~200 KB)
- `pipeline/output/photos/*.avif` — processed artist photos
- `pipeline/output/timetable.json` — slot UUID → set time mapping for push notifications
- `pipeline/output/thumbs/*.avif` — YouTube video thumbnails (240px max, AVIF)

- `server/data/` — runtime databases (hearts.db, chat.db), VAPID keys (gitignored)
- `server/chat/uploads/` — uploaded images/videos (WebP, MP4) in local/bare-uvicorn dev
- `server/chat-uploads/` — the PRODUCTION uploads dir: `docker-compose.yml` bind-mounts `./chat-uploads:/app/chat/uploads`, so live user media lives here on the VPS (inside the deploy git worktree). Gitignored and backed up by `deploy.sh`
- `server/chat/tmp/` — intermediate processing files (auto-cleaned on startup)
- `tests/stress_test/media/` — auto-generated test images (WebP 1500px Q=80) + videos (H.264 MP4) + user-provided files
- `tests/stress_test/report_*.txt` — stress test reports
- `tests/stress_test/debug_*.log` — stress test debug logs

These are regenerable. Source of truth is the live website + `overrides.toml` + DB enrichment data.

## Overrides

`pipeline/scraper/overrides.toml` provides manual corrections. Applied after scraping, before follower fetching.

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
- **Floor annotations**: "curated by" / "hosted by" from `stage_notes` table, shown below floor pills per day
- **Artist schedule notes**: floor + time on every card, "Also" cross-references for multi-slot artists
- **Hamburger menu**: mobile-only, preserves view in localStorage across reloads

### Design system

- **Colors**: CSS variables in `:root` — `--color-text`, `--color-bg`, `--color-surface`, `--color-surface-hover`, `--color-muted`, `--color-muted-icon`, `--color-accent`, `--color-schedule`, `--color-border`
- **Floor colors**: from `locations.color` in DB (RGB channels). CSS generated at build time — cards `rgba(R,G,B, 0.88)`, pills `rgb(R,G,B)`. Unknown floors fall back to gray.
- **Font scale**: `--font-2xl` (2rem) → `--font-xs` (0.75rem/12px min). All `rem`-based to prevent compounding in nested elements. No text below 12px.
- **Shared CSS**: `server/static/shared.css` — unified design tokens (colors, spacing, radius, shadows, z-index, font scale, header height), shared components (hamburger, nav-icon, menu-overlay, toast), utilities (truncate, sr-only). Both pages link it via `<link rel="stylesheet" href="/shared.css">`.
- **Shared JS**: `server/static/shared.js` — shared utilities (escapeHtml/esc, dbg/verify, showToast, fmtTime, ago, storageGet/storageSet/storageRemove, urlBase64ToUint8Array, icon constants). Loaded synchronously before inline scripts.
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
- Every artist-facing href passes a render-layer URL scheme allowlist, so anything that isn't `http(s)` (or `mailto` for social links) renders as `#`: `_safe_href` (Python) + `_safeHref` (emitted popup JS) for artist social links, and an inline `^https?://` guard on bio "Sets" video links. Chat's link-preview cards apply the same guard client-side (`_safeHref`, `chat.html:4777`). This is defense-in-depth at the last output stage even though scraped links are already http(s)-validated — it blocks a `javascript:` URL injected via a hand-edited `overrides.toml`.

## Page Load Flash Prevention

All pages use `body{opacity:0}` in an inline `<style>` in `<head>` (first element) to prevent content from flashing before JS initialization completes. JS sets `document.body.style.opacity='1'` after all init work is done (sticky tops calculated, views switched, data loaded).

The lineup/timetable page has an additional mechanism: a `<script>` in `<head>` (before body) sets `document.documentElement.className='view-list'` or `'view-timetable'` based on the URL/localStorage. CSS rules keyed to these classes control which view, buttons, and menu items are visible — so the correct view state is applied before any body content is parsed. The `switchView()` function updates this class when switching views at runtime.

## Working on the HTML/CSS/JS

All frontend code lives in `pipeline/scraper/render.py` as Python string concatenation. Shared CSS lives in `server/static/shared.css`. Shared JS utilities live in `server/static/shared.js`.

```bash
python pipeline/stone_techno_companion.py --render-only --no-photos
cd pipeline/output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

## Server

FastAPI (`server/api.py`). Sessions via 128-bit URL-safe tokens. Cross-device sync via ephemeral 6-digit PINs (5-min TTL). Real-time sync via WebSocket. Atomic pick/schedule operations via `json_group_array`/`json_each`.

Static file routes (`/bios.json`, `/manifest.json`, `/sw.js`, `/shared.css`, `/shared.js`, `/favicon.*`) are explicit endpoints before the catch-all `/{path:path}` (which serves `index.html`). New static files need an explicit route in `api.py`. `/sw.js`, `/manifest.json`, `/shared.css`, `/shared.js` are served with `Cache-Control: no-cache` so a content deploy that ships new shared bundles is picked up (the catch-all `index.html` uses `no-store`). `timetable.json` has no HTTP route — it's read server-side by the push scheduler and ICS export only.

The catch-all `/{path:path}` serves `index.html` with `Cache-Control: no-store` and explicitly rejects `/chat*` paths (returns 404). Chat module import is **required** — if `chat_api.py` fails to import, the server crashes at startup (fail-fast, no silent degradation).

**Clean-URL static pages**: drop a file at `server/static/pages/<slug>.html` and it is served at `/<slug>` (extensionless) automatically — the catch-all checks `static/pages/{path}.html` before falling back to `index.html`. The slug must match `[a-z0-9][a-z0-9-]*` (no slashes/dots), so the lookup is path-traversal-safe and multi-segment or dotted paths fall through to the SPA. No per-page route needed. `static/` is bind-mounted (`./static:/app/static`) and tracked in git, so new pages deploy via `git pull` with no image rebuild. Example: `static/pages/public-transport.html` → `/public-transport`.

Production: Docker on DigitalOcean VPS behind Caddy (auto-TLS). DBs at `server/data/` volume-mounted (hearts.db, chat.db, vapid_private.pem).

### Environment Variables (`server/.env`)

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Chat moderation (omni-moderation + GPT drug detection). Absence is logged loudly at startup — without it, moderated rooms fall back to the word filter only (AI layers 2-3 silently pass everything). |
| `MAILEROO_API_KEY` | Yes | Magic link email delivery (was Resend, switched July 2026). The `/chat/api/login` endpoint now returns 500 (not a false `{"sent": true}`) when this is unset, so a misconfigured container can't silently take out the only email auth path. |
| `CHAT_EMAIL_FROM` | No | From address for magic links (default: `no-reply@deftlab.dev`) |
| `CHAT_BASE_URL` | Dev only | Set to `https://localhost:<port>` for local dev. Omit in production. |
| `VAPID_PRIVATE_KEY` | Yes | Push notification signing |
| `VAPID_PUBLIC_KEY` | Yes | Push notification subscription |
| `VAPID_CLAIMS_EMAIL` | Yes | VAPID contact email |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID (from Google Cloud Console) |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret (for authorization code exchange) |
| `CHAT_ADMIN_EMAILS` | No | Comma-separated emails that are PERMANENT super-admins (never DB-removable). Additional admins are managed in-panel via the `admins` table. |
| `CHAT_ADMIN_TOKEN` | No | Shared bootstrap/emergency super-admin token (`X-Admin-Token` header). Unattributable — the cookie+role path is the normal per-person auth. |
| `CHAT_EVENT_ID` | No | Event ID (default: `stone-techno-2026`) |
| `CHAT_DB_PATH` | No | Test/dev override for chat.db location. Used by the browser verification harness (`tests/e2ee_browser_check.py`) to point at an isolated scratch DB. |
| `MAPTILER_KEY` | No | MapTiler Cloud API key. Now used **only server-side** for the festival POI dataset fetch (see `MAPTILER_DATASET_ID`) — the meetup map itself no longer uses MapTiler tiles (it's self-hosted NRW aerial + OpenFreeMap). Still returned in `/chat/api/config` but the current map doesn't read it. Free tier = 100k loads/mo, no card, hard-stops. |
| `MAPTILER_DATASET_ID` | No | MapTiler dataset id for festival POIs. When set (with `MAPTILER_KEY`), `GET /chat/api/pois` fetches the dataset's `features.json` server-side, normalizes it (point coords + `name` + `Category`→our type, unknown→`other`), and caches it 120s — so organizers edit pins live in MapTiler with no redeploy, the key stays off the client, and it falls back to the cached copy on error. POIs live ONLY in MapTiler (no local copy is maintained); as a break-glass fallback, a `festival-pois.kmz`/`.kml`/`.json` export dropped into the bind-mounted `server/static/` is picked up with no redeploy. |

### DNS for Email (deftlab.dev)

- **SPF**: `v=spf1 include:_spf.mx.cloudflare.net include:_spf.maileroo.com ~all`
- **DKIM**: TXT record at `mta._domainkey.deftlab.dev` (from Maileroo dashboard)
- **DMARC**: existing `_dmarc.deftlab.dev` record works as-is

### Deploy Checklist

**VPS env vars** (add to `/root/services/stone-techno/server/.env`):
1. `MAILEROO_API_KEY` — required for email magic links
2. `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` — required for Google OAuth
3. `CHAT_ADMIN_EMAILS` — comma-separated admin emails
4. Rename `VAPID_SUBJECT` → `VAPID_CLAIMS_EMAIL` (code expects this name)
5. Remove `CHAT_BASE_URL` (must not be set in production)

**DNS** (already done): SPF includes `_spf.maileroo.com`, DKIM at `mta._domainkey.deftlab.dev`

**Automatic on deploy**: Dockerfile installs ffmpeg + libvips + all Python deps. `chat.db` created fresh on first run. `chat/uploads/` and `chat/tmp/` auto-created. `server/.dockerignore` keeps local runtime files (`chat/uploads/`, `chat/tmp/`, `data/`, `.env`, `*.pem`) out of the image build context (the Dockerfile does `COPY chat/ ./chat/`). Container logs are capped via `docker-compose.yml` (`json-file`, 10m x 5). `.gitignore` covers the live `chat.db`, `chat/uploads/`, and the production `chat-uploads/` bind-mount path so none of them can be committed. The container runs as non-root (`appuser`, uid 1001); `entrypoint.sh` runs as root first to `chown` the bind-mounted volumes (`data/`, `chat/uploads/`, `static/vendor/dop-cache/`) before dropping to `appuser` via `gosu` — this handles VPS volumes left root-owned by the old container automatically, no manual `chown` needed on deploy.

## Push Notifications

### Hard-won invariants (violating any of these silently breaks a subset of browsers)
- **pywebpush mutates `vapid_claims`**: it stamps the FIRST endpoint's origin as `aud` into the dict you pass. Never share one claims dict across a subscription loop — pass `dict(vapid_claims)` per call (both `chat_ws._do_send_push` and the lineup scheduler in `api.py` do this). A shared dict poisons every later push to a *different* push service: FCM rejects an apple `aud` with 403, so a user with Apple + FCM subscriptions only ever reached the first service. Cost a full afternoon in July 2026 because Apple and Mozilla don't enforce the binding — iOS and Firefox kept working while Brave/Chrome got nothing.
- **Push services enforce VAPID asymmetrically**: FCM (all Chromium) strictly validates both the `aud` claim and that the signing key matches the subscription's `applicationServerKey`; Apple and Mozilla accept any self-consistent JWT. **Any push change must be tested against a Chromium-family browser** — Zen/Firefox and iOS passing proves nothing about Chrome/Brave.
- **VAPID key pair consistency is checked at startup** (`_check_vapid_key_consistency` in `api.py`): logs `VAPID key pair verified` or a loud mismatch error. Check this line after touching keys or `.env`.
- **Notification tags must be unique across server restarts**: the payload carries a random `push_id` (`secrets.token_hex(8)`) and sw.js prefers it for the tag. `push_index` alone resets with the process and re-collides with notifications still in iOS Notification Center (see "tag uniqueness" below).
- **Brave subscriptions can silently die**: revoking site notification permission or Brave's "Forget me when I close this site" unsubscribes at the FCM level (next push → 410, row auto-pruned). The client repairs on load (`_repairPushSubscription`, gated by the `push_enabled` localStorage flag so an explicit disable is never overridden), and `_enableAllNotifications` only reports success when subscribe + server POST both succeeded.
- **Lineup and chat share ONE browser subscription**: both pages register `/sw.js` at the root scope, so a browser holds exactly one push subscription per origin. The lineup record lives in `push_subscriptions` (hearts.db, keyed by `session_id`); the chat record lives in `chat_push_subscriptions` (chat.db, keyed by `user_id`) — but both must point at the SAME endpoint. Never `unsubscribe()` before `subscribe()` in an enable flow: that rotates the shared endpoint and orphans the other surface's stored record (its next push → 410, silently unsubscribing that surface). Both `enableNotifications` (lineup, `render.py`) and `_subscribePush` (chat, `chat.html`) reuse the existing subscription instead; chat also resyncs its endpoint to the backend on every load (`_repairPushSubscription`), mirroring lineup. Symmetrically on DISABLE: each surface always deletes its own server record, but only calls `unsubscribe()` on the shared browser subscription when the OTHER surface is also disabled (checked via the shared-origin localStorage flags — chat `push_enabled`, lineup `stc_push`) — so disabling one surface never silently breaks the other. (Known limitation: the `sw.js` `pushsubscriptionchange` handler re-registers only the chat record, since a service worker can't read the lineup session id from localStorage; lineup self-heals its record on its next page load.) Verify with `python tests/verify_push_both.py` after enabling both surfaces in one Chromium profile (expects one endpoint, LIVE in both tables).
- **TTL**: both senders pass `ttl=300` to `webpush()` (pywebpush defaults to TTL=0 = discard if the client is momentarily unreachable). The lineup scheduler payload also carries a `push_id` (`secrets.token_hex(8)`) for the documented tag-uniqueness invariant.
- **Pending exclusion**: `get_unread_counts` and the push-preview fallback queries exclude `moderation_status='pending'`, so a not-yet-moderated (or soon-rejected) message never appears in a push body or inflates a badge.
- **Regression net**: `tests/test_chat_ws.py::TestVapidClaimsIsolation` (claims-dict isolation), `tests/notif_badge_browser_check.py` (badges, truthful enable, gated repair — multi-context Playwright), `tests/test_notifications.py` (SW tag/version assertions), `tests/verify_push_both.py` (both-surfaces single-endpoint check — run against a live Chromium subscription), and `tests/notif_e2e/` (21-scenario automated harness across emission / client / service-worker — see "Automated Notification Test Harness" below).

### Lineup push
- **Scheduler**: background task runs every 60s, matches `timetable.json` slots against sessions' schedule, sends via `pywebpush`
- **Dedup**: `sent_notifications` table, pruned after 7 days. Dead subscriptions auto-removed.
- **Re-sync on load**: client re-sends push subscription to recover from DB purges
- **iOS notification click — tag uniqueness is critical**: iOS silently drops `notificationclick` for any notification that *replaced* an earlier one (same `tag`). The tap opens the app at `start_url` with no event, no error. So `showNotification` MUST use a unique tag per notification (derived from the push URL). This was the root cause of "notification click lands on line-up" — room-tag reuse meant every organic message notification was a replacement, while one-off test pushes (never replaced) worked. Diagnosed July 2026 via [push-diag](https://github.com/gabrielelosurdo/push-diag) + server-side SW timeline logging (`POST /chat/api/swlog`, `[SWLOG]`/`[PUSH-ACK]` log lines). `/chat/api/swlog` and `/chat/api/push/ack` are unauthenticated diagnostics, so both are per-IP rate-limited (swlog 30/min, ack 60/min) to prevent log-flood abuse.
- **iOS notification click — navigation**: SW does all LOCAL work first (iOS may kill the SW right after the app foregrounds — never put network calls before the navigation primitives): write target URL to Cache Storage (`stc-push`/`_push_navigate`), then `postMessage` + `focus()` to the existing client, `openWindow()` only when no window exists. Acks/logging go last. Pages navigate on the SW `navigate` message and poll the cache on `visibilitychange`/`focus`/`pageshow` with retries (0ms, 300ms, 1s) as fallback; the navigation latch is a 3s timeout, not permanent, so an aborted navigation self-heals. `client.navigate()` must not be combined with `postMessage` (two racing navigations abort each other). `openWindow()` silently returns null when a window already exists. Push URL includes message ID (`/chat/msg/{id}`) for scroll-to-message on click.

### Chat push
- **Trigger**: sent after message broadcast to room members who are offline or idle
- **Idle detection**: two-layer approach:
  1. **Instant** (primary): client sends `POST /chat/api/push/idle` via `sendBeacon` on `visibilitychange(hidden)` and `pagehide`. Sets `_last_ws_activity` to 0 on the server, making the user immediately eligible for push. Tested on iOS: `visibilitychange`, `pagehide`, and `unload` all fire on lock screen, home swipe, and force close. `sendBeacon` delivers before the app is suspended.
  2. **30-second fallback** (safety net): if `sendBeacon` fails, the server considers a user idle if no user-initiated WS event (`send_message`, `typing`, `add_reaction`, etc.) in 30 seconds. Passive events (`join_room`, `mark_read`) don't reset the idle timer.
- **iOS limitation**: iOS does not send a WebSocket close frame when a PWA is killed. The connection silently dies until the server's ping times out (~30s). The `sendBeacon` idle signal makes this irrelevant — push is sent before the WS timeout.
- **VAPID key**: production uses file path `/app/data/vapid_private.pem` (Docker), local dev uses `data/vapid_private.pem` (relative). Deploy script overrides to Docker path.
- **Cross-device badge sync**: `mark_read` broadcasts `badge_update` with count=0 to all of the user's connections. Reading on phone clears the badge on desktop.
- **Subscriptions**: stored in `chat_push_subscriptions` table. Old/expired subscriptions auto-removed on 410 Gone response from push service.

### PWA standalone mode
- **Keyboard handling**: `visualViewport` resize handler sets the `#app` element's height directly to the visible viewport when the keyboard opens. Required — iOS doesn't auto-reposition input bars in PWA standalone.
- **Safe area**: no `viewport-fit=cover` (caused inconsistent `env()` values). Bottom padding for home indicator via JS class `pwa-standalone` (detected in `<head>` script before render). 20px fixed padding via static CSS — not zeroed when the keyboard opens.
- **Keyboard accessory bar** (prev/next/done): cannot be hidden in PWA — iOS platform limitation, only native apps (Capacitor) can control it.

## Multi-Event Support

The DB supports multiple events via the `events` table. Artists, artist_links, artist_sets, stages, and venues are global (shared). Schedule and event_stages are scoped per event. CLI flags: `--event-id`, `--event-name`, `--event-edition`. Each event needs its own scraper module — the scraper output format (`parsed` dict with `artists`, `sections`, `locations`, `assignments`) is the interface.

## Chat System

Privacy-first ephemeral chat integrated into the companion app. Accessible via "Chat" button in the command bar / hamburger menu, or directly at `/chat`.

### Architecture

Extends the existing FastAPI server — no separate service. Two SQLite databases: `hearts.db` (favorites, unchanged) and `chat.db` (ephemeral chat data). Chat module mounted at startup via `chat_api.mount_chat(app)`, registered before the catch-all `/{path:path}` route.

### Chat Database (chat.db)

```
users              — id, provider, provider_id, display_name, username, username_lower, country, avatar_url, color_index, session_id, device_fingerprint, muted_until, mute_count, created_at, last_seen, last_active
sessions           — id, user_id, token, expires_at
email_tokens       — token, email, provider_id, fingerprint, expires_at (DB-backed, survives restart)
avatars            — user_id (PK), data (BLOB, WebP 128x128)
user_providers     — user_id, provider, provider_id, created_at (multi-provider auth: same user via Google + email)
bans               — id, user_id, provider, provider_id, device_fingerprint, reason, created_at (survives user deletion)
rooms              — id, event_id, type, name, description, is_main, is_moderated, is_read_only, auto_join, allows_media, ttl_minutes, position, created_at, last_message_at
chat_settings      — key, value (app-level config: room_sort, msg_char_limit, dm_ttl_minutes, room_ttl_minutes, meetup_ttl_minutes)
room_memberships   — user_id + room_id (PK), joined_at, last_read_at (tracks joined rooms + unread)
messages           — id, room_id, user_id, type, content, link_preview, reply_to_id, media_url, expires_at, created_at
message_reactions  — message_id + user_id + emoji (PK), created_at, CASCADE on message delete
meetups            — id, creator_id, stage_id, title, location_lat, location_lng, location_label, meetup_time, note, created_at, expires_at
meetup_attendees   — meetup_id + user_id (PK), joined_at
dm_participants    — room_id + user_id (PK)
blocks             — blocker_id + blocked_id (PK), created_at
reports            — id, reporter_id, reported_user_id, message_snapshot, room_id, reason, status, unverified, created_at, reviewed_at
strikes            — id, user_id, reason, detail, created_at, expires_at (4h TTL, reset on new strike)
chat_push_subscriptions — id, user_id, endpoint, p256dh, auth, created_at
e2ee_device_keys   — user_id + device_id (PK), public_key, created_at, last_seen
admins             — email_hash (PK), role ('admin' | 'super_admin'), label, added_by, created_at (DB-backed admins; layered under the permanent CHAT_ADMIN_EMAILS super-admins)
admin_actions      — id, actor, action, target_user_id, target_room_id, detail, created_at (admin audit log — every mutating admin action, shown in the Audit tab)
```

### Auth

Two passwordless providers: Google OAuth and Email magic link (via Maileroo, 3,000/mo free). Disposable domains blocked via 7,860-domain blocklist (`chat/disposable_domains.txt`). Email validation via `email-validator` library (RFC 5322 + DNS MX check). Ban enforcement is provider-based and covers every linked provider of a user (Google + email); the `bans.device_fingerprint` column exists and the matching logic works, but clients do not currently submit a fingerprint, so it is unused (bans by a **new** account are possible — mitigated by moderation, not fingerprinting). Re-linking a fresh provider to an existing banned account is blocked: `_authenticate` calls `is_user_banned(user_id)` (which checks bans across the frozen identity AND every `user_providers` row) for any already-existing user before issuing a session, and the OAuth paths refuse to link a new provider onto a banned account — so a banned user can't regain their original account by signing in through another provider on the same verified email. The email-based ban check in Google OAuth runs regardless of `email_verified` (only the account-linking step itself requires a verified email), closing an evasion path via an unverified Google email. Magic-link email is also rate-limited per destination address (3 / hour) on top of the per-IP limit, to block mail-bombing a victim. A banned email hitting `/chat/api/login` gets a generic "This account cannot sign in." 403 (no ban reason, no registration-status leak); the WS `banned` event also hides admin-authored ban reasons, sending a generic "You have been banned." instead. Session cookies (non-httpOnly for WS access, Secure in production, SameSite=Strict in production / Lax in dev, path=/). Each login mints its own session row/token, so logout is **per-device**: `/chat/api/logout` deletes only the calling device's session token and closes only that device's WebSocket(s) — other devices keep their own sessions and stay connected. The socket is targeted via `ConnectionManager.conn_token` (conn_id → session token, populated in `connect()`); `close_session(user_id, token)` closes only connections whose token matches. Email tokens stored in DB (not memory) — survive server restarts. **Rate limits** (per client IP, in-memory, self-pruning): magic-link `/chat/api/login` 5 / 15 min; the OAuth + verify endpoints (`_check_auth_rate`) 120 / 5 min — high because a festival venue shares a public IP and magic-link tokens are 128-bit / OAuth is Google-validated, so brute force is not the threat this guards.

### Profile Setup

Mandatory before entering chat: username, avatar photo, country. Optional display name. Profile prompt shown on first login.

- **Username**: unique, case-insensitive alphanumerics (`a-z A-Z 0-9 . _ -`), 2-20 chars, stored lowercase in `username_lower` for uniqueness checks. Live availability check (400ms debounce). Shown in bubbles when no display name set.
- **Display name**: optional, Latin Unicode letters + digits + spaces + `. _ -`, 2-30 chars. Replaces username in bubbles when set. Live validation.
- **Avatar**: circular 128px pan+zoom editor. Click to select image (min 128x128), drag to pan, custom friction slider to zoom. Client crops to 128x128 via `createImageBitmap` with `resizeQuality: 'high'`. Stored as WebP blob in `avatars` table. Served via `/chat/api/avatar/{user_id}?v=timestamp` (version stamp for cache busting). Large images (>2000px) downscaled in browser for smooth editor, full-res used for final crop. Server-side: rate-limited (`_check_upload_rate`), restricted to a raster loader allowlist (rejects SVG and other librsvg/XML vectors), and the re-encoded image passes OpenAI omni-moderation before storage (avatars are shown app-wide with no TTL, so they can't skip moderation the way the old code let them).
- **Country**: searchable dropdown with 196 countries + local name aliases (Deutschland, Italia, Espana, etc.). Search matches from start of word only, exact match for 2-char codes, 3+ chars for aliases. Arrow key navigation, Enter to select, first result highlighted.
- **User colors**: 12 vivid+pastel color pairs assigned randomly at registration (stored as `color_index`). 13th "self" color for own messages. Others see your assigned color.
- **Name moderation**: OpenAI omni-moderation on submit (no word filter for names — too many false positives).
- **Profile edit**: settings menu via avatar in header. Edit display name, avatar (full pan+zoom editor), country. Live preview bubble. Edits propagate to live connections: the server broadcasts `broadcast_profile_update` / a `profile_updated` WS event that patches every rendered message and member entry for that user in real time, and `send_message`/`join_room`/`meetup_invite` re-fetch fresh identity from the DB instead of using values frozen at the WS handshake.

### Moderation Pipeline

Every message in a moderated (group) room passes through three layers before broadcast:

1. **Word filter** (instant) — in-memory set from `chat/blocklist.txt`. Drug terms, slurs, spam. Character substitution normalization (@→a, 0→o, etc.).
2. **OpenAI omni-moderation-latest** (free) — harassment, hate, violence, sexual content. Supports images (WebP data URI) and video (3 frames at 25/50/75% extracted by ffmpeg). Via raw httpx.
3. **GPT-5.4-nano content detection** (Responses API, reasoning=none) — catches drugs, spam/scams, payment links, external platform links (Telegram, WhatsApp, Discord). Explicit safe list for festival conversation.

**DMs skip CONTENT scanning, not ban/mute enforcement**: DMs are created with `is_moderated=False` and are end-to-end encrypted, so the server cannot run the three content layers on them — content moderation is replaced by user reporting (see "End-to-End Encryption (DMs)" below). But ban/mute is enforced on every send regardless of `is_moderated` (the unmoderated branch calls `check_ban_mute` in `chat_moderation.py`), and a banned user is rejected at WS connect (`handle_chat_ws` checks `is_banned` before accepting the socket) — so a banned/muted user cannot keep sending DMs over an open connection.

Layers 2 and 3 run in parallel via `asyncio.gather`. Word filter blocks before AI calls (saves API round-trips).

**Optimistic delivery + pending hold**: message saved to DB immediately with `moderation_status='pending'` (moderated rooms) or `'approved'` (unmoderated), `message_acked` (carries `temp_id` AND `room_id`) sent to sender, moderation runs in a tracked `asyncio.create_task` (references held in a module-level set so the task can't be GC'd mid-flight). If passes: flip to `'approved'` and broadcast — **but only if the row still exists** (a message deleted or TTL-purged during the moderation window is not resurrected) **and the sender wasn't just banned/muted by a concurrent task** (a final `check_ban_mute` re-check runs immediately before the broadcast, closing a race where a second message could land after the first message's ban). If fails: delete from DB, unlink any served media file, send `message_removed` + strike to sender. Mute/ban also deletes all user's active messages and broadcasts removal. Pending messages are excluded from `room_history`, unread counts, and push previews, so nothing unmoderated is ever served or pushed. A stuck-pending sweep in the purge loop deletes any message left `'pending'` past ~3 minutes (its moderation task died on a restart) and sends the sender a `message_removed`. The ack's `room_id` matters: the client keys the pending-message lookup by it, so switching rooms before the ack arrives no longer drops the ack and spuriously reports "message not sent".

**Moderation logging**: OpenAI scores logged via `logger.info` — top 5 categories above 0.1 threshold, FLAGGED line with threshold comparison. `logging.basicConfig(level=INFO)` configured at startup.

**Strike system**: 4-step escalation with expiring strikes (4h TTL, reset on new violation). 1st = warning, 2nd = warning, 3rd = 30-min mute, 4th = permanent ban. Lifetime mute counter: 3 total mutes across the event = permanent ban (prevents cycling). Same escalation for all content types including drugs. Bans stored by provider_id (per linked provider); the device-fingerprint column is unused (clients do not submit one). Automatic bans (strike/mute/AI) and admin ban both cover ALL of a user's linked `user_providers` (not just the frozen `users.provider/provider_id`, so a second provider can't evade it) and closes the user's live WebSocket connections immediately (a still-connected user can't keep sending — DMs included). An admin **strike** that escalates to mute/ban applies the same enforcement (message deletion, `messages_expired` broadcast, `muted`/`banned` event, socket close) — it isn't just a counter bump. `strikes` are FK-less like `bans`/`reports`, so a user's strike history survives account deletion and stays in the admin Logs timeline. `secure_delete=ON` zeros deleted data on disk.

### End-to-End Encryption (DMs)

DMs — and only DMs — are end-to-end encrypted; group rooms stay unencrypted and moderated as above.

- **v2 multi-device design**: each browser profile is a device — a 32-hex `device_id` + a P-256 ECDH key pair generated and kept in localStorage. Content is encrypted once per message with a random per-message key, then that key is wrapped separately for every device of BOTH participants, including the sender's own other devices.
- **Envelope** (stored in the existing `content` TEXT field): `{e2ee, v: 2, sd: <sender_device_id>, ct: <encrypted content>, keys: {<device_id>: <wrapped key>, ...}}`.
- **Server storage**: `e2ee_device_keys` table (capped at 6 devices/user, pruned after 7 days of inactivity). `PUT`/`GET /chat/api/keys` register/fetch device keys with device_id + JWK validation. `key_rotated` WS event notifies DM peers per room plus a self-notification (room_id null) when a device re-keys.
- **Server cannot read DM content**: moderation is skipped, push previews are generic ("Sent you a message"), reply snippets are blanked server-side and rebuilt client-side, link previews are skipped. Reports carry reporter-provided plaintext, flagged `unverified`.
- **Media file cleanup for E2EE DMs**: the file URL is encrypted inside the envelope, so the server can't find the file to garbage-collect it by parsing message content. The client sends the plaintext URL in a top-level `media_url` field, stored in `messages.media_url`; the TTL purge, manual delete, meetup expiry, and admin room-delete all read that column to unlink the served file (and its `_mod*.webp` copies). Without this, encrypted image/video DMs orphaned their files on disk forever. **All** unlink paths go through `_unlink_media_if_orphaned` — a file is deleted only when no other live message still references the same `media_url`, so a crafted `media_url` pointing at another user's file can't delete it on purge/ban/expiry.
- **Fallback**: keyless peers (no registered devices) fall back to plaintext, with lock-icon/banner UI suppressed accordingly. But when a peer HAS registered devices and none of their keys can be wrapped (e.g. all stored JWKs are corrupt), `encrypt()` fails CLOSED — it throws and the send surfaces an error rather than shipping an "encrypted" envelope no recipient can decrypt.
- **No history sync**: a newly registered device cannot decrypt messages sent before it existed; the 60-minute message TTL bounds how long that gap is visible.
- **Specs**: `docs/e2ee-v1.md` (v1 design + server adaptations — v2 inherits its trust model and fallback rules) and `docs/e2ee-multidevice.md` (v2 multi-device design, current).
- **Verification**: `python tests/e2ee_browser_check.py` — 21 checks across 5 browser contexts, isolated server + scratch DB via `CHAT_DB_PATH`.

### Room Properties

Rooms have configurable properties set via the admin page:
- `description` — what the room is for
- `ttl_minutes` — per-room message TTL, defaults from `chat_settings`: DMs 24h (`dm_ttl_minutes: 1440`), rooms 24h (`room_ttl_minutes: 1440`; code falls back to 360 only if the settings row is missing), meetups 1h after meetup time (`meetup_ttl_minutes: 60`). Meetup expiry destroys messages + room + meetup record. DM rooms persist after messages expire (conversation thread stays) — the purge loop only deletes DM shells that NEVER had a message, detected via `rooms.last_message_at IS NULL` (stamped by `create_message`, backfilled on migration). Deriving "empty" purely from a post-purge `messages` scan wrongly destroyed live DM threads once their messages hit TTL.
- `is_moderated` — toggles word filter + AI moderation for the room
- `is_read_only` — only admins can post
- `auto_join` — new users automatically become members on WS connect (always on for main room)
- `allows_media` — disable image/video uploads
- `position` — custom sort order (drag-to-reorder in admin)

### Membership Model

Room "member count" reflects **reachable** users — those who can be notified. A member is reachable if EITHER (`get_reachable_member_count`, `REACHABILITY_HOURS=2`):
- `last_seen` within the last 2 hours, OR
- has any row in `chat_push_subscriptions` (a push subscription makes a user reachable regardless of `last_seen` age — the two branches are OR'd, not AND'd)

`last_seen` updated on: WS connect, WS disconnect, push notification delivery (via `POST /chat/api/push/ack`).
`last_active` updated on: engagement events (send message, react, join meetup, etc.), throttled to 1 write per 60s.

Push ack signals from service worker: `delivered` (updates last_seen), `clicked` (updates last_seen + last_active), `dismissed` (updates last_seen). Auto-resubscribe on `pushsubscriptionchange`.

Mute/delete user → all their messages deleted from DB + `messages_expired` broadcast to connected clients for instant removal.

### Meet-ups

Real-world meetups: a user creates one (title, date/time, location (required — GPS pin via map picker or text label), optional note up to 80 chars, optional meeting-point photo), which spins up a dedicated `type='meetup'` chat room (room id == meetup id, joined by UUID convention — no FK), an attendee/RSVP list, and (when tied to a `stage_id`) a `meetup_invite` card broadcast into that room. Auto-expires `meetup_ttl_minutes` after the meetup time.

**Security/privacy model (hardened July 2026):**
- **Location + attendee identity are attendee-only.** `GET /meetups` and `GET /meetups/{id}` route through `_shape_meetup(...)` in `chat_api.py`: non-attendees get only `id/title/meetup_time/stage_id/attendee_count/is_going/expires_at`; only attendees (rows in `meetup_attendees`) get `location_lat/lng`, `location_label`, `note`, and the named `attendees` list. The `meetup_created` WS broadcast carries only `id/title/meetup_time/stage_id` — never coordinates. When adding any new meetup read path, gate it the same way (mirror `_shape_meetup`). The `meetup_invite` card intentionally shows `title`/`label`/`note` room-wide (textual, creator-chosen) but NEVER precise GPS; the "Open in Maps" link is injected client-side only from the attendee-gated `GET /meetups/{id}` response (`loadMeetupJoinState`).
- **Creation is fully gated** (both the WS `create_meetup` event AND `POST /meetups` — keep them in lockstep): `check_ban_mute` (muted/banned users blocked), word-filter on `title`+`note`+`label`, and `meetup_time` must be timezone-aware, in the future, and ≤ 30 days out. GPS is rounded to 4 decimals (~11 m) and range-validated in `create_meetup` (`_sanitize_coord`).
- **`stage_id` is validated before the invite card is posted** (both the WS `create_meetup` event and REST `POST /meetups`): it must be an existing, non-read-only `stage`/`general` room (never `dm`/`meetup`), or it's set to `None`. The same `valid_target` flag also gates the `meetup_created` broadcast room, so an unauthorized `stage_id` falls back to the main room. This blocks message injection into read-only/announcement rooms and E2EE-bypass into DMs.
- **Blocks are enforced**: a user blocked by (or blocking) the creator cannot join and does not see the meetup in their list (WS + REST).
- **`get_message_context` gates meetup-room permalinks** to attendees (mirrors the DM branch).
- **`join_meetup` (chat_db.py) returns `bool`** — it checks existence and catches `IntegrityError` (concurrent purge), so joining a stale/expired/bogus meetup is a clean 404 (REST) / no-op (WS), never a 500 or a whole-socket teardown. Callers must check the return.
- **Teardown is atomic and single-sourced**: `delete_meetup` deletes attendee/meetup rows, the invite message from the origin group room, *then* `delete_room` last (one commit); it returns `[]` (not `None`) when the meetup doesn't exist, so callers don't need a None-check; `delete_user` and `admin_delete_meetup` both funnel through it; `purge_expired_meetups` also funnels through `delete_meetup`. Creator-only **cancel** is `DELETE /meetups/{id}` (gated on `creator_id`), which broadcasts `meetup_expired` to the meetup room, `message_removed` (with key `"id"`, matching the client handler) to the origin group room for each deleted invite, and evicts `manager.rooms`/`_room_meta`. The invite message is found via `content LIKE '%"meetup_id": "{id}"%'` (matches `json.dumps` default spacing). Client-side, the `meetup_expired` handler purges the invite from `messagesByRoom` cache before redirecting to the main room, preventing a stale-card flash. The `message_removed` handler has `dbg()` logging for `id`, `room_id`, cache hit, and DOM removal. No client-side cancelled-meetup state is needed — the invite is deleted from the DB and absent from `room_history`.

**Frontend:** creation is no longer fire-and-forget — `submitMeetup` shows success only after the server's `meetup_created_ack` (or surfaces `create_meetup_error` / a 6 s timeout). Live attendee counts sync via the REST join/leave endpoints broadcasting `meetup_updated` (the WS `join_meetup`/`leave_meetup` events also broadcast but the shipped client uses REST). DM/meetup rooms are excluded from the `rooms_changed` redirect (they're never in the client `rooms` array). The Create-Meetup modal follows the documented dialog a11y pattern (role/labels/focus trap+return, including the Escape path).

**Attendance = membership:** join/leave/create write `room_memberships` for meetup rooms (not just `meetup_attendees`), so meetup rooms are first-class in the shared membership/unread/reachable-count/push machinery. Opening a meetup room shows a **sticky details banner** (`renderMeetupDetails`) at the top of the message scroll — a compact bar matching the header height with two rows (When / Where) plus an optional third (Note), a photo thumbnail (div with `background-image`, not `<img>`, so `align-items: stretch` works), and a Cancel button for the creator. `line-height: 1` on rows, `overflow-x: clip; overflow-y: visible` for ellipsis without clipping descenders. Photo thumbnail opens in the full image viewer on click. Location label links to Apple Maps on iOS (`maps://`) or Google Maps elsewhere. Cancel uses the app's `showConfirm` dialog (never native `confirm()`), and on confirmation redirects to the main room. Clicking the invite card opens the meetup room. The creator can cancel from the invite card, the room banner, or by re-tapping the selected sidebar row; deleting the invite card (creator, within the 120 s message-delete window) also cancels the meetup + room.

**Location capture — MapLibre GL map picker** (`openMeetupMapPicker` in `chat/chat.html`; **MapLibre GL 5.24 self-hosted** at `/vendor/maplibre/`, lazy-loaded). One fixed **hybrid** view, no basemap switcher:
- **Base:** self-hosted **NRW 10 cm aerial** (Geobasis NRW, open dl-de/by-2-0) via the **`/dop/{z}/{x}/{y}.jpg` proxy** in `api.py` — it renders each web-mercator tile from the NRW WMS and **disk-caches** it under `static/vendor/dop-cache/` (gitignored), guarded to the NRW zoom/extent. So repeat views are instant, and it costs no MapTiler quota.
- **Streets/labels:** **OpenFreeMap** vector `transportation`/`transportation_name`/`place` layers drawn over the aerial (white text, dark halo) in the MapLibre style (`_meetupMapStyle`). Free, OSM-based.
- **POIs:** from the MapTiler dataset (`/chat/api/pois`, see `MAPTILER_DATASET_ID`), rendered as HTML markers — dot and name-label are **separate markers** so z-order is labels > red drag-pin > dots; tapping a POI drops the pin on it. Category → colour via `POI_COLORS` (with sub-type overrides); unknown categories get a stable palette colour from `POI_PALETTE`.
- **POI sub-types:** `_normalize_poi_category(cat, name)` in `chat_api.py` splits coarse MapTiler categories into finer types based on the POI name via `_POI_SUBTYPE_RULES`. Sub-types: `wine_bar` (from drinks), `entrance`, `locker`, `first_aid`, `lost_found`, `vinyl_shop`, `merch_shop`, `shuttle`, `awareness`, `science`. Each sub-type has its own colour in `POI_COLORS` and icon in `POI_ICONS`.
- **POI icons:** `POI_ICONS` maps each type to `{vb, d}` (tight viewBox + SVG path data, Font Awesome). White fill on the category-coloured rounded rectangle. Icons appear at `maxZoom - 2` (CSS class `poi-icons-on`), labels at the same zoom (`poi-labels-on`). At lower zoom levels, POIs render as 14px coloured dots. Icon containers are 30px with 5px padding. All viewBoxes are tightly cropped to path bounds (~3px breathing room).
- **Label collision avoidance:** on map load, POIs are projected to screen coordinates at the label-visible zoom level. For each POI, if another POI sits in the label zone to its right (within 100px × 20px), the label flips to anchor `'right'` (placed to the left). Hover on a dot shows its label at any zoom.
- **Considered but deferred: SDF sprite layer** — would eliminate the sub-pixel HTML-marker jitter during pinch-zoom and give GPU-rendered icons with built-in collision avoidance, but adds complexity (canvas-rendered SDF registration, `map.on('click', layer)` wiring) for a marginal visual improvement with ~20 POIs. Current HTML markers are adequate.
- **Framing is POI-driven:** `/config` returns `meetup_bounds` = the POI extent made **square (in metres) × (1 + `meetup_bbox_expand_pct`)** (chat_settings, default **15%**; empty `meetup_bbox` = auto, or set it to `"w,s,e,n"` to override). The client fits that square inside the container (`fitBoundsOptions`), then locks **min-zoom to the fitted zoom and `maxBounds` to the fitted view** — so the POIs+expand are **always fully visible on any aspect ratio** and default-zoom == min-zoom (zoom in only). Custom attribution: a fixed "i" that opens a full-width credit box to its left (credits Geobasis NRW, OpenFreeMap, OpenStreetMap, MapLibre); any map click closes it.
- **Locate-me button:** top-right corner of the map (`meetup-locate-btn`), crosshair GPS icon. Taps `geolocation.getCurrentPosition` with `enableHighAccuracy`, drops the pin and recenters if inside the venue bounds, shows toast if outside. Loading state via `.loading` class.
- **Location input:** the creation form has a text input + an icon-only GPS button (pin icon, no label) that opens the map picker. Location is mandatory — either GPS coords from the map or a text label must be provided.

**Meeting-point photo** (`meetups.photo_url`, migration-added): uploaded via a clickable 21:9 drop zone (`meetup-photo-upload`) with a landscape icon placeholder — clicking opens the file picker, the photo fills the zone (`object-fit: contain`), clicking again replaces it. Client-side `resizeImage` (bicubic via `createImageBitmap` `resizeQuality: 'high'`) + server-side Lanczos3 (pyvips) to 1500px max. OpenAI-moderated in the create handler (`validate_meetup_photo` in `chat_ws.py`, WS + REST), URL validated against the upload allowlist, attendee-gated in `_shape_meetup`, unlinked on delete/expiry. Stored GPS rounded to ~11 m.

**Deferred (NOT done — conscious decisions, do not re-report as findings):** rooms↔meetups FK + `stage_id`→`origin_room_id` rename; meetup edit; bell "Get notified" RSVP-relabel/decouple; full meetup push wiring (attendees are in `meetup_attendees` but NOT `room_memberships`, so `get_unread_counts` has no meetup branch → meetup-room messages don't generate push yet); pre-meetup reminders; attendee-list UI; dedicated meetup rate limit; keyboard-activatable list rows.

### Admin Page

Dark-themed SPA at `/chat/admin` (shortcut) or `/chat/api/admin`. Tabs: Rooms (create/edit/delete/reorder/set main/auto-join, manual/auto sort toggle, per-room message viewer + single-message delete, delete meetup rooms; DM rooms are excluded — E2EE and unmanageable here — and only group rooms can be set as the main room), Users (search + online-only filter, strike/mute/unmute/ban/unban/clear-warnings/delete, custom reason on ban/strike, view history, status/warnings columns), Reports (filter by pending/actioned/dismissed/all, room name + jump-to-user, ban/strike/dismiss), Banned (unban), Logs (auto-moderation timeline with search), Audit (every admin action with actor, humanized with old -> new values for edits), and — super-admin only — Admins (manage the admin list) and Settings (msg_char_limit + DM/room/meetup TTLs, live). Per-row actions in the Rooms and Users tabs live in a "..." menu (Users menu is grouped restrict / restore / destroy). Stats footer with auto-refresh. The active tab, the Users online-only toggle, and the Reports status filter persist across reloads (localStorage); free-text search boxes intentionally do not.

**Roles & multi-admin** (see `docs/admin-roles.md`). Auth resolves to an actor via `_resolve_admin`: the shared `X-Admin-Token` header is a bootstrap/emergency super-admin credential; a chat session cookie resolves to a role. `CHAT_ADMIN_EMAILS` entries are PERMANENT super-admins (never stored in the DB, never removable via the panel — the lockout-proof root set). DB-backed admins live in the `admins` table with role `admin` | `super_admin`. `_require_super_admin` gates the destructive endpoints (delete room/user, settings, unban, delete-ban, clear-warnings, admin management); moderators keep ban/mute/unmute/strike/reports/room-edit/message-delete/meetup-delete. `_guard_target` blocks moderating an env super-admin outright (owner-safe) and blocks an admin from moderating another admin (only a super-admin can). Every mutating endpoint records actor + action + target in `admin_actions` (surfaced in the Audit tab); this also gives mutes/unmutes/unbans their log visibility. `_require_admin` is per-IP rate-limited on failure (20/5min). Admin-panel handlers never interpolate untrusted strings into inline `on*` attributes (HTML-entity encoding is decoded before an inline handler compiles as JS, so an encoded quote breaks out) — handlers pass only ids and look names up from in-memory arrays; OAuth display names are sanitized before storage. Regression net: `tests/test_chat_admin_roles.py` (role resolution, super-admin gating, admin-account protection, admins CRUD, audit, message/meetup/settings/reports endpoints — through the ASGI stack).

Reports from E2EE DMs carry reporter-provided plaintext the server never independently verified — flagged `unverified` in the `reports` table, shown with a warning banner in the admin UI alongside the reporter/reported user history.

### Chat UI

Main room auto-opens on login. Path-based routing (`/chat`, `/chat/r/{id}`, `/chat/d/{user}`, `/chat/m/{id}`, `/chat/msg/{id}`). Single HTML file (`server/chat/chat.html`).

- **Design system**: CSS custom properties for grays (7 levels, WCAG AA/AAA), fonts (xxs-xl), spacing (4px scale), radius (sm-pill), shadows (sm-lg). 12 user color pairs + self color.
- **Bubble style**: user-colored pastels (assigned at registration), dark text, time bottom-right
- **Header**: room name + member count (reachable users), user avatar (opens settings menu: Profile, Notifications, Log out). Desktop header includes calendar icon linking to lineup.
- **Replies**: double-click on desktop, swipe toward center on mobile. Quote shown inside bubble.
- **Reactions**: hover-based on desktop (200ms dismiss), long-press on mobile. 6-emoji picker. Button outside bubble with 88px hover zone.
- **Input bar**: + button (meetup, location, photo, video) on left, emoji picker inside textarea, send button on right. Textarea expands from 1 to 5 lines as you type (grows upward, buttons stay anchored). Shift+Enter for newline. No scrollbar when scrolling past 5 lines. Pill shape for single line, rounded rectangle when multiline.
- **Message char limit**: configurable via `chat_settings.msg_char_limit` (default 1000). Client reads from `/chat/api/config`, shows red border + disables send at limit, allows typing up to limit+50 for visibility. Server rejects at limit+20 (JSON wrapper overhead). Change in DB, no deploy needed.
- **Images**: client resizes to max 1500px via `createImageBitmap` + converts to WebP Q=0.8 before upload. Server always re-processes through pyvips (OWASP: strip injected metadata), creates 800px moderation copy. HEIC supported via `unlimited=True` fallback for iPhone Live Photos.
- **Videos**: client-side processing via Mediabunny + WebCodecs. HEVC with H.264 fallback, hardware-accelerated. Auto re-encodes if >1080p, >10Mbps, >30fps, or non-AAC audio. Trim editor for >60s. Server validates in temp file (ffprobe) before moving to uploads dir. Frame extraction: ffmpeg→PNG (lossless)→pyvips→WebP Q=60. Intermediate files in `chat/tmp/`, cleaned on server startup. Inline playback (click play/pause, fullscreen icon), expanded viewer with frame sync.
- **Location sharing**: GPS with confirmation dialog, card with map pin icon
- **Meetup cards**: calendar icon, title, time, "N going" count. Full-width Join/Joined button below card (hidden for creator). Join auto-subscribes to meetup chat for notifications.
- **Meetup creation**: modal with title, date + hour/minute selects (15-min intervals), location (required — text input + icon-only map-picker button), note (80-char limit with red-border visual feedback matching message input, hard cap at 100), and meeting-point photo (21:9 clickable drop zone).
- **Message delete**: right-click bubble (desktop) or long-press (mobile), confirmation in same action sheet, 120s window, server enforced
- **Message permalinks**: `/chat/msg/{id}` resolves to room, opens it, scrolls to and highlights message. Graceful fallback for deleted messages.
- **Upload security** (OWASP File Upload Cheat Sheet): all images re-processed through pyvips (strips metadata/payloads). Videos validated in temp file before moving to served directory. Uploads served via secure endpoint with filename allowlist (`[a-f0-9]{32}.(webp|mp4)`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'`. No directory listing. Moderation intermediate files (`_mod*.webp`) not served. Upload rate limit: 10/min per user. Moderation files deleted after use; startup sweeps `chat/tmp/`.
- **Unread badges**: red pill badges on room items and tab headers. `room_memberships` table tracks joined rooms + `last_read_at`. Server sends `badge_counts` on connect and `badge_update` on new messages for offline members. `mark_read` clears on room open. Duplicate message detection (2-min window, 5+ chars).
- **DM list**: `GET /chat/api/dms` returns `other_avatar_url`, `other_color_index`, `other_country`, `other_has_key` per conversation. The list live-refreshes when a `badge_update` arrives for a DM room not currently open.
- **User menu**: action sheet (Send Message, Block, Cancel). Block hides all messages from that user client-side (filtered in renderMessages + appendMessage). Server-side, a blocked sender's messages are also excluded from the blocker's unread counts and live badge fan-out, so blocking the last poster in a room can't strand its unread badge. Blocked users dimmed in member list (40% opacity, sorted to bottom). Unblock via user menu or Settings → Blocked Users list. Blocked user never knows they're blocked.
- **Message context menu**: right-click (desktop) or long-press (mobile) → Reply, Report, Cancel. Report submits message snapshot to admin. Report & Block also blocks the user immediately.
- **Reports**: stored with human-readable snapshot (`[timestamp] Name: text`), survive message TTL. Admin actions: ban, strike, or dismiss.
- **Optimistic messaging**: messages appear instantly with pending state, confirmed on ack, removed if moderation rejects
- **Scroll**: messages pushed to bottom via flex justify-content, app hidden until routing completes, ResizeObserver locks scroll for 1.5s after render
- **Desktop**: sidebar + chat panel side-by-side (768px breakpoint)
- **URL structure**: `/line-up` (lineup), `/timetable` (timetable), `/chat` (main chat), `/chat/r/{id}` (room), `/chat/d/{user}` (DM), `/chat/m/{id}` (meetup), `/chat/v/{token}` (verify email), `/chat/msg/{id}` (message permalink), `/chat/admin` (admin). API under `/chat/api/`. `/` redirects to `/line-up` or `/timetable` based on saved preference.
- **Page titles**: `Line-up · ST26`, `Timetable · ST26`, `Chat · ST26` — short name from `events.short_name` in lineup DB, loaded at server startup
- **Mobile navigation**: chat icon (dialog bubbles) on lineup/timetable header left, calendar icon on chat header left. Both `position: absolute; left: 4px`, matching hamburger at `right: 4px`. SVG viewBox scaled to match hamburger visual weight.
- **Toast**: word-based duration (1.5s + 300ms/word, min 4s), balanced text, max 360px
- **First-run notification prompt**: a NON-blocking top banner (`#notif-prompt-banner`), not a full-screen modal — it sits below the header and never covers the composer/send button. It arms only after the user's first sent message (`sent_first_msg`), auto-dismisses after 12s without persisting (so an unnoticed prompt is re-offered next session), and only an explicit Enable/dismiss sets `notif_prompt_done`. An explicit disable also sets `notif_prompt_done` so the user is never nagged again.
- **Debug**: 236 `dbg()` calls with timecodes across all functions, `verify()` checks DOM state

### Chat Tests

281 tests total — 227 core (`python -m pytest tests/ -v`) + 54 notification (Playwright, run separately):
- `test_chat_db.py` (59) — all CRUD, cascade deletes, purge, wipe
- `test_chat_moderation.py` (39) — word filter, AI mocks, strike escalation (expiry, reset, mute cycling)
- `test_chat_ws.py` (44) — WebSocket rooms, messaging, presence, moderation flow
- `test_chat_api.py` (63) — REST endpoints, auth, rooms, meetups (incl. cancel-deletes-invite), DMs, admin
- `test_chat_admin_roles.py` (22) — multi-admin roles, super-admin gating, admin-account protection, admins CRUD, audit, admin endpoints

Two suites run outside pytest: `test_notifications.py` (54 tests — push debounce, payload, badge, clearing; requires Playwright infra, run separately) and `tests/e2ee_browser_check.py` (standalone Playwright verification, 21 checks — see "End-to-End Encryption (DMs)" below).

### Automated Notification Test Harness

`tests/notif_e2e/` — 21 scenarios across three stages, all green (see its `README.md` and `docs/notification-test-design.md`). Each scenario runs against a fully isolated server (own port, scratch `chat.db` + `hearts.db`, freshly generated VAPID keypair, sensitive env stripped) and writes a signal timeline to `_artifacts/<scenario>.json`.

- **Stage 1 (emission, no browser)** — a WebSocket-client sender drives the real server; a **Fake Push Service** (aiohttp, impersonates FCM/Apple/Mozilla) captures the real WebPush and **decrypts it** (aes128gcm via `http_ece`, using injected subscription keys) and parses the VAPID JWT, so assertions run on the exact payload / TTL / per-service VAPID `aud` (the anti-poisoning invariant), targeting, debounce/silent, 410 pruning, and pending-moderation gating.
- **Stage 2 (client behavior)** — real headless Chromium via Playwright's **sync** API, with a subscribe-success override (headless can't reach a real push service), spies on `Notification`/`setAppBadge`/`sendBeacon`/`document.hasFocus`, and WS + push-network capture. Covers enable/disable/repair, idle beacon, the focus-gated keepalive, cross-device badge fan-out+clear, and the non-blocking first-run banner.
- **Stage 3 (service-worker handlers)** — runs the real `sw.js` source in a **mock SW environment** (`new Function('self','fetch','caches','navigator', swSrc)` with recording mocks) and dispatches synthetic push/notificationclick/notificationclose/pushsubscriptionchange events. Necessary because headless Chromium has no notification backend (a real SW's `showNotification` rejects, so the handler chain that runs swlog/ack never completes) and Playwright's SW Worker handles die too aggressively.

Run: `python tests/notif_e2e/run.py [--sw | --browser | --all | --list]`. **`--browser`/`--all` must run OUTSIDE the command sandbox** (headless Chromium needs Mach-port access). Stage 4 (headed real-browser FCM + real OS-notification render) is designed but not built.

### Stress Test

```bash
# Quick smoke test (20 users, 2 min, no OpenAI cost)
python tests/stress_test/run.py --users 20 --duration 120 --insecure --no-moderation

# Full production-like test (200 users, 30 min, moderation on)
python tests/stress_test/run.py --insecure

# On VPS
python tests/stress_test/run.py --url https://stonetechno.deftlab.dev \
    --db /root/services/stone-techno/server/data/chat.db

# Clean up interrupted run
python tests/stress_test/run.py --cleanup-only
```

Dependencies: `pip install websockets httpx psutil`

Features tested: text messages (with random suffix to avoid dedup), replies, image uploads (1500px WebP Q=80 matching browser output), video uploads (H.264 MP4 matching Mediabunny output), reactions, location sharing, meetup create/join, message deletion, mark read, DMs, multi-room messaging, burst testing (50 concurrent messages, 10 concurrent image uploads with 12s rate-limit cooldown).

Metrics: ack/broadcast/room-history/upload(image+video)/connect latency (p50/p95/p99/max), ack latency over time in 5-min windows, send/recv rates, CPU, RAM, chat.db + uploads/ size growth, network I/O, message delivery verification (sent vs seen), burst test results, estimated moderation cost. Server-side upload instrumentation logs per-step timing (decode/save/mod for images, write/probe/frames for videos).

Cleanup: only deletes users with `provider='stress_test'` and rooms with `name LIKE 'Stress:%'`. Verifies non-test room count is unchanged.
