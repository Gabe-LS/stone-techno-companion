# CSS Unification Phase 2: Font Scale, Component Extraction

## Before you start

1. **Read `CLAUDE.md`** at the project root — it contains the full architecture, file map, database schema, design system docs, and development commands. Read it entirely before proceeding.
2. **Read `review_reports/css-unification.md`** — this is the Phase 1 report. It documents everything that was already done: what was extracted to `shared.css`, what was renamed, what was tokenized, and what was intentionally kept separate. You MUST understand this before making any changes.
3. **Read `server/static/shared.css`** — the shared CSS file created in Phase 1. This is where your new extractions will go.
4. **Check if the server is running** — `lsof -ti :64728` — if it returns a PID, the server is already up. If not, start it:
   ```bash
   cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem
   ```
   Then open `https://localhost:64728/line-up` and `https://localhost:64728/chat`
5. **Playwright and Chromium are already installed** — use them for screenshots and visual comparison. Use `ignore_https_errors=True` on the browser context for localhost HTTPS.
6. **This is a festival companion web app** — a lineup/timetable viewer with an integrated ephemeral chat. Two main user-facing pages served by one FastAPI server. All frontend is vanilla HTML/CSS/JS, no frameworks.
7. **Rebuild the lineup page** after any CSS changes to `scraper/render.py`:
   ```bash
   python stone_techno_companion.py --render-only --no-photos
   ```

## Context

Phase 1 was completed in a previous session (commits `c8117a4`, `f64451e`, `9e7b9ab`). It created `server/static/shared.css` with design tokens and shared components, refactored both pages to use it, and documented the work in `review_reports/css-unification.md`.

This is Phase 2. You're working on the same codebase, same branch (`chat-prototype`). Read the Phase 1 report to understand what was done, what was deferred, and why.

## What to do

### 1. Font Scale Unification

The lineup uses an em-based font scale, the chat uses a px-based scale. They need to be unified into one scale in `shared.css`.

**Current state:**

Lineup (`scraper/render.py` inline `:root`):
```
--font-2xl: 2em
--font-xl: 1.5em
--font-lg: 1.125em
--font-base: 1em
--font-sm: 0.875em
--font-xs: 0.75em (min 12px)
```

Chat (`server/chat/chat.html` inline `:root`):
```
--font-xl: 18px
--font-lg: 16px
--font-base: 15px
--font-sm: 13px
--font-xs: 12px
--font-xxs: 10px
```

**Approach:**

1. Screenshot every text element on both pages at 375px and 1024px — headlines, body text, labels, buttons, pills, timestamps, muted text. Document the actual computed `font-size` in px for each.
2. Design a single px-based scale in `shared.css` that preserves the current rendered sizes on both pages. The lineup currently renders at the browser's default 16px base, so `1em = 16px`, `0.875em = 14px`, etc. Map these to the unified scale.
3. Move the unified scale to `shared.css` `:root`. Remove the per-page scales.
4. Update every `var(--font-*)` reference on both pages to use the unified token names.
5. Screenshot again and compare. The chat page WILL look slightly different after this — that's the point. It's being brought into alignment with the lineup/timetable design, not preserved as-is.

**Priority: consistency with lineup/timetable, not preserving the chat's current pixel values.** The lineup/timetable pages are the design reference. The chat must adapt to match them, not the other way around. The user experience of feeling like one cohesive app is more important than any individual element staying exactly the same size.

**Approach:** Use the lineup's em-based scale as the single source of truth. The chat's px-based scale is being retired. Both pages use the browser's default 16px base. Convert chat values to the nearest em token in the lineup's scale — do NOT invent new in-between values to preserve chat's exact sizes.

**The unified scale should be em-based**, defined once in `shared.css`. Both pages use the same tokens. If a specific element genuinely needs a size outside the scale, justify it with a comment.

**Guardrail:** Map each chat element to the nearest scale token, not an arbitrary one. A text that's currently 16px maps to `--font-base` (1em/16px), not to `--font-sm` (0.875em/14px). The jump should never be more than one step in the scale. If an element would jump two or more steps, something is wrong with the mapping — re-evaluate.

### 2. Component Extraction

The Phase 1 report identified these components as candidates for extraction into `shared.css`. For each one:

1. Compare the current implementation on both pages
2. If they're similar enough, extract to `shared.css` with a single class name
3. If they differ in important ways, document why and leave them separate

#### 2.1 Toast Notifications

| | Lineup | Chat |
|---|---|---|
| Element | Check if exists | `.toast` |
| Position | ? | Fixed bottom-center |
| Animation | ? | Fade in/out |
| Duration | ? | Word-based (1.5s + 300ms/word, min 4s) |
| Max width | ? | 360px |

If the lineup has a toast, unify the styles. If not, move the chat's toast CSS to `shared.css` so the lineup can use it if needed in the future.

#### 2.2 Modal Overlays

| | Lineup | Chat |
|---|---|---|
| Overlay | `.modal-overlay` | `.modal-overlay` (profile, meetup, image viewer) |
| Content box | `.modal-box` | Various inner containers |
| Close behavior | Escape key, click outside | Escape key, click outside |
| Background | Dark semi-transparent | Dark semi-transparent |

Extract the shared overlay + backdrop pattern. Keep page-specific modal content styles inline.

#### 2.3 Form Controls

| | Lineup | Chat |
|---|---|---|
| Inputs | Check if any | `input`, `select`, `button` (auth screen, profile, message input) |
| Buttons | `.cmd-bar button` | `.btn`, `.auth-btn` |
| Style | ? | Consistent padding, border-radius, font |

If both pages have form controls, unify base styles (padding, border, border-radius, font-size, background, focus state). Keep variant styles (colors, sizes) page-specific.

#### 2.4 Pills/Badges

| | Lineup | Chat |
|---|---|---|
| Element | Check if any | `.pill`, `.pill-red`, `.pill-amber`, etc. |
| Usage | ? | Status indicators, room type labels |

If the lineup uses pill-like elements, unify with the chat's pill system.

## Rules

- **Screenshot before and after every change.** Compare. Zero visual regression.
- **Commit after each sub-task** (font scale, desktop header, each component). Do not batch.
- **If unifying a component would cause visual differences**, document the trade-off and ask before proceeding. Do NOT silently change visual appearance.
- **Test interactive states** — modals open/close, toasts appear/disappear, buttons hover/focus/active.
- **Run `python -m pytest tests/ -v`** after each change.
- **Rebuild the lineup** after changes to `scraper/render.py`: `python stone_techno_companion.py --render-only --no-photos`
- **Restart the server** after changes to served files: `kill -9 $(lsof -ti :64728); cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem`

## Write a report

When done, write a report to `review_reports/css-unification-phase2.md` following the same format as your Phase 1 report. Include:
- What was unified and how
- What was intentionally kept separate and why
- Token mapping tables (before → after)
- Class name changes
- Visual regression test results
- Future work remaining
