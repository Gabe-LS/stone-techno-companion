# CSS Unification Review

## Before you start

1. **Read `CLAUDE.md`** at the project root — it contains the full architecture, file map, database schema, design system docs, and development commands. Read it entirely before proceeding.
2. **Read `review_prompts/js-optimization-review.md`** if it exists — the JS refactor may be running in parallel or already completed. Do not conflict with it.
3. **Check if the server is running** — `lsof -ti :64728` — if it returns a PID, the server is already up. If not, start it:
   ```bash
   cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem
   ```
   Then open `https://localhost:64728/line-up` and `https://localhost:64728/chat`
4. **Playwright and Chromium are already installed** — use them for screenshots and visual comparison. Use `ignore_https_errors=True` on the browser context for localhost HTTPS.
5. **This is a festival companion web app** — a lineup/timetable viewer with an integrated ephemeral chat. Two main user-facing pages served by one FastAPI server. All frontend is vanilla HTML/CSS/JS, no frameworks.
6. **Rebuild the lineup page** after any CSS changes to `scraper/render.py`:
   ```bash
   python stone_techno_companion.py --render-only --no-photos
   ```

## Objective

Audit all CSS across the lineup/timetable page (`scraper/render.py`, inline in the generated `output/lineup.html`) and the chat page (`server/chat/chat.html`, inline `<style>` block) to produce a single shared external CSS file (`server/static/shared.css`) that eliminates duplication, enforces visual consistency, and establishes a unified design system.

## Context

This project has two main user-facing pages that must feel like one app:

1. **Lineup/Timetable** — generated HTML from `scraper/render.py` with all CSS inlined in a `<style>` block. Served at `/line-up` and `/timetable`.
2. **Chat** — `server/chat/chat.html` with all CSS inlined in a `<style>` block. Served at `/chat`.

Both pages share the same mobile header (48px, `background: #111`, hamburger menu, navigation icons) but were built independently. CSS is duplicated, inconsistent, and has no shared design tokens.

There is also an admin page (`server/chat/admin.html`) with its own dark-themed CSS — this is lower priority but should be noted for future alignment.

## What to do

### Phase 0: Understand before touching

Before making ANY changes, you MUST:

1. **Screenshot both pages** at 375px and 1024px viewports using Playwright — lineup, timetable, and chat. Save to `/tmp/claude-501/before/`. These are your visual reference.
2. **Read every single CSS rule** in both pages and document what it does — which elements it targets, what visual effect it produces, and why it exists. Do NOT skip rules you think are obvious.
3. **Map the HTML structure** of both pages — identify every shared component (headers, menus, buttons, pills, modals, toasts) and note the exact class names, nesting, and markup differences.
4. **Identify dependencies** — some CSS rules depend on JS-added classes (`.active`, `.open`, `.expanded`), inline styles set by JS, or specific HTML structures. Document these.
5. **After each change**, re-screenshot and compare with the "before" screenshots. If ANY visual element moved, changed size, changed color, or disappeared unintentionally, STOP and fix before continuing.

The goal is zero visual regression. The pages must look pixel-identical before and after the refactor. The only visible changes should be intentional consistency improvements (e.g., matching a border color that was different between pages).

### Phase 1: Audit

Read every CSS rule in both pages. For each, classify as:

- **Shared** — used by both pages (colors, fonts, spacing, header, menu, buttons, pills, modals)
- **Lineup-only** — artist cards, timetable grid, date sections, sticky headers, bio overlay
- **Chat-only** — message bubbles, input bar, room list, reactions, typing indicator, profile editor

Document all inconsistencies:
- Different values for the same visual concept (e.g., border colors, font sizes, padding)
- Different variable names for the same token
- Duplicated rules that could be shared
- Missing hover/focus/active states on one page but not the other
- Mobile breakpoints that differ

### Phase 1.5: Identify shared UI patterns

Go beyond individual CSS rules — identify complete UI components that appear on both pages and must look and behave identically. For each, document the current markup structure, class names, and CSS on each page, and note every difference.

Known shared patterns (verify and expand this list):

| Pattern | Lineup location | Chat location | What must match |
|---------|----------------|---------------|-----------------|
| **Mobile header bar** | `.cmd-bar` (48px, sticky, `#111`) | `.header` (48px, flex, `#111`) | Height, background, text position, icon positions |
| **Left navigation icon** | `.nav-chat-icon` (absolute, left:4px) | `a[href="/line-up"]` (inline styles, margin-left) | Position, size, color, viewBox, tap target |
| **Hamburger icon** | `.hamburger` (absolute, right:4px) | `.chat-hamburger` (absolute, right:4px) | Position, size, color, SVG, hover behavior |
| **Dropdown/slide menu** | `.cmd-dropdown` (fixed, top:48px) | `.chat-menu` (fixed, top:48px) | Background, item padding, font size/weight/color, border color, row height |
| **Menu items** | `.cmd-dropdown button` | `.room-item .room-name` | Font: 13px/600, color #fff, padding, line-height |
| **Pills/badges** | (if any) | `.pill`, `.pill-red`, `.pill-amber`, etc. | Colors, border-radius, font-size, padding |
| **Buttons** | `.cmd-bar button` | `.btn`, `.auth-btn` | Consistent base styling |
| **Toast notifications** | (if any) | `.toast` | Position, animation, styling |
| **Modal overlays** | `.modal-box` | (profile prompt, meetup modal) | Overlay color, content box styling |

For each pattern: the refactored version must use ONE set of class names and ONE set of CSS rules in `shared.css`. Both pages must produce identical markup for these components. If the markup currently differs (e.g., `.cmd-bar` vs `.header`), choose the better structure and update both pages to match.

### Phase 2: Design the shared CSS file

Create `server/static/shared.css` containing:

1. **CSS Reset** — minimal, just what both pages need
2. **Design Tokens** (CSS custom properties on `:root`):
   - Colors: background, surface, text (primary/secondary/muted), border, accent, semantic (red/amber/orange/green/blue)
   - Typography: font family, font sizes (scale from xxs to 2xl), font weights, line heights
   - Spacing: 4px scale (xs through 2xl)
   - Borders: radius values, border colors
   - Shadows: if any
   - Transitions: standard duration
3. **Base Styles** — html, body, *, links, buttons, inputs, selects
4. **Shared Components**:
   - `.header` — the 48px mobile/desktop header bar
   - `.hamburger` / `.nav-icon` — header navigation icons
   - `.menu` / `.menu-item` — dropdown/slide menus
   - `.pill` — status/tag pills (with color variants)
   - `.btn` — buttons (with color variants: red, amber, orange, green, blue, neutral)
   - `.modal` — modal overlay and content
   - `.toast` — toast notifications
   - Form controls — unified input, select, button, toggle styles
5. **Utility Classes** — `.truncate`, `.sr-only`, etc.
6. **Media Queries** — shared breakpoints

### Phase 3: Refactor both pages

- Replace inline CSS in both pages with `<link rel="stylesheet" href="/shared.css">`
- Keep only page-specific CSS inline (or in page-specific `<style>` blocks)
- Ensure both pages use the same class names for shared components
- Update the hamburger menu in `scraper/render.py` to use the same classes as `chat.html`
- Update header rendering in both pages to use identical markup structure

### Phase 4: Verify

- Run `python stone_techno_companion.py --render-only --no-photos` to rebuild lineup
- Restart the server: `kill -9 $(lsof -ti :64728); cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem`
- **Screenshot both pages** at 375px and 1024px using Playwright. Save to `/tmp/claude-501/after/`. Compare pixel-by-pixel with the `/tmp/claude-501/before/` screenshots. Document any differences.
- Test on mobile (375px viewport):
  - Header looks identical on both pages
  - Menu looks identical on both pages
  - Navigation icons match in size, color, position
  - Font sizes and weights are consistent
  - Colors match
  - All interactive states work (see "Interactive state testing" section)
- Test on desktop (1024px+):
  - Command bar and nav elements align
  - Typography scale is consistent
- Run `python -m pytest tests/ -v` to ensure nothing broke
- Verify the admin page still works (it has its own CSS, should be unaffected)
- Verify `shared.css` is served correctly: `curl -sk https://localhost:64728/shared.css | head -5`

### Serving shared.css

The file lives at `server/static/shared.css`. It must be served by the FastAPI server. Check if the existing static file routes in `server/api.py` already cover files in `server/static/` — if not, add an explicit route:

```python
@app.get("/shared.css")
async def serve_shared_css():
    return FileResponse(STATIC_DIR / "shared.css", media_type="text/css")
```

## Key files to read

| File | What it contains |
|------|-----------------|
| `scraper/render.py` | All lineup/timetable CSS (inline in generated HTML). Search for the `<style>` block starting around line 230. Mobile overrides at `@media (max-width: 480px)`. |
| `server/chat/chat.html` | All chat CSS (inline `<style>` block at the top, lines 8-370). CSS variables defined in `:root`. |
| `server/chat/admin.html` | Admin page CSS (separate dark theme, lines 8-120). Lower priority. |
| `output/lineup.html` | Generated output — read this to see the actual rendered CSS and HTML structure. |
| `CLAUDE.md` | Project documentation, design system notes, color system, font scale. |

## Git workflow

Work on the `chat-prototype` branch (already checked out). Commit incrementally after each phase — never batch the entire refactor into one commit.

1. **After Phase 0** (screenshots + audit): commit the audit document if you write one, or just proceed
2. **After creating `shared.css`**: commit the new file alone before touching either page — `"Add shared.css design system with unified tokens and components"`
3. **After refactoring the lineup page**: commit — `"Refactor lineup/timetable CSS to use shared.css"`. Verify screenshots match before committing.
4. **After refactoring the chat page**: commit — `"Refactor chat CSS to use shared.css"`. Verify screenshots match before committing.
5. **After any fixes**: separate commit per fix

This way if a refactor step breaks something, we can revert that single step without losing everything. Never amend previous commits. Never force push.

## CSS file organization

`shared.css` must be well-structured with clear section separation:

```
/* ===== TOKENS ===== */
:root { ... }

/* ===== RESET ===== */
*, *::before, *::after { ... }

/* ===== BASE ===== */
html, body { ... }
a { ... }
button, input, select { ... }

/* ===== COMPONENTS ===== */
/* --- Header --- */
.header { ... }

/* --- Navigation icons --- */
.nav-icon { ... }

/* --- Menu --- */
.menu { ... }

/* --- Pills --- */
.pill { ... }

/* --- Buttons --- */
.btn { ... }

/* --- Modal --- */
.modal { ... }

/* --- Toast --- */
.toast { ... }

/* ===== UTILITIES ===== */
.truncate { ... }

/* ===== MEDIA QUERIES ===== */
@media (max-width: 480px) { ... }
@media (hover: hover) { ... }
```

Each section starts with a clear comment header. Within sections, related rules are grouped together. No blank-line chaos — consistent spacing between rules.

## CSS Variables

Every hardcoded value that appears more than once or represents a design decision MUST use a CSS custom property. This includes:

- All colors (backgrounds, text, borders, accents) — no raw hex/rgb values in component styles
- All font sizes — reference the type scale, never hardcode `13px` or `16px` directly
- All spacing values — reference the spacing scale
- All border-radius values
- All transition durations
- All z-index values (define a z-index scale: `--z-header`, `--z-modal`, `--z-dropdown`, etc.)
- All box-shadow values
- Header height (`--header-h: 48px`) — used in sticky top calculations, menu positioning, etc.

The only place raw values should appear is in the `:root` token definitions. Every component rule should reference tokens. If a value is used once and is truly unique, it can stay raw — but justify it.

## Handling the lineup page generation

The lineup/timetable CSS lives inside `scraper/render.py` as Python string concatenation. This means:

- CSS rules are built with f-strings and `.append()` calls, not a standalone CSS file
- Some CSS values are dynamic (e.g., floor colors generated from DB data)
- The generated `output/lineup.html` references `shared.css` via `<link>` — it depends on the FastAPI server to serve it

**Approach for the lineup page:**
- Move all static/shared CSS out of `render.py` into `shared.css`
- Keep dynamic CSS inline in a small `<style>` block in the generated HTML — this is ONLY for values generated from the database at build time (e.g., per-stage floor colors as `rgba()` values). These change per event and do not belong in the shared file.
- Add `<link rel="stylesheet" href="/shared.css">` to the generated HTML
- The inline `<style>` block should be minimal — ideally just CSS custom property overrides or a few generated rules, not component styles

## Inline styles set by JS

Both pages set inline styles via JavaScript (`element.style.X = ...`). These include:

- `display: none / ''` for show/hide (menus, views, modals)
- `top` values for sticky header calculations (`setStickyTops()` in lineup)
- `visibility: hidden / ''` for initial page load
- Dynamic positioning (dropdown menus, tooltips)

**Rule:** Do NOT refactor JS-set inline styles into CSS classes unless there's a clear benefit. These are runtime state changes and belong in JS. Focus on the `<style>` blocks only.

## Interactive state testing

After each phase, manually test (or script via Playwright) these interactive states:

- **Lineup**: hamburger menu open/close, view switch (lineup/timetable), filter toggle, bio modal open/close, timetable popup, scroll with sticky headers
- **Chat**: login flow, room switching, hamburger menu open/close, message sending, reply/reaction UI, meetup modal, profile settings menu, action menus (long press / right click)
- **Both**: navigation between pages via the header icons, page reload preserving state

## Color palette

There is one theme — no dark/light mode switching. The design uses:

- Light page backgrounds (lineup: white/light gray, chat: white)
- Dark header and menus (`#111`)
- White text on dark surfaces, dark text on light surfaces
- Semantic accent colors (red, amber, orange, green, blue)

The shared tokens must define a single coherent palette that works for both pages. No `prefers-color-scheme` media queries.

## Specificity management

- Shared CSS should use low-specificity selectors (single class: `.header`, `.pill`, `.btn`)
- Page-specific overrides (in inline `<style>` blocks) can use higher specificity if needed
- Avoid `!important` — if you need it, the architecture is wrong
- Avoid deep nesting (`.header .nav .icon svg`) — keep selectors flat
- Use BEM-like naming for variants: `.btn--red`, `.pill--amber` (or `.btn-red`, `.pill-amber` to match existing conventions)

## Performance

Both pages will reference `shared.css` via `<link>`. The file should be small (< 10KB) and will be cached by the browser. This is one additional HTTP request per page load — acceptable.

## Cleanup

This codebase has accumulated CSS workarounds, band-aids, and leftovers from iterative development. As part of this refactor:

- **Remove dead CSS** — rules targeting elements that no longer exist in the HTML. Grep every class name and ID in the CSS against the HTML to verify it's still used.
- **Remove redundant overrides** — rules that cancel out earlier rules (e.g., setting a property then immediately overriding it in a more specific selector). Consolidate into one correct rule.
- **Remove workaround comments** — if a workaround exists because the underlying structure was wrong, fix the structure instead of keeping the workaround.
- **Remove duplicate rules** — same property/value appearing in multiple selectors that could be merged.
- **Remove stale media query overrides** — mobile overrides that override values that no longer exist in the desktop rule, or that set the same value as the desktop rule.
- **Remove unused CSS variables** — variables defined in `:root` but never referenced.
- **Simplify overly specific selectors** — `.cmd-bar .cmd-group .cmd-group-right button:not(.hamburger):hover` should not exist. If it does, the architecture is wrong.
- **Remove inline styles that should be CSS** — any `style="..."` attribute in the HTML that sets static (non-JS-driven) properties should be moved to CSS classes.
- **Flag but do not remove** anything you're unsure about — add a `/* TODO: verify if still needed */` comment and list it in your commit message.

## Constraints

- No emojis in code, comments, or generated files
- No CSS frameworks or preprocessors — plain CSS only
- The shared CSS file must be served via the existing static file routes in `server/api.py`
- Both pages are served by the FastAPI server which handles static files
- Do not break any existing functionality — this is a CSS refactor, not a feature change
- Preserve all existing responsive behavior
- The admin page CSS is self-contained and should NOT be merged into shared.css
- Do not refactor JS inline style assignments — focus on `<style>` blocks only
- Keep dynamic/DB-driven CSS values inline in the generated HTML
- Avoid `!important`
- Keep selector specificity low in shared CSS
