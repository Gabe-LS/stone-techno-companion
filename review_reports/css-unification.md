# CSS Unification Report

## Objective

Audit all CSS across the lineup/timetable page (`scraper/render.py`) and the chat page (`server/chat/chat.html`), then extract shared rules into a single external stylesheet (`server/static/shared.css`) to eliminate duplication, enforce visual consistency, and establish a unified design system.

## Commits

| Commit | Description |
|---|---|
| `c8117a4` | Add shared.css design system with unified tokens and components |
| `f64451e` | Refactor lineup/timetable CSS to use shared.css |
| `9e7b9ab` | Refactor chat CSS to use shared.css |

## What was created

### `server/static/shared.css`

A new external CSS file served at `/shared.css` via an explicit FastAPI route in `server/api.py`. Both pages link it via `<link rel="stylesheet" href="/shared.css">`. The file contains:

**Design tokens** (`:root` custom properties):

| Category | Tokens |
|---|---|
| Gray scale | `--gray-900` through `--gray-50` (7 levels, WCAG AA/AAA compliant) |
| Semantic colors | `--color-text`, `--color-text-secondary`, `--color-muted`, `--color-muted-icon`, `--color-disabled`, `--color-border`, `--color-surface`, `--color-surface-hover`, `--color-bg`, `--color-accent`, `--color-success` |
| Spacing | `--space-xs` (4px) through `--space-2xl` (32px) |
| Border radius | `--radius-sm` (4px), `--radius-md` (8px), `--radius-lg` (16px), `--radius-pill` (999px) |
| Shadows | `--shadow-sm`, `--shadow-md`, `--shadow-lg`, `--shadow-modal` |
| Transitions | `--transition-fast` (0.15s) |
| Z-index scale | `--z-sticky` (10), `--z-header` (40), `--z-menu-overlay` (47), `--z-menu` (49), `--z-modal` (100), `--z-popup` (200), `--z-toast` (300) |
| Layout | `--header-h` (48px), `--font-family` (system font stack) |

**Reset**: Universal `box-sizing: border-box`.

**Base styles**: Body font family, line-height, margin, color, and background.

**Shared components**:

| Component | Class | Purpose |
|---|---|---|
| Hamburger button | `.hamburger` | 48x48px icon button, positioned absolute right, hidden by default, shown on mobile |
| Navigation icon | `.nav-icon` | 48x48px link to sibling page, positioned absolute left, hidden by default, shown on mobile |
| Menu overlay | `.menu-overlay` | Fixed dimming overlay behind slide menus, with `.open` state |

**Utilities**: `.truncate` (text ellipsis), `.sr-only` (screen reader only).

## What was changed

### Lineup/timetable (`scraper/render.py`)

**Removed** (now inherited from shared.css):
- `:root` token definitions: `--color-text`, `--color-bg`, `--color-surface`, `--color-surface-hover`, `--color-muted`, `--color-muted-icon`, `--color-border`, `--shadow-modal`, `--radius-card` (redefined as `var(--radius-md)`), `--transition-fast`
- Reset: `*, *::before, *::after { box-sizing: border-box }`, `html { overscroll-behavior: none }` 
- Body base: `font-family`, `line-height`, `color`, `background`
- Component declarations: `.hamburger { display: none }`, `.menu-overlay { display: none }`, `.nav-chat-icon { display: none }`
- Mobile (480px) hamburger/nav-icon full style blocks (7 rules replaced by 2 `display: flex` overrides)
- Mobile `.menu-overlay.open` rule
- Empty rule: `h4.location-heading { }`

**Renamed**:
- `.nav-chat-icon` to `.nav-icon` (CSS selectors, HTML class, JS selector)

**Tokenized** (hardcoded values replaced with CSS custom properties):
- `.cmd-bar` z-index: `40` to `var(--z-header)`
- `.cmd-bar` padding: `0 16px` to `0 var(--space-lg)`
- `.modal-overlay` z-index: `100` to `var(--z-modal)`
- `.cmd-dropdown` top: `48px` to `var(--header-h)`
- `.cmd-dropdown` z-index: `49` to `var(--z-menu)`
- `.cmd-dropdown` border color: `#374151` to `var(--gray-700)`
- `.cmd-dropdown` font-family: literal stack to `var(--font-family)`
- `.cmd-dropdown` box-shadow: literal to `var(--shadow-modal)`
- `.cmd-bar` mobile height: `48px` to `var(--header-h)`

**Kept inline** (page-specific):
- `--color-accent: #e53e3e` (lineup uses a brighter red than the chat's `#b91c1c`)
- Font scale: `--font-2xl` through `--font-xs` (em-based, different from chat's px-based scale)
- Schedule/timetable tokens: `--color-schedule`, `--color-line-hour`, `--color-line-half`
- `--radius-modal`, `--fade-gradient`
- All component styles (cmd-bar, sticky headings, artist cards, bio overlay, modals, timetable grid, etc.)
- Dynamic floor colors from DB
- Scrollbar hiding on `html` (global for lineup, per-element for chat)

**Net diff**: +15 lines, -33 lines.

### Chat (`server/chat/chat.html`)

**Removed** (now inherited from shared.css):
- `:root` gray scale (7 variables)
- `:root` semantic aliases (11 variables)
- `:root` spacing scale (6 variables)
- `:root` border radius (4 variables)
- `:root` shadows (3 variables)
- Reset: `*, *::before, *::after { box-sizing: border-box }`
- Body base: `margin`, `font-family`, `color`, `background`, `line-height`
- `.chat-hamburger` base declaration (full style block)
- `.chat-menu-overlay { display: none }` 
- Mobile `.chat-menu-overlay.open` rule

**Renamed**:
- `.chat-hamburger` to `.hamburger` (4 occurrences: CSS, 2x HTML template, display override)
- `.chat-menu-overlay` to `.menu-overlay` (5 occurrences: CSS, HTML template, 3x JS getElementById)
- Inline-styled `<a href="/line-up" style="...">` to `<a href="/line-up" class="nav-icon" aria-label="Line-up">`

**Added**:
- `.nav-icon { display: flex }` in mobile media query (nav icon element exists only in mobile HTML but shared.css hides it by default)

**Tokenized**:
- `.header` height: `48px` to `var(--header-h)`
- `.header` z-index: `50` to `var(--z-header)`
- `.chat-menu` top: `48px` to `var(--header-h)`, z-index: `49` to `var(--z-menu)`
- `.menu-section` padding/gap: literal values to `var(--space-lg)`, `var(--space-sm)`
- `.modal-overlay` z-index: `200` to `var(--z-popup)`
- `.action-sheet-overlay` z-index: `200` to `var(--z-popup)`
- `.image-viewer` z-index: `300` to `var(--z-toast)`
- `.reaction-picker` z-index: `300` to `var(--z-toast)`
- `.toast` z-index: `300` to `var(--z-toast)`

**Kept inline** (page-specific):
- Font scale: `--font-xl` through `--font-xxs` (px-based, different from lineup's em-based scale)
- User color palette: 12 color pairs + self color
- All component styles (header, layout, messages, input bar, reactions, action sheets, modals, profile prompt, rooms, avatars, etc.)

**Net diff**: +19 lines, -61 lines.

### Server (`server/api.py`)

Added explicit route to serve `shared.css`:

```python
@app.get("/shared.css")
async def serve_shared_css():
    file_path = STATIC_DIR / "shared.css"
    if file_path.exists():
        return FileResponse(file_path, media_type="text/css")
    raise HTTPException(404, "Not found")
```

Placed before the catch-all `/{path:path}` route, alongside other static file routes.

## Token unification decisions

The two pages had independently chosen color values for the same semantic concepts. The shared tokens adopt the chat page's values (based on a proper WCAG-checked gray scale):

| Token | Lineup (before) | Chat (before) | Shared (after) | Visual impact |
|---|---|---|---|---|
| `--color-text` | `#111` | `#111827` | `#111827` | Imperceptible |
| `--color-surface` | `#f5f5f5` | `#f3f4f6` | `#f3f4f6` | Barely visible |
| `--color-surface-hover` | `#eee` | `#e5e7eb` | `#e5e7eb` | Barely visible |
| `--color-muted` | `#717171` | `#6b7280` | `#6b7280` | Imperceptible |
| `--color-border` | `#e0e0e0` | `#e5e7eb` | `#e5e7eb` | Barely visible |
| `--color-accent` | `#e53e3e` | `#b91c1c` | `#b91c1c` | **Not unified** |

The accent color was intentionally kept separate: the lineup overrides `--color-accent: #e53e3e` in its inline `:root` block because the brighter red is a design choice for heart icons on the lineup page.

Font size scales were also kept separate: the lineup uses em-based values (relative to the browser's 16px default), while the chat uses a px-based scale with a 15px base. Unifying these would cause visible regressions on one or both pages.

## Class name unification

| Before (lineup) | Before (chat) | After (both) |
|---|---|---|
| `.nav-chat-icon` | inline `style="..."` | `.nav-icon` |
| `.hamburger` | `.chat-hamburger` | `.hamburger` |
| `.menu-overlay` | `.chat-menu-overlay` | `.menu-overlay` |

## What was NOT changed

- **Admin page** (`server/chat/admin.html`): Self-contained dark theme CSS, not linked to shared.css. Lower priority, noted for future alignment.
- **JS inline style assignments**: Runtime `element.style.X = ...` calls were not touched (display toggling, positioning, scroll state).
- **Dynamic/DB-driven CSS**: Floor color rules generated from the `stage_colors` dictionary in `render.py` remain inline in the generated HTML.
- **Page-specific component styles**: Artist cards, timetable grid, bio overlay, chat bubbles, input bar, reactions, etc. remain in their respective inline `<style>` blocks.
- **Header container markup**: The lineup uses `.cmd-bar` (28px desktop, 48px mobile) and the chat uses `.header` (always 48px). These are structurally different and were not unified into a single class.

## Verification

### Visual regression testing

Before/after screenshots were captured at 375px (mobile) and 1024px (desktop) viewports for all three views using Playwright:

| Page | Mobile | Desktop |
|---|---|---|
| Lineup | No regression | No regression |
| Timetable | No regression | No regression |
| Chat (login screen) | No regression | No regression |

Screenshots saved to `/tmp/claude-501/before/` and `/tmp/claude-501/after/`.

### Test suite

All 132 tests pass:

| Test file | Tests | Status |
|---|---|---|
| `test_chat_db.py` | 45 | Passed |
| `test_chat_moderation.py` | 39 | Passed |
| `test_chat_ws.py` | 17 | Passed |
| `test_chat_api.py` | 31 | Passed |

### Endpoint verification

- `curl -sk https://localhost:64728/shared.css | head -5` confirms the file is served with correct content
- Admin page (`/chat/admin`) loads unaffected
- Lineup rebuild (`--render-only --no-photos`) succeeds without errors

## Future work

- **Admin page alignment**: Link shared.css from `admin.html` and remove duplicated dark-theme tokens
- **Font scale unification**: Consider whether both pages could share a single type scale (would require visual review of every text element)
- **Accent color unification**: Decide whether the lineup's brighter red should match the chat's darker red, or vice versa
- **More component extraction**: The toast, modal overlay, and form control styles are similar between pages and could be shared with careful refactoring
- **Desktop header**: The lineup's 28px command bar and the chat's sidebar layout are fundamentally different on desktop. Unifying these would require a design decision, not just a CSS refactor.
