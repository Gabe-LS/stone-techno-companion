# 0008. Identity broker (D6)

## Status

Proposed

## Date

2026-07-13

## Context

This is blueprint open decision D6 (section I), gating Phase 3 (commerce and
scale, pretix and Medusa integration, per blueprint section E: "Integrate
pretix (ticketing) and Medusa (merch) at the BFF, behind the single identity
service"). Today, auth lives entirely in the chat module: two passwordless
providers (Google OAuth and email magic link via Maileroo, with a 6-digit
sign-in code for iOS home-screen PWAs whose cookie storage is partitioned
from Safari), sessions as per-device rows (`sessions` table: id, user_id,
token, expires_at), and a `user_providers` table that already supports
multi-provider linking (the same user via Google + email). None of this was
built to be a general-purpose identity provider for other services: it was
built to authenticate people into this app's own chat.

Section G's migration invariants constrain this decision directly:

- **Provider_id continuity.** `bans` and `strikes` are deliberately
  FK-less and provider-keyed specifically so they survive user deletion; ban
  enforcement covers every linked provider of a user (`user_providers`), and
  re-linking a fresh provider to an existing banned account is explicitly
  blocked (`_authenticate` checks `is_user_banned` across the frozen
  identity and every `user_providers` row before issuing a session). Any
  identity change that alters or reassigns `provider_id` values breaks this
  silently: a ban keyed to an old identifier stops matching a user
  presenting a new one from the new identity layer.
- **Existing sessions.** The per-device session model (one session row per
  login, `/chat/api/logout` closing only the calling device's session and
  WebSocket) is load-bearing UX (a user does not get logged out on every
  device by logging out on one). Whatever fronts auth next must preserve
  per-device session semantics, not collapse to a single shared session per
  user.
- **E2EE device keys are tied to browser profiles, not to a portable
  identity claim.** Each browser profile has a `device_id` + P-256 ECDH
  key pair kept in `localStorage`, registered server-side in
  `e2ee_device_keys` keyed by `user_id` + `device_id`. If an identity
  migration changes what `user_id` means (a different internal id post-
  migration for the same real person), every existing device's key
  registration silently orphans: the device's stored keys point at a
  `user_id` the new system no longer recognizes as that person, and DMs
  encrypted to those keys become undecryptable with no server-side recovery
  possible (there is no server-side re-encryption path, per CLAUDE.md's
  E2EE section).

## Options considered

**A. Promote the existing companion auth into a home-grown OIDC provider.**
The current Google OAuth + email magic-link system becomes the identity
provider of record, exposing a standard OIDC discovery document, JWKS, and
authorization-code flow so pretix, Medusa, and Payload can all treat it as
their relying party's IdP.
- *Continuity*: strongest by construction: no user migration at all, since
  the same `users`/`sessions`/`user_providers`/`bans` tables remain the
  source of truth; `provider_id`, session semantics, and `user_id` (and
  therefore E2EE device key registrations) are untouched.
- *Cost*: implementing a spec-correct OIDC provider (discovery metadata,
  key rotation, authorization codes, token introspection/revocation,
  consent screens) is a genuine, ongoing engineering commitment for a team
  whose differentiator is festival data and community, not identity
  infrastructure: and it must stay spec-compliant indefinitely as
  pretix/Medusa/Payload's own OIDC client libraries evolve.

**B. Front everything with an external IdP** (Keycloak, Authentik, or Ory),
migrating existing chat users into it.
- *Continuity*: the external IdP must preserve the existing `provider_id`
  (Google `sub`, email address) exactly as the stable identifier bans and
  strikes are keyed to, and preserve (or exactly re-derive) `user_id` so
  E2EE device key registrations do not orphan: this is a real, one-time
  migration risk, not a design property that comes for free just by picking
  a mature IdP. Per-device session/logout semantics would need to be
  re-derived from the external IdP's token model (its own session/refresh
  token conventions do not automatically match the current
  one-row-per-device close-only-this-device behavior) or wrapped by a thin
  companion-side session layer that still talks to the IdP for
  authentication.
- *Cost*: spec compliance, security patching, and OIDC edge cases become
  someone else's maintenance burden (a real, significant win); but
  introduces a new stateful service with its own database that becomes a
  single point of failure for every login across the whole platform,
  including chat's WebSocket auth (chat's session cookie is intentionally
  non-httpOnly for WS access: any IdP swap must not break that access
  pattern). Self-hosted Keycloak/Authentik avoid third-party data-processor
  concerns that a hosted option (Ory Cloud) could introduce; that
  distinction matters for GDPR posture given the EU festival audience.

**C. Hybrid: keep the companion's own session/cookie model as-is for its
own surfaces (lineup, chat), and add a lightweight OIDC broker in front,
used only by the new commerce relying parties (pretix, Medusa, Payload
admin), federating identity from the companion's existing user store as the
source of truth.** The companion stays the identity provider of record; the
broker is a translation layer, not a replacement, so no live chat user is
migrated at all and no `user_id`/`provider_id`/session semantics change for
the surfaces that already depend on them (E2EE keys included). New-infra
risk (the broker itself) is confined to the commerce surfaces, which are new
integrations anyway and have no existing user-continuity requirement to
protect.

## Leaning

Given how severe the continuity invariants are here specifically: bans and
strikes keyed to `provider_id` and deliberately surviving user deletion,
E2EE device keys keyed to `user_id` with no server-side re-encryption path,
and an already-working per-device session/logout model: a full migration
of the live user base into a new identity system (option B) carries risk
that is disproportionate to what Phase 3 actually needs, which is *new*
relying parties (pretix, Medusa, Payload admin) needing to trust the
existing identity, not existing users needing a new identity. Option C
(hybrid broker in front of the existing companion auth, used only by the new
commerce surfaces) matches the project's own stated philosophy elsewhere:
keep what already works and is hard to safely replace (section B: "Keep"
tier for chat/E2EE), adopt for what is genuinely new (the commerce
integrations). This needs two open questions answered before it can be
finalized: whether pretix's and Medusa's documented OIDC client expectations
can actually be satisfied by a custom broker cleanly (both projects document
self-hosted OIDC integration, but against known IdPs, not arbitrary
brokers), and whether GDPR/data-residency requirements for the EU festival
audience favor a self-hosted broker (Keycloak/Authentik) over any hosted
option. If either answer forces a full external IdP, option B should be
chosen deliberately with those continuity risks explicitly staged and
tested (a dry-run migration against a scratch copy of `chat.db`, verifying
every existing ban/strike/E2EE key still resolves correctly under the new
identity, before any real cutover) rather than assumed safe.

## Decision

Pending.

## Consequences

Pending: depends on the option chosen. Whichever option is chosen, it must
be decided and continuity-tested before pretix and Medusa integration
begins (blueprint Phase 3), since both depend on trusting an identity layer
that does not yet exist; this ADR itself is the D6 blocker named in the
blueprint and should not be left open once Phase 3 planning starts in
earnest.
