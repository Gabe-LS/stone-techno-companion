# UI standards for apps/web

The binding rules for every element in the Next.js app. Written 2026-07-20 after an
owner escalation about visual drift on the transport page. Every new surface and every
style change is checked against this file. Values come exclusively from
`packages/design-tokens/tokens.css`; a hardcoded px value that duplicates a token is a
bug.

## 1. Typography roles

One role per text element. No element invents its own size or color.

| Role | Size token | Weight | Color |
|---|---|---|---|
| Page title (exactly one per page) | `--font-xl` | 700 | `--color-text` |
| Section title (board headers, group headers) | `--font-lg` | 600 | `--color-text` |
| Row title (city, airport, destination) | `--font-base` | 600 | `--color-text` |
| Primary datum (departure time) | `--font-base` | 700 | `--color-text` |
| Body / summary | `--font-base` | 400 | `--color-text-secondary` |
| Meta (duration hints, dates, counts) | `--font-xs` | 400 | `--color-text-secondary` |

Rules: nothing below 12px. A child section's title is never larger than its parent's.
On a dark fill, text is `#fff` or `--gray-400`; never a mid-gray that fails AA.

## 2. Controls

Visual weight follows hierarchy: page tabs are the heaviest tab tier, tabs inside a
panel are visibly lighter, never the reverse.

- **Primary tab pill** (method picker): pill radius, 1px `--color-border`, surface
  background, single line, icon plus label. Active: solid `--gray-900` fill, white
  text. Never an underline (underlines on pills are forbidden, owner decision).
- **Secondary tab pill** (day tabs inside a board): same family, smaller (single line,
  label plus short date in the same color as the label, `--font-xs`), lighter padding
  than the primary tier. Active: same solid fill, ALL text white.
- **Button** (in-page action: Locate me, expanders): solid `--gray-900` pill, white
  text, `--font-sm` 600. This is the only button style.
- **External link** (leaves the site): underlined, `--color-text`, ALWAYS with the
  external-link icon, `rel="noopener noreferrer"`. Nothing else may use this style.
- **In-page expander**: a Button (with chevron), never styled as a link. If it looks
  like a link, it must leave the site; if it stays on the page, it looks like a button.
- **Nav bar items** (top menu only): accent underline active state. This convention
  exists only there.
- **Focus**: every interactive element has the same `:focus-visible` outline
  (1px `currentColor` equivalent per component family, offset 2px).

## 3. Containers

- **Card** (curated rows, departure rows, info strips): `--color-surface` background,
  1px `--color-border`, `--radius-md`. One radius for every card on the page.
- Two densities only: content cards pad `--space-lg`; data rows (departures) pad
  `--space-sm` `--space-md`. Nothing else.
- Lists use flex `gap` (`--space-sm`), never per-row margins.
- No clipped or half-visible elements at rest: scroll-to-position never leaves a cut
  card under a sticky element on first paint.

## 4. Rhythm

- Vertical gaps between page-level blocks: `--space-lg`. Maximum any single gap:
  `--space-xl`. Dead bands larger than that are bugs.
- One `max-width` container per page; every block aligns to it.

## 5. Copy

- No text may reference layout ("above", "below", "left"); layouts change, copy rots.
- Coarse durations only on transport content (see docs/getting-there-design.md).

## 6. Enforcement

Every PR touching apps/web styling states which sections of this file it was checked
against. Visual review happens on real screenshots at 390px and 1280px, both themes
when theming lands, before a styling change is called done.

`tests/web/css_standards_check.py` runs this automatically (wired into
`.github/workflows/ci.yml`, pure file reading, no browser/dev-server needed): it
fails a PR that introduces (a) a raw hex color outside `packages/design-tokens/tokens.css`
and not within 20 lines of a `semantic-exception` marker comment, (b) a raw px value on
`font-size`, `border-radius`, `padding*`, `margin*`, `gap`, or `top`/`right`/`bottom`/`left`/`inset`
outside the `0`/`1px`/`2px` allowlist and not exception-marked, or (c) `text-decoration:
underline` outside `components/ui/ExternalLink.module.css` and `components/Nav.module.css`.
A value that is a genuine one-off (a legacy-parity literal, a decorative micro-nudge,
an operator brand color) gets a `/* semantic-exception */` comment near its declaration
instead of a token — see `components/transport/LiveBoard.module.css`'s NE2/next-departure
block and `components/Nav.module.css`'s `.mobileLink` for the pattern. Everything else
should resolve to a token from `packages/design-tokens/tokens.css`'s semantic component
layer (below) before it is ever typed as a raw literal.

## 7. Component inventory

Every UI primitive lives in `apps/web/components/ui/`, each with its own `*.module.css`
that consumes ONLY tokens from `packages/design-tokens/tokens.css` (primitives + the
semantic component layer — control heights, button/pill/badge/focus-ring/icon-size/
card/toast/input/overlay tokens, all documented inline in that file). **New UI code
uses one of these primitives first.** If none fits, that is a standards change: extend
this file and the token layer together (a new primitive without matching tokens, or a
token with no consumer, is a smell) rather than inventing a one-off style in a page's
own CSS module.

| Primitive | When to use | Token group it consumes |
|---|---|---|
| `Button` | The app's one solid-fill in-page action (DESIGN-STANDARDS.md #2: "Locate me", expanders, future primary actions). Polymorphic: renders `<a>` when given `href` (an in-page control styled as a button because it never leaves the site), else `<button>`. | `--btn-*` |
| `IconButton` | A single-glyph control. `variant="chrome"` (default): round hover/active chrome, for standalone icon controls. `variant="inline"` (owner decision 2026-07-21): a bare glyph that belongs to adjacent text, sized text-relative (0.52em), recolors on hover with NO background chrome, invisible padded hit area — e.g. the transport direction-swap arrow beside the section title. | `--icon-btn-*`, `--focus-ring-*` |
| `Pill` | A tab/picker control, `tier="primary"` (page-level method/section pickers) or `tier="secondary"` (in-panel day/period pickers, lighter padding). Active state is always the solid fill inversion — pills are never underlined (owner decision 2026-07-20). | `--pill-*` |
| `Badge` | A numbered count chip, hidden entirely at zero (returns `null`, never renders "0"). Declares no `position` — the consumer places it via `className` (see `Nav.module.css`'s `.badgePosition`). Not for always-visible text status labels (e.g. transport's "Canceled" tag) — those stay page-local, reusing `--badge-radius`/`--badge-font-size` where the look matches without adopting the hide-at-zero counter semantics. | `--badge-*` |
| `Toast` | The app-wide notification style (CLAUDE.md "Conventions": word-based duration, balanced text, max 360px). Style only for now — no page fires one yet; the timing/dismiss behavior is wired up by the first caller. | `--toast-*` |
| `ExternalLink` | Any link that leaves the site: underlined, `--color-text`, always with the external-link icon, always `target="_blank" rel="noopener noreferrer"`. The only underlined style in the app besides the Nav bar's own accent-underline active state (`components/Nav.module.css`, a distinct, Nav-only convention — DESIGN-STANDARDS.md #2). | `--color-text`, `--icon-size-sm`, `--focus-ring-*` |

Tokens with no primitive consumer yet, predicted from `docs/parity/lineup.md` /
`docs/parity/timetable.md` / the chat sections of `CLAUDE.md` (control heights,
card densities, input, overlay/scrim, divider, icon-size-md): these apply directly
in page/feature CSS modules today (e.g. `--card-*` in `LiveBoard.module.css`'s
`.depItem`, `--overlay-bg` in `Nav.module.css`'s `.overlay`) and graduate to a
dedicated primitive (`Card`, `Input`, action-sheet) only once a second real
consumer needs the same behavior, not preemptively.
