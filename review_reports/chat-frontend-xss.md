# Chat Frontend Review — XSS, State, Race Conditions

Scope: `server/chat/chat.html` (4677 lines), `server/static/shared.js`. Read-only review, no files modified.

Reviewed in 5 parallel passes across the file, then manually verified the highest-severity claims by reading the actual source (in particular `jss()`, `_linkify()`, and the server-side link-preview extractor) before finalizing severities. Several sub-agent findings turned out to be **false positives** after verification — noted below so they aren't re-flagged in a future pass.

## False positives ruled out during verification

- **`jss()` does escape attribute context.** `jss(s) { return esc(String(s).replace(/[\`'\\]/g, '\\$&')); }` (chat.html:4161) — it JS-string-escapes backtick/quote/backslash, then runs the result through `esc()` (HTML entity escaping), so a literal `"` in a name/title becomes `&quot;` before it ever reaches the `onclick="..."` attribute. Multiple sub-agents flagged `onclick="...('${jss(x)}')"` sites (lines ~1746, 1815, 1877, 2203, 2683, 2704, 2733, 3279-3282, 3322, 3350, 3378) as attribute-breakout XSS on the assumption `jss()` only does JS-string escaping. It doesn't — these are not exploitable as described.
- **`_linkify()` does escape before linkifying.** `_linkify(text) { return escapeHtml(text).replace(/(https?:\/\/[^\s<>"')\]]+)/g, ...) }` (chat.html:4162-4163) runs `escapeHtml` first, so message text (chat.html:2137/2139, flagged HIGH by one pass) is not a raw-HTML-injection vector. Entity-encoded quotes inside the matched URL substring do not re-open the `href="..."` attribute early (HTML parsers resolve attribute boundaries on raw markup before entity decoding), so this is safe.
- **Link-preview `href`/`img src` scheme.** `esc(lp.url)` (chat.html:1434) was flagged for allowing `javascript:`/`data:` URIs since `esc()` doesn't scheme-filter. Verified server-side (`server/chat_ws.py`): `_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')` extracts the URL, and `_is_safe_preview_url` additionally rejects any scheme other than `http`/`https` (plus SSRF guards on private/loopback IPs). A non-http(s) scheme can never reach `link_preview.url`. Not exploitable.
- **WS token in `document.cookie` / URL.** One pass flagged the session cookie being JS-readable and appended to the WS URL as a new finding. This is an intentional, already-documented tradeoff (CLAUDE.md: "Session cookies (non-httpOnly for WS access, ...)"), not a new issue — omitted per review scope rules.

## Confirmed findings

**[MEDIUM] chat.html:1931 — Shared global WS-history resolver races on rapid room switching.**
`const historyReady = new Promise(r => { window._roomHistoryResolve = r; setTimeout(r, 3000); });` in `openRoom()` stores the resolver in a single global. If a user opens room A then switches to room B before A's `room_history` WS event arrives, `openRoom(B)` overwrites `window._roomHistoryResolve` with B's resolver. When A's history event later fires (handled elsewhere) and calls `window._roomHistoryResolve()`, it resolves B's promise instead of A's — A's own promise silently falls through to its 3s timeout, delaying `_verifyDmEncryptionState`/`verify()` checks and producing a spurious `verify('openRoom', currentRoom === roomId, ...)` false-negative since `currentRoom` has already moved to B.
Fix: key resolvers by room id (e.g. `window._roomHistoryResolvers[roomId]`) instead of one shared variable.

**[MEDIUM] chat.html:2101-2113 — `appendMessage()` has no dedup guard, can double-render a message.**
`openRoom()` (chat.html:1938) fetches history via REST (`GET /rooms/{id}/messages`) and separately joins the room over WS (chat.html:1945), which can also deliver the same message via a `message` event if it arrives in the race window between the two. `appendMessage()` unconditionally does `el.insertAdjacentHTML('beforeend', renderMessage(m))` with no check for an existing `[data-msg-id="${m.id}"]` node, so the message can render twice in the thread.
Fix: `if (document.querySelector('[data-msg-id="' + m.id + '"]')) return;` before inserting.

**[MEDIUM] chat.html:2247-2297 (`_setupReadObserver`/debounced mark-read) — cross-room read-state clobbering.**
`_lastSeenTime` and `_markReadRoom` are single globals reassigned on every room open, with a 500ms-debounced send. If a user opens room A (scheduling a pending debounced `mark_read` keyed to A's globals) and switches to room B before the timer fires, `_setupReadObserver()` for B overwrites `_lastSeenTime`/`_markReadRoom` first — A's pending callback then fires using B's values, sending a `mark_read` for the wrong room/timestamp and corrupting `unreadByRoom` for both rooms.
Fix: flush or cancel the pending debounced mark-read synchronously before reassigning these globals in `openRoom`/`_setupReadObserver`.

**[MEDIUM] chat.html:2463-2469 (`toggleReaction`) — reaction add/remove race under rapid clicks.**
Add-vs-remove direction is decided from the locally cached `msg.reactions`, which isn't updated until the server broadcasts the change back. Clicking the same emoji twice quickly (picker closes and reopens between clicks) sends two `add_reaction` (or two `remove_reaction`) events instead of a matched add+remove pair, leaving client and server reaction state inconsistent until the next full resync.
Fix: optimistically flip local state immediately on click, or disable the button until the corresponding ack/broadcast arrives.

**[MEDIUM] chat.html:2915-2938 (`makeDraggable`, video trim editor) — orphaned document-level drag listeners.**
`mousedown`/`touchstart` attaches `mousemove`/`mouseup`/`touchmove`/`touchend` listeners on `document`; they're only removed by `onUp`, which only fires on an actual `mouseup`/`touchend`. If the user is mid-drag and taps "Trim & Share", Cancel, or the overlay backdrop, `dismiss()` removes the modal's DOM nodes but never invokes `onUp` for the in-progress handle, so the document-level listeners stay registered indefinitely holding closures over now-detached elements. Any later stray mousemove/mouseup anywhere on the page runs geometry/style code against orphaned nodes.
Fix: call the drag-cleanup path (equivalent of `onUp()`) for both handles inside `dismiss()`.

**[MEDIUM] chat.html:3298-3311 (`_doBlock`) — optimistic block state has no server-ack reconciliation.**
`wsSend('block_user', {...})` is fire-and-forget; `blockedUserIds.add(userId)` and the UI re-render happen immediately regardless of whether the WS send actually reached the server (e.g., mid-reconnect). If the send is dropped, the client shows the user as blocked while the server never persisted it, with no visible indication of failure and no reconciliation on reconnect.
Fix: gate the local state update/toast on a server ack, or reconcile `blockedUserIds` against `GET /blocks` after reconnect.

**[MEDIUM] chat.html:3852-3887 (`_subscribePush`) — no reentrancy guard.**
Callable from both `_repairPushSubscription()` (guarded by the one-shot `_pushRepairAttempted` flag) and the user-initiated "enable notifications" path. If both fire close together at load, two concurrent `pushManager.subscribe()` + `POST /push/subscribe` calls can interleave, one call's success/failure reporting the other's state.
Fix: wrap in a shared in-flight promise (`_pushSubscribeInFlight`) that both callers `await`.

**[MEDIUM] chat.html:4457 — `route()` early-return skips the WebSocket liveness check.**
`if (targetRoom && currentRoom === targetRoom && currentRoomName) return;` short-circuits before `connectWS()`'s liveness check at chat.html:4464. If the WS has silently died while the user remains on the matching room and something re-invokes `route()` for the same URL (e.g. `_pushNavRetry` on focus/pageshow if the push URL matches the currently-open room), the reconnect never runs — the UI looks "on this room" with no live socket and nothing here notices.
Fix: move the WS-liveness check ahead of the same-room short-circuit, or check `ws?.readyState !== 1` as part of the guard condition.

**[MEDIUM] chat.html:4519 — `resize` handler reaches into the router's reentrancy mutex.**
`window.addEventListener('resize', () => { ...; _routing = false; ... })` unconditionally clears `_routing`, which `route()` (chat.html:4433-4436) uses as its own in-flight guard. A resize/orientation-change firing while `route()` is genuinely awaiting network calls (`checkAuth`, `loadRooms`, etc.) flips the guard open underneath it, allowing a second concurrent `route()`/`openRoom()` invocation to run in parallel with the first.
Fix: don't reset `_routing` from unrelated code; if the intent is "unstick a hung router," scope that recovery to a timeout inside `route()` itself.

**[MEDIUM] chat.html:990-1022 (`submitProfile`) — no in-flight guard against double submit.**
No disabled-state flag on the Continue button or a `_submitting` guard around the async body. Rapid double-tap fires two concurrent flows, each potentially re-uploading the avatar crop and issuing a duplicate `PUT /profile`, both then racing to call `connectWS()`/`loadRooms()`.
Fix: set a module-level flag or disable the submit button for the duration of the call.

**[LOW] chat.html:4021 — `u.user_id` interpolated into `onclick` without `jss()`/`esc()`.**
`onclick="_unblockUser('${u.user_id}',this)"` in the blocked-users modal is the one place in the file that skips the escaping helper used everywhere else for values placed inside inline event-handler attributes. `user_id` is server-generated and not attacker-controlled today, so this isn't currently exploitable, but it's an inconsistency worth closing for defense-in-depth (a future change to how IDs are generated/stored would silently reintroduce the class of bug the rest of the file guards against).
Fix: wrap with `jss(u.user_id)` to match the pattern used elsewhere (e.g. chat.html:3322).

**[LOW] chat.html:4019 — avatar-fallback initial inserted unescaped, inconsistent with adjacent line.**
`(u.display_name || u.username || '?')[0].toUpperCase()` goes into `innerHTML` unescaped, while the full name on the very next line (4020) is wrapped in `esc()`. Not exploitable on its own (a single stray `<` can't form a tag), but it's the same escaping-discipline gap as chat.html:752 (`updateProfilePreview`) — both should route through `escapeHtml()` for consistency.

**[LOW] chat.html:4551, 4586 — blind navigation on SW/cache messages without payload validation.**
`_checkPushNavigate()` reads a URL string out of the `stc-push` Cache Storage entry and does `window.location.href = url` (4551); the `serviceWorker` `message` listener does the same for `e.data.url` when `e.data.type === 'navigate'` (4586). Both channels are same-origin only (Cache Storage keys and SW postMessage aren't reachable by a malicious iframe/cross-origin script), so this is not an `event.origin`-style postMessage vulnerability — but neither site validates that `url` is an in-app relative path (e.g. `^/chat(/|$)`) before navigating, which is worth doing as defense-in-depth given the value ultimately flows from a push payload.

**[LOW] chat.html:3990-4006 (`_disableAllNotifications`) — success reported even when server-side unsubscribe fails.**
The `catch` around the `DELETE /push/subscribe` call only `dbg()`-logs the error (no rethrow/flag); `storageRemove('push_enabled')` and the "Notifications disabled" toast fire unconditionally afterward. If the server call fails, the client believes push is off while the server may still hold a live subscription and keep delivering.
Fix: only clear `push_enabled`/show success once both `unsubscribe()` and the DELETE succeed; otherwise surface a retry/warning.

## Accessibility (MEDIUM/LOW, no keyboard/AT support)

- **[MEDIUM] chat.html:895-932** — Avatar zoom slider/pan area is mouse/touch-only; no keyboard handler (arrow keys), no `role="slider"`/`aria-valuenow`/`aria-label`. Keyboard-only users cannot crop an avatar.
- **[MEDIUM] chat.html:811-819** — Country picker list items have no `role="option"`; `#country-input`/`#country-list` lack `role="combobox"`/`role="listbox"`, `aria-expanded`, `aria-activedescendant`. Functionally works via keyboard (handled on the input), but exposes no semantics to screen readers.
- **[LOW] chat.html:2433-2437** — Reaction picker is hover-only (`reactHoverIn`/`reactHoverOut`), no keyboard trigger visible, no `aria-label` per emoji button.
- **[LOW] chat.html:2859** — Trim editor close button (`&times;`) has no `aria-label="Close"`.
- **[LOW] chat.html:2640-2649, 2759-2770** — `toggleChatMenu`/`toggleActionMenu` toggle `.open` classes with no `aria-expanded`, no Escape handler, no focus trap/focus-return to the triggering control on close.

## Summary

No exploitable stored/reflected XSS was found once `jss()`, `_linkify()`, and the server-side link-preview extractor were verified directly — the codebase's `esc()`/`escapeHtml()` discipline holds throughout, with only two minor unescaped-but-low-risk spots (4019, 4021) worth tidying for consistency. The real issues are a cluster of shared-global race conditions (`_roomHistoryResolve`, `_lastSeenTime`/`_markReadRoom`, `_routing`), a couple of missing in-flight/dedup guards (`appendMessage`, reaction toggle, profile submit, push subscribe), one optimistic-without-ack state update (block user), and moderate accessibility gaps on two custom widgets (avatar cropper, country picker). None are deploy-blocking on their own, but the `_routing`/`_roomHistoryResolve` races are worth fixing before relying on rapid room-switching behavior in production.
