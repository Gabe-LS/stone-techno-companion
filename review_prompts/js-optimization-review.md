# JavaScript Optimization Review

## Before you start

1. **Read `CLAUDE.md`** at the project root — it contains the full architecture, file map, database schema, design system docs, and development commands. Read it entirely before proceeding.
2. **Read `review_prompts/css-unification-review.md`** if it exists — the CSS refactor may be running in parallel or already completed. Do not conflict with it.
3. **Check if the server is running** — `lsof -ti :64728` — if it returns a PID, the server is already up. If not, start it:
   ```bash
   cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem
   ```
   Then open `https://localhost:64728/line-up` and `https://localhost:64728/chat`
4. **Playwright and Chromium are already installed** — use them for screenshots and automated testing. Use `ignore_https_errors=True` on the browser context for localhost HTTPS.
5. **This is a festival companion web app** — a lineup/timetable viewer with an integrated ephemeral chat. Two main user-facing pages served by one FastAPI server. All frontend is vanilla HTML/CSS/JS, no frameworks.

## Objective

Audit all JavaScript across the lineup/timetable page (`scraper/render.py`, inline `<script>` blocks in generated HTML) and the chat page (`server/chat/chat.html`, inline `<script>` block) to optimize, standardize, and consolidate JS usage. Produce a shared external JS file (`server/static/shared.js`) for common utilities, and refactor page-specific JS for consistency, performance, and browser compatibility.

## Context

This project has two main user-facing pages:

1. **Lineup/Timetable** — generated HTML from `scraper/render.py` with all JS inlined in `<script>` blocks. Served at `/line-up` and `/timetable`. Heavy use of vanilla JS for view switching, filtering, sticky headers, modals, scroll position management, service worker, push notifications, timetable rendering, QR codes.
2. **Chat** — `server/chat/chat.html` with all JS inlined in a `<script>` block. Served at `/chat`. Heavy use of vanilla JS for WebSocket management, room switching, message rendering, reactions, replies, media upload, video processing, profile editor, push notifications, Google OAuth.

There is also an admin page (`server/chat/admin.html`) — lower priority but should be noted.

Both pages were built independently. JS patterns, naming conventions, utility functions, and browser API usage differ significantly.

## What to do

### Phase 0: Understand before touching

Before making ANY changes:

1. **Test every interactive feature** on both pages using Playwright at 375px and 1024px. Record the current behavior as your reference. If you can, create a GIF or screenshot sequence of key interactions.
2. **Read every single JS function** in both pages. Document what it does, what triggers it, and what DOM elements it touches. Do NOT skip functions you think are obvious.
3. **Map all event listeners** — click, touch, keydown, resize, scroll, popstate, WebSocket events, service worker events. Document which element, which handler, and whether it uses capture/passive.
4. **Identify all shared patterns** — functions that do the same thing on both pages (e.g., `esc()` for HTML escaping, `ago()` for relative timestamps, `dbg()` for debug logging, toast notifications, modal management, push subscription handling).
5. **After each change**, re-test the affected features. If ANY behavior changed unintentionally, STOP and fix before continuing.

The goal is zero behavioral regression. Every interaction must work exactly as before.

### Phase 1: Audit

For every JS function and code block in both pages, evaluate:

#### 1.1 Can CSS replace it?

Identify JS that does what CSS can do natively. Common cases:
- **Show/hide via `element.style.display`** — could use CSS class toggling with `.hidden { display: none }`
- **Hover effects set by JS** — should be CSS `:hover`
- **Transitions/animations triggered by JS** — should be CSS `transition` with class toggling
- **Scroll behavior** — `scroll-behavior: smooth` in CSS vs JS `scrollTo`
- **Sticky positioning** — already CSS `position: sticky`, but JS currently calculates `top` values (this may need to stay in JS due to dynamic stacking)
- **Text truncation** — CSS `text-overflow: ellipsis` vs JS truncation
- **Focus management** — CSS `:focus-visible` vs JS focus handlers

**Rule:** If CSS can achieve the same result with equal or better browser support, replace the JS. If the JS handles edge cases CSS cannot (e.g., iOS Safari quirks, dynamic values), keep it but document why.

#### 1.2 Is it optimal?

For each function, check:
- **DOM queries** — is it querying the DOM repeatedly when it could cache the reference? (`document.querySelector` in a loop)
- **Event listeners** — are there listeners that should use event delegation instead of per-element binding?
- **Reflows/repaints** — does it read layout properties (offsetHeight, getBoundingClientRect) then write styles in a loop? Batch reads before writes.
- **Memory leaks** — are event listeners removed when elements are destroyed? Are intervals/timeouts cleared?
- **Unnecessary work** — is it recalculating things that haven't changed? Could results be cached?
- **String concatenation for HTML** — is it building HTML with string concatenation where `template literals` are cleaner? Are there XSS risks from unescaped user input?
- **Error handling** — are `try/catch` blocks too broad? Are errors silently swallowed?
- **Async patterns** — are there `await` calls that could run in parallel with `Promise.all`? Are there blocking synchronous operations?

#### 1.3 Is it compatible?

Target: **95%+ global browser support** (caniuse.com baseline). This means:
- Safari 15.4+ (iOS 15.4+)
- Chrome 90+
- Firefox 90+

Check every API used against this baseline:
- `fetch` — OK
- `WebSocket` — OK
- `async/await` — OK
- `?.` optional chaining — OK (Safari 13.1+)
- `??` nullish coalescing — OK (Safari 13.1+)
- `structuredClone` — check (Safari 15.4+)
- `Intl.RelativeTimeFormat` — check
- `ResizeObserver` — OK (Safari 13.1+)
- `IntersectionObserver` — OK
- `createImageBitmap` — check Safari support
- `WebCodecs` — NOT widely supported, needs fallback check
- `MediaRecorder` — check Safari support
- `Notification API` — OK but check iOS PWA behavior
- `Push API` — check iOS Safari (16.4+ only)
- `Cache API` — OK
- `crypto.randomUUID()` — check (Safari 15.4+)
- `AbortController` — OK
- `URL`, `URLSearchParams` — OK
- `flatMap`, `Object.fromEntries` — OK

For any API below 95% support: document it, check if it has a fallback in the code, add one if missing.

#### 1.4 Is the naming consistent?

Document all naming conventions currently used, then standardize:

| Category | Convention | Examples |
|----------|-----------|---------|
| Functions | camelCase | `renderAuth()`, `loadUsers()`, `switchView()` |
| Variables | camelCase | `currentRoom`, `messagesByRoom` |
| Constants | UPPER_SNAKE | `DEFAULT_MESSAGE_TTL_MIN`, `API` |
| Private/internal | underscore prefix | `_routing`, `_toastTimer`, `_wsConnecting` |
| DOM element refs | camelCase | `msgInput`, `roomList` |
| Event handlers | `on` prefix or `handle` prefix | `onUserSearch()`, `handleWSEvent()` |
| Boolean vars | `is`/`has`/`should` prefix | `isMobile`, `hasStarted`, `_pushSubscribed` |
| CSS class toggling | verb-based | `addClass`, `toggleClass` |

Check for inconsistencies:
- Mixed naming styles (e.g., `filterActive` vs `_pushSubscribed` for similar concepts)
- Ambiguous names (e.g., `data`, `result`, `item` without context)
- Abbreviations that aren't obvious (e.g., `vl` for view-label, `tt` for timetable)
- Single-letter variables outside short loops
- Function names that don't describe what they do

### Phase 1.5: Identify shared JS patterns

Functions and utilities that exist on both pages and should be consolidated into `shared.js`:

| Utility | Lineup version | Chat version | Notes |
|---------|---------------|--------------|-------|
| HTML escaping | `esc()` (different impl?) | `esc()` | Must handle same edge cases |
| Relative time | verify if exists | `fmtTime()`, `ago()` (admin) | Check both pages |
| Debug logging | verify if exists | `dbg()`, `verify()` | With `[tag]` prefix and timestamps |
| Toast notification | verify if exists | `showToast()` | Duration calculation, word-based timing |
| Local storage | `localStorage.getItem/setItem` | `localStorage.getItem/setItem` | Wrapper with error handling |
| Cookie handling | verify if exists | Cookie parse for session | |
| Service worker registration | `navigator.serviceWorker.register` | Same | |
| Push subscription | Push subscribe/unsubscribe flow | Similar flow | VAPID key handling |
| Modal management | verify if exists | Open/close modal patterns | Focus trapping, scroll lock, escape key |
| Scroll management | `_viewScrollPos` | Scroll to bottom, scroll position save | |
| Fetch wrapper | verify if exists | `api()` with error handling | |
| Device detection | `isMobile = window.innerWidth < 768` | Same pattern | |

For each: choose the better implementation, put it in `shared.js`, and have both pages use it.

### Phase 2: Create shared.js

Create `server/static/shared.js` containing shared utilities. Structure:

```javascript
/* ===== SHARED UTILITIES ===== */

/* --- Debug --- */
const _t0 = performance.now();
function _ts() { ... }
function dbg(...args) { ... }
function verify(label, condition, detail) { ... }

/* --- DOM --- */
function esc(s) { ... }

/* --- Time --- */
function ago(iso) { ... }
function fmtTime(iso) { ... }

/* --- Toast --- */
function showToast(msg, duration) { ... }

/* --- Storage --- */
function storageGet(key, fallback) { ... }
function storageSet(key, value) { ... }

/* --- Device --- */
function checkMobile() { return window.innerWidth < 768; }
```

**Rules:**
- Minimal side effects on load — only passive setup like `isMobile` detection. No DOM manipulation of specific page elements.
- Each function must be self-contained and documented with a one-line comment
- No dependencies between shared utilities (each works standalone)
- Page-specific code stays in the page's inline `<script>` block
- Do NOT introduce shorthand helpers like `$` / `$$` for `querySelector` — they conflict with common libraries and are cryptic. Use `document.querySelector` directly.
- `shared.js` should NOT use `'use strict'` at the top level — it would apply to all inline scripts that follow in some browsers. If strict mode is desired, wrap individual functions.
- Do NOT wrap `shared.js` in an IIFE or module pattern — the utility functions must be globally accessible to both inline scripts.

**Load order:**
- `<script src="/shared.js"></script>` — loaded synchronously in `<head>` or before the inline `<script>` block. No `defer` or `async` — the inline scripts depend on these utilities being available immediately.
- Inline `<script>` — page-specific code, runs after `shared.js` is loaded

### Phase 3: Refactor both pages

- Add `<script src="/shared.js"></script>` to both pages (before the inline script)
- Remove duplicated utility functions from inline scripts
- Standardize naming across both pages
- Replace JS-for-CSS patterns identified in Phase 1.1
- Fix compatibility issues identified in Phase 1.3
- Optimize DOM access patterns identified in Phase 1.2
- Ensure all event listeners are properly cleaned up

### Phase 4: Verify

- **Re-test every interactive feature** documented in Phase 0. Compare behavior with the reference.
- Run `python stone_techno_companion.py --render-only --no-photos` to rebuild lineup
- Restart the server: `kill -9 $(lsof -ti :64728); cd server && set -a && source .env && set +a && uvicorn api:app --port 64728 --ssl-keyfile localhost+1-key.pem --ssl-certfile localhost+1.pem`
- Test on mobile (375px) and desktop (1024px):
  - Lineup: view switching, filtering, sticky headers, bio modal, timetable popup, hearts, schedule, share, sync, push notifications
  - Chat: login (Google + email), room switching, messaging, reactions, replies, media upload, meetups, DMs, profile edit, push notifications, logout/re-login
  - Both: navigation via header icons, page reload state preservation
- Run `python -m pytest tests/ -v`
- Check browser console for errors on both pages (zero errors, zero warnings from our code)
- Verify `shared.js` is served: `curl -sk https://localhost:64728/shared.js | head -5`

### Serving shared.js

The file lives at `server/static/shared.js`. Check if existing static file routes in `server/api.py` already serve files from `server/static/`. If not, add an explicit route:

```python
@app.get("/shared.js")
async def serve_shared_js():
    return FileResponse(STATIC_DIR / "shared.js", media_type="application/javascript")
```

## Key files to read

| File | What it contains |
|------|-----------------|
| `scraper/render.py` | All lineup/timetable JS (inline in generated HTML). Multiple `<script>` blocks. Main JS starts around line 1478. Timetable-specific JS around line 2206. |
| `server/chat/chat.html` | All chat JS (inline `<script>` block, lines 370-3460). WebSocket handling, room management, message rendering, media processing, auth flow. |
| `server/chat/admin.html` | Admin page JS (inline `<script>`, lines 130-560). API calls, tab management, table rendering. Lower priority. |
| `server/static/sw.js` | Service worker — push events, notification clicks, subscription change handling. |
| `output/lineup.html` | Generated output — read this to see the actual rendered JS. |
| `CLAUDE.md` | Project documentation. Notes 126 `dbg()` calls in chat, `verify()` checks, debug patterns. |

## Git workflow

Work on the `chat-prototype` branch. Commit incrementally:

1. **After Phase 0** (documentation): commit or proceed
2. **After creating `shared.js`**: commit the new file alone — `"Add shared.js with common utilities"`
3. **After refactoring the lineup page**: commit — `"Refactor lineup JS to use shared.js and standardize naming"`. Verify all features before committing.
4. **After refactoring the chat page**: commit — `"Refactor chat JS to use shared.js and standardize naming"`. Verify all features before committing.
5. **After CSS-replacement optimizations**: separate commit — `"Replace JS patterns with CSS equivalents"`
6. **After compatibility fixes**: separate commit per fix

Never amend previous commits. Never force push.

## Handling the lineup page generation

The lineup JS lives inside `scraper/render.py` as Python string concatenation:

- JS code is built with `parts.append()`, raw strings, and f-strings
- Some JS values are injected from Python (e.g., `siteShort`, artist data, timetable slots)
- The generated `output/lineup.html` references `shared.js` via `<script src="/shared.js">`

**Approach:**
- Move shared utility functions out of `render.py` into `shared.js`
- Keep page-specific JS and Python-injected values inline
- The inline `<script>` should only contain page-specific logic, not general utilities

## Error handling

Standardize error handling across both pages:

- **Network errors** (fetch/WS): show toast with user-friendly message, log detail via `dbg()`
- **Parse errors** (JSON): catch specifically, log the raw input for debugging
- **DOM errors** (element not found): use optional chaining or guard checks, never crash silently
- **Storage errors** (quota exceeded, private browsing): wrap in try/catch, fall back gracefully
- **Never swallow errors silently** — at minimum `dbg()` them. No empty `catch {}` blocks.
- **Never show raw error messages to users** — translate to human-readable text

## Memory and lifecycle

- **Clear intervals/timeouts** when they're no longer needed (e.g., stats polling in admin, typing indicator timeouts)
- **Remove event listeners** when components are destroyed (e.g., modal close should remove its keydown listener)
- **Avoid global state accumulation** — objects like `messagesByRoom`, `onlineByRoom` should be pruned when rooms are left
- **WebSocket reconnect** — ensure only one reconnect timer runs at a time, clear on successful connect
- **Detach observers** (ResizeObserver, IntersectionObserver, MutationObserver) when no longer needed

## Security

- **All user-generated content** must be escaped before insertion into HTML — verify every `.innerHTML` assignment uses `esc()` on user data
- **No `eval()` or `new Function()`** from user input
- **URL construction** — verify no user input is interpolated into URLs without encoding
- **postMessage** — verify origin checks if used
- **CSP compatibility** — inline scripts are used (no CSP strict mode), but avoid `eval`-style patterns

## Cleanup

This codebase has accumulated JS workarounds, band-aids, and leftovers from iterative development. As part of this refactor:

- **Remove dead code** — functions that are defined but never called. Grep every function name against all call sites to verify it's still used.
- **Remove commented-out code** — old implementations left behind "just in case". If it's in git history, it doesn't need to live in the source.
- **Remove unused variables** — declared but never read. Check for accidental shadowing too.
- **Remove workarounds** — if a hack exists because the underlying approach was wrong, fix the approach. Common examples: setTimeout(fn, 0) to "fix" a timing issue, redundant null checks because a function sometimes returns undefined when it shouldn't.
- **Remove redundant checks** — `if (x !== null && x !== undefined && x)` can be `if (x)` in most cases.
- **Remove stray console calls** — any `console.log`, `console.warn`, `console.error` that aren't part of the `dbg()`/`verify()` infrastructure. Convert legitimate warnings/errors to use `dbg()` with appropriate tags.
- **Simplify over-engineered patterns** — if a simple `if/else` was replaced with a complex ternary chain or unnecessary abstraction, simplify it back.
- **Flag but do not remove** anything you're unsure about — add a `// TODO: verify if still needed` comment and list it in your commit message.

## Service worker

`server/static/sw.js` is part of this audit. Review it for:

- **Push event handling** — is the ack flow (delivered/clicked/dismissed) implemented correctly?
- **Notification click routing** — does it navigate to the correct URL?
- **pushsubscriptionchange** — does auto-resubscribe work?
- **Cache management** — is the `stc-push` cache used correctly? Is it ever cleaned up?
- **Compatibility** — all SW APIs used must meet the 95% browser support target
- **Error handling** — are fetch failures in the SW handled gracefully?

## Code size goal

After the refactor, inline `<script>` blocks should be significantly smaller because shared utilities moved to `shared.js`. As a rough target:
- `shared.js` — shared utilities, < 5KB
- Lineup inline script — page-specific logic only
- Chat inline script — page-specific logic only

The goal is not minification — it's eliminating duplication and keeping each file focused on its responsibility.

## Constraints

- No JS frameworks or libraries (vanilla JS only, except QR code lib already included)
- No build tools, bundlers, or transpilers — plain JS files served directly
- Target 95%+ browser support (Safari 15.4+, Chrome 90+, Firefox 90+)
- No emojis in code, comments, or generated files
- Do not change any server-side Python logic — you WILL need to modify `scraper/render.py` to remove JS that moved to `shared.js` and to add the `<script src>` tag, but do not change any Python logic (scraping, DB, rendering decisions)
- Do not change any visual appearance — this is a behavior-preserving optimization
- The admin page JS is self-contained and should NOT be merged into shared.js (but can use it if convenient)
- Keep `dbg()` calls — they are intentional debugging infrastructure, not leftovers
- Do not refactor CSS — that is handled by the CSS unification review
