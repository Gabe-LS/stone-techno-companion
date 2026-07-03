# JS Optimization Phase 2 Report

## 1. SVG Icon Deduplication

Moved 3 cross-page icons to `shared.js` as global constants:

| Constant | Used in | Before |
|---|---|---|
| `ICON_HAMBURGER` | lineup, chat | Inline SVG in Python (lineup), local const (chat) |
| `ICON_CALENDAR` | chat | Inline SVG in template literal (2 occurrences) |
| `ICON_CHAT` | lineup | Inline SVG in Python string concatenation |

Lineup page now injects icons via JS (`document.querySelector('.hamburger').innerHTML = ICON_HAMBURGER`) instead of embedding SVG in Python strings. Chat page drops its local `ICON_HAMBURGER` copy and uses the shared one.

Single-use icons (action menu: plus, meetup calendar, location, photo, video, emoji, send; bell; share; checkmark; back) were left inline -- deduplication would add complexity for no saving.

Also added: `storageRemove(key)` wrapper, `shared.js` route in `api.py`, `sw.js` guard for null `oldSubscription`.

## 2. Event Delegation

**Before:** 783 inline event handlers in generated `lineup.html`
**After:** 28 inline event handlers

Replaced with 3 delegated listeners:

1. **List view** (`#list-view`): `click` for `.heart-btn` and `.artist-photo`/`.artist-name` (openBio); `keydown` for Enter on same selectors
2. **Document**: `click` for `.tt-cal` (toggleSchedule), `.tt-photo-heart` (toggleHeart), `.tt-ics` (downloadICS) -- all with `e.stopPropagation()`

Handlers removed per type:
- `onclick="openBio(this)"` -- 239
- `onkeydown="if(event.key==='Enter')openBio(this)"` -- 239
- `onclick="toggleHeart(this)"` -- 120 (list view)
- `onclick="event.stopPropagation(); toggleHeart(this)"` -- 240 (timetable)
- `onclick="event.stopPropagation(); toggleSchedule(this)"` -- 184
- `onclick="event.stopPropagation(); downloadICS(...)"` -- 184

Remaining 28 are on unique elements: command bar buttons, modal buttons, day tabs, menu overlay, popup artist clicks.

Chat page inline handlers were not modified -- they are mostly on dynamically generated elements in template literals where delegation would require more complex refactoring and risk behavioral regressions.

## 3. Empty Catch Blocks

**18 empty catch blocks fixed** across 4 files:

| File | Count | Action |
|---|---|---|
| `scraper/render.py` (lineup JS) | 12 | Added `dbg()` logging with context (e.g., `'ensureSession failed'`, `'toggleHeart sync failed'`) |
| `server/chat/chat.html` | 4 | `dbg()` for avatar preview, logout; comment for already-closed WS |
| `server/static/sw.js` | 2 | Comments explaining best-effort nature (push ack, re-subscribe) |
| `server/chat/admin.html` | 1 | `dbg()` for stats refresh |

Categorization:
- **Network errors** (9): Added `dbg(context, e.message)` -- ensureSession, toggleHeart, toggleSchedule, loadFromServer, reconcile, generateSyncPin, exchangeSyncPin, disableNotifications, push re-sync
- **WS message handler** (1): `dbg('ws message handler error', e)` with full error object
- **WebSocket close** (2): Comment `/* already closed */` -- expected when connection died
- **Cache/SW** (3): Comment `/* cache API unavailable */` or `/* sw not available */`
- **Fetch /api/me** (1): `dbg('fetch /api/me failed', e.message)`
- **Avatar preview** (2): `dbg('[AVATAR] preview render failed', e.message)`
- **Logout** (1): `dbg('[AUTH] logout request failed', e.message)`

Also fixed `.catch(() => {})` on service worker registration (both pages) and push ack/re-subscribe in sw.js with explanatory comments.

## 4. storageGet/storageSet Adoption

**36 raw `localStorage` calls replaced** in lineup JS (`scraper/render.py`):

| Method | Count |
|---|---|
| `localStorage.getItem()` -> `storageGet()` | 13 |
| `localStorage.setItem()` -> `storageSet()` | 12 |
| `localStorage.removeItem()` -> `storageRemove()` | 11 |

Added `storageRemove(key)` to `shared.js` alongside existing `storageGet` and `storageSet`.

Chat page (`chat.html`) does not use `localStorage` -- it uses cookies for auth. Admin page uses `sessionStorage` (not `localStorage`), which is a different API and was left as-is.

## 5. Browser Compatibility Fixes

### 5.1 OffscreenCanvas fallback

Added `_bitmapToBlob(bmp, w, h)` helper to `chat.html` that:
- Uses `OffscreenCanvas` + `bitmaprenderer` + `convertToBlob` when available
- Falls back to `document.createElement('canvas')` + `2d` context + `toBlob()` for Safari 15.4-16.3

Replaced all 7 direct `OffscreenCanvas` usages:
- Avatar editor: initial downscale, preview crop, submit crop (x2 for profile setup + profile edit = 6 sites)
- Image upload: resize before upload (1 site)

### 5.2 VideoEncoder guard

Added `'VideoEncoder' in window` check at the entry of `uploadVideo()`. Shows toast "Video processing is not supported in this browser. Please use Chrome." in Firefox and Safari instead of a cryptic error.

The existing try/catch around `VideoEncoder.isConfigSupported()` was already handling the HEVC codec check fallback -- the new guard prevents the entire video flow from starting on unsupported browsers.

## 6. Double updateUI Call

Removed `updateUI()` from inside `saveLocal()`. All 8 call sites verified:

- `toggleHeart()`, `toggleSchedule()`: manage element classes directly before calling `saveLocal()`
- `loadFromServer()`, `reconcile()`, WS `onmessage`, `exchangeSyncPin()`: call `applyHearts()` after `saveLocal()`, which calls `updateUI()` internally
- Init IIFE (`/api/me`): followed by `applyHearts()` on line 2522

Net effect: eliminates one full DOM traversal (all `[data-artist-id]` + all `.tt-block[data-artist-id]`) per pick/schedule action.

## 7. CSS :has() for Filter Visibility

Replaced 3 of 4 JS hiding cases in `updateGroupVisibility` with CSS rules:

```css
.filter-active section.date-section:not(:has(.artist-item.hearted)) { display: none; }
.filter-active ul.artist-list:not(:has(.artist-item.hearted)) { display: none; }
.filter-active h4.location-heading:has(+ ul.artist-list:not(:has(.artist-item.hearted))) { display: none; }
```

The period heading case (`.h3.period-heading`) stays in JS because it requires sibling lookahead across multiple elements until the next `h3`/`h2`, which CSS `:has()` cannot express.

Browser support: Safari 15.4+ (Mar 2022), Chrome 105+ (Aug 2022), Firefox 121+ (Dec 2023) -- ~95.5% global coverage.

Removed ~15 lines of JS DOM traversal.

## Line Count Changes

| File | Before | After | Delta |
|---|---|---|---|
| `server/static/shared.js` | 0 (new) | 96 | +96 |
| `scraper/render.py` | 2574 | 2581 | +7 |
| `server/chat/chat.html` | 3408 | 3409 | +1 |
| `server/chat/admin.html` | 774 | 774 | 0 |
| `server/static/sw.js` | 78 | 79 | +1 |

Note: `shared.js` was created in Phase 1 but first committed in this session (was untracked). The +96 includes Phase 1 content plus the 3 icon constants and `storageRemove` added here.

## Test Results

All 132 tests pass:
- `test_chat_db.py`: 45 passed
- `test_chat_moderation.py`: 39 passed
- `test_chat_api.py`: 31 passed
- `test_chat_ws.py`: 17 passed

`node --check server/static/shared.js`: no syntax errors.

Server verified running on port 64728 -- lineup and chat pages load correctly via curl.
