# Stone Techno Companion

A multi-event festival companion tool: scraper, enrichment pipeline, static site generator, and ephemeral chat with AI moderation. Includes real-time favorites, cross-device sync, push notifications, and privacy-first group chat with meetups.

**Live at:** [stonetechno.deftlab.dev](https://stonetechno.deftlab.dev/)

## What It Does

1. **Scrapes** the festival lineup page using Playwright (headless Chromium)
2. **Enriches** each artist via SoundCloud, Instagram, Spotify, and Resident Advisor — collects follower counts, discovers missing social links, fetches biographies
3. **Discovers YouTube sets** via yt-dlp — selects top DJ sets per artist with thumbnail downloads
4. **Processes photos** — downloads, resizes to 240x240, encodes to AVIF targeting ssimulacra2 quality score 78
5. **Generates** an interactive page (~650 KB) with lazy-loaded bios (~200 KB) and timetable data
6. **Serves** via a FastAPI backend with favorites API, WebSocket sync, push notifications, and ICS calendar export
7. **Chat** — ephemeral group chat with photo/video sharing, meetups, DMs. AI-moderated (word filter + OpenAI + GPT drug detection). Profile with avatar, country, name. Email magic link auth via Maileroo.

## Project Structure

```
stone-techno-companion/
├── services/
│   ├── data/                    # PRE-PRODUCTION — content preparation (runs locally)
│   │   ├── stone_techno_companion.py  # CLI entry point — orchestrates the full pipeline
│   │   ├── fetch_videos.py      # YouTube set discovery via yt-dlp → artist_sets table
│   │   ├── scraper/
│   │   │   ├── scrape.py        # Lineup parser + SC/IG/Spotify/RA scrapers
│   │   │   ├── db.py            # SQLite schema, upserts, queries — all event-scoped
│   │   │   ├── images.py        # Photo download, resize (pyvips), AVIF encoding
│   │   │   ├── render.py        # HTML generation — lineup + timetable + modals + JS
│   │   │   ├── timetable_json.py  # Generates timetable.json for push scheduler + ICS
│   │   │   ├── overrides.toml   # Manual corrections for links, curators, YouTube
│   │   │   └── icons/           # SVG icons — deduplicated via <symbol>/<use> sprite
│   │   ├── output/               # Generated (gitignored): lineup.html, bios.json,
│   │   │                        #   timetable.json, photos/*.avif, thumbs/*.avif
│   │   └── lineup.db            # SQLite database (gitignored)
│   └── companion/                # THE PRODUCT — what users interact with (runs on VPS)
│       ├── api.py                # FastAPI — favorites + schedule + push + ICS + chat mount
│       ├── chat_db.py             # Chat SQLite schema + CRUD (chat.db)
│       ├── chat_moderation.py     # Word filter + OpenAI + GPT drug detection
│       ├── chat_ws.py             # Chat WebSocket server + purge loop
│       ├── chat_api.py            # Chat REST API + admin page + auth
│       ├── chat/
│       │   ├── chat.html          # Chat frontend (single file, inline CSS/JS)
│       │   ├── admin.html         # Admin dashboard SPA
│       │   ├── blocklist.txt      # Drug/slur word filter (editable)
│       │   ├── disposable_domains.txt # 7,860 blocked email domains
│       │   └── uploads/           # Chat media uploads (ephemeral, gitignored)
│       ├── static/                # Shared bundles, sw.js, manifest, vendor libs,
│       │                        #   symlinks into services/data/output/
│       ├── generate_vapid_keys.py # One-time VAPID key pair generator
│       ├── Dockerfile             # Python 3.12 slim + uvicorn
│       ├── docker-compose.yml     # Container config with volume mounts
│       └── requirements.txt       # fastapi, uvicorn[standard], pywebpush
├── apps/web/                     # Next.js app (empty placeholder, Stage 3)
├── packages/                     # Shared design tokens / API types (placeholders)
├── tests/                       # 281 tests + standalone harnesses
│   ├── test_chat_*.py           # Core suites (db, moderation, ws, api, admin roles)
│   ├── test_notifications.py    # Push tests (Playwright, run separately)
│   ├── notif_e2e/               # 21-scenario notification harness
│   ├── e2ee_browser_check.py    # E2EE browser verification
│   └── stress_test/             # 200-user chat load test
├── docs/                        # Living design specs (E2EE, admin roles, notif testing)
└── deploy.sh                    # Server deploy: backup + pull + rebuild + health check
```

## Database Schema

```
events            — id, name, edition, source_url, website, start/end_date, timezone, address, lat/lng
venues            — id, name, about, address, lat/lng
stages            — id, name, about, venue_id (FK → venues)
event_stages      — event_id + stage_id (PK), color (RGB), position
stage_notes       — stage_id, date, note, position
stage_details     — stage_id, label, value, position
artists           — id, name, photo_url, photo_file, bio (markdown)
artist_links      — artist_id + platform (PK), url, follower_count, position
artist_sets       — id, artist_id, platform, url, title, view_count, duration_min, upload_date, position
schedule          — artist_id + event_id + start_time (PK), stage_id, end_time, date, period, set_type
```

- **Artists, links, sets, stages, and venues are global** — shared across events
- **Stages are reusable** — event-specific config (color, position) in `event_stages` junction
- **Venues** hold physical addresses — stages reference their venue. Single-venue events use one venue or NULL
- **artist_links** normalizes all platforms — adding Mixcloud or Bandcamp is just an INSERT
- **artist_sets** normalizes all media sources — `platform` column for YouTube, SoundCloud, etc.
- **events** split `name` ("Stone Techno") and `edition` ("2026") — page title derived as `"{name} {edition} Companion"`
- **SQLite**: WAL mode, foreign key enforcement, `sqlite3.Row` everywhere

## Requirements

- Python 3.12+
- [Playwright](https://playwright.dev/python/) with Chromium (`playwright install chromium`)
- [pyvips](https://github.com/libvips/pyvips) (requires libvips: `brew install vips`)
- [ssimulacra2](https://github.com/cloudinary/ssimulacra2) binary in PATH
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [markdown](https://python-markdown.github.io/)

## Usage

### Full pipeline

```bash
python services/data/stone_techno_companion.py
```

### Common flags

| Flag | Effect |
|---|---|
| `--render-only` | Skip scraping, regenerate HTML from the cached database |
| `--no-followers` | Skip fetching follower counts from social platforms |
| `--no-photos` | Skip photo download and processing |
| `--refresh-followers` | Clear all cached counts and re-fetch |
| `--refresh-photos` | Clear all cached photos and re-process |
| `--deploy` | Rsync output to the production VPS after generating |
| `--event-id ID` | Event identifier (default: `stone-techno-2026`) |
| `--event-name NAME` | Event name (default: `Stone Techno`) |
| `--event-edition ED` | Event edition (default: `2026`) |
| `--url URL` | Override the source lineup URL |
| `--output-dir DIR` | Override the output directory (default: `services/data/output/`) |

### Quick regeneration

```bash
python services/data/stone_techno_companion.py --render-only --no-photos
```

### Local preview

```bash
cd services/data/output && python3 -m http.server 8321
# Open http://localhost:8321/lineup.html
```

Do not use `file://` — fetch-based features (bios, API) require HTTP.

### YouTube sets

```bash
python services/data/fetch_videos.py
```

Run separately from the main pipeline (~50 min for 100 artists). Results stored in `artist_sets` table.

### Deploy to production

```bash
python services/data/stone_techno_companion.py --render-only --deploy
```

Rsyncs HTML, bios.json, timetable.json, photos, thumbs, sw.js, and manifest.json to the VPS.

## Overrides

`services/data/scraper/overrides.toml` provides manual corrections applied after scraping.

```toml
[Amoral]
ra = "https://ra.co/dj/amoral"

[ROD]
soundcloud = "https://soundcloud.com/bennyrodrigues"
photo = "https://cdn.example.com/photo.webp"

[youtube_names]
"Serge" = "Serge Clone"

[youtube_videos]
"Function" = ["abc123", "def456"]

[youtube_videos_add]
"Rødhåd" = ["ghi789"]

[floor_curators]
"2026-07-11.koksofenbatterie" = "curated by Freddy K"
"2026-07-12.werksschwimmbad" = "hosted by Clone Records"
```

## Favorites System

No sign-up required. First heart tap auto-creates a session.

- **Sessions**: 128-bit URL-safe tokens — `session_id` (read+write), `share_token` (read-only)
- **Cross-device sync**: ephemeral 6-digit PINs (5-min TTL, single-use) via QR code or manual entry
- **Real-time**: WebSocket sync — hearts and schedule changes appear instantly on all connected devices
- **Sharing**: read-only links show only picked artists
- **Offline**: hearts persist in localStorage, reconcile on next successful sync

### API Endpoints

Base URL: `https://stonetechno.deftlab.dev/api`

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/session` | Create a new session |
| `GET` | `/api/session/{code}` | Load picks (session_id or share_token) |
| `POST` | `/api/session/{code}/pick/{artist_id}` | Add a pick |
| `DELETE` | `/api/session/{code}/pick/{artist_id}` | Remove a pick |
| `POST` | `/api/session/{code}/schedule/{slot_id}` | Add to schedule |
| `DELETE` | `/api/session/{code}/schedule/{slot_id}` | Remove from schedule |
| `POST` | `/api/session/{code}/sync-pin` | Generate sync PIN |
| `POST` | `/api/sync/{pin}` | Exchange PIN for credentials |
| `GET` | `/api/push/vapid-key` | VAPID public key |
| `POST` | `/api/session/{code}/push/subscribe` | Store push subscription |
| `DELETE` | `/api/session/{code}/push/subscribe` | Remove push subscription |
| `GET` | `/ics/{slot_id}` | Download .ics calendar file |
| `WS` | `/ws/{code}` | WebSocket real-time sync |

### Rate Limits

| Endpoint | Limit |
|---|---|
| Session creation | 10/hour/IP |
| Pick/schedule operations | 600/hour/IP |
| Session load | 600/hour/IP |

## Push Notifications

Sends notifications 10 minutes before scheduled sets.

- **Scheduler**: background task in `api.py` runs every 60s, matches `timetable.json` against sessions' schedules
- **Dedup**: `sent_notifications` table, pruned after 7 days. Dead subscriptions auto-removed.
- **iOS**: requires Safari + Add to Home Screen. Notification click uses Cache Storage flag (service worker can't access localStorage).
- **Brave**: requires enabling Google push messaging in `brave://settings/privacy`

## Deployment Architecture

| Component | Detail |
|---|---|
| VPS | DigitalOcean, Ubuntu 24.04 |
| Domain | `stonetechno.deftlab.dev` |
| DNS | Cloudflare A record |
| Reverse proxy | Caddy (auto-TLS, zstd/gzip compression) |
| App | FastAPI + uvicorn in Docker |
| Database | SQLite (WAL mode) volume-mounted at `data/hearts.db` |

### Content deploys (local)

```bash
python services/data/stone_techno_companion.py --deploy
```

### Code deploys (automatic)

Push to `main` with changes in `services/companion/` triggers GitHub Actions → SSH → `git pull` + `docker compose up -d --build --force-recreate`.

### Caddy configuration

```caddyfile
stonetechno.deftlab.dev {
    encode zstd gzip
    header /photos/* Cache-Control "public, max-age=31536000, immutable"
    reverse_proxy stone-techno:8080
}
```

### First-time VPS setup

1. DNS: Cloudflare A record `stonetechno` → VPS IP
2. Clone repo on VPS: `cd /root/services && git clone ... stone-techno`
3. Deploy static files: `python services/data/stone_techno_companion.py --render-only --no-photos --deploy`
4. Generate VAPID keys, create `services/companion/.env`
5. Start container: `cd services/companion && docker compose up -d`
6. Add Caddy block, reload: `docker exec caddy caddy reload --config /etc/caddy/Caddyfile`
7. GitHub secret: `gh secret set VPS_SSH_KEY < ~/.ssh/id_ed25519`

## Generated HTML Features

- Single page (~650 KB) with inline CSS, JS, SVG sprite, lazy-loaded bios
- **Lineup view**: artist cards with photos, social links (dynamic from DB), follower counts, schedule annotations
- **Timetable view**: CSS grid (desktop) / HTML table with native scroll (mobile), sticky headers, now-line, dynamic row height
- **Bio overlay**: markdown-rendered biography, YouTube sets with thumbnails, lazy-loaded on first tap
- **Scroll position**: saved per view — switching lineup ↔ timetable restores position
- **Popup → Bio**: clicking artist name/photo in timetable popup opens bio modal
- **Body scroll lock**: `position: fixed` technique for iOS Safari compatibility
- **Accessibility**: `tabindex`/`role="button"` on all interactive elements, ARIA attributes on modals/popups, keyboard navigation, focus trapping, meaningful alt text
- **Responsive**: mobile breakpoint at 480px, hamburger menu, `@media (hover: hover)` guards
- **PWA**: manifest, service worker, Add to Home Screen support
- **WCAG 2.1 AA**: contrast ratios, 12px minimum font size, no text below accessible floor

## Chat System

Privacy-first ephemeral group chat at `/chat`. Messages auto-delete after 60 minutes. AI moderation on every message.

### Features

- **Main room auto-opens** on login. Path-based routing: `/chat`, `/chat/r/{id}`, `/chat/d/{user}`, `/chat/m/{id}`, `/chat/v/{token}`, `/chat/msg/{id}`.
- **Auth**: Email magic link via Maileroo (DB-backed tokens, survive restarts). Google/Apple OAuth ready (backend implemented). 7,860 disposable domains blocked. Email validated via RFC 5322 + DNS MX.
- **Profile**: mandatory username (unique, `a-z 0-9 . _ -`) + avatar + country. Optional display name (Latin Unicode). Circular pan+zoom avatar editor with friction slider, 128x128 WebP stored in DB. Searchable country dropdown with 195 countries + local name aliases. 12 user colors assigned at registration. Live bubble preview during setup. OpenAI moderation on submit.
- **Moderation**: word filter (1,546 terms) + OpenAI omni-moderation + GPT content detection (drugs, spam, payment links, external links). Images moderated via WebP data URI, videos via 3 extracted frames. Duplicate message detection (2-min window).
- **Strike system**: warning, 30-min mute, permanent ban. Drug terms escalate faster.
- **Media**: photos (client resize + WebP, stored as-is), videos (Mediabunny + WebCodecs, HEVC/H.264 fallback, hardware-accelerated, trim editor for >60s), location sharing, meetup cards. All with SVG icons.
- **Unread badges**: red pill badges on room items and tab headers (Rooms/Meetups/DMs). Room memberships track joined rooms + last-read. Server pushes badge updates via WS for offline members. Auto-clears on room open.
- **Message permalinks**: `/chat/msg/{id}` resolves to room, opens it, scrolls to and highlights message. Graceful fallback for deleted messages.
- **Meetup cards**: Join/Joined button (hidden for creator), attendee count, auto-join chat on join.
- **Message delete**: right-click/long-press on own messages within 120s, inline confirmation.
- **Bubble chat**: user-colored pastels, reply quotes, reactions (hover on desktop, long-press on mobile). Photo avatars in bubbles.
- **Video player**: inline play/pause, fullscreen icon, frame sync between inline and expanded views.
- **Settings**: avatar in header opens menu (Profile edit, Notifications, Log out).
- **Desktop**: sidebar + chat, centered modals. **Mobile**: bottom drawers.
- **Auto-purge**: runs on startup + every 30s. Deletes expired messages, meetups, sessions, media files.
- **Design system**: CSS custom properties — gray scale (WCAG AA/AAA), font scale, spacing scale, 12 user colors, standardized dialogs/toasts.

### Chat Database (chat.db, separate from hearts.db)

```
users (username, display_name, country, avatar_url, color_index),
sessions, email_tokens, avatars (WebP BLOB),
bans, rooms (is_main), room_memberships (last_read_at),
messages (60-min TTL), message_reactions,
meetups (30-min grace), meetup_attendees, dm_participants,
blocks, reports, strikes
```

### Tests

126 tests: `python -m pytest tests/ -v`

## Multi-Event Support

The DB supports multiple events. Artists, links, sets, stages, and venues are global (shared). Schedule and event_stages are scoped per event. Each event needs its own scraper module — the scraper output format (`parsed` dict with `artists`, `sections`, `locations`, `assignments`) is the stable interface between event-specific scrapers and the generic pipeline.
