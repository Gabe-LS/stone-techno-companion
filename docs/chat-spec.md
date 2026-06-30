# Stone Techno Chat — Technical Specification

## Overview

Privacy-first, ephemeral group chat integrated into the Stone Techno Festival companion app. Chat rooms map to festival stages, with dedicated meetup chats, DMs, and a general room. All messages auto-delete after 60 minutes. AI moderation on every message.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                      Caddy                           │
│            (TLS, reverse proxy, compression)         │
│   /* → FastAPI         /ws/chat/* → FastAPI WS       │
└──────────────┬───────────────────────┬───────────────┘
               │                       │
      ┌────────▼────────┐    ┌─────────▼──────────┐
      │    FastAPI       │    │   FastAPI WebSocket │
      │  REST + Static   │    │   Chat rooms        │
      │  (existing app)  │    │   Presence          │
      └────────┬─────────┘    └─────────┬──────────┘
               │                        │
      ┌────────▼────────┐    ┌──────────▼─────────┐
      │   hearts.db     │    │     chat.db         │
      │   (favorites,   │    │  (users, messages,  │
      │    sessions,     │    │   rooms, meetups,   │
      │    push)         │    │   reports, bans)    │
      └─────────────────┘    └────────────────────┘
```

**Everything stays Python/FastAPI.** No Node.js. No PostgreSQL. No Redis. The existing server is extended with chat endpoints and WebSocket rooms. Two SQLite databases: `hearts.db` (existing) and `chat.db` (new, ephemeral chat data).

---

## Database Schema (chat.db)

SQLite, WAL mode, foreign keys ON. Separate from hearts.db — different lifecycle (ephemeral vs persistent).

### users

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID, generated server-side |
| provider | TEXT NOT NULL | 'google', 'apple', 'email' |
| provider_id | TEXT NOT NULL | Google/Apple user ID, or email hash |
| display_name | TEXT NOT NULL | 3-30 chars, alphanumeric + underscores + spaces |
| avatar_url | TEXT | Nullable, URL to uploaded image |
| session_id | TEXT | FK → hearts.db sessions (links chat identity to favorites) |
| device_fingerprint | TEXT | Canvas hash + screen + timezone + language |
| created_at | TEXT NOT NULL | ISO 8601 |
| last_seen | TEXT | Updated on disconnect |

**UNIQUE** on `(provider, provider_id)` — one account per identity.

### bans

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| user_id | TEXT | FK → users.id, nullable (user may be deleted) |
| provider | TEXT NOT NULL | 'google', 'apple', 'email' |
| provider_id | TEXT NOT NULL | Banned identity |
| device_fingerprint | TEXT | Banned device, nullable |
| reason | TEXT NOT NULL | |
| created_at | TEXT NOT NULL | |

Bans are checked by `(provider, provider_id)` AND `device_fingerprint` on every auth attempt. Separate table so bans survive user deletion.

### rooms

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | Slug: stage ID, 'general', meetup UUID, or DM UUID |
| type | TEXT NOT NULL | 'stage', 'general', 'meetup', 'dm' |
| name | TEXT NOT NULL | Display name |
| created_at | TEXT NOT NULL | |

Stage rooms and the general room are seeded from the `stages` table on startup. Meetup rooms are created by users. DM rooms are created on first message.

### messages

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| room_id | TEXT NOT NULL | FK → rooms.id |
| user_id | TEXT NOT NULL | FK → users.id |
| type | TEXT NOT NULL | 'text', 'image', 'location', 'meetup_invite' |
| content | TEXT NOT NULL | JSON payload (see Message Types) |
| expires_at | TEXT NOT NULL | ISO 8601, default now() + 60 min |
| created_at | TEXT NOT NULL | |

**Index:** `CREATE INDEX idx_messages_expires ON messages(expires_at);`
**Index:** `CREATE INDEX idx_messages_room ON messages(room_id, created_at);`

### meetups

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID (also used as room_id for the meetup chat) |
| creator_id | TEXT NOT NULL | FK → users.id |
| stage_id | TEXT | FK → stages.id in lineup.db (which stage it was created from) |
| title | TEXT NOT NULL | |
| location_lat | REAL | |
| location_lng | REAL | |
| location_label | TEXT | "Near Grand Hall entrance" |
| meetup_time | TEXT NOT NULL | ISO 8601 |
| note | TEXT | "I'll be wearing a black cap" |
| created_at | TEXT NOT NULL | |
| expires_at | TEXT NOT NULL | meetup_time + 30 min |

**Index:** `CREATE INDEX idx_meetups_expires ON meetups(expires_at);`

### meetup_attendees

| Column | Type | Notes |
|---|---|---|
| meetup_id | TEXT NOT NULL | FK → meetups.id |
| user_id | TEXT NOT NULL | FK → users.id |
| joined_at | TEXT NOT NULL | |

**PK:** `(meetup_id, user_id)`

### dm_participants

| Column | Type | Notes |
|---|---|---|
| room_id | TEXT NOT NULL | FK → rooms.id |
| user_id | TEXT NOT NULL | FK → users.id |

**PK:** `(room_id, user_id)`

### reports

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| reporter_id | TEXT NOT NULL | FK → users.id |
| reported_user_id | TEXT NOT NULL | FK → users.id |
| message_snapshot | TEXT NOT NULL | Plaintext copy of message content |
| room_id | TEXT NOT NULL | |
| reason | TEXT NOT NULL | |
| status | TEXT NOT NULL | 'pending', 'reviewed', 'actioned', 'dismissed' |
| created_at | TEXT NOT NULL | |
| reviewed_at | TEXT | |

Reports retain a plaintext snapshot beyond the 60-min window — necessary for moderation review. Snapshots are purged 30 days after the report is resolved.

### strikes

| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| user_id | TEXT NOT NULL | FK → users.id |
| reason | TEXT NOT NULL | 'word_filter', 'ai_moderation', 'manual' |
| detail | TEXT | The flagged content |
| created_at | TEXT NOT NULL | |

Three strikes → automatic permanent ban.

---

## Authentication

### Providers

Three options, all free, all passwordless:

1. **Sign in with Google** — OAuth 2.0, one tap on mobile
2. **Sign in with Apple** — Sign in with Apple JS, required for iOS
3. **Sign in with email** — magic link (click link in inbox, no password)

Email magic links sent via [Resend](https://resend.com) (3,000 emails/month free tier). Disposable email domains blocked using [disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains) blocklist (110K+ domains, loaded in-memory on startup).

### Auth Flow

```
User taps "Join Chat" on a stage
       │
       ▼
Login screen: Google / Apple / Email
       │
       ▼
Provider verifies identity → returns provider_id
       │
       ▼
Check bans table: (provider, provider_id) or device_fingerprint
       │── BANNED → show "You have been banned" + reason
       ▼
Find or create user in chat.db
       │
       ▼
Prompt for display_name (pre-filled from Google/Apple profile)
       │
       ▼
Link to existing favorites session_id (if one exists in localStorage)
       │
       ▼
Set signed HTTP-only session cookie
       │
       ▼
Connected to chat
```

### Session Management

Sessions stored in `chat.db` as a `sessions` table (id, user_id, token, expires_at). Cookie is HTTP-only, Secure, SameSite=Strict. Expired sessions cleaned by the purge job.

### Device Fingerprinting

Collected on login: canvas fingerprint hash + screen resolution + timezone + navigator language. Stored on the user record. Used as a secondary ban check — if a banned user creates a new Google account, their device fingerprint still matches.

---

## Integration with Existing App

### No Separate Page

Chat is **not** at `/chat/grand-hall`. It's integrated into the existing timetable and lineup views:

- **Timetable view**: each stage block gets a chat icon (bottom-right). Tap opens a slide-up chat panel for that stage.
- **Meetup discovery**: a "Meetups" tab in the command bar shows upcoming meetups across all stages, filterable by time and stage.
- **Bio overlay**: could include a "Fan meetup" button to create a meetup for fans of that artist (future).

### Chat Panel

Not a full page navigation. A bottom sheet / slide-up panel that overlays the timetable:

```
┌─────────────────────────────────────────┐
│  Timetable (still visible, dimmed)      │
│                                         │
├─────────────────────────────────────────┤
│  ▼ Grand Hall · 🟢 47 online           │  ← Drag handle + room name
│─────────────────────────────────────────│
│                                         │
│  @techno_lover  22:41                   │
│  this set is incredible 🔥              │
│                                         │
│  📍 MEETUP                              │
│  Meet at the main bar · 23:00           │
│  8 going · [I'm in] · [Open chat]      │
│                                         │
│  @bass_head  22:43                      │
│  anyone know who's playing next?        │
│                                         │
├─────────────────────────────────────────┤
│  [😀] [📷] [📍] [🤝]  Type...    [Send] │  ← Input bar
└─────────────────────────────────────────┘
```

**Panel states:**
- Collapsed (just the drag handle visible)
- Half-height (default, timetable still visible)
- Full-height (dragged up, timetable hidden)

**Navigation within the panel:**
- Room selector (swipe or tabs): stage rooms, general, DMs
- Meetup cards are inline in the stage room chat
- Tapping "Open chat" on a meetup card navigates to the meetup's dedicated chat room

---

## WebSocket Protocol

Single WebSocket connection per user at `/ws/chat/{session_token}`. Multiplexed across rooms (user can be in multiple rooms).

### Client → Server

| Event | Payload | Description |
|---|---|---|
| `join_room` | `{ room_id }` | Subscribe to a room's messages |
| `leave_room` | `{ room_id }` | Unsubscribe |
| `send_message` | `{ room_id, type, content }` | Send message (moderated before broadcast) |
| `typing` | `{ room_id, active }` | Typing indicator (true/false) |
| `join_meetup` | `{ meetup_id }` | RSVP to a meetup |
| `leave_meetup` | `{ meetup_id }` | Cancel RSVP |
| `create_meetup` | `{ stage_id, title, lat, lng, label, meetup_time, note }` | Create a meetup |
| `open_dm` | `{ target_user_id }` | Open or create a DM |
| `report_message` | `{ message_id, reason }` | Report a message |

### Server → Client

| Event | Payload | Description |
|---|---|---|
| `message` | `{ id, room_id, user, type, content, created_at }` | New message (already moderated) |
| `message_rejected` | `{ reason }` | Moderation blocked the message (sender only) |
| `typing` | `{ room_id, user_id, active }` | Typing indicator |
| `presence` | `{ room_id, user_id, online }` | User joined/left room |
| `messages_expired` | `{ room_id, message_ids }` | Remove messages from UI |
| `meetup_created` | `{ meetup }` | New meetup in a stage room |
| `meetup_updated` | `{ meetup_id, attendees }` | Attendee list changed |
| `meetup_expired` | `{ meetup_id }` | Meetup + its chat are gone |
| `banned` | `{ reason }` | User has been banned — disconnect |
| `strike` | `{ count, reason }` | Warning: strike N of 3 |

---

## Message Types

All `content` fields are JSON strings.

### text
```json
{ "text": "this set is incredible 🔥" }
```

### image
```json
{ "url": "/chat/uploads/uuid.avif", "width": 1200, "height": 800 }
```
Uploaded as multipart. Server processes: validate MIME + magic bytes, resize longest side to 1500px, encode to AVIF (quality 50, pyvips — same pipeline as artist photos). Max upload: 5MB. File deleted with message at expiry.

### location
```json
{ "lat": 51.4862, "lng": 7.0442, "label": "Near Grand Hall entrance" }
```
Browser Geolocation API. Rendered as a static map tile (OpenStreetMap) or tappable link.

### meetup_invite
```json
{ "meetup_id": "uuid", "title": "Meet at the main bar", "meetup_time": "2026-07-12T23:00:00+02:00" }
```
Auto-generated when a meetup is created. Appears as a card in the stage room. Not user-authored — the system creates this message.

---

## Moderation Pipeline

Every message passes through this pipeline before any other user sees it. No exceptions.

```
User sends message
       │
       ▼
[1] Rate limiter (max 5 msgs / 10s per user)
       │── THROTTLED → reject with "Slow down"
       ▼
[2] Word filter (in-memory Set, <1ms)
       │── MATCH → reject, add strike
       ▼
[3] OpenAI omni-moderation-latest (async, ~100-200ms)
       │── FLAGGED → reject, add strike
       ▼
[4] For images: OpenAI omni-moderation-latest (image input)
       │── FLAGGED → reject, add strike
       ▼
[5] Store in chat.db + broadcast to room
```

### Word Filter

In-memory Python `set`, loaded on startup from a text file (`chat/blocklist.txt`). Checked against normalized message text (lowercase, stripped diacritics, common substitutions like `@` → `a`, `0` → `o`). Includes:

- Drug terms and festival slang (mdma, molly, ket, ketamine, speed, pills, coke, acid, dealer, plug, score, etc.)
- Slurs and hate speech
- Common spam patterns

The blocklist is a text file — editable without redeploying.

### OpenAI Moderation

**Model:** `omni-moderation-latest`
**Cost:** Free, unlimited
**Latency:** ~100-200ms
**Input:** Text and images

Categories checked: `hate`, `harassment`, `harassment/threatening`, `sexual`, `sexual/minors`, `self-harm`, `violence`, `violence/graphic`, `illicit`, `illicit/violent`.

Thresholds (configurable):
- Any category score > 0.80 → block
- `sexual/minors` or `violence/graphic` > 0.50 → block + immediate ban (no strikes)
- `illicit` > 0.70 → block + strike

### Strike System

| Strike | Action |
|---|---|
| 1st | Message blocked, user sees warning: "Your message was flagged. Repeated violations will result in a ban." |
| 2nd | Message blocked, 30-minute mute (can read but not send) |
| 3rd | Permanent ban. Account disabled. Provider ID + device fingerprint added to bans table. WebSocket disconnected with `banned` event. |

Strikes persist for the duration of the event (not per-session). Drug-related word filter matches escalate faster: first drug match is strike 1, second is immediate ban.

### Image Moderation

Images are moderated in two steps:
1. **Server-side validation**: MIME type + magic bytes check. Only JPEG, PNG, WebP, AVIF accepted.
2. **OpenAI moderation**: The processed image is sent to `omni-moderation-latest` alongside any text content.

### Manual Reporting

Users can report any message. The report saves a plaintext snapshot of the message content (survives the 60-min deletion). Admin reviews via CLI or a simple admin endpoint. Reports resolved as 'actioned' (ban) or 'dismissed'. Snapshots purged 30 days after resolution.

---

## Meetups

Meetups are first-class entities, not just messages.

### Creating a Meetup

1. User taps the 🤝 button in a stage room
2. Form: title, location (map pin or current GPS), time, optional note
3. Server creates:
   - A `meetups` row
   - A `rooms` row (type='meetup', id=meetup_id)
   - A `meetup_invite` message in the source stage room
4. Creator is auto-added as first attendee

### Meetup Lifecycle

- **Active**: meetup_time is in the future. Card shows "I'm in" button. Chat is active.
- **Happening now**: meetup_time has passed but < 30 min ago. Card shows "Happening now". Chat still active.
- **Expired**: meetup_time + 30 min. Meetup row, room, all messages, and attendees are deleted by the purge job. Card disappears from the stage room.

### Meetup Chat

Each meetup has its own dedicated chat room. Only attendees can read and write. Tap "Open chat" on the meetup card to enter. Same message types, same moderation, same 60-min expiry on individual messages.

### Meetup Discovery

A "Meetups" view (accessible from the command bar) shows all active meetups across all stages, sorted by time. Filterable by stage. Shows: title, location, time, attendee count, source stage.

---

## DMs

### Creating a DM

User taps another user's name in a chat room → option to "Send message". Server finds or creates a DM room between the two users.

### DM Privacy

Only the two participants can see DM messages. DM messages follow the same 60-min expiry. If a user is banned, their side of all DMs is deleted.

### DM Notifications

Reuse the existing push notification infrastructure (VAPID + service worker). When a DM arrives and the recipient's tab is in the background, send a push notification: "New message from @username".

---

## Push Notifications

Reuses the existing VAPID + service worker infrastructure. Two notification types in v1:

### DM Notifications

Triggered immediately when a DM message is stored. If the recipient has an active WebSocket connection and the chat panel is focused on that DM, no push is sent (they're already reading it). Otherwise:

- **Title**: display name of the sender
- **Body**: message preview (first 100 chars of text, or "Sent an image" / "Shared a location")
- **Click action**: opens the app and navigates to that DM conversation

Sent via the existing `pywebpush` infrastructure. The chat user's push subscription is stored alongside their existing schedule push subscription (same `push_subscriptions` mechanism in `hearts.db`).

### Meetup Reminders

Integrated into the existing push notification scheduler (background task in `api.py` that runs every 60 seconds). The scheduler already checks `timetable.json` for upcoming sets — extend it to also check `meetups` in `chat.db`:

```
For each meetup where meetup_time is 10 minutes from now:
  For each attendee of that meetup:
    If attendee has a push subscription:
      Send: "Your meetup '{title}' starts in 10 minutes"
```

Dedup via the existing `sent_notifications` table — key `(user_id, meetup_id)` prevents duplicate sends on scheduler re-runs.

- **Title**: "Meetup in 10 minutes"
- **Body**: meetup title + location label (e.g. "Main bar hangout · Near Grand Hall entrance")
- **Click action**: opens the app and navigates to the meetup chat

### Not in v1

- Stage room activity notifications (too noisy)
- @mention notifications (requires parsing, defer)
- "New meetup in a stage you're watching" (nice-to-have, not essential)

---

## Auto-Deletion Pipeline

### Purge Job

Runs every 30 seconds via `asyncio` background task in FastAPI:

```python
async def purge_expired():
    while True:
        await asyncio.sleep(30)
        now = datetime.utcnow().isoformat()

        # 1. Get expired messages (need IDs for client notification + file cleanup)
        expired = db.execute(
            "SELECT id, room_id, type, content FROM messages WHERE expires_at <= ?", (now,)
        ).fetchall()

        # 2. Group by room for client notification
        by_room = defaultdict(list)
        for msg in expired:
            by_room[msg["room_id"]].append(msg["id"])
            # Delete image files
            if msg["type"] == "image":
                content = json.loads(msg["content"])
                path = Path(content["url"].lstrip("/"))
                path.unlink(missing_ok=True)

        # 3. Delete messages
        db.execute("DELETE FROM messages WHERE expires_at <= ?", (now,))

        # 4. Delete expired meetups + their rooms
        expired_meetups = db.execute(
            "SELECT id FROM meetups WHERE expires_at <= ?", (now,)
        ).fetchall()
        for meetup in expired_meetups:
            db.execute("DELETE FROM meetup_attendees WHERE meetup_id = ?", (meetup["id"],))
            db.execute("DELETE FROM messages WHERE room_id = ?", (meetup["id"],))
            db.execute("DELETE FROM rooms WHERE id = ?", (meetup["id"],))
        db.execute("DELETE FROM meetups WHERE expires_at <= ?", (now,))

        # 5. Clean expired sessions
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

        db.commit()

        # 6. Notify connected clients
        for room_id, message_ids in by_room.items():
            await broadcast_to_room(room_id, {
                "event": "messages_expired",
                "room_id": room_id,
                "message_ids": message_ids,
            })
```

### Report Snapshot Purge (daily)

```sql
DELETE FROM reports
WHERE status IN ('actioned', 'dismissed')
AND reviewed_at < datetime('now', '-30 days');
```

---

## REST API Endpoints

Base: `https://stonetechno.deftlab.dev/chat/api`

### Auth

| Method | Endpoint | Body | Response |
|---|---|---|---|
| POST | `/auth/google` | `{ id_token, device_fingerprint }` | Sets cookie, returns `{ user }` |
| POST | `/auth/apple` | `{ id_token, device_fingerprint }` | Sets cookie, returns `{ user }` |
| POST | `/auth/email/start` | `{ email, device_fingerprint }` | `{ sent: true }` or `{ error: "disposable" }` |
| GET | `/auth/email/verify` | `?token=...` (magic link) | Sets cookie, redirects to app |
| POST | `/auth/logout` | — | Clears cookie |
| DELETE | `/auth/account` | — | Deletes user + all data |
| PUT | `/auth/profile` | `{ display_name?, avatar? }` | `{ user }` |

### Rooms

| Method | Endpoint | Response |
|---|---|---|
| GET | `/rooms` | `[{ id, type, name, online_count }]` |
| GET | `/rooms/:id/messages` | Last 60 min of messages, paginated |
| GET | `/rooms/:id/online` | `[{ user_id, display_name, avatar_url }]` |

### Meetups

| Method | Endpoint | Body | Response |
|---|---|---|---|
| GET | `/meetups` | — | All active meetups (filterable by `?stage_id=`) |
| GET | `/meetups/:id` | — | Meetup details + attendees |
| POST | `/meetups` | `{ stage_id, title, lat, lng, label, meetup_time, note }` | `{ meetup, room }` |
| POST | `/meetups/:id/join` | — | `{ attendees }` |
| DELETE | `/meetups/:id/join` | — | `{ attendees }` |

### DMs

| Method | Endpoint | Body | Response |
|---|---|---|---|
| GET | `/dms` | — | List of active DM conversations |
| POST | `/dms` | `{ target_user_id }` | `{ room }` (creates or returns existing) |

### Media

| Method | Endpoint | Body | Response |
|---|---|---|---|
| POST | `/upload/image` | Multipart (max 5MB) | `{ url, width, height }` |
| POST | `/upload/avatar` | Multipart (max 500KB) | `{ avatar_url }` |

### Reports (Admin)

| Method | Endpoint | Body | Response |
|---|---|---|---|
| GET | `/admin/reports` | `?status=pending` | `[{ report }]` |
| PATCH | `/admin/reports/:id` | `{ status, action? }` | `{ report }` |
| POST | `/admin/ban/:user_id` | `{ reason }` | `{ success }` |
| POST | `/admin/unban/:user_id` | — | `{ success }` |

Admin endpoints protected by a middleware checking user role.

---

## Image Processing

Same pipeline as artist photos, reused from `scraper/images.py`:

1. Validate MIME type + magic bytes (JPEG, PNG, WebP only)
2. Strip EXIF data (privacy — removes GPS, camera info)
3. Resize: longest side to 1500px max, preserve aspect ratio, lanczos3
4. Encode to AVIF (pyvips, quality 50 — aggressive compression for chat)
5. Store in `chat/uploads/` directory
6. Delete file when message expires (purge job)

Max upload: 5MB raw. Output typically 50-150KB after processing.

---

## Emoji

No API. Client-side Unicode emoji picker ([emoji-mart](https://github.com/missive/emoji-mart), ~40KB gzipped). Emoji are Unicode characters in text messages — no special handling server-side.

---

## Privacy

### What the server stores

- User: display name, avatar URL, provider type + provider ID, device fingerprint, creation date, last seen
- Messages: plaintext content (not encrypted — deleted after 60 min), room, sender, timestamp
- Report snapshots: plaintext copies of reported messages (max 30 days after resolution)
- That's it. No IP logs. No read receipts. No analytics. No email stored (only hash for email auth).

### Why no encryption at rest

Messages delete after 60 minutes. AES encryption at rest protects against database theft — but if someone has access to the server, the encryption key is in an environment variable on the same server. It's security theater for ephemeral data. TLS in transit is the real protection.

### No logging

- Caddy: disable access logging for `/chat/*` and `/ws/chat/*` routes
- FastAPI: no message content in logs, only event names and room IDs
- SQLite: no query logging by default

---

## Reconnection Handling

Festival WiFi is terrible. The client must handle disconnection gracefully:

1. **Auto-reconnect**: exponential backoff (1s, 2s, 4s, 8s, max 30s)
2. **On reconnect**: client sends `{ last_message_id }` per room. Server sends missed messages from the last 60 min (only messages not yet expired).
3. **Optimistic UI**: message appears immediately with a "sending" indicator. If the send fails, show retry button.
4. **Offline queue**: if disconnected, queue up to 10 messages in memory. Send on reconnect. Messages older than 5 min in the queue are dropped (stale).

---

## Infrastructure

### Existing VPS (DigitalOcean)

No new server needed. Chat runs on the same VPS as the existing app.

| Resource | Current | With chat |
|---|---|---|
| RAM | 2 GB | 4 GB recommended (WebSocket connections + in-memory data) |
| CPU | 2 cores | Sufficient |
| Disk | 20 GB | Sufficient (chat data is ephemeral) |

Upgrade RAM if needed. Everything else stays.

### Caddy Configuration

Add to existing Caddyfile:

```caddyfile
stonetechno.deftlab.dev {
    encode zstd gzip
    header /photos/* Cache-Control "public, max-age=31536000, immutable"

    # Disable access logging for chat routes
    @chat path /chat/* /ws/chat/*
    log @chat {
        output discard
    }

    reverse_proxy stone-techno:8080
}
```

### Docker Compose

Extend the existing `docker-compose.yml`:

```yaml
services:
  stone-techno:
    build: .
    restart: always
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
      - APPLE_CLIENT_ID=${APPLE_CLIENT_ID}
      - RESEND_API_KEY=${RESEND_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - CHAT_SESSION_SECRET=${CHAT_SESSION_SECRET}
      - VAPID_PRIVATE_KEY=/app/data/vapid_private.pem
      - VAPID_PUBLIC_KEY=${VAPID_PUBLIC_KEY}
      - VAPID_SUBJECT=${VAPID_SUBJECT}
    volumes:
      - ./data:/app/data          # hearts.db + chat.db
      - chat-uploads:/app/chat/uploads

volumes:
  chat-uploads:
```

### New Dependencies

Add to `server/requirements.txt`:

```
google-auth           # Google OAuth token verification
pyjwt[crypto]         # Apple Sign In JWT verification
resend                # Email magic links
openai                # Moderation API
python-multipart      # File uploads
```

---

## Frontend

### Libraries

| Library | Purpose | Size |
|---|---|---|
| emoji-mart (standalone) | Emoji picker | ~40KB gzip |

Everything else is vanilla JS. No frameworks. Chat UI is generated by `render.py` as part of the existing HTML, same as all other frontend code.

### Client-Side State

Messages exist only in the live DOM. No localStorage, no IndexedDB for messages. When a message expires (`messages_expired` event), the DOM element is removed. When the tab closes, messages are gone.

User session (cookie) persists across tab closes. On reopen, the client reconnects and fetches the last 60 min of messages for the current room.

---

## Resolved Decisions

1. **Avatars**: generated (initials on a color derived from user ID). No custom uploads in v1 — zero moderation needed.
2. **Data retention**: wipe everything (including bans) 30 days after event end. Clean slate each edition. No data survives between editions.
3. **Map tiles**: tappable link styled as a card ("Open in Maps"). Zero infrastructure.
4. **GIF support**: deferred to v2. Text + images + emoji + location + meetups is enough for v1.
5. **Notifications**: DM notifications (immediate) + meetup reminders (10 min before). No stage room notifications.

## Open Decisions

1. **Admin tooling**: minimal admin page at `/chat/admin` with pending reports + ban/dismiss buttons, or CLI/database access only?
2. **Slow mode**: auto-activate in rooms with >100 messages/minute, or leave stage rooms unthrottled?
