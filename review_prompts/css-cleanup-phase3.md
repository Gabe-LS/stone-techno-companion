# CSS Cleanup Phase 3: Optimize Inline CSS

## Before you start

1. **Read `CLAUDE.md`** at the project root — full architecture, file map, design system docs.
2. **Read `review_reports/css-unification.md`** and **`review_reports/css-unification-phase2.md`** — understand what Phases 1 and 2 already did. Phase 1 extracted shared tokens and components to `shared.css`. Phase 2 unified the font scale and extracted the toast component.
3. **Read `server/static/shared.css`** — the shared CSS file. Know what tokens and components are already there.
4. **Check if the server is running** — `lsof -ti :64728` — if it returns a PID, it's up. If not, start it:
   ```bash
   cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem
   ```
5. **Playwright and Chromium are already installed** — use `ignore_https_errors=True` on the browser context.
6. **This is a festival companion web app** — lineup/timetable + ephemeral chat. Vanilla HTML/CSS/JS, no frameworks.
7. **Rebuild the lineup** after changes to `scraper/render.py`:
   ```bash
   python stone_techno_companion.py --render-only --no-photos
   ```
8. **A JS optimization review may be running in parallel or already completed.** Check `review_reports/js-optimization.md` if it exists. The JS review may have renamed CSS classes in JS selectors or restructured HTML templates. If you see class names in CSS that don't match the HTML, check whether the JS review already renamed them before assuming they're dead.
9. **The admin page** (`server/chat/admin.html`) is out of scope for this cleanup. Do not touch it.

## Objective

Phases 1 and 2 extracted shared CSS into `shared.css` but did NOT touch the inline CSS that remained in each page. The inline `<style>` blocks in `scraper/render.py` (lineup/timetable) and `server/chat/chat.html` (chat) still contain:

- Dead rules targeting elements that no longer exist
- Redundant overrides that cancel out earlier rules
- Workarounds and hacks from iterative development
- Inconsistent naming (class names, variable names)
- Overly specific selectors
- Duplicate rules that do the same thing
- Stale media query overrides
- Inline `style="..."` attributes that should be CSS classes
- Properties that could use shared tokens but still have hardcoded values

This phase cleans up and optimizes the inline CSS in each page WITHOUT changing the visual output.

## What to do

### Step 0: Screenshot reference

1. **Screenshot both pages** at 375px and 1024px using Playwright — lineup, timetable (switch view), and chat (login screen + chat room if possible). Save to `/tmp/claude-501/phase3-before/`.
2. Also screenshot interactive states: hamburger menu open, a modal open, timetable view.

### Step 1: Audit lineup/timetable CSS

Read every CSS rule inside `scraper/render.py` (the `<style>` block and any inline styles in the HTML generation). For each rule:

1. **Is it used?** — grep the class/ID against the generated HTML in `output/lineup.html`. If the element doesn't exist, the rule is dead. Remove it.
2. **Is it redundant?** — does it set a property that's already set by a more general rule or by `shared.css`? Remove the duplicate.
3. **Is it a workaround?** — does it exist to fix a problem caused by another bad rule? Fix the root cause and remove both.
4. **Could it use a shared token?** — any hardcoded color, font-size, spacing, radius, z-index, or shadow that matches a token in `shared.css` should reference the token instead. Check: `#111`, `#222`, `#333`, `#aaa`, `#fff`, `16px`, `48px`, `0.15s`, etc.
5. **Is the selector too specific?** — `.cmd-bar .cmd-group .cmd-group-right button:not(.hamburger):hover` is too deep. Simplify.
6. **Is the class name consistent?** — all classes should be kebab-case. No camelCase, no underscores.
7. **Are media query overrides clean?** — does the mobile override set the same value as the desktop rule? Remove it. Does it override a value that no longer exists in the desktop rule? Remove it. Is there an empty rule block? Remove it.
8. **Are there inline `style="..."` attributes** in the Python HTML generation that set static properties? Move them to CSS classes.

### Step 2: Audit chat CSS

Same process for `server/chat/chat.html`. Read every CSS rule in the `<style>` block. Same checklist as above, plus:

1. **Chat-specific**: the chat has ~300 lines of CSS with many component styles (messages, reactions, input bar, profile editor, rooms, meetups, etc.). Each component section should be clearly separated with comments.
2. **CSS variable usage**: the chat previously defined its own `:root` tokens which were moved to `shared.css`. Check that no orphaned references remain (variables that were removed but are still referenced).
3. **Color consistency**: verify all colors used in the inline CSS either reference shared tokens or are intentionally page-specific (like the 12 user color pairs). No orphaned hex values that should be tokens.
4. **Semi-identical colors**: look for hex values that are very close to a shared token but not exact (e.g., `#333` vs `var(--gray-700)` which is `#374151`, or `#999` vs `var(--color-muted)` which is `#6b7280`). If the visual difference is imperceptible, replace with the shared token. If the difference is intentional, document why.

### Step 3: Organize and document

After cleanup, each page's inline CSS should be well-organized:

```css
/* ===== PAGE-SPECIFIC TOKENS ===== */
:root { /* only tokens not in shared.css */ }

/* ===== LAYOUT ===== */
/* page structure, grid, flex containers */

/* ===== COMPONENTS ===== */
/* each component separated by a comment */
/* --- Component Name --- */

/* ===== STATES ===== */
/* .active, .open, .expanded, etc. */

/* ===== MEDIA QUERIES ===== */
@media (...) { /* all overrides grouped here, not scattered */ }
```

If media query overrides are currently scattered throughout the CSS (rule, then its mobile override, then next rule, then its mobile override), consolidate them into one `@media` block at the end. **Warning:** Moving a media query rule changes its position in the cascade. If two rules have the same specificity, the later one wins. Before consolidating, verify that no rule depends on its position relative to other rules. Test after consolidating — if anything breaks, keep the scattered structure for those specific rules.

### Step 4: Verify

1. Rebuild lineup: `python stone_techno_companion.py --render-only --no-photos`
2. Restart server: `kill -9 $(lsof -ti :64728); cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem`
3. **Screenshot both pages** at 375px and 1024px. Save to `/tmp/claude-501/phase3-after/`. Compare with phase3-before. Zero visual regression.
4. **Test interactive states**: menus, modals, view switching, scroll behavior, sticky headers.
5. Run `python -m pytest tests/ -v` — all tests pass.
6. Check browser console — zero errors from our CSS (no `unknown property`, no `invalid value`).

## Git workflow

Work on the `chat-prototype` branch. Commit incrementally:

1. **After lineup cleanup**: commit — `"Clean up and optimize lineup/timetable inline CSS"`. Verify screenshots match before committing.
2. **After chat cleanup**: commit — `"Clean up and optimize chat inline CSS"`. Verify screenshots match before committing.
3. **After organization pass** (if separate): commit — `"Organize inline CSS with section comments and consolidated media queries"`

Never amend previous commits. Never force push.

## Rules

- **Zero visual regression** — the pages must look identical before and after. This is cleanup, not redesign.
- **If unsure whether a rule is needed, keep it** and add `/* TODO: verify if still needed */`. Do NOT remove rules you're not sure about.
- No emojis in code or comments.
- **Lineup CSS lives inside Python strings** in `scraper/render.py`. Be careful with escaping — single quotes, backslashes, and f-string braces have special meaning in Python. Test the build after every change: `python stone_techno_companion.py --render-only --no-photos`

## Specific things to look for

### Dead CSS patterns

- Rules for elements that were removed during previous refactors (e.g., Apple auth button styles, old menu classes)
- Hover rules guarded by `@media (hover: hover)` for elements that no longer exist
- Keyframe animations that are never referenced
- Font-face declarations that are unused

### Workaround patterns

- `margin-left: calc(-1 * var(--space-lg))` — is this still needed or was it a positioning hack?
- `overflow: hidden` on containers just to clear floats (no floats exist)
- `position: relative` on elements only to create a stacking context for a child that was removed
- `-webkit-` prefixed properties — check each against the browser targets (Safari 15.4+, Chrome 90+, Firefox 90+). Keep `-webkit-tap-highlight-color` (Safari-only), `-webkit-overflow-scrolling` (still needed for iOS momentum scroll). Remove prefixed versions of properties that are unprefixed in all targets (e.g., `-webkit-appearance` can be just `appearance` in Safari 15.4+). When in doubt, keep both the prefixed and unprefixed version.

### Tokenization opportunities

Every hardcoded value that matches a `shared.css` token should reference it:

Do NOT guess which token a hardcoded value maps to. **Read `shared.css` first** and use the actual token names and values defined there. For each hardcoded value in inline CSS, search `shared.css` for a matching token. Only replace if the value matches exactly or is semantically equivalent. Examples of what to look for:

- Hardcoded colors → `var(--color-*)` or `var(--gray-*)`
- Hardcoded `48px` for header height → `var(--header-h)`
- Hardcoded spacing (4px, 8px, 12px, 16px, 24px, 32px) → `var(--space-*)`
- Hardcoded border-radius → `var(--radius-*)`
- Hardcoded `0.15s` transitions → `var(--transition-fast)`
- Hardcoded z-index → `var(--z-*)`
- Hardcoded shadows → `var(--shadow-*)`

If a value doesn't match any existing token and appears multiple times, consider adding a new page-specific token in the inline `:root` block — but only if it's used 3+ times.

## Write a report

When done, write a report to `review_reports/css-cleanup-phase3.md`. Include:
- Number of rules removed per page (dead, redundant, duplicate)
- Number of values tokenized
- Workarounds removed and what they were fixing
- Inline styles moved to classes
- Any rules flagged with TODO
- Before/after line counts for each page's inline CSS
