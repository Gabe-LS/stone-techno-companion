# Lineup Data API: Design (Stage 2)

Status: proposed, no implementation. This is the read API the Next.js front
(ADR 0001) will call for lineup and timetable data, replacing the data that
`services/data/scraper/render.py` bakes directly into `lineup.html` today. It
does not cover the favorites/schedule write API already implemented in
`services/companion/api.py` (`/api/session/*`), except where this design must match its
existing id formats exactly (section 5).

Grounded against: `CLAUDE.md` (Database schema, Architecture data flow,
Multi-Event Support), `docs/invariants.md` (INV-1), `docs/parity/lineup.md`,
`docs/parity/timetable.md`, `services/data/scraper/timetable_json.py`,
`services/data/scraper/db.py`, `services/data/scraper/render.py`, and a local read of
`services/data/lineup.db` (row counts and byte sizes cited below are measured
from that file and from `services/data/output/` on 2026-07-13, not estimated).

## 1. Principles

**Read-only.** Every endpoint in this document is a `GET`. Writes (hearts,
schedule picks, sessions, sync PINs) stay exactly where they are today, in
`services/companion/api.py` against `hearts.db`. This API only serves the festival
content that `lineup.db` already holds.

**Event-scoped, multi-event ready from day one.** Every endpoint is rooted
at `/api/v1/events/{event_id}/...`. This is not speculative: `CLAUDE.md`
("Multi-Event Support") states the schema already supports it, `artists`,
`artist_links`, `artist_sets`, `stages`, and `venues` are global tables
shared across events, while `schedule` and `event_stages` are the two
tables actually scoped by `event_id`. The API's URL shape mirrors that
split: event/lineup/timetable endpoints filter by `event_id`, the artist
detail endpoint does not (an artist's bio, links, and sets are the same
regardless of which event's page is asking for them).

**Slot UUIDs are the slot identity everywhere (INV-1).** Every timetable
slot in this API is identified by the exact value `slot_uuid()` in
`services/data/scraper/timetable_json.py` already computes, not a new id scheme.
That function is the single source of truth for a set's identity, used
today for saved schedules, push dedup, and ICS export; this API must call
the same function (or a byte-identical port of it), never reimplement the
hashing. Per-artist heart/pick ids (a separate, narrower id than the slot
id, see section 5) are likewise reproduced exactly as `render.py` computes
them today, not redesigned.

**Payloads sized for festival wifi.** Today's split is: `lineup.html`
(~650 KB, measured 636 KB locally) contains the full line-up markup plus
the inline `TT_ARTISTS`/`TT_SECTIONS` timetable data baked into `<script>`
tags, fetched once on page load; `bios.json` (~200 KB, measured 214 KB
locally) holds every artist's bio/links/sets and is fetched lazily, only on
the first bio-overlay open, then cached in memory for the rest of the
session (`docs/parity/lineup.md` section 3). This API keeps that same
split: the lineup + timetable endpoints (section 2) return only what is
needed to paint the two views (names, times, floor refs, photo file
names, follower counts, slot ids), and bio markdown/HTML, full sets list,
and thumbnails stay behind the separate artist-detail endpoint, fetched
only when a bio overlay actually opens, exactly as `bios.json` is lazy
today. Nothing this API returns duplicates the ~636 KB of HTML/CSS/JS
`render.py` currently emits: only the data, never markup.

## 2. Proposed endpoints

All response bodies are JSON. All paths are additions under the existing
FastAPI app (`services/companion/api.py`), which already serves the favorites/schedule
API at `/api/session/*` and static content at `/`, `/bios.json`,
`/manifest.json`, etc.

### 2.1 `GET /api/v1/events/{event_id}`

Event metadata: `events` table columns, plus `short_name` (used by the
front for the `"{Line-up|Timetable} · {short_name}"` title convention) and
`edition` (used for the `"{name} {edition} Companion"` page-title pattern
per `CLAUDE.md`).

```json
{
  "id": "stone-techno-2026",
  "name": "Stone Techno",
  "short_name": "ST26",
  "edition": "2026",
  "timezone": "Europe/Berlin",
  "start_date": null,
  "end_date": null,
  "address": null,
  "website": null,
  "source_url": null,
  "latitude": null,
  "longitude": null
}
```

Note (grounded, not invented): in the current local `lineup.db`,
`start_date`/`end_date`/`address`/`website`/`source_url`/`latitude`/
`longitude` are all `NULL` for the one real event row (`ensure_event` writes
whatever the scraper passes as kwargs, and today's scraper never populates
them). The front cannot rely on `start_date`/`end_date` to enumerate
festival days today; it must derive the day list from the distinct dates
present in the timetable endpoint's `days` array (section 2.3), the same
way `render.py`'s `dates_seen` is derived from `schedule.date`, not from
`events.start_date`. If Stage 2 wants the front to trust `events.start/
end_date` directly, the pipeline must start populating them; that is a
pipeline change, not an API design change, and is called out again as an
open question in section 6.

### 2.2 `GET /api/v1/events/{event_id}/lineup`

The list-view data: every artist appearance grouped by date, period, and
(for `night` periods only) floor, mirroring `render.py`'s grouping in
`render_artist_card`'s caller. Grouping keys are also present on each
entry, so the client is not forced to trust the server's chosen nesting.

```json
{
  "event_id": "stone-techno-2026",
  "days": [
    {
      "date": "2026-07-11",
      "periods": [
        {
          "period": "night",
          "floors": [
            {
              "floor_id": "koksofenbatterie",
              "floor_name": "Koksofenbatterie",
              "artists": [
                {
                  "id": "5296a367-bad4-4fb3-a383-68bc57f1d4d4",
                  "card_id": "b2c1e0b0-....",
                  "name": ".VRIL",
                  "photo": "photos/5296a367-bad4-4fb3-a383-68bc57f1d4d4.avif",
                  "links": [
                    { "platform": "instagram", "url": "https://instagram.com/vril", "follower_count": 41200 },
                    { "platform": "soundcloud", "url": "https://soundcloud.com/vril", "follower_count": null }
                  ],
                  "slot": { "start_time": "2026-07-11T23:00", "end_time": "2026-07-12T01:00" },
                  "all_slots": [
                    { "date": "2026-07-11", "period": "night", "floor_id": "koksofenbatterie", "floor_name": "Koksofenbatterie", "start_time": "2026-07-11T23:00", "end_time": "2026-07-12T01:00" }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

Field notes, grounded in `db.py`'s `load_assignments_from_db`:

- `id` is the raw `artists.id` (what `render.py` emits as `data-oid`).
- `card_id` is the derived per-card heart id: exact formula in section 5.
  It is included here, precomputed, because the client must send this
  exact value to `POST /api/session/{code}/pick/{card_id}` and any
  divergence silently breaks existing favorites (see section 5); it is not
  something the client should be trusted to rederive independently.
- `links` mirrors `artist_links` rows directly (`platform`, `url`,
  `follower_count`), the same fields `_load_all_artist_links` already
  selects. `follower_count` is `null`, not `0` or an empty string, when
  unset, matching the DB column's actual nullability (today's HTML instead
  collapses a missing count to an empty string via `format_followers`;
  this API keeps the raw `null` and leaves that formatting decision to the
  client, consistent with principle: data, not presentation).
- `slot` is this entry's own time in this specific date/period/floor
  grouping.
- `all_slots` is every schedule row for this artist across the whole event
  (the same structure `db.py`'s `_load_artist_all_slots` already builds
  internally), not a pre-formatted "Also X, Y" string. `render.py` today
  collapses this into the `.artist-also` text at render time
  (`_format_artist_schedule`); `docs/parity/timetable.md` documents that
  the timetable view never shows this cross-reference at all, only the
  list view does. Shipping the raw `all_slots` array once lets the Next.js
  list view build the "Also" line and lets the Next.js timetable view
  correctly omit it, without the API baking a view-specific decision into
  the data.
- Non-night periods (`day`) have a single flat `artists` array per period,
  no `floors` grouping (`floor_id`/`floor_name` omitted at that level),
  matching `render.py`'s day/night branch (`docs/parity/lineup.md` 2).
- An artist with zero `links` is an empty array, not omitted, so the
  client can render "No links" itself (today's `<span class="missing">`).

### 2.3 `GET /api/v1/events/{event_id}/timetable`

The timetable-view data: days, then periods, then floors (with stage
colors), then slots. A slot is one `(floor, start_time, end_time)` group,
which may hold multiple artists (a B2B set).

```json
{
  "event_id": "stone-techno-2026",
  "floors": [
    { "id": "koksofenbatterie", "name": "Koksofenbatterie", "color": "197, 213, 249" },
    { "id": "eisbahn", "name": "Eisbahn", "color": "198, 249, 197" }
  ],
  "days": [
    {
      "date": "2026-07-11",
      "periods": [
        {
          "period": "night",
          "is_night": true,
          "grid_start_min": 1380,
          "grid_end_min": 1620,
          "floor_ids": ["eisbahn", "koksofenbatterie", "listening-floor"],
          "notes": [
            { "floor_id": "koksofenbatterie", "note": "curated by Freddy K" }
          ],
          "slots": [
            {
              "slot_id": "b2c1e0b0-....",
              "floor_id": "koksofenbatterie",
              "start_time": "2026-07-11T23:00",
              "end_time": "2026-07-12T01:00",
              "is_b2b": true,
              "artists": [
                {
                  "id": "5296a367-bad4-4fb3-a383-68bc57f1d4d4",
                  "card_id": "d4f8a1c2-....",
                  "name": ".VRIL",
                  "photo": "photos/5296a367-bad4-4fb3-a383-68bc57f1d4d4.avif"
                },
                {
                  "id": "bc591736-bcef-45a8-9f83-a639c51909af",
                  "card_id": "9e17b0aa-....",
                  "name": "Function",
                  "photo": "photos/bc591736-bcef-45a8-9f83-a639c51909af.avif"
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

Field notes, grounded in `render.py`'s timetable-data build and
`timetable_json.py`:

- `floors` is one global list per event (from `event_stages` joined to
  `stages`), each with `color` copied verbatim from `event_stages.color`
  (an `"R, G, B"` string, e.g. `"197, 213, 249"` as actually stored in
  `lineup.db`) so the client applies the exact same `rgba(...,0.88)` card /
  opaque `rgb(...)` pill convention documented in `CLAUDE.md`.
- `grid_start_min`/`grid_end_min` are the same floored-to-hour /
  night-wraparound-adjusted minute values `render.py` computes today
  (`grid_start`, `grid_end`), so the client can reproduce the exact grid
  math (`row = (minute - grid_start) + 2`) without recomputing wraparound
  logic itself.
- `floor_ids` gives the per-period floor column order actually present
  that period. Note: today's order comes from a hardcoded Python list
  (`canonical_floor_order` in `render.py`) with any other floor id
  appended in encounter order, not from `event_stages.position`. This API
  should source the order from `event_stages.position` going forward (the
  DB already carries an explicit position for exactly this purpose) and
  the pipeline's hardcoded list should be treated as a workaround to
  retire, not a convention to copy forward. Flagged, not silently changed.
- `notes` is `stage_notes` for that specific `(date, floor_id)`, the same
  key `load_stage_curators` already builds (`"{date}.{stage_id}"`, e.g.
  `"2026-07-11.koksofenbatterie"`), shaped as a list instead of a
  string-keyed dict so the client does not need to parse a compound key.
- `slot_id` is `slot_uuid()`, unchanged (INV-1). For a B2B slot it is
  computed over all member artist ids jointly, exactly as `render.py`
  computes `data-artist-id` on the shared `.tt-block`; schedule/ICS state
  is per-slot, not per-artist, consistent with `docs/parity/timetable.md`
  section "B2B Sets".
- Each artist inside a slot carries its own `card_id` (the per-artist heart
  id, distinct from `slot_id`), because hearts are tracked per-artist even
  inside a shared B2B card (`docs/parity/timetable.md` "B2B Sets": each
  `.tt-artist-row` has its own `data-artist-id`, only the outer `.tt-cal`/
  ICS button uses the shared slot id).
- There is no `also` or cross-reference field on a timetable slot's
  artists: `docs/parity/timetable.md` is explicit that `render.py` never
  computes an "Also playing" note on a timetable card, only on list-view
  cards. This API does not invent one either; a client wanting that data
  for a timetable-rendered artist should look it up via the lineup
  endpoint's `all_slots` for that artist id.

### 2.4 `GET /api/v1/events/{event_id}/artists/{artist_id}`

Artist detail: bio, links, sets. This is the lazy-loaded replacement for
today's `bios.json` entry, fetched only when a bio overlay opens, keyed by
the raw `artists.id` (matching `bios.json`'s existing keying today, not the
derived `card_id`, see section 5 for why these must never be confused).

```json
{
  "id": "5296a367-bad4-4fb3-a383-68bc57f1d4d4",
  "name": ".VRIL",
  "photo": "photos/5296a367-bad4-4fb3-a383-68bc57f1d4d4.avif",
  "bio_html": "<p>.VRIL is ...</p>",
  "links": [
    { "platform": "instagram", "url": "https://instagram.com/vril", "follower_count": 41200 }
  ],
  "sets": [
    {
      "id": "9Zvb6IgLEIE",
      "platform": "youtube",
      "url": "https://www.youtube.com/watch?v=9Zvb6IgLEIE",
      "title": "STOOR Live in Paradiso - March 2026 - Erika x Function x .VRIL x Wata Igarashi x Speedy J",
      "view_count": 24847,
      "duration_min": 420,
      "upload_date": 20260331,
      "thumb": "thumbs/9Zvb6IgLEIE.avif"
    }
  ]
}
```

Field notes:

- `bio_html` is pre-rendered, sanitized HTML, not raw markdown. Decision
  and justification: today's `_render_markdown()` in `render.py` does
  `markdown.markdown(text, extensions=['nl2br'])` through a hand-written
  `HTMLParser`-based allowlist sanitizer (allowed tags, `href`-only on
  `<a>`, `^https?://` scheme check per INV-14), after `_strip_booking()`
  removes booking/contact paragraphs. That sanitizer is the one place this
  content's XSS surface is audited. Moving markdown rendering to the
  Next.js client would mean either porting that exact sanitizer to a JS
  library (a second implementation of the same security-critical logic to
  keep in sync) or trusting a generic markdown-to-React library's default
  sanitization for scraped, semi-trusted bio text (populated from a
  festival's website and `overrides.toml`, not a fully trusted source per
  INV-14's own reasoning). Rendering stays server-side, in whichever
  process now owns `_render_markdown`/`_strip_booking` (the pipeline, or
  this API's process reusing the same function), so there is exactly one
  audited sanitizer, matching the current architecture. This is revisited
  as an open question in section 6 because the orchestrator may weigh
  Next.js ecosystem sanitizers (e.g. `rehype-sanitize`) differently.
- `sets` mirrors `artist_sets` columns (`id`, `platform`, `url`, `title`,
  `view_count`, `duration_min`, `upload_date`), renamed from `db.py`'s
  `load_all_sets` shape (`views`/`duration`/`date`) back to their DB column
  names for API clarity, plus a computed `thumb` path. `artist_sets.id` is
  the platform's native video id (e.g. a YouTube video id like
  `9Zvb6IgLEIE`, confirmed from `lineup.db`), and thumbnails are keyed by
  that id, not the artist id: `thumbs/{artist_sets.id}.avif`, exactly as
  `docs/parity/lineup.md` documents (`thumbs/{video.id}.avif`).
- An artist with no bio has `bio_html: ""` (matching today's fallback,
  never `null`, so the client's "no biography available" branch is a
  falsy-string check, unchanged from today's `_loadBios()` logic).

### 2.5 Static asset conventions

Unchanged from today, not reinvented by this API:

- Photos: `photos/{artists.photo_file}`, AVIF, 120x120 desktop / 72x72
  mobile as displayed (encoded once by the pipeline at whatever source
  resolution `images.py` targets). Absent `photo_file` means no `photo`
  field value (empty string in the JSON, matching `bios.json`'s existing
  convention), the client renders its own placeholder.
- Video thumbnails: `thumbs/{artist_sets.id}.avif`, keyed by the video id,
  not the artist id, 240px max per `CLAUDE.md`.
- Both are served as plain static files (today via the pipeline's
  `--deploy` rsync into `services/companion/static/`; see section 3 for how this
  extends to the new JSON endpoints), not proxied or regenerated by this
  API.

## 3. Serving strategy

Three options, weighed against how this data actually changes: today, every
byte in `lineup.html`/`bios.json`/`timetable.json` changes only when the
pipeline runs (`--render-only --deploy` or a full scrape), never in
response to a user action; the only per-request-varying data in the whole
system is favorites/schedule state, which already lives in `hearts.db`
behind the separate write API this document does not touch.

**(a) FastAPI serves JSON generated live from `lineup.db` on every
request.** Simplest to keep in sync (no separate generation step), but
pays a SQLite read (several joins across `schedule`, `artists`,
`artist_links`, `event_stages`, `stage_notes`) on every single page load
from every festival attendee, for data that is provably static between
pipeline runs. It also means the API process must hold `lineup.db` open
under concurrent request load, and any CDN/edge caching in front of it can
only be time-based (a TTL), not correctness-based (there is no cheap
signal for "has anything changed since the last request").

**(b) The pipeline emits static JSON files at build time, served exactly
like today's `bios.json`/`timetable.json`.** Zero query cost per request
(a static file read), trivially CDN-cacheable, and fits the existing
`--render-only --deploy` flow with no new moving parts: `deploy_to_vps` in
`stone_techno_companion.py` already stages and rsyncs `timetable.json` and
`bios.json` today, alongside `lineup.html`; adding `event.json`,
`lineup.json`, and a `timetable-view.json` (distinct from the existing
`timetable.json`, see section 4) to that same staging/rsync loop is a
small, well-understood change to code that already exists. The gap: static
files alone give no natural cache-invalidation signal beyond file
mtime/ETag, so a client that cached the old JSON aggressively could miss a
deploy until it revalidates.

**(c) Hybrid: static JSON emitted by the pipeline, fronted by the
companion FastAPI app for explicit cache headers.** The same static files
as (b), but served through explicit FastAPI routes (mirroring the existing
pattern for `/bios.json`, `/manifest.json`, `/shared.css`, `/shared.js`,
which already get `Cache-Control: no-cache` specifically so a content
deploy is picked up without a hard reload, versus the catch-all's
`no-store` for `index.html`) rather than as content read by a generic
static file server the API has no control over.

**Recommendation: (c), hybrid.** The data changes only on a pipeline run,
never on a user request, so paying a live SQLite query per page load (a)
buys nothing (no request ever sees fresher data than the last deploy) while
adding load and a new failure mode (the API process becomes a dependency
for every page paint, where today a CDN or even a dumb static host could
serve `lineup.html` with the backend fully down). Pure static files (b)
get the performance right but leave cache correctness to file mtimes with
no explicit policy, whereas the existing FastAPI static routes already
solve exactly this problem (`no-cache` for content that changes on deploy,
`no-store` for the catch-all) and Next.js gets to call one set of URLs
under the same origin/API surface it already uses for favorites and
schedule, rather than mixing "hit the API" and "hit a bare static host"
patterns for what is, to the client, one logical data source.

## 4. What this replaces

- **The inline JS data structures baked into `lineup.html`**: the
  server-rendered `<ul class="artist-list">` markup per date/period/floor,
  and the two inline `<script>` blobs `var TT_ARTISTS = {...}` (per-slot
  artist arrays, `_artists_json`'s output, keyed by `slot_uuid`) and `const
  TT_SECTIONS = [...]` (the `{date, period, key}` array driving client-side
  period-tab rendering). Both become, respectively, the `lineup` endpoint
  (section 2.2) and the `timetable` endpoint (section 2.3, whose `days` /
  `floor_ids` arrays are the structured equivalent of `TT_SECTIONS`, and
  whose `slots[].artists` is the structured equivalent of `TT_ARTISTS`).
  The ~636 KB of HTML/CSS/JS around that data is retired entirely once
  Next.js owns rendering (per ADR 0001, `render.py` is frozen then deleted
  at Phase 2 parity, not rewritten line for line).

- **`timetable.json` (generated by `services/data/scraper/timetable_json.py`):
  coexists, is not replaced.** It serves a narrower, server-internal
  purpose unrelated to painting a page: the push notification scheduler
  (`services/companion/api.py`'s `_push_notification_scheduler`, matching due slots
  against sessions' saved schedules) and the ICS export endpoint both read
  it directly off disk; per `CLAUDE.md`, "`timetable.json` has no HTTP
  route, it's read server-side by the push scheduler and ICS export only."
  This design's timetable endpoint (2.3) is presentation data for the
  Next.js client; `timetable.json` stays a private, non-HTTP file consumed
  server-side. Both must keep deriving slot ids from the exact same
  `slot_uuid()` call (INV-1) so the two never disagree about what a given
  slot's id is, which is precisely why this design does not reimplement
  that hashing, only reads it.

- **`bios.json`: absorbed into the new artist-detail endpoint** (section
  2.4). The lazy-fetch-once-and-cache behavior it enables today is
  preserved by design (principle 4, section 1): bio/links/sets content
  never rides along with the lineup/timetable payload, only fetched on
  first bio-overlay open, same as today.

## 5. Compatibility constraints

The existing favorites/schedule API (`services/companion/api.py`, `hearts.db`) is
unaffected by this migration and must keep working against whatever this
API serves. That means this API's ids are not a free design choice: they
must equal, byte for byte, the ids `services/companion/api.py` already validates and
stores.

- **Heart/pick id** (`card_id` in this design, `sessions.picks` server-side,
  validated by `UUID_RE` on `POST/DELETE /api/session/{code}/pick/{id}`):
  `str(uuid.uuid5(uuid.NAMESPACE_URL, f"{artist_id}:{date}:{period}:
  {floor_id or ''}"))`. This is computed identically today in two places
  that must never drift apart: `render_artist_card`'s `card_key` (list
  view) and the timetable per-artist-row id (`a_card_key`, where
  `floor_id` is included only for `night` periods, `loc_for_id = fid if
  is_night else ''`, matching the list view's own floor-inclusion rule).
  `artist_id` here is the raw `artists.id`.

- **Schedule/slot id** (`slot_id` in this design, `sessions.schedule`
  server-side, validated by the same `UUID_RE` on `POST/DELETE
  /api/session/{code}/schedule/{id}`): `slot_uuid()` from
  `timetable_json.py` (INV-1), taking the joined artist ids, date, period,
  floor id, start/end time, and the full set of `(start, end)` pairs for
  that group (for collision disambiguation). For a B2B slot this is one id
  shared by every artist in the group, not a per-artist id.

- **Format constraint, not just value**: `services/companion/api.py`'s `UUID_RE` is
  `^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`, a
  literal lowercase-hyphenated UUID string shape. Both ids above already
  satisfy it (they are `str(uuid.uuid5(...))` output). Any future change to
  either id-generation function must keep producing that exact textual
  shape, or the existing, unmodified favorites/schedule API will reject
  every request with a 400 before this migration's own code ever runs.

- **Raw artist id** (`artists.id`, exposed today as `data-oid` on list
  cards and as `oid` inside each `TT_ARTISTS` entry): this is what the
  artist-detail endpoint (2.4) is keyed by, and what `bios.json` is already
  keyed by today. It must never be substituted for `card_id` or `slot_id`
  in a hearts/schedule request: the raw id is not a valid pick/schedule key
  today and reusing it would either silently no-op (id not found) or, if
  it happens to collide with something, corrupt an unrelated user's saved
  state. This is the single most important thing to get right when the
  Next.js client is wired up to both this new read API and the existing
  write API side by side.

## 6. Open questions for the orchestrator

**Bio response language: markdown vs rendered HTML.** Recommendation:
rendered, sanitized HTML (`bio_html`), server-side, reusing (or a direct
port of) today's `_render_markdown()`/`_strip_booking()` in `render.py`,
per the justification in section 2.4. The counter-case worth the
orchestrator weighing explicitly: if the Next.js team standardizes on a
markdown-in-React pipeline (e.g. `react-markdown` + `rehype-sanitize`)
across other platform surfaces (CMS content from Payload, per ADR 0004),
consistency with that pattern might outweigh keeping bio rendering
Python-side. This document's recommendation is HTML server-side because it
reuses an already-audited sanitizer rather than introducing a second one to
keep in sync with INV-14's allowlist, not because client-side markdown
rendering is unsafe in general.

**Pagination.** Recommendation: none. Measured from `services/data/lineup.db`
locally: 101 rows in `artists`, 381 in `artist_links`, 402 in `artist_sets`,
120 in `schedule`, 7 in `stages`/`event_stages`, across 3 dates x 2 periods
(day/night). The full artist-detail payload this replaces (`bios.json`)
measures 214 KB for all 101 artists combined; the lineup/timetable payloads
this design proposes are strictly smaller than that (no bio text, no sets
list) for the same 101 artists and 120 slots. None of these counts
approach a scale where offset/cursor pagination pays for its own
complexity; a single-event page load already fetches everything in one
request today (`lineup.html` at 636 KB, `bios.json` at 214 KB, unpaginated)
without complaint. Multi-event growth (CLAUDE.md's stated direction) would
multiply the row counts by however many events are live at once, but each
event-scoped endpoint stays scoped to one `event_id`, so growth in the
number of events does not by itself grow any single response.

**Versioning strategy.** Recommendation: URL path versioning
(`/api/v1/...`), plus reusing the existing `Cache-Control: no-cache`
pattern (section 3) rather than a version field inside each JSON payload,
because content changes atomically per pipeline run, not per request: there
is no meaningful "version" of the lineup data other than "as of the last
deploy," which cache headers already express. A breaking shape change
(e.g. adding required fields, renaming `id` conventions) bumps `v1` to
`v2` as a new path prefix, mirroring how `deploy.sh --ref` already lets a
whole deploy be pinned to a specific ref without touching `main`; a
payload-embedded version number would let the client detect a mismatch but
would not, by itself, give Next.js two different response shapes to code
against, which a path version does.
