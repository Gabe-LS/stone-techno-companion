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
