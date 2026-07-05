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

# Run full server locally (lineup + chat)
cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem
# Open https://localhost:64728/line-up and https://localhost:64728/chat

# Run tests
python -m pytest tests/ -v
```

## Local Development

**Always preview via HTTP, never `file://`.** The page uses `fetch()` for lazy-loaded bios and API calls. Browsers block fetch from `file://` origins (CORS).

**For lineup only**: `cd output && python3 -m http.server 8321` — expected 404s for `/manifest.json`, `/sw.js`, `/api/me`.

**For lineup + chat**: run the full FastAPI server: `cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem`. Symlinks in `server/static/` point to `output/` files so lineup reflects latest build.

**Chat requires auth**: sign in via email magic link at `/chat`. For local dev, set `CHAT_BASE_URL=https://localhost:64728` in `.env` so the magic link points to localhost.

## System Dependencies

Not pip-installable, must be present on the system:

- **Playwright + Chromium**: `pip install playwright && playwright install chromium`
- **libvips**: `brew install vips` (macOS) — required by pyvips for image processing
- **ssimulacra2**: binary must be in PATH — perceptual quality targeting for AVIF encoding

Python dependencies: `playwright`, `beautifulsoup4`, `pyvips` (scraper); `fastapi`, `uvicorn[standard]`, `pywebpush` (server); `yt-dlp` (video discovery); `markdown` (bio rendering); `email-validator` (auth); `maileroo` (magic link emails).

System: `ffmpeg` + `ffprobe` must be in PATH for video upload (frame extraction for moderation).

## Architecture

### Data flow

1. `stone_techno_companion.py` orchestrates: scrape → enrich → process photos → render HTML + timetable.json + bios.json
2. `lineup.db` (SQLite, WAL mode, FK enforcement) is the single source of truth — artists, links, sets, schedule, locations, events
3. `scraper/overrides.toml` provides manual corrections (artist links), editorial data (floor curators), and YouTube video overrides — applied as patches to the DB
4. `fetch_videos.py` discovers YouTube sets via yt-dlp and writes to the `artist_sets` table
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
| `scraper/scrape.py` | Lineup parser + SoundCloud/Instagram/Spotify/Resident Advisor scrapers. Each event needs its own scraper module. |
| `scraper/db.py` | SQLite schema, upserts, overrides, queries — all event-scoped |
| `scraper/images.py` | Photo resize (pyvips lanczos3) + AVIF encode (ssimulacra2 target 78) |
| `scraper/render.py` | HTML generation — line-up list + timetable grid, CSS, JS, modals, hearts, schedule, push notifications. Markdown bio rendering. Dynamic floor color CSS. SVG icons via `<symbol>`/`<use>` sprite |
| `scraper/timetable_json.py` | Generates `timetable.json` — slot UUID → set time mapping for push scheduler and ICS endpoint. Reads timezone from events table. |
| `fetch_videos.py` | YouTube set discovery via yt-dlp. Writes to `artist_sets` table with `platform='youtube'`. |
| `seed_timetable.py` | Seeds fake timetable data (floors + time slots) for development |
| `migrate_db.py` | One-time migration from any old schema version to current. Creates backup, migrates artists + links + sets + locations + notes. |
| `server/api.py` | FastAPI app — favorites + schedule API + WebSocket sync + push scheduler + ICS export + static file routes. Mounts chat module at startup. |
| `server/chat_db.py` | Chat SQLite schema (chat.db) — users, sessions, bans, rooms, messages, meetups, reactions, blocks, reports, strikes, E2EE device key store |
| `server/chat_moderation.py` | Three-layer moderation: word filter + OpenAI omni-moderation + GPT-5.4-nano drug detection. All via raw httpx. |
| `server/chat_ws.py` | Chat WebSocket server — rooms, optimistic messaging, presence, typing, reactions, replies, meetups, DMs, purge loop, E2EE content gating (DM-only envelopes, generic previews, snippet redaction) |
| `server/chat_api.py` | Chat REST API — auth (Google/Email), rooms, meetups, DMs, media upload, admin page, E2EE key endpoints. Mounts routes + WS into FastAPI. |
| `server/chat/chat.html` | Chat frontend — single HTML file with inline CSS/JS. WhatsApp-style bubbles, reactions, replies, action menus, client-side E2EE (per-device keys, encrypt/decrypt, key rotation). |
| `server/chat/admin.html` | Admin dashboard — dark-themed SPA with tabs: Rooms, Users, Reports, Banned, Logs. Room management (create/edit/delete/reorder), user moderation (strike/mute/ban/clear), moderation log. |
| `server/chat/blocklist.txt` | Word filter blocklist (drug terms, slurs). Editable without deploy. |
| `server/static/shared.css` | Unified design tokens and shared CSS components (loaded by all pages) |
| `server/static/shared.js` | Shared JS utilities: escapeHtml, dbg, showToast, storageGet/Set, icon constants (loaded by all pages) |
| `server/static/sw.js` | Service worker — push ack (delivered/clicked/dismissed), notification click, pushsubscriptionchange auto-resubscribe |
| `server/static/manifest.json` | PWA manifest — enables Add to Home Screen and push on iOS |
| `tests/test_chat_db.py` | 59 tests — users, sessions, bans, rooms, messages, meetups, DMs, blocks, reports, strikes |
| `tests/test_chat_moderation.py` | 39 tests — word filter, strike system (expiry, reset, mute cycling), AI moderation pipeline |
| `tests/test_chat_ws.py` | 42 tests — WebSocket rooms, messaging, presence, moderation flow |
| `tests/test_chat_api.py` | 55 tests — REST endpoints, auth, rooms, meetups, DMs, admin |
| `tests/test_notifications.py` | 54 tests — push debounce, payload, badge, clearing. Requires Playwright infra. |
| `tests/e2ee_browser_check.py` | Standalone Playwright verification (not part of the pytest suite) — 21 checks across 5 browser contexts, see "End-to-End Encryption (DMs)" below |
| `stress_test/run.py` | Chat stress test — 200 concurrent WS users, multi-room + DMs, burst testing, media uploads, latency/throughput/resource metrics, moderation cost estimation |

### Deploy

```bash
# Server code deploy (backup + pull + rebuild + health check)
./deploy.sh

# Content deploy (lineup HTML + photos — no server restart needed)
python stone_techno_companion.py --render-only --deploy
```

`deploy.sh` does: download VPS data to `backups/{timestamp}/` locally, create timestamped backup on VPS, `git pull`, `docker compose up --build --force-recreate`, health check (container + chat API), prune old VPS backups (keeps 5). Local backups survive VPS disk failure.

## Generated Artifacts (gitignored)

- `lineup.db` — SQLite database (all tables)
- `lineup.db.bak` — backup created by migrate_db.py
- `output/lineup.html` — generated page (~650 KB)
- `output/bios.json` — artist bios + sets, lazy-loaded on first artist tap (~200 KB)
- `output/photos/*.avif` — processed artist photos
- `output/timetable.json` — slot UUID → set time mapping for push notifications
- `output/thumbs/*.avif` — YouTube video thumbnails (240px max, AVIF)

- `server/data/` — runtime databases (hearts.db, chat.db), VAPID keys (gitignored)
- `server/chat/uploads/` — uploaded images/videos (WebP, MP4)
- `server/chat/tmp/` — intermediate processing files (auto-cleaned on startup)
- `stress_test/media/` — auto-generated test images (WebP 1500px Q=80) + videos (H.264 MP4) + user-provided files
- `stress_test/report_*.txt` — stress test reports
- `stress_test/debug_*.log` — stress test debug logs

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

## Page Load Flash Prevention

All pages use `body{opacity:0}` in an inline `<style>` in `<head>` (first element) to prevent content from flashing before JS initialization completes. JS sets `document.body.style.opacity='1'` after all init work is done (sticky tops calculated, views switched, data loaded).

The lineup/timetable page has an additional mechanism: a `<script>` in `<head>` (before body) sets `document.documentElement.className='view-list'` or `'view-timetable'` based on the URL/localStorage. CSS rules keyed to these classes control which view, buttons, and menu items are visible — so the correct view state is applied before any body content is parsed. The `switchView()` function updates this class when switching views at runtime.

## Working on the HTML/CSS/JS

All frontend code lives in `scraper/render.py` as Python string concatenation. Shared CSS lives in `server/static/shared.css`. Shared JS utilities live in `server/static/shared.js`.

```bash
python stone_techno_companion.py --render-only --no-photos
cd output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

## Server

FastAPI (`server/api.py`). Sessions via 128-bit URL-safe tokens. Cross-device sync via ephemeral 6-digit PINs (5-min TTL). Real-time sync via WebSocket. Atomic pick/schedule operations via `json_group_array`/`json_each`.

Static file routes (`/bios.json`, `/manifest.json`, `/sw.js`, `/favicon.*`) are explicit endpoints before the catch-all `/{path:path}` (which serves `index.html`). New static files need an explicit route in `api.py`. `timetable.json` has no HTTP route — it's read server-side by the push scheduler and ICS export only.

The catch-all `/{path:path}` serves `index.html` with `Cache-Control: no-store` and explicitly rejects `/chat*` paths (returns 404). Chat module import is **required** — if `chat_api.py` fails to import, the server crashes at startup (fail-fast, no silent degradation).

Production: Docker on DigitalOcean VPS behind Caddy (auto-TLS). DBs at `server/data/` volume-mounted (hearts.db, chat.db, vapid_private.pem).

### Environment Variables (`server/.env`)

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Chat moderation (omni-moderation + GPT drug detection) |
| `MAILEROO_API_KEY` | Yes | Magic link email delivery (was Resend, switched July 2026) |
| `CHAT_EMAIL_FROM` | No | From address for magic links (default: `no-reply@deftlab.dev`) |
| `CHAT_BASE_URL` | Dev only | Set to `https://localhost:<port>` for local dev. Omit in production. |
| `VAPID_PRIVATE_KEY` | Yes | Push notification signing |
| `VAPID_PUBLIC_KEY` | Yes | Push notification subscription |
| `VAPID_CLAIMS_EMAIL` | Yes | VAPID contact email |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID (from Google Cloud Console) |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret (for authorization code exchange) |
| `CHAT_ADMIN_EMAILS` | No | Comma-separated admin emails for cookie-based admin auth |
| `CHAT_ADMIN_TOKEN` | No | Admin token for header-based admin auth (fallback) |
| `CHAT_EVENT_ID` | No | Event ID (default: `stone-techno-2026`) |
| `CHAT_DB_PATH` | No | Test/dev override for chat.db location. Used by the browser verification harness (`tests/e2ee_browser_check.py`) to point at an isolated scratch DB. |

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

**Automatic on deploy**: Dockerfile installs ffmpeg + libvips + all Python deps. `chat.db` created fresh on first run. `chat/uploads/` and `chat/tmp/` auto-created.

## Push Notifications

### Hard-won invariants (violating any of these silently breaks a subset of browsers)
- **pywebpush mutates `vapid_claims`**: it stamps the FIRST endpoint's origin as `aud` into the dict you pass. Never share one claims dict across a subscription loop — pass `dict(vapid_claims)` per call (both `chat_ws._do_send_push` and the lineup scheduler in `api.py` do this). A shared dict poisons every later push to a *different* push service: FCM rejects an apple `aud` with 403, so a user with Apple + FCM subscriptions only ever reached the first service. Cost a full afternoon in July 2026 because Apple and Mozilla don't enforce the binding — iOS and Firefox kept working while Brave/Chrome got nothing.
- **Push services enforce VAPID asymmetrically**: FCM (all Chromium) strictly validates both the `aud` claim and that the signing key matches the subscription's `applicationServerKey`; Apple and Mozilla accept any self-consistent JWT. **Any push change must be tested against a Chromium-family browser** — Zen/Firefox and iOS passing proves nothing about Chrome/Brave.
- **VAPID key pair consistency is checked at startup** (`_check_vapid_key_consistency` in `api.py`): logs `VAPID key pair verified` or a loud mismatch error. Check this line after touching keys or `.env`.
- **Notification tags must be unique across server restarts**: the payload carries a random `push_id` (`secrets.token_hex(8)`) and sw.js prefers it for the tag. `push_index` alone resets with the process and re-collides with notifications still in iOS Notification Center (see "tag uniqueness" below).
- **Brave subscriptions can silently die**: revoking site notification permission or Brave's "Forget me when I close this site" unsubscribes at the FCM level (next push → 410, row auto-pruned). The client repairs on load (`_repairPushSubscription`, gated by the `push_enabled` localStorage flag so an explicit disable is never overridden), and `_enableAllNotifications` only reports success when subscribe + server POST both succeeded.
- **Regression net**: `tests/test_chat_ws.py::TestVapidClaimsIsolation` (claims-dict isolation), `tests/notif_badge_browser_check.py` (badges, truthful enable, gated repair — multi-context Playwright), `tests/test_notifications.py` (SW tag/version assertions).

### Lineup push
- **Scheduler**: background task runs every 60s, matches `timetable.json` slots against sessions' schedule, sends via `pywebpush`
- **Dedup**: `sent_notifications` table, pruned after 7 days. Dead subscriptions auto-removed.
- **Re-sync on load**: client re-sends push subscription to recover from DB purges
- **iOS notification click — tag uniqueness is critical**: iOS silently drops `notificationclick` for any notification that *replaced* an earlier one (same `tag`). The tap opens the app at `start_url` with no event, no error. So `showNotification` MUST use a unique tag per notification (derived from the push URL). This was the root cause of "notification click lands on line-up" — room-tag reuse meant every organic message notification was a replacement, while one-off test pushes (never replaced) worked. Diagnosed July 2026 via [push-diag](https://github.com/gabrielelosurdo/push-diag) + server-side SW timeline logging (`POST /chat/api/swlog`, `[SWLOG]`/`[PUSH-ACK]` log lines).
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
rooms              — id, event_id, type, name, description, is_main, is_moderated, is_read_only, auto_join, allows_media, ttl_minutes, position, created_at
chat_settings      — key, value (app-level config: room_sort, msg_char_limit, dm_ttl_minutes, room_ttl_minutes, meetup_ttl_minutes)
room_memberships   — user_id + room_id (PK), joined_at, last_read_at (tracks joined rooms + unread)
messages           — id, room_id, user_id, type, content, link_preview, reply_to_id, expires_at, created_at
message_reactions  — message_id + user_id + emoji (PK), created_at, CASCADE on message delete
meetups            — id, creator_id, stage_id, title, location_lat, location_lng, location_label, meetup_time, note, created_at, expires_at
meetup_attendees   — meetup_id + user_id (PK), joined_at
dm_participants    — room_id + user_id (PK)
blocks             — blocker_id + blocked_id (PK), created_at
reports            — id, reporter_id, reported_user_id, message_snapshot, room_id, reason, status, unverified, created_at, reviewed_at
strikes            — id, user_id, reason, detail, created_at, expires_at (4h TTL, reset on new strike)
chat_push_subscriptions — id, user_id, endpoint, p256dh, auth, created_at
e2ee_device_keys   — user_id + device_id (PK), public_key, created_at, last_seen
```

### Auth

Two passwordless providers: Google OAuth and Email magic link (via Maileroo, 3,000/mo free). Disposable domains blocked via 7,860-domain blocklist (`chat/disposable_domains.txt`). Email validation via `email-validator` library (RFC 5322 + DNS MX check). Device fingerprinting for ban enforcement. Session cookies (non-httpOnly for WS access, Secure in production, SameSite=Strict in production / Lax in dev, path=/). Email tokens stored in DB (not memory) — survive server restarts.

### Profile Setup

Mandatory before entering chat: username, avatar photo, country. Optional display name. Profile prompt shown on first login.

- **Username**: unique, case-insensitive alphanumerics (`a-z A-Z 0-9 . _ -`), 2-20 chars, stored lowercase in `username_lower` for uniqueness checks. Live availability check (400ms debounce). Shown in bubbles when no display name set.
- **Display name**: optional, Latin Unicode letters + digits + spaces + `. _ -`, 2-30 chars. Replaces username in bubbles when set. Live validation.
- **Avatar**: circular 128px pan+zoom editor. Click to select image (min 128x128), drag to pan, custom friction slider to zoom. Client crops to 128x128 via `createImageBitmap` with `resizeQuality: 'high'`. Stored as WebP blob in `avatars` table. Served via `/chat/api/avatar/{user_id}?v=timestamp` (version stamp for cache busting). Large images (>2000px) downscaled in browser for smooth editor, full-res used for final crop.
- **Country**: searchable dropdown with 196 countries + local name aliases (Deutschland, Italia, Espana, etc.). Search matches from start of word only, exact match for 2-char codes, 3+ chars for aliases. Arrow key navigation, Enter to select, first result highlighted.
- **User colors**: 12 vivid+pastel color pairs assigned randomly at registration (stored as `color_index`). 13th "self" color for own messages. Others see your assigned color.
- **Name moderation**: OpenAI omni-moderation on submit (no word filter for names — too many false positives).
- **Profile edit**: settings menu via avatar in header. Edit display name, avatar (full pan+zoom editor), country. Live preview bubble.

### Moderation Pipeline

Every message in a moderated (group) room passes through three layers before broadcast:

1. **Word filter** (instant) — in-memory set from `chat/blocklist.txt`. Drug terms, slurs, spam. Character substitution normalization (@→a, 0→o, etc.).
2. **OpenAI omni-moderation-latest** (free) — harassment, hate, violence, sexual content. Supports images (WebP data URI) and video (3 frames at 25/50/75% extracted by ffmpeg). Via raw httpx.
3. **GPT-5.4-nano content detection** (Responses API, reasoning=none) — catches drugs, spam/scams, payment links, external platform links (Telegram, WhatsApp, Discord). Explicit safe list for festival conversation.

**DMs are exempt**: DMs are created with `is_moderated=False` and are end-to-end encrypted, so the server cannot run these layers on their content. Moderation is replaced by user reporting — see "End-to-End Encryption (DMs)" below.

Layers 2 and 3 run in parallel via `asyncio.gather`. Word filter blocks before AI calls (saves API round-trips).

**Optimistic delivery**: message saved to DB immediately, `message_acked` sent to sender, moderation runs in `asyncio.create_task`. If passes: broadcast to others. If fails: delete from DB, send `message_removed` + strike to sender. Mute/ban also deletes all user's active messages and broadcasts removal.

**Moderation logging**: OpenAI scores logged via `logger.info` — top 5 categories above 0.1 threshold, FLAGGED line with threshold comparison. `logging.basicConfig(level=INFO)` configured at startup.

**Strike system**: 4-step escalation with expiring strikes (4h TTL, reset on new violation). 1st = warning, 2nd = warning, 3rd = 30-min mute, 4th = permanent ban. Lifetime mute counter: 3 total mutes across the event = permanent ban (prevents cycling). Same escalation for all content types including drugs. Bans stored by provider_id + device fingerprint. `secure_delete=ON` zeros deleted data on disk.

### End-to-End Encryption (DMs)

DMs — and only DMs — are end-to-end encrypted; group rooms stay unencrypted and moderated as above.

- **v2 multi-device design**: each browser profile is a device — a 32-hex `device_id` + a P-256 ECDH key pair generated and kept in localStorage. Content is encrypted once per message with a random per-message key, then that key is wrapped separately for every device of BOTH participants, including the sender's own other devices.
- **Envelope** (stored in the existing `content` TEXT field): `{e2ee, v: 2, sd: <sender_device_id>, ct: <encrypted content>, keys: {<device_id>: <wrapped key>, ...}}`.
- **Server storage**: `e2ee_device_keys` table (capped at 6 devices/user, pruned after 7 days of inactivity). `PUT`/`GET /chat/api/keys` register/fetch device keys with device_id + JWK validation. `key_rotated` WS event notifies DM peers per room plus a self-notification (room_id null) when a device re-keys.
- **Server cannot read DM content**: moderation is skipped, push previews are generic ("Sent you a message"), reply snippets are blanked server-side and rebuilt client-side, link previews are skipped. Reports carry reporter-provided plaintext, flagged `unverified`.
- **Fallback**: keyless peers (no registered devices) fall back to plaintext, with lock-icon/banner UI suppressed accordingly.
- **No history sync**: a newly registered device cannot decrypt messages sent before it existed; the 60-minute message TTL bounds how long that gap is visible.
- **Specs**: `docs/e2ee-dev.md` (v1 design + server adaptations) and `docs/e2ee-multidevice.md` (v2 multi-device design, current).
- **Verification**: `python tests/e2ee_browser_check.py` — 21 checks across 5 browser contexts, isolated server + scratch DB via `CHAT_DB_PATH`.

### Room Properties

Rooms have configurable properties set via the admin page:
- `description` — what the room is for
- `ttl_minutes` — per-room message TTL, defaults from `chat_settings`: DMs 24h (`dm_ttl_minutes: 1440`), rooms 24h (`room_ttl_minutes: 1440`; code falls back to 360 only if the settings row is missing), meetups 1h after meetup time (`meetup_ttl_minutes: 60`). Meetup expiry destroys messages + room + meetup record. DM rooms persist after messages expire (conversation thread stays).
- `is_moderated` — toggles word filter + AI moderation for the room
- `is_read_only` — only admins can post
- `auto_join` — new users automatically become members on WS connect (always on for main room)
- `allows_media` — disable image/video uploads
- `position` — custom sort order (drag-to-reorder in admin)

### Membership Model

Room "member count" reflects **reachable** users — those who can be notified:
- Has active WebSocket connection, OR
- Has valid push subscription AND `last_seen` < 2 hours ago

`last_seen` updated on: WS connect, WS disconnect, push notification delivery (via `POST /chat/api/push/ack`).
`last_active` updated on: engagement events (send message, react, join meetup, etc.), throttled to 1 write per 60s.

Push ack signals from service worker: `delivered` (updates last_seen), `clicked` (updates last_seen + last_active), `dismissed` (updates last_seen). Auto-resubscribe on `pushsubscriptionchange`.

Mute/delete user → all their messages deleted from DB + `messages_expired` broadcast to connected clients for instant removal.

### Admin Page

Dark-themed SPA at `/chat/admin` (shortcut) or `/chat/api/admin`. Auth via chat session cookie (matching `CHAT_ADMIN_EMAILS`) or `X-Admin-Token` header. Tabs: Rooms (create/edit/delete/reorder/set main/auto-join, manual/auto sort toggle), Users (search, strike/mute/ban/unban/delete, view history, status/warnings columns), Reports (ban/strike/dismiss), Banned (unban), Logs (moderation timeline with search). Stats footer with auto-refresh. Room sort modes: Auto (main first, bell-on by activity, bell-off by activity) or Manual (by position, drag-to-reorder).

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
- **Meetup creation**: modal with title, date + hour/minute selects (15-min intervals), GPS location, note.
- **Message delete**: right-click bubble (desktop) or long-press (mobile), confirmation in same action sheet, 120s window, server enforced
- **Message permalinks**: `/chat/msg/{id}` resolves to room, opens it, scrolls to and highlights message. Graceful fallback for deleted messages.
- **Upload security** (OWASP File Upload Cheat Sheet): all images re-processed through pyvips (strips metadata/payloads). Videos validated in temp file before moving to served directory. Uploads served via secure endpoint with filename allowlist (`[a-f0-9]{32}.(webp|mp4)`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'`. No directory listing. Moderation intermediate files (`_mod*.webp`) not served. Upload rate limit: 10/min per user. Moderation files deleted after use; startup sweeps `chat/tmp/`.
- **Unread badges**: red pill badges on room items and tab headers. `room_memberships` table tracks joined rooms + `last_read_at`. Server sends `badge_counts` on connect and `badge_update` on new messages for offline members. `mark_read` clears on room open. Duplicate message detection (2-min window, 5+ chars).
- **DM list**: `GET /chat/api/dms` returns `other_avatar_url`, `other_color_index`, `other_country`, `other_has_key` per conversation. The list live-refreshes when a `badge_update` arrives for a DM room not currently open.
- **User menu**: action sheet (Send Message, Block, Cancel). Block hides all messages from that user client-side (filtered in renderMessages + appendMessage). Blocked users dimmed in member list (40% opacity, sorted to bottom). Unblock via user menu or Settings → Blocked Users list. Blocked user never knows they're blocked.
- **Message context menu**: right-click (desktop) or long-press (mobile) → Reply, Report, Cancel. Report submits message snapshot to admin. Report & Block also blocks the user immediately.
- **Reports**: stored with human-readable snapshot (`[timestamp] Name: text`), survive message TTL. Admin actions: ban, strike, or dismiss.
- **Optimistic messaging**: messages appear instantly with pending state, confirmed on ack, removed if moderation rejects
- **Scroll**: messages pushed to bottom via flex justify-content, app hidden until routing completes, ResizeObserver locks scroll for 1.5s after render
- **Desktop**: sidebar + chat panel side-by-side (768px breakpoint)
- **URL structure**: `/line-up` (lineup), `/timetable` (timetable), `/chat` (main chat), `/chat/r/{id}` (room), `/chat/d/{user}` (DM), `/chat/m/{id}` (meetup), `/chat/v/{token}` (verify email), `/chat/msg/{id}` (message permalink), `/chat/admin` (admin). API under `/chat/api/`. `/` redirects to `/line-up` or `/timetable` based on saved preference.
- **Page titles**: `Line-up · ST26`, `Timetable · ST26`, `Chat · ST26` — short name from `events.short_name` in lineup DB, loaded at server startup
- **Mobile navigation**: chat icon (dialog bubbles) on lineup/timetable header left, calendar icon on chat header left. Both `position: absolute; left: 4px`, matching hamburger at `right: 4px`. SVG viewBox scaled to match hamburger visual weight.
- **Toast**: word-based duration (1.5s + 300ms/word, min 4s), balanced text, max 360px
- **Debug**: 236 `dbg()` calls with timecodes across all functions, `verify()` checks DOM state

### Chat Tests

195 tests total: `python -m pytest tests/ -v`
- `test_chat_db.py` (59) — all CRUD, cascade deletes, purge, wipe
- `test_chat_moderation.py` (39) — word filter, AI mocks, strike escalation (expiry, reset, mute cycling)
- `test_chat_ws.py` (42) — WebSocket rooms, messaging, presence, moderation flow
- `test_chat_api.py` (55) — REST endpoints, auth, rooms, meetups, DMs, admin

Two suites run outside pytest: `test_notifications.py` (54 tests — push debounce, payload, badge, clearing; requires Playwright infra, run separately) and `tests/e2ee_browser_check.py` (standalone Playwright verification, 21 checks — see "End-to-End Encryption (DMs)" below).

### Stress Test

```bash
# Quick smoke test (20 users, 2 min, no OpenAI cost)
python stress_test/run.py --users 20 --duration 120 --insecure --no-moderation

# Full production-like test (200 users, 30 min, moderation on)
python stress_test/run.py --insecure

# On VPS
python stress_test/run.py --url https://stonetechno.deftlab.dev \
    --db /root/services/stone-techno/server/data/chat.db

# Clean up interrupted run
python stress_test/run.py --cleanup-only
```

Dependencies: `pip install websockets httpx psutil`

Features tested: text messages (with random suffix to avoid dedup), replies, image uploads (1500px WebP Q=80 matching browser output), video uploads (H.264 MP4 matching Mediabunny output), reactions, location sharing, meetup create/join, message deletion, mark read, DMs, multi-room messaging, burst testing (50 concurrent messages, 10 concurrent image uploads with 12s rate-limit cooldown).

Metrics: ack/broadcast/room-history/upload(image+video)/connect latency (p50/p95/p99/max), ack latency over time in 5-min windows, send/recv rates, CPU, RAM, chat.db + uploads/ size growth, network I/O, message delivery verification (sent vs seen), burst test results, estimated moderation cost. Server-side upload instrumentation logs per-step timing (decode/save/mod for images, write/probe/frames for videos).

Cleanup: only deletes users with `provider='stress_test'` and rooms with `name LIKE 'Stress:%'`. Verifies non-test room count is unchanged.
