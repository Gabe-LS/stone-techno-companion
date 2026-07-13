# 0007. Repo and hosting shape (D5)

## Status

Accepted

## Date

2026-07-13 (proposed and accepted the same day, owner decision)

## Context

This is blueprint open decision D5 (section I), gating Phase 2. Today the
entire project is one repository: `pipeline/` (scrape/enrich/render),
`server/` (FastAPI companion API + chat), `tests/`, `monitoring/`, docs, all
committed together, deployed by a single `deploy.sh` to a single DigitalOcean
VPS running one `docker-compose` stack behind Caddy (auto-TLS). That
`deploy.sh` has real, hard-won operational discipline built into it: env-var
sync with backup, VAPID key-pair preflight, transactionally consistent
`VACUUM INTO` DB snapshots downloaded and `PRAGMA quick_check`-verified
before any change, `chat.db` seeding on first deploy, log archiving,
`docker compose up -d --build --force-recreate`, a post-deploy health check
that exits non-zero on failure, and backup pruning (5 on the VPS, 15
locally). This is the bar any replacement orchestration must meet, per
blueprint section F's own framing of the deploy/monitoring row.

The target architecture adds, per the reference architecture (section C) and
build/buy matrix (section B): a Next.js app (ADR 0001), and up to four
adopted OSS services each wanting their own datastore: pretix (Django,
ticketing), Medusa (Node/TS, merch), Payload (TS, CMS, pending ADR 0004),
Meilisearch (Rust, search): plus Postgres and Redis (ADR 0006 moves the
data there). The blueprint states plainly: "one VPS will not hold Next.js +
FastAPI + pretix + Medusa + Payload + Meilisearch + Postgres + Redis" and
that "the orchestration story and monthly budget must be decided before
Phase 2 begins" (section I). This is explicitly a *before Phase 2*
blocker, not a Phase 2 nice-to-have.

## Options considered

**A. Monorepo.** `apps/web` (Next.js), `services/companion` (the current
`server/`: FastAPI, chat, WebSocket), `services/data` (the current
`pipeline/`), `packages/` (shared types, e.g. API contracts consumed by both
the Next.js BFF and the Python services, shared config/lint rules). This
extends the project's current shape (already one repo containing pipeline,
server, and tests together) rather than breaking with it. Cross-cutting
changes (e.g. a schema field that both the companion API and the Next.js
front need to agree on) land in one PR, one CI run, one versioned commit,
with no cross-repo coordination for changes that touch more than one service.
The tradeoff: the adopted OSS projects (pretix, Medusa, Payload) are
upstream projects with their own release cadence and are integrated, not
forked (per section C: "integrated at the API/BFF layer, not merged into one
DB": the same logic argues against merging their *source* into this repo
either). So "monorepo" in practice means the team's own code
(`apps/web`, `services/companion`, `services/data`, `packages/`) lives
together, while pretix/Medusa/Payload/Meilisearch run as their own
container images, configured (env vars, plugins, themes) rather than
vendored wholesale: the monorepo's scope is integration glue and
deployment manifests for those services, not their codebases.

**B. Polyrepo.** Each piece (`apps/web`, `services/companion`,
`services/data`) is its own repository with its own CI/CD and versioning,
mirroring how pretix/Medusa/Payload themselves are separate upstream
projects: "matching the grain" of the buy decisions in section B more
literally. The cost: today's single pytest+Playwright suite (315 core tests
plus notification/E2EE/transport harnesses) spans pipeline and server code
that would need to either split across repos or gain cross-repo integration
testing; a change to the session/auth model that both the Next.js BFF and
FastAPI depend on becomes a coordinated multi-repo change with versioned
API contracts between them, which is real overhead for a small team and
does not match how tightly coupled `render.py`'s retirement (ADR 0001) is
to the companion API's routes.

**C. Hybrid: monorepo for team-owned code, each adopted OSS service run
as its own deployed container, configured not forked.** Functionally, this
is option A stated precisely: the "monorepo" question is really about
scope (does it include forks of the OSS projects, no) rather than a
distinct fourth option. Listed separately here because it is worth being
explicit that choosing "monorepo" does not mean vendoring pretix or Medusa's
source into this repository.

**Hosting.**

**a. Vertical scaling: a bigger single VPS (or a small number of VPS
instances), keep docker-compose, add per-service resource limits.**
Cheapest and smallest operational delta from today; directly extends
`deploy.sh`'s existing discipline rather than replacing it. Does not solve
orchestration concerns (rolling deploys, secrets management, per-service
health/observability) as service count grows past a handful, and each
`docker compose up --force-recreate` deploy has a larger blast radius as
more services share the same compose file and host.

**b. Multiple VPS instances split by service group** (e.g. web + companion
on one host, a commerce cluster (pretix + Medusa + Payload) on another,
data services on a third), each still docker-compose, manually load-balanced
via DNS/Caddy. A middle ground: more fault isolation than (a), but multiple
compose files, multiple deploy scripts (or a generalized version of
`deploy.sh`), and secrets/config duplicated or centralized across hosts,
more ops surface than (a) without yet adopting a real orchestrator.

**c. Managed orchestration** (DigitalOcean Kubernetes Service, or a PaaS
like Railway/Render/Fly.io). Solves scaling, secrets, and observability more
natively as service count grows, but is a larger operational shift away from
the current `deploy.sh`-centric discipline: the backup/VAPID-preflight/
seed/rollback logic that discipline encodes would all need to be
re-implemented against the new platform's primitives (or the new platform's
own equivalents adopted and trusted in their place), which is nontrivial
given how specific and hard-won some of those checks are (e.g. the
`VACUUM INTO` snapshot-then-`quick_check` sequence, the VAPID key-pair
consistency check).

## Leaning

Monorepo (option A), matching the working assumption already stated in the
roadmap: `apps/web`, `services/companion`, `services/data`, `packages/`. It
extends the project's current single-repo shape instead of breaking from it,
keeps cross-cutting changes (auth model, API contracts between the Next.js
BFF and FastAPI) atomic and reviewable in one PR, and scopes the monorepo
correctly to team-owned code: the adopted OSS services stay upstream
projects, integrated via configuration and API calls, not forked into the
repo.

For hosting, lean toward vertical scaling (option a) as the first step
rather than jumping straight to managed orchestration (option c): it
directly extends the operational discipline the team has already built and
proven in `deploy.sh` (snapshot verification, VAPID preflight, health
checks), matching the blueprint's own stated philosophy elsewhere of
deferring heavier infrastructure until scale actually demands it (the
Elixir on-sale service is explicitly "only if/when spikes demand it,"
section B). A monthly budget line for the added Postgres/Redis/service
footprint still needs to be set explicitly before Phase 2 begins, regardless
of which hosting option is chosen: this ADR does not set that number and it
should not be left implicit.

## Decision

Monorepo (option A, scoped per option C's clarification): `apps/web`,
`services/companion`, `services/data`, `packages/` live in this repository;
pretix, Medusa, Payload, and Meilisearch stay upstream projects run as their
own container images, integrated via configuration and API calls, never
vendored.

Hosting: vertical scaling (option a). One bigger VPS, docker-compose with
per-service resource limits, extending the existing `deploy.sh` discipline.
Budget line: approximately 50 EUR/month. Managed orchestration (option c)
is explicitly deferred until service count or load proves the single-host
model insufficient; revisit via a superseding ADR if commerce (Stage 5)
demands it.

## Consequences

- Stage 2 is unblocked: the monorepo restructure can begin.
- The current repo is restructured in place (history preserved), not split.
- `deploy.sh`'s backup/preflight/health-check discipline is generalized to
  the multi-service compose file rather than replaced (INV-16 in
  `docs/invariants.md` region: the snapshot-verify-then-change sequence
  must survive the restructure).
- The 50 EUR/month ceiling constrains Stage 5: pretix and Medusa must fit
  the single-host model or trigger the superseding-ADR conversation before
  any commerce integration starts.
- The single-VPS blast-radius tradeoff documented under option (a) is
  accepted knowingly: per-service resource limits and the health check are
  the mitigations.
