# Platform migration roadmap

Stone Techno Companion: single-event festival app to European festival platform.

## 1. Purpose and how to use this file

This is the single living "where are we" document for the platform migration. Every
working session, human or agent, starts here before touching code or opening any
other migration doc.

Division of labor across the migration docs:

- **This file (`docs/roadmap.md`):** current state, what stage we're in, what's next.
  Updated after every working session that moves the migration forward, whether that
  session shipped code, wrote a doc, or just recorded a decision.
- **`docs/platform-blueprint.html`:** the strategy: build/buy verdicts (section B),
  reference architecture (section C), the phased path (section E), the asset map
  (section F), the migration invariants (section G), and the open decisions D1-D6
  (section I). This is the *why* and the *what*. It does not change week to week.
- **`docs/adr/`:** one file per numbered architectural decision (0001-0008 as of this
  writing). An ADR is written when a decision in `docs/platform-blueprint.html` section I
  is actually made, not before. Until an ADR exists, the corresponding blueprint
  question is still open and the stage it blocks cannot start.
- **`docs/invariants.md`:** the never-break register: the hard-won constraints in
  blueprint section G (slot UUIDs, one push subscription per origin, one manifest,
  VAPID key continuity, client-side E2EE keys, provider-keyed ban continuity) plus
  anything new the post-event retrospective or later migration work surfaces. Every
  migration PR is checked against this file before merge (see Standing rules, section 5).
- **`docs/parity/{transport,pwa-shell,lineup,timetable}.md`:** one acceptance-criteria
  document per surface being ported to Next.js in Stage 3, written before that surface's
  port starts. A surface cuts over only when its parity doc is fully checked.

If you are starting a session and are not sure what to do next: read section 2 below,
find the stage marked "in progress," read that stage's section, and pick up the first
unchecked workstream item.

**Naming note.** This roadmap uses **Stage 1-5**, a finer-grained sequence agreed on top
of the blueprint's **Phase 0-3** language. Rough correspondence: Stage 1 continues and
closes out blueprint Phase 0; Stage 2 is groundwork the blueprint didn't break out
separately but that Phase 2 (the Next.js front) depends on; Stage 3 is blueprint Phase 2;
Stage 4 is blueprint Phase 1's content (multi-event data model, AI agent, new pillars),
deliberately resequenced to *after* Stage 3 so its UI has a Next.js home to be built in
(backend/data-model pieces may start earlier, in parallel with Stage 3, see Stage 4);
Stage 5 is blueprint Phase 3. Where wording differs, this file is authoritative for
sequencing and the blueprint is authoritative for strategy and rationale.

## 2. Status dashboard

| Stage | State | Current focus | Next actions |
|---|---|---|---|
| **1. Stabilize and document** | In progress (started 2026-07-13) | Documentation backbone (this file, plus `docs/adr/`, `docs/invariants.md`, `docs/parity/` stubs) being produced today; post-event retrospective and Bucket 1 menu fixes queued right behind it | Finish the documentation backbone; mine `monitor.sh` logs, push ack data (`chat_push_subscriptions`, `sent_notifications`), and moderation logs for real usage data; ship the four Bucket 1 menu fixes (see 3.1) |
| **2. Foundations** | Not started | None yet | Blocked on Stage 1 exit criteria; first concrete step is the ADR 0007 repo-shape decision |
| **3. The Next.js front** | Not started | None yet | Blocked on Stage 2 exit criteria |
| **4. Multi-event and moat features** | Not started | None yet | Backend/data-model work (ADR 0003, ADR 0004 decisions) may start once Stage 2 lands, in parallel with Stage 3; all UI work blocked until Stage 3 delivers a Next.js app to build it in |
| **5. Commerce** | Not started | None yet | Blocked on a second event having run on the platform, and on ADR 0008 |

## 3. Stage detail

### 3.1 Stage 1: Stabilize and document

**Goal.** Close out the current single-event codebase cleanly: learn from the event that
just ran, fix what's cheap and migration-proof, write down every decision and invariant
so Stage 2 onward doesn't have to rediscover them, and stop new feature growth in the
code that Stage 3 is going to delete.

**Workstreams**

- [ ] **Post-event retrospective.** Mine real usage data from the event that just concluded:
  - [ ] `monitor.sh` hourly logs (HTTP/TLS/latency probes, VPS internals, restarts, DB
    `quick_check` results) for anything that degraded during the live event
  - [ ] Push delivery data: `sent_notifications` (dedup table), push ack timeline
    (`POST /chat/api/push/ack` → `[PUSH-ACK]` log lines), `chat_push_subscriptions` /
    `push_subscriptions` for dead-subscription churn (410s) and the FCM-vs-Apple-vs-Mozilla
    split described in the push invariants in `CLAUDE.md`
  - [ ] Moderation logs (`logger.info` score dumps, FLAGGED lines) for false-positive rate,
    strike escalation frequency, and whether the word filter or the AI layers carried
    most of the load
  - [ ] Fold anything that qualifies as a hard constraint into `docs/invariants.md`;
    fold anything that changes a blueprint open decision (section I) into the relevant
    ADR once written
- [ ] **Bucket 1 menu fixes** (from `docs/menu-sequencing-strategy.md`, all migration-proof:
  none of this is wasted when Next.js's `<Nav>` replaces it in Stage 3):
  - [ ] Unified active-state convention + group labels + hamburger unread badge (today: three
    divergent active-state conventions across the app's hand-written menus)
  - [ ] Fix the 767px vs 768px breakpoint bug (chat's menu does not open at exactly 768px)
  - [ ] Give chat desktop a way back to Line-up / Timetable / Transport (the "zero nav" hole:
    chat currently has no desktop nav back to the rest of the app)
  - [ ] Chat hamburger `aria-label` + Escape-to-close (verified accessibility gaps)
  - [ ] Explicitly **not** doing: the `renderMenu()` / Web Components standardization
    (Bucket 2). Next.js is the committed near-term plan, so per the menu-sequencing
    decision record, that abstraction is built exactly once, in Stage 3, as the unified
    nav component (ADR 0002)
- [ ] **Documentation backbone**
  - [ ] `docs/roadmap.md` (this file)
  - [ ] `docs/adr/0001` through `0008` (see section 4 below for what each one decides)
  - [ ] `docs/invariants.md`: seed it from blueprint section G (slot UUIDs, one push
    subscription per origin, one manifest/app identity, VAPID continuity, client-side
    E2EE keys, provider-keyed ban/strike continuity) plus retrospective findings
  - [ ] `docs/parity/transport.md`, `docs/parity/pwa-shell.md`, `docs/parity/lineup.md`,
    `docs/parity/timetable.md`: acceptance criteria stubs for the four Stage 3 surfaces,
    in the same risk order they'll be ported, expanded just before each port starts
- [ ] **Feature freeze on `pipeline/scraper/render.py`.** No new features land in the legacy
  HTML/CSS/JS generator from this point on. Bug fixes and the Bucket 1 menu fixes above are
  allowed (they're migration-proof); anything net-new that only exists to make the
  current site nicer is out of scope until it can be built once, in Next.js, in Stage 3

**Exit criteria**

- All four Bucket 1 menu fixes shipped and verified in the running app
- `docs/adr/0001`-`0008`, `docs/invariants.md`, and the four `docs/parity/*.md` stubs exist
  and are linked from this file
- Retrospective findings are captured (in `docs/invariants.md` and/or the relevant ADR),
  not left in raw logs
- No commits touch `pipeline/scraper/render.py` except the Bucket 1 fixes and true bug fixes
- The full test suite is green: `python -m pytest tests/ -v` (315 tests: 241 chat + 20
  transport + moderation/db/ws/api/admin-roles suites), notification suite separately
  (`python tests/notif_e2e/run.py --all`, run outside the command sandbox)

**Gating tests.** `python -m pytest tests/ -v`; `python tests/notif_e2e/run.py --list`
then `--sw`/`--browser`/`--all` as applicable; no new gating beyond what already exists,
since Stage 1 doesn't touch the architecture.

**ADRs blocking this stage.** None. Stage 1 is documentation and cheap fixes on the
existing system, it doesn't require an architectural decision to proceed.

---

### 3.2 Stage 2: Foundations

**Goal.** Build the scaffolding Stage 3 needs before a single line of Next.js UI is
written: a repo shape that can hold a Python data pipeline, a Python API, and a Next.js
app side by side; contracts between them that are typed and versionable instead of an
HTML blob; and a dev/CI setup that proves the whole stack still works together on every
change.

**Workstreams**

- [ ] **Monorepo restructure** (resolves ADR 0007)
  - [ ] `apps/web`: the Next.js app (empty scaffold at this stage; Stage 3 fills it in)
  - [ ] `services/companion`: today's `server/` (FastAPI: favorites, schedule sync,
    push scheduler, ICS export, transport proxy, DOP tile proxy, chat)
  - [ ] `services/data`: today's `pipeline/` (scrape → enrich → normalize → AVIF, writes
    `lineup.db`)
  - [ ] `packages/`: shared design tokens (ported from `server/static/shared.css`) and
    shared TypeScript types generated from the new OpenAPI contracts below
- [ ] **Formalize API contracts, contract-first**
  - [ ] OpenAPI spec for the existing companion API (`services/companion`): the surface
    Next.js will call for favorites, schedule, push, ICS, transport, chat
  - [ ] A **new JSON read API over lineup data**: today the pipeline's only output is
    `lineup.html` (a rendered page) plus `bios.json`/`timetable.json` as side files; Next.js
    needs a real JSON API over artists/schedule/stages/events, not an HTML page to scrape
    or a side-file convention to keep growing
  - [ ] Generate/pin shared types from both specs into `packages/`
- [ ] **Dev infrastructure**
  - [ ] Single `docker compose` bringing up the full stack (companion API, data service,
    Next.js dev server, Postgres/Redis if pulled forward from Stage 4) for local dev and CI
  - [ ] CI runs the existing 315 pytest tests plus the Playwright harnesses
    (`tests/notif_e2e`, `tests/e2ee_browser_check.py`, `tests/verify_push_both.py`,
    `tests/notif_badge_browser_check.py`, transport `*_check.py` scripts)
  - [ ] Stand up the new e2e layer for the Next.js front (empty/smoke-test only at this
    stage; it grows with each ported surface in Stage 3)

**Exit criteria**

- `apps/web`, `services/companion`, `services/data`, `packages/` exist with the current
  code moved in (not rewritten) and everything still runs and deploys
- OpenAPI specs exist for the companion API and the new lineup JSON read API; at least one
  real endpoint is being served from the new JSON API (proves the contract isn't paper)
- `docker compose up` brings up the full stack locally in one command
- CI is green on every PR: the 315 core tests, the Playwright harnesses, and a smoke-level
  Next.js e2e run

**Gating tests.** The full existing suite (unchanged pass/fail contract: a repo move
must not regress anything), plus a new CI job asserting the compose stack boots and the
JSON read API returns data matching the pipeline's SQLite source of truth.

**ADRs blocking this stage.** ADR 0007 (monorepo restructure / repo and hosting shape,
resolves blueprint open decision D5) must be decided before the directory move starts;
it also has to answer where things get hosted once the repo no longer fits "one VPS, one
compose file" (see blueprint section F, "Deploy + monitoring" row).

---

### 3.3 Stage 3: The Next.js front, ported in risk order

**Goal.** Build the platform's actual front end, cutting each surface over from
`pipeline/scraper/render.py`'s generated HTML to Next.js one at a time, cheapest and
lowest-risk first, hardest last, and prove the push notification invariants work on the
new front early, not as a surprise at the end.

**Workstreams**

- [ ] **Scaffold + design tokens + nav.** Port design tokens from `server/static/shared.css`
  (color variables, spacing scale, radius scale, shadow scale, font scale) into
  `packages/`. Build the unified nav component **once**, here, per ADR 0002. This is
  where the Bucket 2 work deferred in Stage 1 finally happens, as a native Next.js
  component instead of hand-rolled Web Components
- [ ] **Port transport first** (`docs/parity/transport.md`): standalone SPA today
  (`server/static/pages/transport.html`), clean existing API (`/api/transport/*`),
  lowest risk of the four surfaces. No shared state with lineup/chat to get wrong
- [ ] **Then the PWA shell** (`docs/parity/pwa-shell.md`): same-origin serving, root-scope
  `sw.js`, single manifest, `start_url: "/"`. This is where the push invariants in blueprint
  section G get proven on the new front: one push subscription per origin, VAPID
  continuity, no unsubscribe-then-resubscribe. Do this **early**, with
  `tests/notif_e2e` and `tests/verify_push_both.py` run against it, not deferred to the end
  where a failure blocks everything already built
- [ ] **Then lineup** (`docs/parity/lineup.md`): the ~650 KB generated page, covering
  artist list, bio modal (lazy `bios.json` fetch), hearts/favorites, schedule sync,
  floor colors, push-to-schedule
- [ ] **Then timetable** (`docs/parity/timetable.md`): hardest surface, ported last on
  purpose. The CSS-grid/HTML-table dual layout, the scroll-driven title compaction
  (`animation-timeline: scroll()`), per-view scroll-position restore, and the mobile
  no-document-scroll model (`.view-timetable body { overflow: hidden }` with a single
  `.tt-v-scroll` scroller) are all genuinely hard to reproduce faithfully
- [ ] **Retire `render.py` HTML generation** once all four surfaces have cut over. The
  pipeline itself does not go away; it keeps running as the data producer behind the
  new JSON read API from Stage 2; only the HTML-string-concatenation generation code is
  deleted
- [ ] **Chat stays exactly as-is behind the front.** `chat.html` and the chat WS/REST
  stack are not touched in this stage. Porting `chat.html` into the shared component
  system is optional and, per blueprint section F, comes last if ever

**Exit criteria (per surface, before that surface cuts over)**

- Its `docs/parity/*.md` doc is fully checked (every documented behavior of the
  `render.py`-generated version is reproduced)
- `tests/notif_e2e` (all scenarios) and `tests/verify_push_both.py` pass against the new
  front in a Chromium-family browser (this gate applies to every surface once the PWA
  shell is live, since all four share one origin and one subscription)
- The surface is served at feature parity on the same origin as everything else

**Exit criteria (whole stage)**

- Every content page (transport, PWA shell behaviors, lineup, timetable) is served by
  Next.js at parity
- `pipeline/scraper/render.py`'s HTML generation is deleted
- The push harnesses pass against the fully cut-over front

**Gating tests.** Per surface: that surface's `docs/parity/*.md` checklist, plus
`tests/notif_e2e/run.py --all` and `python tests/verify_push_both.py` from the PWA
shell surface onward. Whole-stage: the full 315-test suite still green against
`services/companion` (untouched by the front swap) and the new Next.js e2e layer green
across all four surfaces.

**ADRs blocking this stage.** ADR 0002 (unified nav component) must be decided before
the scaffold workstream starts.

---

### 3.4 Stage 4: Multi-event and moat features

**Goal.** Turn the single-event companion into the actual moat described in blueprint
section A: multi-event data, the AI support agent, and the new differentiator pillars
(safety, food and drink, ride sharing): the things no competitor (Woov included) offers.

**Important sequencing rule:** backend and data-model pieces of this stage may start in
parallel with Stage 3 once its ADRs are resolved, since they don't depend on Next.js
existing. **All new UI for this stage is built only in Next.js.** Nothing here gets a
new hand-written page in `render.py` or `chat.html`, even if the backend is ready early.

**Workstreams**

- [ ] **Multi-event data model**: event picker; festival/route/artist data becomes
  config-driven rows instead of files (transport itineraries move off hand-edited
  `server/static/timetable-transport.json` and CLI flags like `--event-id`/`--event-name`
  into the data model). Needs ADR 0003 (ingestion model for event #2) resolved first:
  hand-written scraper module vs. organizer self-serve vs. partnership feeds changes the
  data model's shape
- [ ] **Postgres cutover** (ADR 0006): `lineup.db` first (regenerable, lowest risk, no
  live user data), then `hearts.db`/`chat.db` (live user data, must preserve every
  invariant in blueprint section G / `docs/invariants.md`: slot UUIDs, push subscription
  rows, provider-keyed bans and E2EE device keys carried over byte-identically)
- [ ] **Meilisearch**: typo-tolerant search over artists / line-ups / POIs
- [ ] **AI support agent** (ADR 0005): bot user inside chat, dedicated support room,
  tool-calling over the structured DB (schedule, artists, transport, POIs, FAQs); answers
  grounded in real data, not hallucination; must define how its answers interact with the
  moderation pipeline (its posts land in a moderated room)
- [ ] **Complete the deferred meetup system gaps** (carried over from the current
  companion, listed as deferred in `CLAUDE.md`): push wiring (`room_memberships` branch
  for meetup rooms so meetup messages generate push), attendee-list UI, pre-meetup
  reminders
- [ ] **New pillars, in value order** (per blueprint section B, all "Build" verdicts):
  1. Safety: emergency alerts, first aid/awareness/safe-space/water POIs, one-tap contact
  2. Food and drink: vendor directory, menus/prices, dietary filters, map locations
  3. Ride sharing: offer/request rides, route matching, chat-based coordination

**Exit criteria**

- Adding a third event needs only a scraper module (or its ADR-0003 successor) plus
  config, no code changes elsewhere
- The AI agent answers schedule, transport, and POI questions from live data in a
  moderated room without bypassing moderation
- `lineup.db` and then `hearts.db`/`chat.db` are running on Postgres with every section-G
  invariant verified intact (slot UUIDs unchanged, no push subscription orphaned, no ban
  silently expired)
- Safety, food-and-drink, and ride-sharing pillars are live, each as Next.js UI over a
  real data model (no `render.py`/`chat.html` additions)

**Gating tests.** Full existing suite plus new coverage per pillar as it's built; the
Postgres cutover specifically needs a before/after data-integrity diff on slot UUIDs,
push subscriptions, and ban/strike rows (this is the concrete check behind the
"section G invariants" gate; see `docs/invariants.md` once it exists for the literal
assertions).

**ADRs blocking this stage.** ADR 0003 (ingestion model) and ADR 0004 (organizer
dashboard owner: the safety/food pillars assume organizers manage POI/menu data via a
dashboard that doesn't exist yet; ADR 0004 picks its home per blueprint decision D2)
block the multi-event data model workstream. ADR 0005 blocks the AI agent. ADR 0006
blocks the Postgres cutover.

---

### 3.5 Stage 5: Commerce

**Goal.** Only after a second event has actually run on the platform (proving the
multi-event model, not just designing it): add ticketing and merch behind one identity,
turning the app into a commercial platform.

**Workstreams**

- [ ] **Identity broker** (ADR 0008): companion auth promoted to an OIDC identity
  provider, or an external IdP (Keycloak/Authentik/Ory) fronting everything. Must satisfy
  every continuity invariant in blueprint section G: `provider_id` continuity, session
  handling, E2EE device key registration
- [ ] **pretix**: ticketing (Django, festival-native, offline check-in), adopted not
  built, integrated at the BFF behind the identity broker
- [ ] **Medusa**: merch (Node/TS, headless commerce), storefront themed rather than
  commerce logic written from scratch
- [ ] **Payload**: CMS for editorial/event content (TS/Next-native), free MIT core, note
  the paid Enterprise plugin tier exists on top of it

**Exit criteria**

- A real ticket and a real merch order complete end to end through the BFF with one login
- Identity continuity holds: a user who existed pre-commerce keeps their bans/strikes/
  E2EE keys/sessions across the identity broker cutover

**Gating tests.** End-to-end purchase flow (ticket + merch) through the BFF with one
session; identity continuity diff against `docs/invariants.md`'s provider-keyed
ban/strike/device-key rules.

**ADRs blocking this stage.** ADR 0008 (identity broker) must be resolved before pretix
or Medusa integration starts; both need to sit behind whatever identity layer that ADR
picks.

## 4. Decision dependency map

Each ADR lives at `docs/adr/000N-*.md`. An ADR is "resolved" when that file exists with a
decision recorded, not when the question is merely raised in the blueprint. Numbering and
titles below follow the open decisions in blueprint section I (D1-D6) plus the two
decisions the phased path (section E) calls out directly.

| ADR | Decides | Blueprint ref | Blocks |
|---|---|---|---|
| 0001 | Reference architecture and build/buy verdict (Next.js front, Python backbone, adopt pretix/Medusa/Payload/Meilisearch) | Sections B-D | Foundational: already in force, everything downstream assumes it |
| 0002 | Unified nav component: build once, in Next.js, per the menu-sequencing decision record | Section E, Phase 2 | Stage 3 (scaffold + nav workstream) |
| 0003 | Ingestion model for festival number two: hand-written scraper module vs. organizer self-serve vs. partnership feeds | D1 (Phase 1) | Stage 4 (multi-event data model, transport itineraries as data rows) |
| 0004 | Organizer dashboard owner: Payload admin, pretix organizer area, or a custom surface; also decides where POIs live long-term | D2 (Phase 1) | Stage 4 (safety and food-and-drink pillars, which assume organizer-maintained data) |
| 0005 | AI agent model choice, per-event cost ceiling, rate limits, and its interaction with the moderation pipeline | D3 (Phase 1) | Stage 4 (AI support agent workstream) |
| 0006 | Postgres cutover order and dual-write/cutover mechanics: `lineup.db` first, then `hearts.db`/`chat.db` | D4 (Phase 2) | Stage 4 (Postgres cutover workstream) |
| 0007 | Monorepo vs polyrepo shape, and hosting/orchestration once one VPS no longer fits the service count | D5 (Phase 2) | Stage 2 (foundations: the repo restructure literally can't start without this) |
| 0008 | Identity broker: companion auth as its own OIDC IdP vs. an external IdP (Keycloak/Authentik/Ory) | D6 (Phase 3) | Stage 5 (commerce: pretix/Medusa integration sits behind whatever this picks) |

## 5. Standing rules

These apply for the whole migration, regardless of which stage is active.

- **Live-event override.** If a festival is imminent (the transport config in
  `server/static/timetable-transport.json` is dated around the current window), that
  overrides every other rule in this file: freeze architecture work entirely, ship at
  most the safest cosmetic fixes, and resume normal stage work only after the event.
  This is the same rule recorded in `docs/menu-sequencing-strategy.md` and it applies to
  the whole migration, not just menu work.
- **Feature freeze on `pipeline/scraper/render.py`.** In force from Stage 1 onward until
  Stage 3 deletes it. Bug fixes are allowed; new features are not. If a change would only
  make the current hand-generated HTML nicer without being a bug fix or one of the four
  Bucket 1 menu fixes, it waits for Next.js.
- **All new UI is built in Next.js only, starting the moment `apps/web` exists.** This
  applies even to Stage 4 features whose backend lands before Stage 3 finishes porting
  the last surface; the backend can be ready early, the UI still waits.
- **Every migration PR is checked against `docs/invariants.md`.** Before merging any PR
  that touches data storage, push, auth, or E2EE, confirm it does not violate one of the
  registered invariants (slot UUID stability, one push subscription per origin, one
  manifest, VAPID key continuity, client-side E2EE key handling, provider-keyed ban/strike
  continuity). If a PR needs to cross an invariant deliberately (e.g. the Postgres
  cutover touching push subscription storage), the invariant's entry says how to do it
  safely; that's the point of writing it down once instead of re-learning it.
- **Update this file after every working session that moves the migration forward.**
  At minimum: tick off finished checklist items, update section 2's state/focus/next-actions
  for whichever stage changed, and add a line if a new ADR got resolved or a new invariant
  got discovered.
