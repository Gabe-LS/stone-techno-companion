# Stone Techno Companion — Spec

## Overview

A scraper + static site generator for the Stone Techno 2026 festival lineup,
with a favorites (hearts) feature for attendees to build and share their
personal timetable. Deployed to `stonetechno.deftlab.dev` on a DigitalOcean
VPS behind Caddy.

---

## 1. Data Pipeline (existing, runs locally)

### 1.1 Scraper (`stone_techno_companion.py`)

Source: `https://www.stone-techno.com/`

Scrapes the lineup page with Playwright (headless Chromium) and extracts:

- **Artists**: name, photo URL, social links (IG, SC, Spotify, YouTube, Linktree)
- **Sections**: date, period (day 12:00–23:59 / night 23:00–07:00)
- **Locations**: venue name + description (Grand Hall, Mischanlage) for night events
- **Artist assignments**: which artist plays which section at which location

Then enriches by visiting each artist's profiles:

1. **SoundCloud** (scraped first): follower count + discovers IG/Spotify/Linktree/YouTube links
2. **Instagram** (scraped second, benefits from SC-corrected links): exact follower count via GraphQL API + discovers SC/Spotify/Linktree/YouTube from bio
3. **Spotify**: monthly listener count from artist page

All data is cached in `lineup.db` (SQLite). Follower counts and photos are
only fetched once unless `--refresh-followers` or `--refresh-photos` is used.

### 1.2 Overrides (`overrides.toml`)

Manual corrections for wrong or missing links. Applied after scraping,
before follower fetching. When a link changes, its associated count is
cleared for re-fetch.

Supported fields: `instagram`, `soundcloud`, `spotify`, `linktree`,
`youtube`, `photo`.

### 1.3 Image Processing

Artist photos are downloaded, resized to 240x240 (lanczos3 + adaptive
post-downscale sharpening in LAB space), and encoded to AVIF using binary
search to hit ssimulacra2 score of 78. Stored in `output/photos/`.

### 1.4 HTML Generation

Produces a single self-contained HTML file (`output/lineup.html`) with:

- Inline CSS (responsive, mobile breakpoint at 480px)
- Inline SVG icons from `icons/` folder (IG, SC, Spotify, Linktree, YouTube)
- Sticky headers (H1 title, H2 date, H3 period, H4 location) with gradient fade
- Artist cards: photo, name, schedule annotation, social links with follower counts
- IntersectionObserver JS for sticky gradient activation
- Photos referenced as relative paths (`photos/*.avif`)

### 1.5 CLI Flags

| Flag | Effect |
|---|---|
| `--url URL` | Override source URL |
| `--output-dir DIR` | Override output directory |
| `--title TEXT` | Override page title |
| `--no-followers` | Skip follower fetching |
| `--no-photos` | Skip photo processing |
| `--render-only` | Skip scraping, regenerate HTML from DB |
| `--refresh-followers` | Clear and re-fetch all follower counts |
| `--refresh-photos` | Clear and re-process all photos |
| `--deploy` | Deploy output to production after generating |

---

## 2. Favorites / Hearts Feature (new)

### 2.1 User Flow

1. User opens the lineup page on any device
2. Taps the heart icon on an artist card — heart fills, pick is saved
3. First heart tap auto-creates a session (no signup, no login)
4. A small banner appears: "Your picks are saved. Code: **K9MX3P**"
5. On another device, user enters the code → sees their picks
6. Both devices auto-sync automatically:
   - Every heart toggle pushes the change immediately
   - Every tab focus pulls the latest state from the server
   - No manual refresh needed after the initial code entry
7. User can share a read-only code with friends

### 2.2 Session Model

Each session has two codes:

| Code | Length | Purpose | Stored in |
|---|---|---|---|
| `edit_code` | 8 chars | Read + write access | localStorage on user's devices |
| `share_code` | 6 chars | Read-only access | Shown in UI, safe to share publicly |

No accounts, no passwords, no personal data collected.

Codes are generated with `secrets.token_urlsafe` and truncated.
Collision probability is negligible at this scale (62^8 = 218 trillion
combinations for edit codes).

### 2.3 API (FastAPI)

Base URL: `https://stonetechno.deftlab.dev/api`

#### `POST /api/session`

Create a new session.

Response `201`:
```json
{
  "edit_code": "K9MX3PAB",
  "share_code": "R3PQ7Z"
}
```

#### `POST /api/session/{edit_code}/pick/{artist_id}`

Add a single pick. Atomic — the server adds the artist_id to the
picks set in a single SQL statement. No read-modify-write cycle.

Response: `204 No Content`

Error cases:
- `403` if code is a `share_code` (read-only)
- `404` if code doesn't exist
- `422` if artist_id is not a valid UUID format
- `429` if rate limited

#### `DELETE /api/session/{edit_code}/pick/{artist_id}`

Remove a single pick. Same atomicity guarantees as POST.

Response: `204 No Content`

Error cases: same as POST above.

#### `GET /api/session/{code}`

Load picks. Works with either `edit_code` or `share_code`.

Response `200`:
```json
{
  "picks": ["overlay_id_1", "overlay_id_2"],
  "readonly": false
}
```

`readonly` is `true` when accessed via `share_code`.

Error cases:
- `404` if code doesn't exist

### 2.4 Client-side JS

Embedded in the generated HTML (~100 lines). Responsibilities:

- Render heart icon (SVG) on each artist card via `data-artist-id`
- On heart click:
  1. Toggle visual state immediately (optimistic UI)
  2. If no session yet, `POST /api/session` to create one, store
     `edit_code` in localStorage
  3. `POST` or `DELETE` the individual pick
  4. If API fails, revert the visual state (rollback)
- On page load:
  1. Check URL for `?code=XXXX` — if present, load that session
     (read-only if it's a share_code)
  2. Otherwise, check localStorage for `edit_code` and load picks
  3. Apply heart states to all matching cards
- On tab focus (`visibilitychange` → `visible`):
  1. Re-fetch picks from API
  2. Diff against local state, update hearts that changed
  3. This handles cross-device sync without polling
- Share modal:
  1. "Share" button in header (only visible when session exists)
  2. Opens a modal showing: share_code text, copy button, QR code
  3. QR encodes `https://stonetechno.deftlab.dev/?code={share_code}`
  4. QR generated client-side (~2KB JS, no external dependency)
- Heart counter badge next to the page title (e.g. "❤ 12")

### 2.5 Offline Resilience

- Hearts always toggle immediately in the UI and in localStorage
- If the API call fails (network error), the local state persists
- On next successful sync (tab focus or next toggle), the full
  local state is reconciled with the server
- Reconciliation: GET server picks, compute diff, POST/DELETE each
  difference. This handles offline hearts gracefully.

### 2.6 Storage

SQLite database (`data/hearts.db`) on the VPS, volume-mounted outside the
container for persistence across container rebuilds.

```sql
CREATE TABLE sessions (
    edit_code   TEXT PRIMARY KEY,
    share_code  TEXT UNIQUE NOT NULL,
    picks       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_share_code ON sessions(share_code);
```

`picks` is a JSON array of overlay_id strings. The atomic add/remove
operations use `json_insert` / `json_remove` with `json_each` to
avoid read-modify-write races:

```sql
-- Add pick (atomic, idempotent)
UPDATE sessions SET picks = (
    SELECT json_group_array(value) FROM (
        SELECT value FROM json_each(picks)
        UNION SELECT ?
    )
), updated_at = datetime('now')
WHERE edit_code = ?;

-- Remove pick (atomic)
UPDATE sessions SET picks = (
    SELECT json_group_array(value) FROM json_each(picks)
    WHERE value != ?
), updated_at = datetime('now')
WHERE edit_code = ?;
```

### 2.7 Rate Limiting

In-memory per-IP sliding window in FastAPI middleware:

| Endpoint | Limit |
|---|---|
| `POST /api/session` | 10 per hour per IP |
| `POST/DELETE .../pick/*` | 300 per hour per IP |
| `GET /api/session/*` | 300 per hour per IP |

Exceeding the limit returns `429 Too Many Requests` with a
`Retry-After` header.

### 2.8 Session Expiry

Sessions with no activity (no `updated_at` change) for 90 days are
eligible for cleanup. A daily cron or startup task prunes them.
The festival is July 10–12, so most sessions will be created in the
days before and used during the event. 90 days gives ample buffer.

---

## 3. Deployment Architecture

### 3.1 VPS Setup

| Component | Detail |
|---|---|
| VPS | DigitalOcean, Ubuntu 24.04, 2 vCPU, 4GB RAM |
| IP | `209.38.244.136` |
| Domain | `stonetechno.deftlab.dev` |
| DNS | Cloudflare A record → `209.38.244.136` (proxied or DNS-only) |
| Reverse proxy | Caddy (Docker container on `apps` network) |
| App | FastAPI + uvicorn in Docker container |
| Network | Docker `apps` network (shared with Caddy) |

### 3.2 Container

```
/root/services/stone-techno/
├── docker-compose.yml
├── Dockerfile
├── api.py
├── requirements.txt          # fastapi, uvicorn
├── static/
│   ├── index.html
│   └── photos/
│       └── *.avif
└── data/                     # volume-mounted, persists
    └── hearts.db
```

**Dockerfile**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py .
COPY static/ static/
EXPOSE 8080
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
```

**docker-compose.yml**:
```yaml
services:
  stone-techno:
    build: .
    container_name: stone-techno
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./static:/app/static
    networks:
      - apps
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/"]
      interval: 30s
      timeout: 5s
      retries: 3

networks:
  apps:
    external: true
```

Note: `static/` is also volume-mounted so deploys update files
without rebuilding the container.

### 3.3 Caddy Configuration

Add to `/root/services/caddy/Caddyfile`:

```caddyfile
stonetechno.deftlab.dev {
    encode zstd gzip
    header /photos/* Cache-Control "public, max-age=31536000, immutable"
    reverse_proxy stone-techno:8080
}
```

Caddy auto-provisions the SSL certificate via Let's Encrypt.
Compression and caching headers are set at the reverse proxy level.

### 3.4 Deploy Strategy

Two separate deploy mechanisms for two types of changes:

#### Content deploys (HTML + photos)

Triggered by: running the scraper locally after lineup changes.

The `--deploy` flag in `stone_techno_companion.py`:

1. Rsyncs `output/lineup.html` and `output/photos/` to VPS:
   `rsync -avz --delete output/lineup.html output/photos/ root@209.38.244.136:/root/services/stone-techno/static/`
2. Prints the live URL

No container restart needed — static files are volume-mounted.

#### Code deploys (server/, Dockerfile, api.py)

Triggered by: `git push` to `main` with changes in `server/`.

GitHub Actions workflow (`.github/workflows/deploy.yml`):

```yaml
name: Deploy server
on:
  push:
    branches: [main]
    paths: [server/**]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: 209.38.244.136
          username: root
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /root/services/stone-techno
            git pull origin main
            docker compose up -d --build
```

Requires one GitHub secret:
- `VPS_SSH_KEY`: private SSH key with access to root@209.38.244.136

The VPS has a clone of the repo at `/root/services/stone-techno`.
GH Actions SSHs in, pulls the latest server code, and rebuilds
the container. Static files are not affected (volume-mounted
separately, updated by `--deploy`).

### 3.5 First-time Setup (manual, once)

1. Add Cloudflare DNS A record: `stonetechno` → `209.38.244.136`
2. Clone the repo on the VPS:
   ```bash
   ssh root@209.38.244.136 "cd /root/services && git clone git@github.com:Gabe-LS/stone-techno-companion.git stone-techno"
   ```
3. Deploy static files:
   ```bash
   python3 stone_techno_companion.py --render-only --no-photos --deploy
   ```
4. Start the container:
   ```bash
   ssh root@209.38.244.136 "cd /root/services/stone-techno/server && docker compose up -d"
   ```
5. Add Caddy block and reload:
   ```bash
   ssh root@209.38.244.136 "docker exec caddy caddy reload --config /etc/caddy/Caddyfile"
   ```
6. Add `VPS_SSH_KEY` secret to the GitHub repo:
   ```bash
   gh secret set VPS_SSH_KEY < ~/.ssh/id_ed25519
   ```

---

## 4. HTML Modifications for Hearts

### 4.1 Artist Card Changes

Each `<li class="artist-item">` gets:

- `data-artist-id="{overlay_id}"` attribute
- A heart SVG button as the last element inside the card:
  ```html
  <button class="heart-btn" data-artist-id="{id}" aria-label="Add to favorites">
    <svg viewBox="0 0 24 24" width="22" height="22">
      <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5
              2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09
              C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5
              c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/>
    </svg>
  </button>
  ```

### 4.2 New CSS

```css
.heart-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  flex-shrink: 0;
  align-self: flex-start;
  margin-top: 2px;
}
.heart-btn svg {
  fill: none;
  stroke: #ccc;
  stroke-width: 2;
  transition: fill 0.15s, stroke 0.15s;
}
.heart-btn:hover svg { stroke: #e53e3e; }
.heart-btn.active svg { fill: #e53e3e; stroke: #e53e3e; }

.share-bar {
  display: none;
  background: #f7f7f7;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 0.85em;
  margin-bottom: 16px;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.share-bar.visible { display: flex; }
.share-bar code {
  background: #fff;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 1.2em;
  font-weight: 700;
  letter-spacing: 0.1em;
  border: 1px solid #ddd;
}
.share-bar button {
  background: #222;
  color: #fff;
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.9em;
}

.qr-modal {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 100;
  align-items: center;
  justify-content: center;
}
.qr-modal.visible { display: flex; }
.qr-modal-content {
  background: #fff;
  padding: 24px;
  border-radius: 12px;
  text-align: center;
  max-width: 300px;
}

.heart-counter {
  font-size: 0.5em;
  font-weight: normal;
  color: #e53e3e;
  vertical-align: middle;
}
```

### 4.3 Mobile Adjustments

```css
@media (max-width: 480px) {
  .heart-btn { padding: 4px; }
  .heart-btn svg { width: 18px; height: 18px; }
  .share-bar { font-size: 0.8em; padding: 8px 12px; }
}
```

---

## 5. FastAPI Application (`api.py`)

```python
# ~60 lines total

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
import sqlite3, secrets

app = FastAPI()

# Serve static files (index.html + photos)
app.mount("/photos", StaticFiles(directory="static/photos"), name="photos")

# API endpoints: /api/session, /api/session/{code}, etc.
# SQLite connection per request (thread-safe with check_same_thread=False)

# Root serves index.html
@app.get("/")
async def index():
    return FileResponse("static/index.html")
```

Key implementation details:
- SQLite with WAL mode for concurrent reads
- Parameterized queries throughout (no SQL injection)
- `secrets.token_urlsafe(6)[:8]` for edit codes, `[:6]` for share codes
- Retry on `SQLITE_BUSY` (unlikely but safe)
- Startup event creates table if not exists

---

## 6. Optimization Checklist

### 6.1 Performance

- [x] Photos: AVIF with ssimulacra2 78, 240x240
- [x] HTML: single file, inline CSS/JS, no external requests except photos
- [ ] Caddy: `Cache-Control: immutable, 1 year` for photos
- [ ] Caddy: `encode zstd gzip` for HTML/JS/CSS
- [x] Lazy loading on photos (`loading="lazy"`)
- [ ] Preload first 3 above-the-fold photos with `<link rel="preload">`

### 6.2 SEO / Social Sharing

- [ ] `<meta name="description">` tag
- [ ] Open Graph tags (`og:title`, `og:description`, `og:image`)
- [ ] Twitter Card tags
- [ ] Favicon (SVG or PNG)

### 6.3 PWA (Progressive Web App)

- [ ] `manifest.json` (name, icons, theme color, start URL, display: standalone)
- [ ] `<meta name="apple-mobile-web-app-capable" content="yes">`
- [ ] `<meta name="theme-color" content="#111">`
- [ ] This prevents iOS Safari's 7-day localStorage eviction
- [ ] Users can "Add to Home Screen" for app-like experience
- [ ] No service worker needed (content changes rarely)

### 6.4 Accessibility

- [x] All images have `alt` text (artist name)
- [ ] Heart buttons have `aria-label` and `aria-pressed`
- [ ] Keyboard navigation: Enter/Space to toggle hearts
- [ ] Focus-visible styles on interactive elements
- [ ] `prefers-reduced-motion` media query to disable transitions
- [ ] Sufficient color contrast on all text (WCAG AA)

### 6.5 Security

- [ ] Rate limiting on all API endpoints
- [ ] Input validation: artist_id must match UUID format
- [ ] Input validation: code must match `^[A-Za-z0-9_-]{6,8}$`
- [ ] Parameterized SQL queries (no injection)
- [ ] HTTPS enforced by Caddy (auto-redirect HTTP → HTTPS)
- [ ] No personal data collected or stored
- [ ] No cookies used (localStorage only)
- [ ] CORS: not needed (same origin)

### 6.6 Reliability

- [ ] Docker health check (curl localhost:8080)
- [ ] `restart: unless-stopped` in docker-compose
- [ ] SQLite WAL mode for concurrent reads during writes
- [ ] hearts.db volume-mounted outside container
- [ ] Daily backup of hearts.db (cron: `cp data/hearts.db data/hearts.db.bak`)
- [ ] Caddy access logs for debugging

### 6.7 Session Cleanup

- [ ] Prune sessions with `updated_at` older than 90 days
- [ ] Run on API startup or via daily cron
- [ ] Log pruned count for monitoring

---

## 7. File Inventory

### Repository (tracked in git)

```
stone-techno-companion/
├── stone_techno_companion.py     # CLI entry point
├── scraper/
│   ├── __init__.py
│   ├── db.py                     # database layer
│   ├── scrape.py                 # SC, IG, Spotify scrapers
│   ├── images.py                 # photo processing (vips, ssimulacra2)
│   ├── render.py                 # HTML generation
│   ├── overrides.toml            # manual link corrections
│   └── icons/*.svg               # platform icons
├── server/
│   ├── api.py                    # hearts API (FastAPI)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── .github/workflows/
│   └── deploy.yml                # deploy server/ to VPS on push
├── spec.md
└── .gitignore
```

### Not tracked (gitignored)

| File | Purpose |
|---|---|
| `lineup.db` | SQLite cache (artists, sections, followers) |
| `output/lineup.html` | Generated HTML |
| `output/photos/*.avif` | Processed artist photos |

### VPS (`/root/services/stone-techno/`)

The VPS has a clone of the repo. Server code (`server/`) is
deployed via GH Actions (git pull + docker rebuild). Static
content (`static/`) is deployed via `--deploy` (rsync).

| Path | Source | Deploy method |
|---|---|---|
| `server/*` | git repo | GH Actions (auto on push) |
| `static/lineup.html` | local `output/` | `--deploy` (rsync) |
| `static/photos/*.avif` | local `output/` | `--deploy` (rsync) |
| `data/hearts.db` | auto-created | volume-mounted, persists |

---

## 8. Future Considerations

### 8.1 Set Times / Timetable

When stone-techno.com publishes exact set times (typically days before
the festival), the scraper can extract them and the HTML can render a
timeline/grid view. Hearts integrate naturally — hearted artists are
highlighted in the timetable.

### 8.2 SoundCloud Embeds

SoundCloud allows free iframe embeds. Could add a small inline player
on each artist card to preview their music. The embed URL is
`https://w.soundcloud.com/player/?url={soundcloud_profile_url}`.

### 8.3 Shared Timetables

The share code already supports read-only views. A future enhancement
could show a combined view of multiple share codes — "our group's picks"
— by merging picks from several codes client-side.
