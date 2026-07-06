# JavaScript Optimization Review

## Summary

Audited all JavaScript across the lineup page (`scraper/render.py`), chat page (`server/chat/chat.html`), admin page (`server/chat/admin.html`), and service worker (`server/static/sw.js`). Created `server/static/shared.js` to consolidate duplicated utilities, fixed security issues, removed dead code, and optimized DOM access patterns.

## What was moved to shared.js (2,580 bytes, 85 lines)

| Utility | Lineup source | Chat source | Admin source | Notes |
|---------|--------------|-------------|--------------|-------|
| `escapeHtml(s)` / `esc` | `_escHtml(s)` | `escapeHtml(s)` + `const esc` | `esc(s)` (DOM-based) | Unified on regex approach (faster, no DOM allocation) |
| `dbg()` / `_ts()` / `verify()` | (none) | Lines 347-358 | Lines 147-154 | Parameterized via `setDbgTag(tag)` per page |
| `showToast(msg, duration)` | (none) | Lines 390-401 | (none) | Auto-creates `#toast` element if missing |
| `fmtTime(iso)` | (none) | Lines 404-406 | (none) | Localized HH:MM formatting |
| `ago(iso)` | (none) | (none) | Lines 176-183 | Relative time ("5m ago") |
| `_urlBase64ToUint8Array()` | Lines 2092-2098 | Lines 3034-3041 | (none) | Identical implementations on both pages |
| `storageGet(key, fallback)` | (none) | (none) | (none) | New: safe localStorage wrapper with try/catch |
| `storageSet(key, value)` | (none) | (none) | (none) | New: safe localStorage wrapper with try/catch |
| `DEBUG`, `_t0`, `_dbgTag` | (none) | Lines 347-348 | Lines 147-148 | Debug infrastructure state |

Design decisions:
- All declarations use `var` and `function` (not `const`/`let`) to avoid cross-script-block redeclaration errors
- `setDbgTag(tag)` called per page: lineup uses `[lineup]`, chat uses `[chat]`, admin uses `[admin]`
- `showToast` dynamically creates the `#toast` element if missing (supports lineup page which has no static toast element; toast CSS is already in shared.css)
- No IIFE wrapper -- utilities are direct globals as required by inline scripts

## Serving

Added explicit route `/shared.js` in `server/api.py` (same pattern as `/shared.css`). Loaded via `<script src="/shared.js"></script>` synchronously in `<head>` (chat, admin) or before the inline `<script>` block (lineup).

## Dead code removed

| Function | Page | Evidence |
|----------|------|----------|
| `navigateTo(hash)` | chat | 0 call sites; router uses `history.replaceState` not `location.hash` |
| `goBack()` | chat | 0 call sites; superseded by hamburger menu / mobile navigation |
| `leaveRoom()` | chat | Only caller was `goBack()` (also dead) |
| `hashchange` listener | chat | `navigateTo` was the only hash setter; router uses pathname-based routing |
| `timetable` + `tr` variables in `openBlockPopup` | lineup | Assigned but never read; popup uses click coordinates directly |

## Security issues found and fixed

| Issue | Location | Fix |
|-------|----------|-----|
| `avatar_url` in `innerHTML` without `esc()` | chat L558 (profile setup header) | Wrapped in `esc()` |
| `avatar_url` in `innerHTML` without `esc()` | chat L1439 (chat header) | Wrapped in `esc()` |
| `avatar_url` in `innerHTML` without `esc()` | chat L2655 (profile edit modal) | Wrapped in `esc()` |
| `avatar_url` in `innerHTML` without `esc()` | chat L2730 (edit preview bubble) | Wrapped in `esc()` |
| `avatar_url` in `innerHTML` without `esc()` | chat L2980 (post-save header update) | Wrapped in `esc()` |

Risk: Low (server controls `avatar_url`), but defense-in-depth. A compromised DB entry could inject `" onerror="alert(1)` into the attribute.

## DOM optimizations applied

| Optimization | Page | Details |
|-------------|------|---------|
| `setStickyTops` layout thrashing | lineup | Batched all `offsetHeight` reads before all `style.top` writes (was interleaving 4 read-write pairs causing 4 forced layouts) |
| `truncateNames` ResizeObserver throttle | lineup | Wrapped in `requestAnimationFrame` debounce (was firing unthrottled on every body resize, each call doing binary search with layout reads per element) |

## Silent error swallowing fixed

| Location | Before | After |
|----------|--------|-------|
| `sock.onmessage` catch (chat) | `catch {}` | `catch (err) { dbg('[WS] event handler error:', err); }` |

This was the most impactful fix -- any exception in `handleWSEvent` (bad data shape, null reference, DOM missing) was silently swallowed, making production bugs invisible.

## Stray console calls converted

| Location | Before | After |
|----------|--------|-------|
| `enableNotifications` catch (lineup) | `console.warn('Push subscribe failed', e)` | `dbg('Push subscribe failed', e)` |

## Service worker improvements

- Added null guard for `event.oldSubscription` in `pushsubscriptionchange` handler (was accessing `.options` on potentially null subscription)

## Browser compatibility notes

Pre-existing issues (not introduced by this refactor):

| API | Support | Impact | Existing fallback |
|-----|---------|--------|-------------------|
| `OffscreenCanvas` + `convertToBlob` | Safari 16.4+ | Avatar crop + image resize fail on Safari 15.4-16.3 | None -- feature unavailable |
| `VideoEncoder` (WebCodecs) | Chrome 94+, no Firefox | Video processing unavailable | Code wraps in try/catch with AVC fallback, but Mediabunny itself requires WebCodecs |
| `createImageBitmap` resize options | Safari 15.2+ | Within baseline | N/A |

All shared.js utilities use universally supported APIs (ES5+).

## Naming changes

| Before | After | Page | Reason |
|--------|-------|------|--------|
| `_escHtml(s)` | `esc(s)` | lineup | Standardized to match chat/admin convention |

No other naming changes were needed -- both pages already follow consistent conventions (camelCase functions/vars, UPPER_SNAKE constants, underscore prefix for internal state).

## Line count changes

| File | Before | After | Delta |
|------|--------|-------|-------|
| `shared.js` | (new) | 85 | +85 |
| `chat.html` | 3454 | 3393 | -61 |
| `admin.html` | 771 | 755 | -16 |
| `render.py` | 2578 | 2572 | -6 |
| `sw.js` | 78 | 79 | +1 |
| **Net** | | | **+3** |

The line count is roughly neutral because the consolidation moves code rather than deleting it. The real win is eliminating 3-way duplication of utilities.

## Test results

- All 132 Python tests pass (`pytest tests/ -v`)
- `shared.js` passes `node --check` (no syntax errors)
- Playwright verification: both pages load without JS errors at 375px and 1024px viewports
- All 10 shared utilities available and functional on both pages
- View switching, timetable interaction, toast notifications all verified working
- Debug tags correctly set per page (`[lineup]`, `[chat]`, `[admin]`)

## Future work

1. **OffscreenCanvas fallback** -- add `<canvas>` fallback for Safari 15.4-16.3 in avatar crop and image resize
2. **VideoEncoder guard** -- check `'VideoEncoder' in window` before calling `processVideo` to prevent failure on Firefox
3. **Event delegation** -- replace 239 inline `onclick="openBio(this)"` handlers with one delegated listener on `#list-view` (reduces HTML payload)
4. **CSS `:has()` for filter visibility** -- three of four hiding cases in `updateGroupVisibility` can become pure CSS (`.filter-active section.date-section:not(:has(.artist-item.hearted)) { display: none }`)
5. **Empty catch blocks** -- 13+ `catch {}` blocks in lineup JS silently swallow errors; the most impactful ones (session creation, sync PIN exchange) should show toasts on failure
6. **`storageGet`/`storageSet` adoption** -- replace raw `localStorage.getItem`/`setItem` calls with the safe wrappers from shared.js
7. **`saveLocal`/`applyHearts` double `updateUI`** -- remove `updateUI()` from inside `saveLocal()` since callers already handle it
