# 0006. Postgres cutover order (D4)

## Status

Proposed

## Date

2026-07-13

## Context

This is blueprint open decision D4 (section I), gating Phase 2. Today there
are three SQLite databases: `pipeline/lineup.db` (scraped/enriched festival
data, WAL mode, FK enforcement, entirely regenerable from the live event site
plus `overrides.toml`), `server/data/hearts.db` (favorites, sessions, push
subscriptions, schedule picks), and `server/data/chat.db` (users, sessions,
rooms, messages, meetups, reactions, blocks, reports, strikes, E2EE device
keys). The blueprint is explicit that this move is not about load: "SQLite
has served flawlessly at current scale; the move is driven by multi-service
topology, not by load" (section I). The reason is architectural: each
adopted OSS service (pretix, Medusa, Payload) wants its own datastore, and
"Postgres + Redis is the near-universal pairing across pretix, Saleor,
Medusa and Discourse" (section C), so the migration is about giving each
service the datastore its own ecosystem expects, and enabling proper
multi-service operation (concurrent access, connection pooling, replication)
that a file-based SQLite database was never designed for at that topology.

Section G's migration invariants apply directly and specifically constrain
`hearts.db` and `chat.db`:

- **Slot UUIDs are permanent.** `slot_uuid()` in `timetable_json.py` is a
  deterministic, collision-aware function of schedule data, not an
  autoincrement key: it is the identity of a set for saved schedules, push
  dedup, and ICS export. Migrating its *storage* from SQLite to Postgres
  does not by itself change the id values, provided the migration copies
  rows rather than regenerating ids; the actual risk is a schema change
  made *during* the same migration (e.g. re-deriving ids instead of copying
  them) breaking this invariant as a side effect.
- **Push subscription endpoints, provider-keyed bans, and E2EE device keys
  must carry over byte-identically.** These live in `hearts.db`
  (`push_subscriptions`) and `chat.db` (`chat_push_subscriptions`, `bans`,
  `e2ee_device_keys`). Endpoint URLs, provider_id strings, and JWK public
  keys are opaque values compared for exact equality elsewhere in the
  system (a push send targets the stored endpoint exactly; a ban check
  matches `provider_id` exactly); any re-encoding, whitespace change, or
  type coercion (e.g. TEXT to a different column type) during migration
  risks silently breaking matches that used to work.
- **Deliberate FK-lessness must be preserved where it matters.**
  `bans`/`reports`/`strikes` are documented as FK-less specifically so a
  user's ban/strike/report history survives account deletion. A Postgres
  schema that "improves" this by adding a foreign key with `ON DELETE
  CASCADE` would silently erase exactly the history the current design
  protects.

## Options considered

**A. `lineup.db` first, then `hearts.db`, then `chat.db` (the order the
blueprint itself suggests, section I).** `lineup.db` is entirely regenerable
from the pipeline (scrape + enrich + `overrides.toml`), so a failed or
imperfect migration has a trivial recovery path: rebuild from source and
re-run. It also carries the fewest section G invariants: its main
constraint is `slot_uuid()` determinism, which is an algorithmic property
independent of storage engine, not a byte-identity requirement on live user
data. `hearts.db` is next: smaller schema (sessions, favorites, push
subscriptions, schedule picks) than `chat.db`, and its push-subscription
byte-identity requirement is well understood and already has a working
precedent (`deploy.sh`'s `VAPID preflight` and snapshot-verify discipline).
`chat.db` is last: largest schema, the most section G invariants stacked
together (E2EE keys, provider-keyed bans, per-device sessions, live
WebSocket state), and the highest blast radius if something goes wrong
mid-migration (an in-progress festival's live chat).

**B. `hearts.db`/`chat.db` first, `lineup.db` last.** Rejected as the
starting point: it front-loads the migration's highest-risk, least-
recoverable data (live user identity, bans, E2EE keys) before the team has
practiced the mechanics on lower-stakes data, and gains nothing: `lineup.db`
being regenerable means there is no benefit to migrating it later, only to
migrating it first as a rehearsal.

**C. All three simultaneously ("big bang" cutover).** Rejected: combines the
blast radius of all three databases into one deploy window with no
incremental verification checkpoint between them, and offers no advantage
over a sequential order other than a shorter total calendar time: the
existing deploy.sh discipline (snapshot backup, `PRAGMA quick_check`
verification, abort-before-any-change on corruption) explicitly favors
verified, staged changes over single large ones, and this migration should
follow that same philosophy.

**D. Dual-write / shadow period per database.** Write to both SQLite and
Postgres during a transition window per database, verify parity (row counts,
spot-checked byte-identical fields for the section G invariants), then cut
reads over and retire the SQLite copy. This is orthogonal to the ordering
question (A vs B vs C): it is a *mechanism* that can be applied to whichever
database is being migrated at the time, and is the recommended mechanism for
`hearts.db` and `chat.db` specifically (their live-data risk justifies the
extra operational overhead of running both stores in parallel briefly);
`lineup.db`, being regenerable, likely does not need it and can use a
simpler snapshot-migrate-verify-cutover pattern instead.

## Leaning

Option A (`lineup.db`, then `hearts.db`, then `chat.db`), using option D's
dual-write/shadow-verify mechanism for `hearts.db` and `chat.db` but a
simpler regenerate-and-diff approach for `lineup.db`. This matches both the
blueprint's own suggested order and the general principle of migrating the
least risky, most recoverable data first to build confidence and tooling
before touching the two databases where section G's invariants (push
endpoint byte-identity, provider-keyed ban survival, E2EE key custody) can
break in ways that are not automatically recoverable. Before either
`hearts.db` or `chat.db` moves, each section G invariant relevant to that
database should have an explicit, scripted verification step (not manual
inspection) run against the migrated copy before any read traffic is cut
over to it.

## Decision

Pending.

## Consequences

Pending: depends on the option chosen. Whichever order and mechanism is
chosen, the section G invariants must be written as automated checks (ideally
extending the existing `tests/verify_push_both.py` and `tests/e2ee_browser_check.py`
harnesses to run against the Postgres-backed store) before any cutover is
considered complete, not verified ad hoc during the migration itself.
