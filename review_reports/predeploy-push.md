# Findings: push

## [SEVERITY: CRITICAL] Enabling notifications on one surface silently kills the other surface's push subscription
- Where: `scraper/render.py:2191-2196` (lineup `enableNotifications()`), `server/chat/chat.html:3854-3863` (`_subscribePush()`), `server/chat/chat.html:4555` + `scraper/render.py:635` (both register `/sw.js` at root scope)
- Evidence:
  - Lineup: `var oldSub = await reg.pushManager.getSubscription(); if (oldSub) await oldSub.unsubscribe(); const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });` then POSTs only to `/session/{sessionId}/push/subscribe`.
  - Chat: `let sub = await reg.pushManager.getSubscription(); if (sub) await sub.unsubscribe(); sub = await reg.pushManager.subscribe(...)` then POSTs only to `/push/subscribe` (chat's `/chat/api/push/subscribe`).
  - Both pages `navigator.serviceWorker.register('/sw.js')` with default (root) scope, so both share exactly one `PushManager` subscription per browser/origin — there is no such thing as "the lineup subscription" and "the chat subscription" as separate browser objects, only two separate *server-side records* of whichever endpoint currently exists.
- Impact: A user who enables lineup timetable alerts and later enables chat notifications (or vice versa — both are independent UI toggles, and the app explicitly supports using both features together per the PWA architecture) will silently lose push for whichever surface was enabled first: `unsubscribe()` revokes the old endpoint at the push service, `subscribe()` mints a brand-new one, and only the just-run flow's backend table gets the new endpoint. The other table still points at a now-dead endpoint; the next send attempt 404s/410s and prunes the row — the user is permanently unsubscribed from that surface with no error surfaced to them.
- Fix: Make the two flows cooperate — e.g. never call `unsubscribe()` before `subscribe()` when a subscription already exists with the same `applicationServerKey` (re-subscribing without unsubscribing generally returns the existing subscription unchanged), and/or have each "enable" flow POST the resulting endpoint to *both* backends (lineup's `/api/session/{code}/push/subscribe` and chat's `/chat/api/push/subscribe`) whenever it (re)subscribes.

## [SEVERITY: HIGH] Asymmetric self-healing lets the collision above become permanent for chat
- Where: `scraper/render.py:2606-2618` vs `server/chat/chat.html:3877-3895`
- Evidence:
  - Lineup unconditionally re-POSTs the *current* subscription every page load: `if (storageGet('stc_push') === '1' && 'serviceWorker' in navigator) { ... var existingSub = await swReg.pushManager.getSubscription(); if (existingSub && sessionId) { ...fetch(API + '/session/' + sessionId + '/push/subscribe', ...) } }` — this repairs lineup's record even if some *other* code path rotated the endpoint.
  - Chat only repairs when a subscription is entirely missing: `_repairPushSubscription()` → `if (Notification.permission !== 'granted' || _pushSubscribed) return;` where `_pushSubscribed` is just `!!(await reg.pushManager.getSubscription())` (`chat.html:3828-3838`). If lineup's enable flow rotated the shared endpoint, `getSubscription()` still returns *a* subscription (just the wrong one from chat's perspective), so `_pushSubscribed` is `true` and repair never fires.
- Impact: Once the CRITICAL collision above happens in the "lineup enabled after chat" direction, chat push stays broken indefinitely — there is no automatic path back to a working state; the user must manually open chat's notification settings and explicitly disable+re-enable.
- Fix: Give chat the same "always resync current subscription to my backend on load" step that lineup has, rather than gating solely on subscription presence/absence.

## [SEVERITY: HIGH] `pushsubscriptionchange` only repairs the chat subscription, never lineup's
- Where: `server/static/sw.js:95-112`
- Evidence:
```js
self.addEventListener('pushsubscriptionchange', function (event) {
  if (!event.oldSubscription) return;
  event.waitUntil(
    self.registration.pushManager.subscribe(event.oldSubscription.options).then(function (sub) {
      return fetch('/chat/api/push/subscribe', { method: 'POST', ... });
    }).catch(function () {})
  );
});
```
- Impact: When the push service unilaterally rotates a subscription (browser-initiated, no user action), the SW re-subscribes and reports the new endpoint only to chat's backend. Lineup's `push_subscriptions` row still has the old endpoint; the next scheduled timetable push 404/410s and is pruned. Since this event fires without any page open, lineup's own resync-on-load (finding above) can't run until — if ever — the user happens to revisit the lineup page while `stc_push==1`. This directly matches focus item #10 ("does `pushsubscriptionchange` re-subscribe use the correct endpoint for **both** lineup and chat?") — it does not.
- Fix: Persist the active lineup `session_id` (analogous to the existing `stc-push`/`_push_navigate` Cache Storage bridge already used for click navigation) somewhere the SW can read, and also POST to `/api/session/{code}/push/subscribe` from this handler; or have both subscribe endpoints key off the push subscription's cookie/session rather than requiring the SW to know a `session_id`.

## [SEVERITY: MEDIUM] Lineup page runs two independent, racing implementations of push-navigate cache consumption
- Where: `scraper/render.py:612-639` (head IIFE: `nav()`/`chkCache()`/`_navigating`) vs `scraper/render.py:2625-2652` (body script: `_checkPushNavigate()`/`_pushNavRetry()`/`_pushNavigating`)
- Evidence:
  - Head script: `var _navigating=false; ... function chkCache(){ if(_navigating...) ... caches.open('stc-push').then(...).then(function(r){ if(r) r.text().then(function(u){ ...c.delete('/_push_navigate').then(function(){nav(u);}); }); }); } [0,300,800,1500,3000,5000].forEach(function(d){setTimeout(chkCache,d);});`
  - Body script (separately scoped `_pushNavigating`, different navigation call, different trigger source): `function _checkPushNavigate() { if (_pushNavigating...) ... caches.open('stc-push').then(...match('/_push_navigate')...).then(function(url){ ... c.delete(...).then(function() { ... window.location.href = url; }); }); } ... document.addEventListener('visibilitychange', ...); window.addEventListener('focus', _pushNavRetry); window.addEventListener('pageshow', _pushNavRetry);`
  - `server/chat/chat.html` has only **one** such implementation (`chat.html:4509-4535`), matching the documented "poll on visibilitychange/focus/pageshow, 0/300/1000ms retries" design — the lineup page's extra head-script copy (fixed 0/300/800/1500/3000/5000ms timer burst + its own `nav()`/`_navigating` guard) is not described anywhere in the documented mechanism and isn't present in chat.html.
- Impact: Both consumers read/delete the same `stc-push` cache entry with independent guard flags and no locking. `cache.match()` doesn't remove the entry, so if e.g. the head script's `t=0` timer and a `pageshow`-triggered `_pushNavRetry()` both fire near page load (a very plausible timing coincidence — both are triggered by page load/foregrounding), both can read the un-deleted cache value before either calls `delete()`, and both independently set `window.location.href`. More importantly, this is exactly the subsystem that was the source of a full afternoon of iOS debugging (per project history) — having two divergent copies means a future fix applied to one (e.g., the documented visibilitychange-based one) can leave the other silently un-fixed and reintroduce the same class of bug.
- Fix: Delete the redundant implementation. Keep the `pushsubscriptionchange`-message listener plus the single `visibilitychange`/`focus`/`pageshow` retry pattern already used by chat.html (or vice versa — but consolidate to one).

## [SEVERITY: LOW] Lineup push payload sets a `tag` field that `sw.js` never reads
- Where: `server/api.py:348` vs `server/static/sw.js:35`
- Evidence: `server/api.py:348`: `"tag": f"stc-{slot_id}",` — but `sw.js:35`: `var tag = 'stc-' + (data.room_id || '') + '-' + (data.push_id || data.push_index || Math.random().toString(36).slice(2));` never reads `data.tag`.
- Impact: Harmless today only because the fallback (`Math.random()`) happens to always be unique too, so the tag-uniqueness invariant still holds by coincidence rather than by design for lineup pushes. But it's dead/misleading code: a future change to the SW's tag formula that assumes `data.tag` is authoritative, or a future attempt to intentionally collapse duplicate timetable notifications via a stable per-slot tag, would silently do nothing.
- Fix: Either remove the unused `"tag"` field from the lineup payload, or have `sw.js` actually prefer `data.tag` when present (still falling back to `push_id`/random for chat payloads that don't set it).

## [SEVERITY: LOW] Lineup scheduler's per-session loop lacks exception isolation (defense-in-depth gap)
- Where: `server/api.py:334-392`
- Evidence: `for session_id, slot_id in to_send: subs = db.execute(...); ...; slot = slot_map[slot_id]; artists = " b2b ".join(slot["artists"]); ...; for endpoint, p256dh, auth in subs: try: ... except WebPushException as e: ...`
- Impact: The per-*endpoint* `webpush()` call is correctly isolated (a bad subscription doesn't abort the loop — confirmed for invariant/focus #9), but nothing wraps the per-*session* work (payload construction, dict lookups) in its own try/except. If any single session's slot/subscription data is malformed, the exception propagates up to the outer `except Exception: logger.exception(...)` at `server/api.py:395-396`, aborting the whole `to_send` batch — every other due session in that 60s cycle silently gets no notification (and won't retry, since the slot exits the 60s window on the next poll). Low likelihood given current data shapes, but the chat push path (`chat_ws.py:942-953`, one `asyncio.create_task` per user) is fully isolated per-user by contrast.
- Fix: Wrap the body of the `for session_id, slot_id in to_send:` loop in its own try/except that logs and `continue`s, matching the isolation chat push already has per-user.

## Invariant check
1. Fresh `dict(claims)` per pywebpush call in both paths — HOLDS: `server/api.py:372` (lineup), `server/chat_ws.py:478` (chat).
2. Unique notification tags via `push_id` — HOLDS for chat (`server/chat_ws.py:451`, `sw.js:35`); holds for lineup only incidentally via the `Math.random()` fallback since lineup never sends `push_id`/`room_id` (see LOW finding above re: dead `tag` field).
3. `notificationclick` does local work (cache write, postMessage+focus, openWindow) before network (ack/log) — HOLDS: `server/static/sw.js:69-88`.
4. `client.navigate()` never combined with `postMessage` — HOLDS: `sw.js` never calls `client.navigate()` anywhere; pages navigate themselves via `window.location.href` on the `postMessage` (`chat.html:4557`, `render.py:637`).
5. `_repairPushSubscription` gated by `push_enabled` flag; `_enableAllNotifications` reports success only on subscribe+POST both succeeding — HOLDS: `chat.html:3885` (`if (storageGet('push_enabled') !== '1') return;`), `chat.html:3865-3870` (`if (!postRes.ok) { ...return false; }`). But see HIGH finding above — the gate is correct, the *coverage* of when repair triggers is incomplete (only fires when subscription is fully absent).
6. Dead subscriptions (410) auto-pruned in both push paths — HOLDS: `server/api.py:375-382` (lineup), `server/chat_ws.py:480-488` (chat).

## Verified clean
- **VAPID key path handling (#7)**: both `server/api.py:367` and `server/chat_ws.py:410,472` read the identical `VAPID_PRIVATE_KEY` env var (set once, to the Docker path in production per `deploy.sh:62-64`) — no possibility of divergence since it's the same value, not two independently-configured paths.
- **Scheduler dedup / restart safety (#8)**: `sent_notifications` is DB-persisted (`server/api.py:141-146`) and only inserted after `any_sent` is true (`server/api.py:384-389`), so a mid-window restart cannot cause a double-send. DST edge cases don't apply meaningfully to this deployment given the event runs in July (outside CET/CEST transition dates in `server/api.py:894-907`).
- **One bad target doesn't block others (#9, chat side)**: each push target gets its own `asyncio.create_task(_push_or_defer(...))` (`server/chat_ws.py:942-953`), fully isolating failures per user. Payload sizes are all small, bounded strings (`text_preview[:100]`/`[:80]`, `server/chat_ws.py:420,423,426`) — no risk of exceeding push service payload limits.
- **Badge consistency (#11)**: client-side `_updateAppBadge()` recomputes from the same `unreadByRoom` state driven by `badge_counts`/`badge_update` WS events (`chat.html:1472-1512`, `4605-4608`); the push payload's `total_unread` (`server/chat_ws.py:444`) is computed from the same `get_unread_counts` source of truth used by the WS path — no structural inconsistency, only an inherent (harmless) race window between push send time and WS delivery time.
- **Idle detection (30s fallback + `sendBeacon`)**: `visible` WS event refreshes `_last_ws_activity` every ≤20s while foregrounded (`chat.html:4560-4567`, `server/chat_ws.py:1180-1181`), safely under the 30s threshold used at `server/chat_ws.py:931`; `sendBeacon` fires on both `visibilitychange(hidden)` and `pagehide` (`chat.html:4570-4580`) to the correctly cookie-authenticated `/chat/api/push/idle` endpoint (`server/chat_api.py:1484-1491`).
