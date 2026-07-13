# Menu refinement: sequencing strategy

Decision record for: "Do we refine the menu now, or after introducing Next.js?"

Companion to `docs/platform-blueprint.html` (the build-vs-buy + reference architecture).
The menu investigation and desktop redesign it refers to are captured in the
menu-proposal artifact (four hand-written menus, two CSS systems, the 767 vs 768
breakpoint bug, three active-state conventions, chat's missing desktop nav).

## The principle

Never build a shared-component layer right before adopting a framework whose
entire value is a shared-component layer. Next.js's `<Nav>` component IS the
"one menu everywhere" standardization, so building a Web Components `renderMenu()`
now, then rebuilding it in Next.js later, means building that abstraction twice.

That caveat applies only to the architectural half of the menu work. The rest is
migration-proof and should be done regardless.

## Split the work into two buckets

### Bucket 1: cheap, migration-proof fixes (do these regardless)

These survive any rewrite (a blue underline is re-expressed in Next.js in minutes):

- blue active state + group labels + hamburger unread badge
- the 767 -> 768 breakpoint bug (chat's menu does not open at exactly 768px)
- chat hamburger `aria-label` + Escape-to-close (verified accessibility gaps)
- give chat desktop a way back to Line-up / Timetable / Transport (the "zero nav" hole)

None of this is wasted by a future Next.js. It just makes the live app better now.

### Bucket 2: the renderMenu() / Web Components standardization (throwaway-if-Next.js)

Building the one-component abstraction by hand now, then rebuilding it as a Next.js
component later, is duplicated effort. Keep the DESIGN as a spec (the three-zone
desktop bar, chat-gets-a-nav, one active model), because the design carries forward,
but implement the COMPONENT once, in the target framework.

## The deciding variable: how real and how soon is Next.js?

- Committed / within about 6 months: do Bucket 1 now, skip Bucket 2 entirely, let
  Next.js's `<Nav>` be the standardization.
- A year or more out, or an aspirational "someday": do Bucket 1 now AND Bucket 2 with
  Web Components. Four divergent menus is too long to carry, so the interim
  standardization pays for itself before Next.js arrives.

## Live-event override

If a festival is imminent (the transport config is dated around the current window),
that overrides everything: freeze architecture, ship only the safest cosmetic fixes,
or wait until after the event. Do not refactor the shell days before a live crowd uses it.

## Recommendation

1. If an event is imminent: touch nothing but, at most, the two safest one-line fixes
   (breakpoint + aria-label). Otherwise freeze until after.
2. After the event, or if no event is soon: do Bucket 1 now (cheap, real, migration-proof).
3. Hold Bucket 2 unless Next.js is more than a year away. If Next.js is a real near-term
   plan, let it do the standardization and never build it twice.

Note: the platform blueprint currently lists menu standardization under Phase 0
("Web Components"). If Next.js is genuinely the target, amend Phase 0 to say the
standardization is deferred to the Next.js migration (do the component once, there).
