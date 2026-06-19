# Stone Techno Companion

A scraper, enrichment pipeline, and static site generator for the [Stone Techno](https://www.stone-techno.com/) festival lineup. Produces an interactive single-page lineup with artist photos, social links, follower counts, and a favorites system that syncs across devices in real time.

**Live at:** [stonetechno.deftlab.dev](https://stonetechno.deftlab.dev/)

## What It Does

1. **Scrapes** the official Stone Techno lineup page using Playwright (headless Chromium)
2. **Enriches** each artist by visiting their SoundCloud, Instagram, and Spotify profiles to collect follower/listener counts and discover missing social links
3. **Processes photos** — downloads, resizes to 240x240 with adaptive sharpening, and encodes to AVIF using binary search to hit a target ssimulacra2 quality score of 78
4. **Generates** a single self-contained HTML file with inline CSS, JS, and SVG icons
5. **Serves** the page via a FastAPI backend with a favorites API and WebSocket-based real-time sync

## Project Structure

```
stone-techno-companion/
├── stone_techno_companion.py    # CLI entry point — orchestrates the full pipeline
├── scraper/
│   ├── scrape.py                # Lineup page parser + SC/IG/Spotify scrapers
│   ├── db.py                    # SQLite schema, upserts, queries, overrides
│   ├── images.py                # Photo download, resize (pyvips), AVIF encoding
│   ├── render.py                # HTML generation with inline hearts JS + modals
│   ├── overrides.toml           # Manual corrections for wrong/missing links
│   ├── qrcode.min.js            # QR code generator (bundled into HTML)
│   └── icons/                   # SVG icons for Instagram, SoundCloud, Spotify,
│       ├── instagram-square-round.svg      Linktree, YouTube — inlined into HTML
│       ├── soundcloud-square-round.svg
│       ├── spotify-square-round.svg
│       ├── linktree-square-round.svg
│       ├── youtube-square-round.svg
│       ├── favicon.svg              # Favicon (calendar + music note)
│       └── favicon.png              # PNG version for OG image previews
├── server/
│   ├── api.py                   # FastAPI app — favorites API + WebSocket sync
│   ├── Dockerfile               # Python 3.12 slim + uvicorn
│   ├── docker-compose.yml       # Container config with volume mounts
│   └── requirements.txt         # fastapi, uvicorn[standard]
├── .github/workflows/
│   └── deploy.yml               # Auto-deploy server to VPS on push to main
├── output/                      # Generated (gitignored)
│   ├── lineup.html              # The final page (~2800 lines)
│   └── photos/*.avif            # Processed artist photos (~100 files)
└── lineup.db                    # SQLite cache (gitignored)
```

## Requirements

- Python 3.12+
- [Playwright](https://playwright.dev/python/) with Chromium (`playwright install chromium`)
- [pyvips](https://github.com/libvips/pyvips) (requires libvips system library)
- [ssimulacra2](https://github.com/cloudinary/ssimulacra2) binary in PATH
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)

## Usage

### Full pipeline (scrape + enrich + photos + generate)

```bash
python stone_techno_companion.py
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
| `--url URL` | Override the source lineup URL |
| `--output-dir DIR` | Override the output directory (default: `output/`) |
| `--title TEXT` | Override the page title |

### Quick regeneration

To tweak the HTML template or CSS without re-scraping:

```bash
python stone_techno_companion.py --render-only --no-photos
```

### Deploy to production

```bash
python stone_techno_companion.py --render-only --deploy
```

This rsyncs `output/lineup.html` and `output/photos/` to the VPS at `/root/services/stone-techno/server/static/`. No container restart needed — static files are volume-mounted.

## Data Pipeline

### Scraping

The scraper visits `stone-techno.com` and extracts:

- **Artists** — name, photo URL, social links (Instagram, SoundCloud, Spotify, YouTube)
- **Sections** — date + period (day 12:00-23:59 / night 23:00-07:00)
- **Locations** — venue name + description (e.g. Grand Hall, Mischanlage) for night events
- **Assignments** — which artist plays which section at which location

### Enrichment

After scraping the lineup, each artist's social profiles are visited in order:

1. **SoundCloud** — follower count + discovers IG/Spotify/Linktree/YouTube links from bio
2. **Instagram** — exact follower count via GraphQL API + discovers SC/Spotify/Linktree/YouTube from bio links
3. **Spotify** — monthly listener count

All data is cached in `lineup.db`. Counts are only fetched for artists that don't have them yet, unless `--refresh-followers` is used.

### Overrides

`scraper/overrides.toml` provides manual corrections for wrong or missing links. Applied after scraping, before follower fetching. When a link is changed, its associated count is automatically cleared for re-fetch.

```toml
[Amoral]
instagram = "https://www.instagram.com/amoral___dj/"

[ROD]
soundcloud = "https://soundcloud.com/bennyrodrigues"
photo = "https://cdn.amsterdam-dance-event.nl/images/.../photo.webp"
```

Supported fields: `instagram`, `soundcloud`, `spotify`, `linktree`, `youtube`, `photo`.

### Image Processing

Photos go through:

1. Download from source URL
2. Auto-rotate based on EXIF
3. Flatten alpha channel (white background)
4. Resize to 240x240 using lanczos3, center crop
5. Adaptive post-downscale sharpening in LAB space (intensity scales with downscale ratio)
6. AVIF encoding via binary search on quality parameter to hit ssimulacra2 score of 78

## Favorites System

The generated page includes a hearts/favorites feature with no sign-up required.

### How it works

1. User taps a heart on any artist card
2. First tap auto-creates a session — no signup, no login, no personal data
3. Each session gets two 128-bit URL-safe tokens (`secrets.token_urlsafe(16)`):
   - **Session ID** — stored in localStorage, grants read + write access
   - **Share token** — used in share URLs, grants read-only access
4. Cross-device sync uses ephemeral 6-digit PINs (5-min TTL, single-use) — the PIN appears in the QR code and dialog, never the session ID
5. Hearts sync across devices in real time via WebSocket
6. Users can share a read-only link with friends (shows only picked artists)
7. When a sync PIN is used, the sender gets a real-time confirmation via WebSocket

### API Endpoints

Base URL: `https://stonetechno.deftlab.dev/api`

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/session` | Create a new session (returns session_id + share_token) |
| `GET` | `/api/session/{code}` | Load picks (works with session_id or share_token) |
| `POST` | `/api/session/{code}/pick/{artist_id}` | Add a pick |
| `DELETE` | `/api/session/{code}/pick/{artist_id}` | Remove a pick |
| `POST` | `/api/session/{code}/sync-pin` | Generate a 6-digit sync PIN (5-min TTL, single-use) |
| `POST` | `/api/sync/{pin}` | Exchange a sync PIN for session credentials |
| `WS` | `/ws/{code}` | WebSocket for real-time sync |

Pick operations are atomic — they use `json_group_array` with `json_each` and `UNION`/`WHERE` to avoid read-modify-write races.

### Rate Limits

| Endpoint | Limit |
|---|---|
| Session creation | 10 per hour per IP |
| Pick add/remove | 600 per hour per IP |
| Session load | 600 per hour per IP |

### Offline Resilience

Hearts toggle immediately in the UI and persist in localStorage. If the API call fails, the local state survives. On the next successful sync (tab focus or next toggle), local and server state are reconciled by diffing and replaying missed operations.

## Deployment Architecture

### Infrastructure

| Component | Detail |
|---|---|
| VPS | DigitalOcean, Ubuntu 24.04 |
| Domain | `stonetechno.deftlab.dev` |
| DNS | Cloudflare A record |
| Reverse proxy | Caddy (auto-TLS via Let's Encrypt, zstd/gzip compression) |
| App | FastAPI + uvicorn in Docker |
| Database | SQLite (WAL mode) volume-mounted at `data/hearts.db` |

### Two deploy paths

**Content deploys** (HTML + photos) are triggered locally:

```bash
python stone_techno_companion.py --deploy
```

Rsyncs files to the VPS. No container restart — static files are volume-mounted.

**Code deploys** (server changes) are automatic via GitHub Actions:

When changes to `server/**` are pushed to `main`, the workflow SSHs into the VPS, pulls the latest code, and rebuilds the Docker container:

```yaml
# .github/workflows/deploy.yml
name: Deploy server
on:
  push:
    branches: [main]
    paths: [server/**]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: 209.38.244.136
          username: root
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /root/services/stone-techno
            git pull origin main
            cd server
            docker compose up -d --build
```

Requires one GitHub secret: `VPS_SSH_KEY` (SSH private key for root access).

### Caddy configuration

Add to `/root/services/caddy/Caddyfile` on the VPS:

```caddyfile
stonetechno.deftlab.dev {
    encode zstd gzip
    header /photos/* Cache-Control "public, max-age=31536000, immutable"
    reverse_proxy stone-techno:8080
}
```

Caddy auto-provisions the TLS certificate. The `stone-techno` container and Caddy share the external Docker network `apps`.

### First-time VPS setup

1. Add Cloudflare DNS A record: `stonetechno` → `209.38.244.136`

2. Clone the repo on the VPS:
   ```bash
   ssh root@209.38.244.136 "cd /root/services && git clone git@github.com:Gabe-LS/stone-techno-companion.git stone-techno"
   ```

3. Deploy static files from your local machine:
   ```bash
   python stone_techno_companion.py --render-only --no-photos --deploy
   ```

4. Start the container:
   ```bash
   ssh root@209.38.244.136 "cd /root/services/stone-techno/server && docker compose up -d"
   ```

5. Add the Caddy block above and reload:
   ```bash
   ssh root@209.38.244.136 "docker exec caddy caddy reload --config /etc/caddy/Caddyfile"
   ```

6. Add the deploy secret to GitHub:
   ```bash
   gh secret set VPS_SSH_KEY < ~/.ssh/id_ed25519
   ```

## Generated HTML Features

- Single self-contained file with inline CSS, JS, and SVG icons
- Responsive layout with mobile breakpoint at 480px
- Sticky section headers (date, period, location) with gradient fade effect using IntersectionObserver
- Artist cards with photo, name, schedule annotation, social links + follower counts
- Lazy-loaded AVIF photos
- Command bar: Show My Picks (filter toggle) | Share My Picks | Sync My Picks
- Share modal with readonly URL input and copy-to-clipboard
- Sync modal with QR code, 6-digit PIN, live countdown timer, and success confirmation via WebSocket
- Read-only share views auto-filter to show only picked artists
- Favicon (SVG inline + PNG for OG image) and Open Graph / Twitter Card metadata
- Accessible: `aria-label`, `aria-pressed`, keyboard navigation, focus management in modals
- Escape key closes modals, tab trapping within open modals, scroll lock
