# 0004. Organizer dashboard owner (D2)

## Status

Proposed

## Date

2026-07-13

## Context

This is blueprint open decision D2 (section I), gating Phase 1. The blueprint
assigns two pillars, safety/awareness and food/drink, to a Build tier on the
premise that "organizers manage it via CMS or the dashboard" (section B), but
section I is explicit that "no pillar builds that dashboard": there is
today no organizer-facing management surface at all. Everything an organizer
would need to control is currently either hard-coded, held in a third-party
dataset, or edited by the same person who runs the pipeline:

- Floor/stage curator annotations (`stage_notes` table, `[floor_curators]`
  in `overrides.toml`) are edited by hand in a TOML file and applied at
  pipeline run time.
- Stage colors (`event_stages.color`) and event/venue metadata are DB rows
  populated by the scraper/enrichment pipeline, not organizer-editable.
- Chat room configuration (description, TTL, moderation flags, read-only,
  auto-join) already has an admin surface (the `/chat/admin` SPA) but it
  is scoped to chat moderation, not festival content, and its role model
  (`admins`/`admin_actions` tables, `CHAT_ADMIN_EMAILS` permanent
  super-admins, `_require_super_admin` gating) is chat-specific.
- Festival POIs (first aid, water, safe space, vendor locations: exactly
  the data the safety and food pillars need) live today in a MapTiler
  dataset (`MAPTILER_DATASET_ID`), fetched server-side by `GET
  /chat/api/pois` in `chat_api.py`, normalized and cached 120s, with no
  local copy maintained. CLAUDE.md documents this as deliberate: "organizers
  edit pins live in MapTiler with no redeploy, the key stays off the
  client." A break-glass fallback (a `festival-pois.kmz`/`.kml`/`.json`
  drop into `server/static/`) exists for when MapTiler is unavailable.

Whichever surface is chosen as the organizer dashboard also decides where
POIs live long term: staying on MapTiler keeps today's zero-build
arrangement (and its dependency on a third party's free-tier limits, "100k
loads/mo, no card, hard-stops"), while adopting a dashboard implies POIs
should move into whatever system that dashboard is, so organizers manage
festival content and safety/food locations in one place instead of two.

## Options considered

**A. Payload's admin.** The blueprint already places Payload in the Adopt
tier for CMS (section B: "Payload (MIT core, TS/Next-native) for editorial +
flexible schemas"), and ADR 0001 makes Next.js the front-end, so Payload is
TS/Next-native and shares an ecosystem with the chosen front. Payload's admin
UI is generated from custom collection schemas, which is a natural fit for
structured, flexible content: floor curator notes, safety POI categories,
food vendor listings with menus/prices/dietary filters, editorial copy. It
does not natively do geospatial map-pin editing the way MapTiler's dataset
editor does, so moving POIs into Payload would mean building (or adopting) a
map-picker admin field, comparable in shape to the meetup-creation map picker
the chat frontend already has (`openMeetupMapPicker`, MapLibre-based): that
existing code is a concrete reference implementation to reuse or port rather
than build from scratch.

**B. pretix's organizer area.** pretix is the Adopt-tier choice for
ticketing (section B). Its organizer area is built for event and ticket
configuration (products, quotas, check-in), not general editorial or
geospatial content; using it for POIs or food-vendor listings would be
bending a ticketing admin into a CMS role it was not designed for, and it
would tie festival-content editing to the ticketing integration timeline
(Phase 3 in the blueprint), which is materially later than when the safety/
food pillars are wanted (Phase 1).

**C. A custom-built organizer surface.** Purpose-built for exactly this
project's needs: festival metadata, stage/curator notes, safety POIs, food
vendor listings, all in one place, potentially with the map-pin UX already
proven in the meetup picker. Gives full control over the geodata editing
experience (the thing Payload lacks out of the box) without waiting on
pretix's Phase 3 timeline. Costs real build time for something two adopted
OSS projects already partially offer, and duplicates general-purpose CMS
functionality (auth, roles, revision history) that Payload already provides
for free.

## Leaning

Payload's admin is the best fit: it is TS/Next-native (same stack as ADR
0001's Next.js front, unlike pretix's Django admin or a from-scratch build),
it is already the Adopt-tier answer for CMS/editorial content generally, and
its collection-based admin generation matches the shape of the actual
content (curator notes, food vendor entries, safety info) far better than
bending pretix's ticketing-focused organizer area to the task. The one real
gap is geospatial editing for POIs: Payload does not give you a MapLibre
pin-drop editor for free. The recommended path is Payload for all editorial/
structured content now, with POIs staying on MapTiler until (or unless) a
custom map-editing field is built inside Payload's admin reusing the
MapLibre + OpenFreeMap + NRW-aerial stack already proven in the meetup
picker (`openMeetupMapPicker`): at which point POIs migrate off the
third-party dataset and its free-tier ceiling entirely. That migration
should be its own follow-up decision once Payload is actually stood up, not
decided speculatively here.

## Decision

Pending.

## Consequences

Pending: depends on the option chosen. Whatever is chosen determines
whether `MAPTILER_DATASET_ID`/`MAPTILER_KEY` remain long-term dependencies or
are retired once organizer POI editing has a first-party home, and sets the
pattern new organizer-facing features (safety alerts, food vendor edits)
follow going forward.
