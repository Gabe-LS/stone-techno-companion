# iOS PWA Push Notification Click Navigation — What Actually Works

Tested every service worker navigation strategy on iOS to determine which ones reliably navigate to a target URL when a user taps a push notification in a standalone PWA.

**TL;DR**: `postMessage` and `client.navigate()` both work. `openWindow()` is broken. iOS foregrounds the app *before* the SW runs, so the page is always awake. Declarative Web Push is not yet natively supported.

**TL;DR 2 (production follow-up)**: none of the above matters if `notificationclick` never fires — and iOS silently drops it for any notification that **replaced** an earlier one via `tag` reuse, and for notifications shown before a SW update. Use a **unique tag per notification**, do all **local work first** in the click handler, and prune old notifications with `getNotifications()` + `close()` instead of tag replacement. See [Production Findings](#production-findings-july-2026-follow-up).

## Test Setup

- **Device**: iPhone, iOS 18.x
- **Mode**: Standalone PWA (added to home screen)
- **Date**: July 2026
- **Method**: Custom diagnostic PWA with two pages (source + target), its own service worker, and a shared IndexedDB log. Every event is timestamped — page lifecycle events, SW actions, cache reads, navigation results. Each strategy tested independently: open source page, background app, send push, tap notification, check which page loads and read the log.
- **Diagnostic tool**: [github.com/gabrielelosurdo/push-diag](https://github.com/gabrielelosurdo/push-diag) (standalone, no dependencies on any app)

## The Key Discovery

**iOS brings the PWA to foreground BEFORE the service worker's `notificationclick` handler executes.**

In every single test, `clients.matchAll()` reported the page as `visibilityState: "visible"` and `focused: true`. The page's `focus` and `visibilitychange` events fired 1-4ms before the SW's `click-start`.

This means:
- The page's JavaScript is fully alive when the SW communicates with it
- `postMessage` is received immediately
- `window.location.href` assignment works
- `client.navigate()` resolves successfully
- The "frozen page" theory is wrong — at least on iOS 18.x

## Results

### postMessage + window.location.href — WORKS

```
SW: clients.matchAll() → [vis=visible, focused=true]
SW: client.postMessage({type:'navigate', url})
Page: received postMessage → window.location.href = url
Target page loaded in ~100-460ms
```

The SW sends a message, the page receives it and navigates itself. Works because the page is already visible when the message arrives.

### client.navigate() — WORKS

```
SW: clients.matchAll() → [vis=visible, focused=true]
SW: client.navigate(url) → resolved with WindowClient
Target page loaded in ~90-125ms
```

`client.navigate()` resolves successfully and the page loads at the target URL. Despite widespread reports of it being "unreliable on iOS PWA standalone", it worked consistently in testing. The prior reports may have been about older iOS versions or different timing conditions.

### clients.openWindow() — FAILS

```
SW: clients.openWindow(url) → resolved with null
Target page never loaded. User stays on source page.
```

`openWindow()` silently returns `null` when a PWA window already exists. No error thrown, no navigation. This is the only strategy that fails. **Any SW code that uses `openWindow()` as the primary path for existing windows will break on iOS.**

Note: `openWindow()` works correctly when the app is fully closed (no existing window) — iOS opens the PWA at the specified URL.

### Cache Storage fallback — WORKS (data channel)

```
SW: caches.open('push-cache').put('/_nav', new Response(url))
Page: (on visibilitychange) caches.open('push-cache').match('/_nav') → found URL
```

The SW writes the target URL to Cache Storage before attempting any navigation. The page reads it when it becomes visible. The data arrives reliably within 300ms. This works as a communication channel, but requires the page to have its own `window.location.href` navigation code in the cache-reading handler.

### Declarative Web Push — NOT NATIVELY SUPPORTED

```json
{
  "web_push": "8-0-3-0",
  "notification": {
    "title": "...",
    "navigate": "https://example.com/target"
  }
}
```

The browser did not handle this natively. The SW's `push` event fired normally (fallback behavior). Declarative Web Push was introduced in iOS 18.4 / Safari 18.5, but did not activate in this test. May require specific conditions or a newer WebKit build.

## Summary Table

| Strategy | Works? | Notes |
|---|---|---|
| `postMessage` + `location.href` | **Yes** | Page always visible. Most flexible. |
| `client.navigate()` | **Yes** | Simplest. No page-side handler needed. |
| `openWindow()` | **No** | Returns null when window exists. |
| Cache Storage fallback | Data arrives | Needs page-side nav code. Good safety net. |
| Declarative Web Push | Not yet | Falls back to SW handler. |

## Recommended Implementation

Updated with the production findings: unique tag in the push handler, local-first ordering in the click handler.

```js
// Service worker — push handler
self.addEventListener('push', function(event) {
  var data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || 'Notification', {
      body: data.body || '',
      // CRITICAL: unique tag per notification. iOS drops notificationclick
      // on notifications that replaced an earlier one (same tag).
      tag: 'push-' + (data.url || Math.random().toString(36).slice(2)),
      data: { url: data.url || '/' },
    })
    // any analytics/ack fetch goes HERE, after showNotification
  );
});

// Service worker — notificationclick handler
self.addEventListener('notificationclick', function(event) {
  event.preventDefault();
  event.notification.close();

  var url = (event.notification.data && event.notification.data.url) || '/';
  var fullUrl = new URL(url, self.location.origin).href;

  event.waitUntil(
    // LOCAL WORK ONLY until navigation is triggered — iOS may kill the SW
    // moments after foregrounding the app; a network fetch here can abort
    // everything downstream.
    // Write to cache as safety net
    caches.open('push-nav').then(function(cache) {
      return cache.put('/_pending', new Response(url));
    }).then(function() {
      return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    }).then(function(list) {
      // Primary: postMessage to any client (page is always visible on iOS)
      for (var i = 0; i < list.length; i++) {
        list[i].postMessage({ type: 'navigate', url: fullUrl });
        list[i].focus();
        return;
      }
      // Fallback: no window exists (app was fully closed)
      return self.clients.openWindow(fullUrl);
    })
    // any analytics/ack fetch goes HERE, chained after the local work
  );
});
```

```js
// Page — receive navigation from SW
navigator.serviceWorker.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'navigate') {
    window.location.href = e.data.url;
  }
});
```

### Why not just use `client.navigate()`?

It works, but `postMessage` + `window.location.href` gives you more control on the page side — you can log, clean up state, or conditionally navigate. Both are valid.

### Why keep the cache fallback?

Belt and suspenders. If a future iOS update changes the foreground timing (making the page frozen when the SW runs), the cache fallback catches it. Cost is ~10 lines of code.

## Production Findings (July 2026 follow-up)

The strategies above all passed in the isolated diag PWA, yet the real app's notification taps still landed on `start_url` with no event. Debugging that gap (server-side SW timeline logging, clean reinstalls, option-by-option bisection) surfaced four findings this tool's single-push-per-test methodology could not catch.

### 1. Tag reuse kills notificationclick — THE BIG ONE

**iOS silently drops `notificationclick` for any notification that replaced an earlier one (same `tag`).** The tap opens the PWA at `start_url`. No event, no error, nothing to observe from the SW.

The app tagged chat notifications per room (`chat-{room_id}`, `renotify: true`) so each new message replaced the room's existing notification — standard practice on Chrome/Android. On iOS, in an active room, every organic notification is therefore a *replacement*, and every tap on it is dead. One-off test pushes (a room URL, an old message URL) were never replaced, so they worked — creating the false impression that the *URL* determined success.

The diag tool never caught this because each strategy test sent a single isolated push. A replaced-notification test case would need: push A (tag X) → push B (same tag X) → tap B.

**Rule: unique tag per notification.** Derive it from the target URL or a random suffix:

```js
tag: 'push-' + (data.url || Math.random().toString(36).slice(2)),
```

### 2. SW updates orphan displayed notifications

Notifications shown by a previous version of the service worker do not dispatch `notificationclick` after the SW is updated (byte-diff → install → `skipWaiting`/`claim`). Tapping them just launches the app.

Testing consequence: after every sw.js deploy, notifications already on the lock screen are dead. **Always test with a freshly delivered notification** — open the app once so the new SW activates, then send a new push. During iterative development this orphaning poisons the first tap after every deploy, which makes results look random.

### 3. Local work first in notificationclick

iOS may terminate the SW moments after the app comes to the foreground — `event.waitUntil()` does not reliably keep it alive through network round-trips. If a `fetch()` (analytics ack, server logging) is sequenced *before* the cache write and `postMessage`, the SW can die mid-fetch and the navigation never happens, indistinguishable from the click not firing.

**Rule: cache write → `postMessage` → `focus()` first (all local, ~10ms total), network calls last.**

### 4. Do not combine postMessage with client.navigate()

Both work *individually* (see Results above). Combined — SW posts the message, page starts `window.location.href`, SW then calls `client.navigate()` on the same client — the two navigations race and abort each other, and the user stays on the source page. Pick one; `postMessage` gives the page control, and the cache entry covers the no-listener case.

Related page-side rule: don't latch "navigation done" flags permanently before the navigation actually commits, and don't destroy the cache entry's information without navigating — an aborted navigation otherwise disarms every fallback until the page reloads. A short timed latch (~3s) self-heals.

### Pruning old notifications without tag replacement

Unique tags mean notifications stack (one per message) instead of replacing per room. To keep Notification Center tidy, prune from the SW with `registration.getNotifications()` + `close()` — programmatic close does not go through tag replacement, so it does not trigger finding #1 on the notification you keep:

```js
self.addEventListener('push', function (event) {
  var data = event.data ? event.data.json() : {};
  var options = {
    body: data.body || '',
    tag: 'push-' + (data.url || Math.random().toString(36).slice(2)),  // unique, never reused
    data: { url: data.url || '/', group: data.group || null },         // group = e.g. room id
  };
  event.waitUntil(
    // Prune BEFORE showing: close older notifications of the same group,
    // keeping at most N. The new notification is created fresh (never a
    // replacement), so its click always dispatches.
    self.registration.getNotifications().then(function (list) {
      var same = list.filter(function (n) {
        return n.data && n.data.group && n.data.group === data.group;
      });
      // keep the newest (N-1) existing ones; with N = 3, close all but 2
      same.slice(0, Math.max(0, same.length - 2)).forEach(function (n) { n.close(); });
    }).catch(function () {}).then(function () {
      return self.registration.showNotification(data.title || 'New message', options);
    })
  );
});
```

Notes:
- `getNotifications()` returns currently displayed notifications for this registration, oldest first.
- Closing and re-showing is NOT a safe substitute for tag replacement if you close the notification and re-show it with the *same* tag — keep tags unique regardless.
- Pruning happens on the next push, so the group cap is eventual, not strict: momentarily N+1 notifications can be visible before `showNotification` resolves.
- The tapped notification is removed by iOS automatically; `event.notification.close()` in `notificationclick` covers the rest.

### Debugging technique: server-side SW timeline

An iOS SW is a black box — no console, and Web Inspector rarely attaches at the right moment. What worked:

1. A `SW_VERSION` constant in sw.js, included in every ack/log POST — proves *which* SW version actually ran on the device (finding #2 makes this essential).
2. A trivial logging endpoint (`POST /api/swlog` → server log line). The SW posts steps (`push-received`, `click-done` with the outcome), the pages post theirs (`postmessage-received`, `cache-hit`, `nav-go`, `nav-blocked`).
3. Because of finding #3, SW-side logs go *after* the local work — the page-side logs are the source of truth for whether the mechanism fired.

The absence pattern is diagnostic by itself: `push-received` present + `click-done` absent + no page-side events = the click was never dispatched (finding #1 or #2), not a navigation failure.

## Common Misconceptions Debunked

1. **"The page is frozen when the notification is tapped"** — False on iOS 18.x. iOS foregrounds the app first, then fires `notificationclick`. The page is alive.

2. **"`client.navigate()` is unreliable on iOS PWA"** — Not reproducible. It resolved successfully in every test. May have been true on older iOS versions.

3. **"`openWindow()` is the recommended approach"** — It is for the cold-start case (no window). But it silently fails when a window exists. Never use it as the primary strategy for backgrounded PWAs.

4. **"You need Declarative Web Push for iOS"** — Not yet available in practice. The traditional SW handler works fine.

5. **"Cache Storage is unreliable on iOS"** — It works. Both reads and writes succeed. Viable as a SW-to-page communication channel.

6. **"Tag + renotify replacement is safe, it's standard on Android"** — On iOS, a notification that replaced another via tag reuse does not dispatch `notificationclick` when tapped. Use unique tags and prune with `getNotifications()` + `close()` instead.

7. **"If postMessage works and navigate() works, using both is a belt-and-suspenders win"** — They race and abort each other. Use `postMessage` (+ cache fallback), not both navigations.

8. **"waitUntil keeps the SW alive, so ordering inside notificationclick doesn't matter"** — iOS can kill the SW shortly after the app foregrounds, even mid-waitUntil. Network calls placed before the local navigation work can silently abort it. Local first, network last.

9. **"If the tap opens the app, the click handler must have run"** — No. Replaced notifications (see 6) and notifications shown by a since-updated SW both open the app at `start_url` without ever firing `notificationclick`.

## Event Timeline (annotated example)

```
T+0.000  page   visibilitychange  hidden        ← user backgrounded the app
T+8.000  sw     push-received                   ← push arrives while app is in background
T+10.00  page   focus                           ← iOS foregrounds the app (user tapped notification)
T+10.00  page   visibilitychange  visible       ← page is now visible
T+10.00  sw     click-start                     ← SW notificationclick fires AFTER page is visible
T+10.01  sw     cache-written                   ← safety net stored
T+10.01  sw     clients-found     [vis=visible] ← confirms: page is visible
T+10.01  sw     postMessage sent                ← message dispatched
T+10.01  page   postMessage received            ← page receives it immediately
T+10.01  page   window.location.href = url      ← navigation triggered
T+10.10  target page-load                       ← target page loads (~100ms later)
```
