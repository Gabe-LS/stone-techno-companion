# Architecture Decision Records

This directory records the decisions behind the platform migration described in
`docs/platform-blueprint.html` (build/buy verdict, reference architecture, migration
invariants, open decisions D1 through D6) and `docs/menu-sequencing-strategy.md`
(the menu component sequencing call). An ADR captures a decision, the options that
were weighed, and why the chosen option won, so a future contributor does not have
to reconstruct the reasoning from old chat logs or code archaeology.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-nextjs-frontend.md) | Next.js as the platform front-end, Python backbone kept | Accepted | 2026-07-13 |
| [0002](0002-menu-component-deferred.md) | Shared menu component deferred to Next.js | Accepted | 2026-07-13 |
| [0003](0003-event-ingestion-model.md) | Ingestion model for festival number two (D1) | Proposed | 2026-07-13 |
| [0004](0004-organizer-dashboard-owner.md) | Organizer dashboard owner (D2) | Proposed | 2026-07-13 |
| [0005](0005-ai-agent-model-and-cost.md) | AI agent model and cost ceiling (D3) | Proposed | 2026-07-13 |
| [0006](0006-postgres-cutover-order.md) | Postgres cutover order (D4) | Proposed | 2026-07-13 |
| [0007](0007-repo-and-hosting-shape.md) | Repo and hosting shape (D5) | Proposed | 2026-07-13 |
| [0008](0008-identity-broker.md) | Identity broker (D6) | Proposed | 2026-07-13 |

## Process

**When to write an ADR.** Write one whenever a decision is expensive to reverse:
it changes the stack, the data model, the deployment topology, the auth model, or
any of the migration invariants in blueprint section G (slot UUID permanence, the
single push subscription per origin, the single manifest/app identity, VAPID key
continuity, client-side E2EE key custody, provider-keyed ban/strike continuity).
Do not write an ADR for a reversible implementation detail (a CSS variable name,
an internal function signature): those belong in code comments or CLAUDE.md.

**Statuses.**
- **Proposed**: the options and tradeoffs are documented, the decision is not yet
  made, or is pending information named in the ADR. The "Decision" section reads
  "Pending".
- **Accepted**: the decision has been made and is currently in force.
- **Superseded**: the decision was later replaced. An accepted ADR is superseded
  by a new ADR, it is never edited in place after acceptance (edit only for typo
  fixes that change no meaning). The new ADR states which number it supersedes,
  and the old ADR gets a one-line note at the top pointing to its successor and
  its status changes to Superseded.

**An accepted ADR is immutable.** If circumstances change, write a new ADR with
the next number, reference the old one, and mark the old one Superseded. This
keeps the historical record honest: the ADR reflects what the team believed and
decided at the time it was written, not a retroactively edited version of events.

**Numbering.** Sequential, zero-padded to four digits, never reused, never
renumbered even if an ADR is superseded or abandoned.

## Template

Every ADR file uses this structure:

```markdown
# NNNN. Title

## Status

Proposed | Accepted | Superseded (by NNNN)

## Date

YYYY-MM-DD

## Context

What is the situation that forces this decision. What forces are in tension
(technical, legal, operational, cost). What in the existing system constrains
the options. Ground this in the actual codebase and the blueprint, not
hypotheticals.

## Options considered

Each realistic option, with what it would mean concretely for this codebase,
and its tradeoffs (including the ones that are inconvenient for the preferred
answer).

## Decision

The option chosen, and the reasoning for choosing it over the alternatives.
For a Proposed ADR, this section reads "Pending" and a "Leaning" subsection
gives a provisional recommendation with reasoning, so a reader knows where the
thinking currently stands without it being mistaken for a final decision.

## Consequences

What becomes easier, what becomes harder, what follow-on work or new ADRs this
decision creates, and what it forecloses.
```
