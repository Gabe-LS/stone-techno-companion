# CSS Unification Phase 2 Report

## Objective

Unify the font scale across both pages and extract shared components into `shared.css`, continuing the work from Phase 1.

## Commits

| Commit | Description |
|---|---|
| `66eb3fe` | Unify font scale across lineup and chat into shared.css |
| `0f541dc` | Extract toast component to shared.css |

## 1. Font Scale Unification

### Before

The lineup and chat used independently defined font scales with different units:

| Token | Lineup (em) | = px | Chat (px) |
|---|---|---|---|
| `--font-2xl` | 2em | 32px | (none) |
| `--font-xl` | 1.5em | 24px | 20px |
| `--font-lg` | 1.125em | 18px | 17px |
| `--font-base` | 1em | 16px | 15px |
| `--font-sm` | 0.875em | 14px | 13px |
| `--font-xs` | 0.75em | 12px | 11px |
| `--font-xxs` | (none) | - | 10px |

### After

A single em-based scale in `shared.css`, using the lineup's values as the source of truth:

```css
--font-2xl: 2em;    /* 32px */
--font-xl: 1.5em;   /* 24px */
--font-lg: 1.125em; /* 18px */
--font-base: 1em;   /* 16px */
--font-sm: 0.875em; /* 14px */
--font-xs: 0.75em;  /* 12px */
```

### Chat token mapping

Each chat element maps to the same-name unified token. The `--font-xxs` token (10px) was retired and all references mapped to `--font-xs` (12px), respecting the 12px minimum.

| Chat token | Chat px | Unified token | Unified px | Delta |
|---|---|---|---|---|
| `--font-xl` | 20px | `--font-xl` | 24px | +4px |
| `--font-lg` | 17px | `--font-lg` | 18px | +1px |
| `--font-base` | 15px | `--font-base` | 16px | +1px |
| `--font-sm` | 13px | `--font-sm` | 14px | +1px |
| `--font-xs` | 11px | `--font-xs` | 12px | +1px |
| `--font-xxs` | 10px | `--font-xs` | 12px | +2px |

### Chat elements affected by `--font-xxs` retirement

These elements moved from 10px to 12px (`--font-xs`):

- `.header .online` (online count indicator)
- `.room-count` (room member count)
- `.msg-time` (message timestamps)
- `.link-preview-domain` (domain in link previews)
- `.reply-preview-name`, `.reply-quote-name` (reply author names)
- `.avatar-zoom span` (zoom slider label)
- `.unread-badge` (unread badge number)

### Visual impact

- **Lineup**: No change. The em values are identical, just moved from inline to shared.css.
- **Chat**: All text slightly larger (1-4px depending on token). Most elements increase by 1px. Headings (`--font-xl`) increase by 4px. `--font-xxs` elements increase by 2px. This is intentional — the chat is brought into alignment with the lineup's type scale.

### Files changed

- `server/static/shared.css`: Added font scale tokens to `:root`
- `scraper/render.py`: Removed 6-line font scale from inline `:root`
- `server/chat/chat.html`: Removed 7-line font scale from inline `:root`, replaced all `--font-xxs` references (9 occurrences) with `--font-xs`

## 2. Component Extraction

### Toast notifications (extracted)

The chat's `.toast` component was moved to `shared.css`. The lineup has no toast currently, but the component is now available for both pages.

**Extracted rules**: `.toast` (16 properties) and `.toast.show` (opacity toggle).

**Files changed**:
- `server/static/shared.css`: Added toast component
- `server/chat/chat.html`: Removed 2-line toast CSS block

### Modal overlays (kept separate)

The two pages use fundamentally different toggle mechanisms:

| Property | Lineup | Chat |
|---|---|---|
| Class | `.modal-overlay` | `.modal-overlay` |
| Default state | `display: none` | `display: flex; opacity: 0` |
| Open toggle | `display: flex` | `opacity: 1` |
| Z-index | `var(--z-modal)` (100) | `var(--z-popup)` (200) |
| Background | `rgba(0,0,0,.4)` | `rgba(0,0,0,0.5)` |
| Animation | None | Opacity + scale transition |
| Content class | `.modal-box` | `.modal` |
| Content structure | `h3` + `.sub` + flat content | `.modal-header` + `.modal-body` + `.modal-footer` |

The display-toggle vs opacity-toggle approach makes these incompatible for a shared base without changing behavior. The only common properties (`position: fixed; inset: 0`) are too minimal to justify a shared component. Left separate.

### Form controls (kept separate)

| Aspect | Lineup | Chat |
|---|---|---|
| Inputs | None (no dedicated input CSS) | 5 input variants (auth, profile, country, modal, message) |
| Buttons | `.modal-box .btn` (inline, small) | `.modal-btn-primary`, `.auth-btn` (full-width, larger) |
| Padding | `7px 18px` | `var(--space-md)` (12px) |
| Border-radius | `5px` | `var(--radius-md)` (8px) |
| Font-size | `--font-sm` | `--font-base` |

The lineup has minimal, context-specific form elements scoped under `.modal-box`. The chat has a rich form system with multiple input/button variants. Unifying would force visual changes on the lineup with no benefit — its forms are self-contained modal widgets, not reusable components.

### Pills/badges (kept separate)

| Element | Lineup | Chat |
|---|---|---|
| Floor pills | `.floor-header > span:first-child` (colored, 999px radius) | (none) |
| Day tabs | `.day-tab` (6px radius, toggle buttons) | (none) |
| Unread badge | (none) | `.unread-badge` (red pill, 999px radius) |
| Reaction pills | (none) | `.reaction-pill` (border + shadow) |

These are functionally different elements with no shared styling beyond `--radius-pill` (already a shared token). The floor pills are colored labels from DB-driven stage colors; the badges are notification indicators. No extraction value.

## Verification

### Visual regression testing

Before/after screenshots captured at 375px (mobile) and 1024px (desktop):

| Page | Mobile | Desktop |
|---|---|---|
| Lineup | No regression | No regression |
| Chat (login) | Expected size increase (font scale alignment) | Expected size increase |

### Test suite

All 132 tests pass after both changes:

| Test file | Tests | Status |
|---|---|---|
| `test_chat_db.py` | 45 | Passed |
| `test_chat_moderation.py` | 39 | Passed |
| `test_chat_ws.py` | 17 | Passed |
| `test_chat_api.py` | 31 | Passed |

### Endpoint verification

- `shared.css` serves correctly with font scale and toast
- Lineup rebuild succeeds without errors
- Chat login screen renders with unified font scale

## Summary of shared.css after Phase 2

| Category | Contents |
|---|---|
| Tokens | Gray scale (7), semantic colors (11), font family, **font scale (6)**, spacing (6), radius (4), shadows (4), transition, z-index (7), layout |
| Reset | `box-sizing: border-box` |
| Base | Body font, line-height, color, background |
| Components | Hamburger, nav-icon, menu-overlay, **toast** |
| Utilities | `.truncate`, `.sr-only` |

**Phase 2 additions in bold.**

## Future work

- **Admin page alignment**: Link shared.css from `admin.html` and remove duplicated tokens
- **Accent color unification**: Lineup uses `#e53e3e`, chat uses `#b91c1c` — design decision needed
- **Input focus pattern**: Both pages use `border-color: var(--gray-400)` for focus — could be a shared `:focus` rule if more form elements are added to the lineup
- **Scrollbar hiding**: Lineup hides on `html`, chat hides per-element — could share a `.no-scrollbar` utility
