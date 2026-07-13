# 0005. AI agent model and cost ceiling (D3)

## Status

Proposed

## Date

2026-07-13

## Context

This is blueprint open decision D3 (section I), gating Phase 1. The blueprint
names the AI support agent as one of three genuine differentiators (section
A's wedge, alongside realtime transit and scene-depth chat/meetups): "a bot
user inside the chat system in a dedicated support room, grounded in your
festival data via tool-calling (schedule, artists, transport, POIs, FAQs)."
It is explicitly Build tier, not Adopt, because no competitor offers one
(section B). Phase 1's "done when" criterion is that "the agent answers
schedule, transport and POI questions from live data" (section E).

The agent is not a blank-slate LLM integration: it lands inside a chat
system that already has real constraints:

- Chat has an existing OpenAI dependency: `chat_moderation.py` calls
  `omni-moderation-latest` for image/video/text harassment-hate-violence-
  sexual content, and a separate `GPT-5.4-nano` call (Responses API,
  `reasoning=none`) for drugs/spam/scam/external-platform-link detection,
  both invoked via raw httpx, run in parallel with `asyncio.gather`.
  `OPENAI_API_KEY`'s absence is already logged loudly at startup because
  its absence silently degrades moderation to word-filter-only.
- Every message in a moderated (group) room passes word filter, then the two
  AI layers, before broadcast (CLAUDE.md, "Moderation Pipeline"). The bot's
  own answers, if posted into a moderated support room like any other
  message, would be run through the exact same pipeline as user content,
  which is either redundant (the bot's answers are grounded in the DB, not
  free-form generation, so the harassment/drugs/spam categories the pipeline
  screens for are not the actual risk surface for its output) or, worse,
  adds real latency and false-positive risk to every bot reply for
  categories that do not meaningfully apply to it.
- The codebase already has a strong, repeated pattern for rate limiting
  abuse-prone endpoints: per-IP in-memory limiters, self-pruning (magic-link
  login 5/15min, OAuth/verify 120/5min, `/chat/api/swlog` 30/min, push ack
  60/min, admin-auth-failure 20/5min, upload 10/min per user). An AI agent
  budget ceiling should follow the same shape: per-user and/or per-event
  request caps: rather than inventing a new mechanism.
- Strikes/bans/mute enforcement happens per user message before broadcast
  (`check_ban_mute`), and a banned/muted user is rejected at WS connect. A
  user asking the bot questions is still a user subject to that enforcement;
  the open question is specifically about the bot's own generated content,
  not about whether the user asking is moderated (they still are, normally).

## Options considered

**A. Anthropic Claude (a Haiku-class model for cost/latency, Sonnet-class
for quality).** Tool-calling over the structured DB (schedule, transport,
POIs, FAQs) is a first-class use case for Claude's tool-use API. Would be a
new provider integration alongside the existing OpenAI dependency (two
vendor relationships, two API keys, two billing lines, two outage surfaces
to plan for in `monitor.sh`-style health checks), but decouples agent
behavior/cost from the moderation vendor entirely, so a moderation-vendor
outage or pricing change does not also take out the agent, and vice versa.

**B. OpenAI (reuse the existing `OPENAI_API_KEY`/httpx integration, e.g. a
GPT-5-class model or the already-integrated nano tier).** Zero new vendor
relationship: the project already depends on OpenAI for moderation, already
has the raw-httpx calling pattern, already logs loudly on missing key, and
the ops/monitoring story (section "Monitoring" in CLAUDE.md checks OpenAI
moderation reachability from inside the container) already covers this
vendor. The real risk is coupling: an OpenAI outage or account issue would
now take out moderation AND the support agent simultaneously, and a single
vendor's pricing change affects both cost lines at once.

**C. Self-hosted open-weight model.** Avoids per-token vendor cost
entirely, but requires GPU-capable infrastructure the current single-VPS
deployment does not have, and ADR 0007 already documents that today's one
VPS cannot even hold the *non-GPU* services the platform is adding (Next.js,
FastAPI, pretix, Medusa, Payload, Meilisearch, Postgres, Redis). Realistic
only if hosting is re-architected for the commerce pillars anyway (ADR 0007)
and a GPU-capable node is added to that plan specifically for this; not a
near-term option otherwise.

**Interaction with moderation, regardless of model chosen.** The bot's
replies should not go through the full three-layer pipeline built for
user-submitted content: the drugs/spam/external-link/harassment categories
it screens for are not the bot's risk surface, since its answers are
tool-call-grounded rather than freely generated, and running the AI layers
(each an external API round-trip) on every bot reply adds latency and cost
without addressing a real threat. The actual risk surface for the bot is
prompt injection via user messages, crafted to make the bot say something
harmful, expose data it should not (e.g. private meetup coordinates, DM
content) or "recommend" an external link the platform would otherwise block
(GPT-5.4-nano's job today). Rate limiting per user matters more here than
content-scanning the output: a per-user query cap and a per-event daily
budget ceiling (with a circuit breaker, not a silent cost blowout) are the
first line of defense; a lightweight word-filter pass on the bot's output
(the cheap, instant layer, not the two AI calls) is reasonable
defense-in-depth against an injection that gets the bot to echo something
blocklisted.

## Leaning

Reuse OpenAI (option B) rather than introduce a second LLM vendor: the
project already has the moderation dependency, the httpx calling pattern,
the "log loudly if key missing" precedent, and the ops monitoring already
watches OpenAI reachability from inside the container. The coupling risk
(one vendor outage affects both moderation and the agent) is real but
manageable, and is outweighed by not doubling the vendor/ops surface for a
Phase 1 feature. The recommended cost model: a per-event daily token/request
budget ceiling enforced server-side with a hard stop (not just an alert), a
per-user per-hour query cap following the existing rate-limiter pattern
(in-memory, self-pruning, keyed like the login/upload limiters already in
`chat_ws.py`/`chat_api.py`), and the bot's own output skipping the two AI
moderation layers entirely but still passing the instant word filter as
cheap defense-in-depth against prompt-injection echo. Exact per-token
pricing and rate-limit numbers need to be pinned to whatever OpenAI model is
actually chosen at implementation time (pricing moves) and are deliberately
left out of this ADR rather than guessed.

## Decision

Pending.

## Consequences

Pending: depends on the option chosen. At minimum, whichever model is
picked needs its own budget-ceiling circuit breaker before the agent is made
public (section B: "cheap to prototype, but the cost model must exist before
it is public"), and the moderation-interaction rule (bot output skips the
two AI layers, keeps the word filter, relies on rate limits rather than
content-scanning its own replies) should be written into
`chat_moderation.py`'s design docs once implemented, not left as tribal
knowledge.
