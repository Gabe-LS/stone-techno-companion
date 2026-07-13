# 0003. Ingestion model for festival number two (D1)

## Status

Proposed

## Date

2026-07-13

## Context

This is blueprint open decision D1 (section I), gating Phase 1. Today the
system supports exactly one ingestion path: a hand-written scraper module per
event. `pipeline/scraper/scrape.py` is the lineup parser plus the
SoundCloud/Instagram/Spotify/Resident Advisor scrapers for the current event;
CLAUDE.md is explicit that "each event needs its own scraper module." The
interface between a scraper and the rest of the pipeline is a `parsed` dict
with `artists`, `sections`, `locations`, and `assignments` keys, which
`pipeline/scraper/db.py` upserts into `lineup.db`. Manual corrections (artist
links, editorial floor-curator text, YouTube overrides) are layered on top via
`pipeline/scraper/overrides.toml`, applied after scraping and before follower
fetching. The DB schema already supports multiple events (an `events` table,
with artists/artist_links/artist_sets/stages/venues global and
schedule/event_stages scoped per event, per the "Multi-Event Support"
section), so the schema is not the blocker: the ingestion source for a
*second* festival is.

Section H of the blueprint names this as the actual make-or-break risk, ahead
of any UI or framework question: "Scraping all of Europe is a permanent ops
+ legal/ToS burden. Many sites forbid it; you'll need partnerships/APIs."
Whatever is decided here also drives the Phase 1 data model: the blueprint's
Phase 1 description calls for "a festival/route/artist data model
(config-driven, not files; the transport itineraries move from hand-edited
JSON into it)": a materially different data model depending on whether the
data enters through a Python scraper module, an organizer-facing form, or a
partner feed's schema.

## Options considered

**A. Hand-written scraper module per event (current contract, extended).**
Every new festival gets its own scraper module conforming to the existing
`parsed` dict interface (artists/sections/locations/assignments), the same
pattern used for the current event's lineup page plus its per-platform
scrapers. Overrides continue via a TOML-like mechanism (or its Phase 1
successor, a config-driven equivalent per the blueprint).
- *Legal/ToS exposure*: highest and most fragmented: every new source site
  has its own terms of service, and scraping is inherently adversarial to
  sites that forbid it (section H). Risk is per-site and per-scraper, and
  compounds linearly with each new festival if each publishes its own
  lineup differently.
- *Phase 1 data model impact*: lowest disruption: the existing `parsed`
  dict contract can be kept as the ingestion interface "until organizer
  self-serve exists" (blueprint section F, `pipeline/` row), meaning the
  Phase 1 config-driven data model can be built underneath the same
  interface without touching how data arrives.
- *Cost*: proportional to number of festivals and how differently each
  publishes its lineup/schedule; no organizer effort required, but every new
  event is engineering work, not organizer self-service, which does not
  scale past a handful of festivals.

**B. Organizer self-serve through a CMS/dashboard.** Organizers enter their
own lineup, schedule, and location data directly, through whatever surface
ADR 0004 (D2, organizer dashboard owner) decides.
- *Legal/ToS exposure*: essentially eliminated for lineup data: organizers
  are entering their own data, not third parties': but shifts the
  liability surface to data accuracy, moderation of organizer-submitted
  content, and account/permission management (who at a festival is
  authorized to edit its data).
- *Phase 1 data model impact*: the largest change: requires structured
  input forms/validation (artist entries, schedule slots, stage
  assignments), versioning or draft/publish states, and almost certainly
  reuses or extends whatever CMS is chosen in ADR 0004, rather than
  the scraper's `parsed` dict contract at all. `overrides.toml`-style manual
  correction becomes redundant (organizers edit directly) but a moderation/
  review step before publish likely replaces it.
- *Cost*: near-zero marginal engineering cost per new festival once the
  dashboard exists, but requires organizer buy-in, onboarding, and the
  dashboard itself (a real product to build, currently unbuilt per
  section I's own framing: "no pillar builds that dashboard").

**C. Partnership data feeds.** Formal data-sharing agreements with an
existing aggregator, ticketing platform, or festival's own systems, ingested
via API/webhook instead of scraping HTML.
- *Legal/ToS exposure*: converted from an adversarial scraping risk into a
  contractual one: a signed agreement removes the ToS-violation exposure
  entirely for that source, but introduces business-development lead time,
  potential licensing cost, and a dependency on the partner's API stability
  and coverage (it only solves ingestion for festivals that have such a
  partner).
- *Phase 1 data model impact*: medium: requires an adapter/normalizer layer
  mapping each partner's schema onto the internal data model (similar
  shape to option A's per-event scraper module, but the module talks to a
  documented API instead of parsing HTML, and update cadence is
  partner-defined, e.g. webhooks vs polling, rather than run-on-demand).
- *Cost*: mostly non-engineering (partnership negotiation) up front, then
  low marginal engineering cost per additional festival on the same
  partner; does not scale to festivals without a partner relationship.

**What is needed to decide.** Which festival is actually the "festival
number two" target and whether its organizers are willing/able to self-serve
(option B is worthless if no organizer wants to touch a dashboard); a legal
read (not just engineering judgment) on the ToS of the specific target
site(s) under option A; whether any partnership conversations (option C) are
already underway or realistic within the Phase 1 timeline; and team bandwidth
to build a second scraper module (A) versus dashboard input surfaces (B) in
the time available.

## Leaning

Start with option A for the immediate next festival (it requires zero new
product surface and is the proven, already-working path), but do not let
that choice leak into the Phase 1 data model as a hard assumption. Design the
Phase 1 data model with an ingestion-source abstraction from the start (the
`parsed` dict contract already gives this for free per blueprint section F),
so that option B or C can be added later as an alternate populator of the
same schema without another migration. Option A alone does not scale past a
small number of festivals given the legal exposure named in section H, so
this is a sequencing choice (A first, ship Phase 1 on schedule) rather than a
belief that A is the permanent answer: ADR 0004's dashboard decision and any
partnership progress should revisit this ADR once either is concrete.

## Decision

Pending.

## Consequences

Pending: depends on the option chosen. At minimum, whichever option is
chosen determines the shape of the Phase 1 "festival/route/artist data
model" work named in the blueprint, and should be finalized before that data
model's schema is locked, not after.
