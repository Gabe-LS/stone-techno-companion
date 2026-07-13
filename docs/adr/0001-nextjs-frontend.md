# 0001. Next.js as the platform front-end, Python backbone kept

## Status

Accepted

## Date

2026-07-13

## Context

The project is moving from a single-event companion app to a multi-pillar
festival platform: content, community, ticketing, merch, and a database of
festivals, transport, and line-ups across Europe (blueprint section A/B). Today
the entire front-end is Python: `pipeline/scraper/render.py` generates every
line of the lineup/timetable page (HTML/CSS/JS, ~650 KB) as string
concatenation, and `server/chat/chat.html` is a single hand-written HTML file
with inline CSS/JS. Neither is a component system; both are one file each,
which is workable for one event and one team member but does not scale to a
platform with a CMS, a storefront, a ticketing UI, and a search experience all
needing to share layout, navigation, and design tokens.

The blueprint's stack verdict (section D) is built from a market read, not a
preference: React/Next.js recurs across every verifiable content-commerce-
community platform researched, and every adoptable storefront in the build/buy
matrix (Medusa, Saleor) ships Next.js starters, so a Next.js front integrates
with the "Adopt" pillars (pretix, Medusa, Payload, Meilisearch, per section B)
with the least friction. Python, meanwhile, is where the actual moat lives:
the scrape → enrich → normalize → AVIF pipeline (`pipeline/`), the realtime
transit proxies (`server/static/pages/transport.html` + `/api/transport/*`),
and the bespoke E2EE chat + moderation system (`server/chat_*.py`,
`server/chat/chat.html`) are all Python, all hard to copy, and none of them
benefit from a rewrite in Node: a rewrite would spend effort re-deriving
correctness the current code already has (see the push notification "hard-won
invariants" and moderation pipeline in CLAUDE.md) for zero product gain.

## Options considered

**A. Next.js front-end, Python backbone kept (chosen).** Next.js (SSR/SSG)
becomes the presentation and BFF layer; it calls the existing FastAPI
companion API and chat WebSocket, and later the adopted services (pretix,
Medusa, Payload, Meilisearch), all of which are Python/Node OSS with their own
datastores integrated at the API layer (blueprint section C). `render.py`'s
pages are re-implemented in Next.js at feature parity, then `render.py` is
retired (Phase 2 in the blueprint). Chat's frontend (`chat.html`) is kept
behind Next.js unchanged; porting it into the shared component system is
explicitly optional and last, if ever (blueprint section F).

**B. Rewrite the backbone in Node/TypeScript to unify the stack.** One
language everywhere. Rejected: it would require re-implementing the pipeline
(Playwright scraping, pyvips/AVIF encoding tuned against ssimulacra2, the
`overrides.toml` patch model), the WebSocket chat server (rooms, presence,
reactions, E2EE envelope handling, three-layer moderation calling OpenAI via
raw httpx), and the push notification code with its many hard-won,
non-obvious invariants (VAPID claims-dict isolation, iOS notification tag
uniqueness, shared single-subscription-per-origin). None of that logic is
UI-shaped; rewriting it in Node buys stack uniformity at the cost of
re-earning correctness that already exists, with no user-facing benefit.

**C. Stay with server-rendered Python (Jinja2/Django templates) instead of a
JS framework.** Keeps everything in one language and is a smaller step from
today's `render.py`. Rejected: it does not solve the actual problem, which is
a shared component/design system across many surfaces built by different
adopted OSS projects (Payload and Medusa are TypeScript/Next-native; a
Python-templated front would need custom integration work for every adopted
piece instead of using their existing Next.js starters), and it does not
match how the rest of the researched market builds this class of product
(blueprint section D).

**D. SvelteKit or another modern framework instead of Next.js.** Considered
and rejected in the blueprint's stack verdict: Next.js "beats SvelteKit at
platform scale on ecosystem + integration fit" because the adoptable
storefronts ship Next.js starters specifically, not generic framework
starters: picking a different framework means writing the Medusa/Payload
integration glue from scratch instead of adapting an existing starter.

## Decision

Next.js is the platform front-end. It consumes the existing Python services
(pipeline data, FastAPI companion API, chat) rather than replacing them, and
later the adopted OSS services, all integrated at the API/BFF layer per
service, each keeping its own datastore (blueprint section C). The Python
backbone is not rewritten in Node.

## Consequences

All new UI work happens in Next.js only: no new pages or components are
added to `render.py` or as further one-off HTML files going forward.
`render.py` is frozen: it keeps running and generating the current lineup/
timetable pages until the Next.js re-implementation reaches feature parity
(the parity checklist is the timetable/scroll/modal behaviors `render.py`
currently encodes, per blueprint section F), at which point it is deleted
(Phase 2, "Done when" criteria in blueprint section E). Chat's frontend
(`chat.html`) keeps running as-is behind the new front; porting it is
optional future work, not required for Phase 2 to complete.

This creates a temporary two-stack period (Next.js front + Python
render.py-generated pages coexisting until parity) that must be actively
retired, not left indefinite, or the project ends up maintaining both
permanently. It also means every migration invariant in blueprint section G
(slot UUID permanence, one push subscription per origin, one manifest/app
identity, VAPID key continuity, client-side E2EE key custody) must be
verified to still hold once Next.js serves the pages that currently carry
those behaviors: `tests/notif_e2e` and `tests/verify_push_both` are named in
the blueprint as the gate for that cutover and must pass against the new
front before `render.py` is deleted. This decision does not by itself resolve
repo/hosting shape (ADR 0007) or identity brokering (ADR 0008); those are
separate open decisions the blueprint tracks as D5 and D6.
