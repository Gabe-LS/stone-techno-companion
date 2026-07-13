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
| **1. Stabilize and document** | COMPLETE (2026-07-13) | All exit criteria met: backbone shipped, Bucket 1 resolved, retrospective done (bcecd28), core suite 261 green, notif harness restored to 21/21 (it had been silently broken since 2026-07-07 by a dangling symlink, then stale vs the 8be87cf token-hashing commit; both fixed: dff54a1, ae8b445) | Human follow-up: one pending moderation report at /chat/admin |
| **2. Foundations** | In progress (2026-07-13) | Monorepo restructure MERGED AND LIVE: stage2-monorepo merged to main (2279070), VPS cutover done 2026-07-13 evening (data copied not moved, deploy green, monitor 24/24 OK). Cutover notes: old container had to be removed manually (compose project rename), untracked static content files (index.html, photos, thumbs, bios/timetable.json) needed a manual copy alongside data/. Old server/ paths kept on the VPS as fallback, clean up after ~a week healthy | Next: lineup JSON read API implementation, CI, dev compose stack. Rollback tag: pre-monorepo |
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

- [x] **Post-event retrospective.** Done 2026-07-13, findings in `docs/retrospective-2026-07.md`
  (commit bcecd28): zero restarts, clean DBs, 48 of 51 error lines from one WS disconnect-race
  noise bug (fix queued), push healthy, moderation cost grounding fed to ADR 0005, INV-3
  field-data caveat added to `docs/invariants.md`. QNAP monitor log unreachable from dev,
  VPS-side evidence substituted. One moderation report left pending for human review.
  - [x] `monitor.sh` hourly logs (HTTP/TLS/latency probes, VPS internals, restarts, DB
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
- [x] **Bucket 1 menu fixes** (from `docs/menu-sequencing-strategy.md`), resolved 2026-07-13:
  - [x] Fixed the 767px vs 768px breakpoint bug (chat's menu did not open at exactly 768px;
    all chat.html breakpoints aligned to the shared convention, commit 4269c51)
  - [x] Chat hamburger `aria-label` + Escape-to-close with focus return (same commit)
  - [x] Unified active-state convention + group labels + hamburger unread badge: DECIDED
    (owner, 2026-07-13) to defer all three to the Next.js nav component in Stage 3.
    Design inputs recorded there. Do not re-raise.
  - [x] Chat desktop nav back to Line-up / Timetable / Transport: DECIDED (owner,
    2026-07-13) to leave the gap until the Next.js front ships. The signed-in desktop
    calendar icon stays display:none. Do not ship a stopgap.
  - [x] Explicitly **not** doing: the `renderMenu()` / Web Components standardization
    (Bucket 2). Next.js is the committed near-term plan, so per the menu-sequencing
    decision record, that abstraction is built exactly once, in Stage 3, as the unified
    nav component (ADR 0002)
- [x] **Documentation backbone** (shipped 2026-07-13, commits 1558268 and 039f813)
  - [x] `docs/roadmap.md` (this file)
  - [x] `docs/adr/0001` through `0008` (see section 4 below for what each one decides)
  - [x] `docs/invariants.md`: 17 invariants (INV-1 through INV-17) from blueprint section G
    plus the hard-won CLAUDE.md knowledge; retrospective findings still to be folded in
  - [x] `docs/parity/transport.md`, `docs/parity/pwa-shell.md`, `docs/parity/lineup.md`,
    `docs/parity/timetable.md`: full acceptance checklists (507 items), not stubs; they
    were extracted from the actual code and already corrected drift in CLAUDE.md
    (transport polling/cache numbers, timetable "Also" cross-references, the undocumented
    read-only shared-picks mode) and fixed a broken Playwright check (commit cd345e3)
- [ ] **Feature freeze on `services/data/scraper/render.py`.** No new features land in the legacy
  HTML/CSS/JS generator from this point on. Bug fixes and the Bucket 1 menu fixes above are
  allowed (they're migration-proof); anything net-new that only exists to make the
  current site nicer is out of scope until it can be built once, in Next.js, in Stage 3

**Exit criteria**

- Bucket 1 resolved: the two shippable fixes (768px breakpoint, hamburger a11y) shipped and
  verified; the design-gated items explicitly deferred to the Stage 3 nav component by
  owner decision (2026-07-13), recorded in 3.1 and in the Stage 3 nav workstream
- `docs/adr/0001`-`0008`, `docs/invariants.md`, and the four `docs/parity/*.md` stubs exist
  and are linked from this file
- Retrospective findings are captured (in `docs/invariants.md` and/or the relevant ADR),
  not left in raw logs
- No commits touch `services/data/scraper/render.py` except the Bucket 1 fixes and true bug fixes
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

- [ ] **Monorepo restructure** (ADR 0007: Accepted 2026-07-13, monorepo + single bigger VPS at ~50 EUR/mo)
  - [ ] `apps/web`: the Next.js app (empty scaffold at this stage; Stage 3 fills it in)
  - [ ] `services/companion`: today's `server/` (FastAPI: favorites, schedule sync,
    push scheduler, ICS export, transport proxy, DOP tile proxy, chat)
  - [ ] `services/data`: today's `pipeline/` (scrape → enrich → normalize → AVIF, writes
    `lineup.db`)
  - [ ] `packages/`: shared design tokens (ported from `services/companion/static/shared.css`) and
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
`services/data/scraper/render.py`'s generated HTML to Next.js one at a time, cheapest and
lowest-risk first, hardest last, and prove the push notification invariants work on the
new front early, not as a surprise at the end.

**Workstreams**

- [ ] **Scaffold + design tokens + nav.** Port design tokens from `services/companion/static/shared.css`
  (color variables, spacing scale, radius scale, shadow scale, font scale) into
  `packages/`. Build the unified nav component **once**, here, per ADR 0002. This is
  where the Bucket 2 work deferred in Stage 1 finally happens, as a native Next.js
  component instead of hand-rolled Web Components. Design inputs deferred here by
  owner decision on 2026-07-13: (a) one unified active-state convention (today three
  coexist: chat rows use a background tint, chat tabs a bottom border, lineup tabs an
  inverted background); (b) hamburger unread indicator upgraded from the boolean dot
  to a numbered badge if space allows; (c) signed-in desktop chat gets real nav back
  to Line-up / Timetable / Transport (the known "zero nav" gap, deliberately left open
  until this component ships); (d) grouped/labeled menu sections where lists are long
- [ ] **Port transport first** (`docs/parity/transport.md`): standalone SPA today
  (`services/companion/static/pages/transport.html`), clean existing API (`/api/transport/*`),
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
- `services/data/scraper/render.py`'s HTML generation is deleted
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
  `services/companion/static/timetable-transport.json` and CLI flags like `--event-id`/`--event-name`
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
| 0007 | DECIDED: monorepo (apps/, services/, packages/), vertical scaling on one bigger VPS, ~50 EUR/mo budget | D5 (Phase 2) | Stage 2 unblocked (Accepted 2026-07-13) |
| 0008 | Identity broker: companion auth as its own OIDC IdP vs. an external IdP (Keycloak/Authentik/Ory) | D6 (Phase 3) | Stage 5 (commerce: pretix/Medusa integration sits behind whatever this picks) |

## 5. Standing rules

These apply for the whole migration, regardless of which stage is active.

- **Live-event override.** If a festival is imminent (the transport config in
  `services/companion/static/timetable-transport.json` is dated around the current window), that
  overrides every other rule in this file: freeze architecture work entirely, ship at
  most the safest cosmetic fixes, and resume normal stage work only after the event.
  This is the same rule recorded in `docs/menu-sequencing-strategy.md` and it applies to
  the whole migration, not just menu work.
- **Feature freeze on `services/data/scraper/render.py`.** In force from Stage 1 onward until
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
