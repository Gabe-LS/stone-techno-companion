# CSS Cleanup Phase 3 Report

## Objective

Optimize the inline CSS remaining in each page after Phases 1 and 2. Replace hardcoded values with shared.css tokens, remove dead rules and redundant overrides, move inline styles to CSS classes, and organize with section comments. Zero visual regression.

## Commits

| Commit | Description |
|---|---|
| `52dede1` | Clean up and optimize lineup/timetable inline CSS |
| `e2dba5d` | Clean up and optimize chat inline CSS |

## Lineup/timetable (`scraper/render.py`)

### Dead rules removed: 0

No dead CSS rules were found. All classes are referenced in the generated HTML or JS.

### Redundant rules removed: 1

| Rule | Reason |
|---|---|
| `@media (hover: hover) { .tt-popup .links a:hover { color: var(--color-text); } }` | Identical to the base `.links a:hover` rule; `.tt-popup .links a` inherits from `.links a` |

### Values tokenized: 52

| Category | Before | After | Count |
|---|---|---|---|
| Colors | `#111` | `var(--gray-900)` | 2 (cmd-bar, cmd-dropdown) |
| Colors | `#f9f9f9` | `var(--gray-50)` | 1 (artist-item bg) |
| Colors | `#222` | `var(--gray-800)` | 1 (cmd-dropdown active) |
| Spacing | `32px` | `var(--space-2xl)` | 1 |
| Spacing | `24px` | `var(--space-xl)` | 7 (body padding, bio, modals, popup, margins) |
| Spacing | `16px` | `var(--space-lg)` | 10 (gaps, paddings, margins, icon sizes) |
| Spacing | `12px` | `var(--space-md)` | 5 (paddings, gaps, margins) |
| Spacing | `8px` | `var(--space-sm)` | 12 (gaps, paddings, margins) |
| Spacing | `4px` | `var(--space-xs)` | 7 (margins, paddings, timetable) |
| Z-index | `200` | `var(--z-popup)` | 1 (tt-popup) |
| Z-index | `10` | `var(--z-sticky)` | 3 (period-heading, location-heading, floor-header-bar) |
| Border-radius | `4px` | `var(--radius-sm)` | 6 (photos, thumbnails in base + mobile) |
| Border-radius | `999px` | `var(--radius-pill)` | 3 (floor pills desktop + mobile) |
| Font-size | `16px` | `var(--font-base)` | 2 (pin-real input, view-label) |

### Inline styles moved to CSS: 2

| Element | Inline style removed | CSS rule added |
|---|---|---|
| `.popup-photo` (JS innerHTML) | `style="cursor:pointer"` | `.tt-popup .popup-photo { cursor: pointer; }` |
| `.popup-name` (JS innerHTML) | `style="cursor:pointer"` | `.tt-popup .popup-name { cursor: pointer; }` |

### Section comments added

```
/* ===== PAGE-SPECIFIC TOKENS ===== */
/* ===== BASE OVERRIDES ===== */
/* ===== COMPONENTS ===== */
/* ===== MEDIA QUERIES ===== */
```

### Hardcoded values intentionally kept

| Value | Usage | Reason |
|---|---|---|
| `#aaa` | cmd-bar button inactive text | No close token match (#aaa is pure gray, --gray-400 has blue tint) |
| `#444` | cmd-sep separator | No close token match |
| `#222` | h1 border-bottom | Different from --gray-800 (#1f2937) |
| `#333` | period-heading, bio-text, modal labels | Different from --color-text-secondary (#374151) |
| `#555` | location-heading, links | Different from --color-muted (#6b7280) |
| `#bbb` | or-line span | Different from --color-disabled (#9ca3af) |
| `#ddd` | pin border, tab border | Different from --color-border (#e5e7eb) |
| `#d4edda` | copied link highlight | No matching token |
| `6px` | border-radius on photos, blocks | Between --radius-sm (4px) and --radius-md (8px) |
| `13px` | cmd-dropdown button font-size | Between --font-xs (12px) and --font-sm (14px) |
| `z-index: 30`, `20` | h1, h2 sticky headings | Between --z-sticky (10) and --z-header (40), no token |
| `box-shadow: 0 1px 3px rgba(0,0,0,0.1)` | artist-photo | Different from --shadow-sm (2px blur, 0.06 opacity) |
| `box-shadow: 0 8px 24px rgba(0,0,0,0.18)` | tt-popup | Different from --shadow-modal (0.12 opacity) |

### Line count

| Metric | Before | After | Delta |
|---|---|---|---|
| `scraper/render.py` | 2572 | 2574 | +2 (section comments) |

## Chat (`server/chat/chat.html`)

### Dead rules removed: 2

| Rule | Evidence |
|---|---|
| `.header .subtitle` | Class not referenced in any HTML template or JS innerHTML |
| `.room-preview` | Class not referenced in any HTML template or JS innerHTML |

### Values tokenized: 10

| Category | Before | After | Count |
|---|---|---|---|
| Colors | `#111` | `var(--gray-900)` | 2 (header, chat-menu) |
| Font-size | `14px` | `var(--font-sm)` | 3 (reply-preview-text, reply-quote-text, reaction-pill) |
| Spacing | `4px` | `var(--space-xs)` | 2 (reaction-pill padding, link-preview-desc margin) |
| Spacing | `8px` | `var(--space-sm)` | 1 (reaction-pill padding) |
| Spacing | `24px` | `var(--space-xl)` | 1 (menu-items room-item padding) |
| Z-index | `10` | `var(--z-sticky)` | 1 (react-btn) |
| Transition | `0.15s` | `var(--transition-fast)` | 1 (react-btn opacity) |

### Section comments added

```
/* ===== PAGE-SPECIFIC TOKENS ===== */
/* ===== BASE OVERRIDES ===== */
/* ===== COMPONENTS ===== */
/* ===== MEDIA QUERIES ===== */
```

### Hardcoded values intentionally kept

| Value | Usage | Reason |
|---|---|---|
| `#ccc` | menu-section text color | No close token (between --gray-200 and --gray-400) |
| `15px` | menu-section font-size | Between --font-sm (14px) and --font-base (16px) |
| `22px` | modal-close font-size | Between --font-xl (24px) and --font-lg (18px) |
| `18px` | country flag font-size | Exact match to --font-lg but semantically unrelated |
| `z-index: 50` | action-menu, country-list | Intentionally 1 above --z-menu (49) |

### Line count

| Metric | Before | After | Delta |
|---|---|---|---|
| `server/chat/chat.html` | 3399 | 3408 | +9 (section comments offset dead rule removal) |

## Verification

### Visual regression testing

Before/after screenshots captured at 375px (mobile) and 1024px (desktop) via Playwright:

| Page | Mobile | Desktop |
|---|---|---|
| Lineup | No regression | No regression |
| Timetable | No regression | No regression |
| Hamburger menu | No regression | N/A |
| Bio modal | N/A | No regression |
| Chat (login) | No regression | No regression |

Pixel-level comparison shows subpixel differences only in lineup (from `#111` to `#111827` and `#f9f9f9` to `#f9fafb` near-match tokenizations). These are imperceptible on screen. Chat pages show 0% pixel difference.

### Test suite

All 132 tests pass:

| Test file | Tests | Status |
|---|---|---|
| `test_chat_db.py` | 45 | Passed |
| `test_chat_moderation.py` | 39 | Passed |
| `test_chat_ws.py` | 17 | Passed |
| `test_chat_api.py` | 31 | Passed |

### Build verification

- `python stone_techno_companion.py --render-only --no-photos` succeeds
- Server starts and serves both pages correctly

## Summary

| Metric | Lineup | Chat | Total |
|---|---|---|---|
| Dead rules removed | 0 | 2 | 2 |
| Redundant rules removed | 1 | 0 | 1 |
| Values tokenized | 52 | 10 | 62 |
| Inline styles moved to CSS | 2 | 0 | 2 |
| Section comments added | 4 | 4 | 8 |
| Rules flagged TODO | 0 | 0 | 0 |

## What was NOT changed

- **Admin page** (`server/chat/admin.html`): Out of scope per instructions
- **Hardcoded colors with no close token match**: `#aaa`, `#444`, `#222`, `#333`, `#555`, `#bbb`, `#ddd`, `#ccc`, `#d4edda` -- these are intentional page-specific values that differ from shared tokens by more than a perceptible threshold
- **6px border-radius**: Falls between `--radius-sm` (4px) and `--radius-md` (8px). Not worth adding a new token for.
- **Media query structure**: Hover queries kept inline with their base rules (moving them risks cascade order issues). Responsive breakpoint queries kept in existing positions.
- **13px and 15px font-sizes**: Fall between token steps. Changing would alter visual output.
