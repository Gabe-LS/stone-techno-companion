# 0002. Shared menu component deferred to Next.js

## Status

Accepted

## Date

2026-07-13

## Context

The current app has four hand-written menus (lineup command bar, timetable
view, chat's mobile hamburger, and chat's (currently absent) desktop nav)
across two separate CSS systems, with three different active-state
conventions, and a verified 767px-vs-768px breakpoint bug where chat's menu
does not open at exactly 768px. `docs/menu-sequencing-strategy.md` is the
decision record for whether to fix this now, by hand, or wait.

ADR 0001 commits the platform to a Next.js front-end. Next.js's component
model (a `<Nav>` component reused across every page) is itself the "one menu
everywhere" standardization this project needs. The sequencing strategy's
core principle is: never build a shared-component layer (e.g. hand-rolled Web
Components with a `renderMenu()` function) right before adopting a framework
whose entire value proposition is a shared-component layer. Doing so means
building the same abstraction twice: once now in Web Components, once again
later in Next.js: for a component that is throwaway the moment Next.js
lands.

The sequencing strategy splits the menu work into two buckets: Bucket 1
(cheap, migration-proof fixes: blue active state, group labels, hamburger
unread badge, the breakpoint bug, chat's missing `aria-label`/Escape-to-close,
giving chat desktop a way back to Line-up/Timetable/Transport) survives any
future rewrite and should be done regardless. Bucket 2 (the actual
`renderMenu()`/Web Components standardization) is explicitly throwaway if
Next.js is committed and near-term. The strategy's deciding variable is how
real and how soon Next.js is: committed and within about six months means
skip Bucket 2 entirely; a year or more out, or merely aspirational, means
build Bucket 2 as an interim standardization because carrying four divergent
menus for over a year is not worth avoiding duplicated effort. The strategy
also carries a live-event override: if a festival is imminent, freeze
architecture entirely and ship at most the two safest one-line fixes
(breakpoint + aria-label), or ship nothing.

## Options considered

**A. Build Bucket 2 now as Web Components, defer nothing (chosen: rejected).**
Standardize the menu across all four surfaces immediately using framework-
agnostic Web Components, ahead of any Next.js work. Rejected per the
sequencing strategy's core principle: this is the exact "build the
abstraction twice" trap, and it is only justified if Next.js is a year or
more away or purely aspirational: which is not the case here, since ADR 0001
makes Next.js the committed target for the front-end.

**B. Build Bucket 1 only, defer Bucket 2 to Next.js (chosen).** Ship the
migration-proof fixes now (they are cheap, real improvements to the live app
and are not wasted by any future rewrite), and let the Next.js `<Nav>`
component be the eventual standardization, built once, in the target
framework. This matches the sequencing strategy's recommendation for the
"committed and within about six months" branch.

**C. Do nothing until Next.js ships.** Leave even the cheap fixes (breakpoint
bug, missing aria-label, no chat desktop nav) unfixed until the Next.js
migration lands. Rejected: Bucket 1 fixes are real accessibility and UX bugs
in the live app today (the breakpoint bug and the missing aria-label are
verified gaps), they cost little, and nothing about deferring the
architectural component work requires also deferring bug fixes that survive
the rewrite unchanged.

## Decision

Ship Bucket 1 now: unified active state, group labels, hamburger unread
badge, the 767/768px breakpoint fix, chat's `aria-label` and Escape-to-close,
and a way back to Line-up/Timetable/Transport from chat desktop. Do not build
Bucket 2 (the `renderMenu()`/Web Components abstraction) at all: the shared
menu component is built exactly once, later, as a Next.js component. The
three-zone desktop bar / chat-gets-a-nav / one active-state design from the
menu proposal is kept as a design spec that carries forward into the Next.js
component, not implemented twice.

If a festival is imminent at the time this work is picked up (transport
config dated around the current window), the live-event override in
`docs/menu-sequencing-strategy.md` takes precedence: touch nothing but, at
most, the breakpoint and aria-label fixes, or wait until after the event.

## Consequences

The live app gets safer and more consistent navigation now without
architectural risk, and none of that work is wasted when Next.js lands.
Chat continues to have no desktop nav beyond the Bucket 1 fix (a full,
polished desktop nav is the Next.js component's job, not retrofitted onto
`chat.html`). Four divergent menu implementations (with only their worst bugs
fixed) persist until the Next.js front-end reaches the pages that need them.
This is an accepted, time-boxed inconsistency, not a permanent one. If
Next.js slips past roughly a year out, this ADR should be revisited and
superseded with a decision to build Bucket 2 after all, per the sequencing
strategy's own stated threshold. Blueprint Phase 0 currently lists menu
standardization under "Web Components" (blueprint section E); per the
sequencing strategy's closing note, that phase description should be amended
to say the standardization is deferred to the Next.js migration, since
Next.js is the genuine target.
