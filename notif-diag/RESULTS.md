# Web Notification API — Cross-Platform Verified Behavior

Tested every Web Notification API feature to determine what actually works in practice across platforms. Built a standalone diagnostic tool (this folder) with server-side logging for real-time inspection.

**TL;DR**: Chromium has the full toolkit — tag replacement, `close()`, prune-then-show, action buttons all work. iOS PWA has `getNotifications()` and `notificationclick` but `tag` replacement and `notification.close()` are complete no-ops. The only way to manage notification volume on iOS is server-side throttling.

## Test Setup

- **iOS**: iPhone, iOS 18.x, standalone PWA (added to home screen), July 2026
- **Chromium**: Playwright-automated Chromium (macOS), notification permission granted, July 2026
- **Method**: Custom diagnostic PWA (`notif-diag/`) with its own service worker. Every event timestamped and POSTed to the server, written to `logs/` as JSONL for real-time `tail -f` inspection. SW snapshots `getNotifications()` before and after every `showNotification()` call. Cache Storage breadcrumbs for `notificationclick` verification (survives SW kill). Multi-delay verification (500ms + 3s) to rule out async timing issues.
- **Playwright WebKit**: not a valid test target — `showNotification()` is non-functional in Playwright's stripped-down WebKit build. iOS device tests are the authoritative WebKit/Safari results.

## Feature Detection

Features present in `Notification.prototype` (does not mean they work — see behavioral tests below):

| Feature | Chromium | iOS PWA |
|---|---|---|
| `Notification` API | Yes | Yes |
| `showNotification()` | Yes | Yes |
| `getNotifications()` | Yes | Yes |
| `tag` | Yes | Yes (but ignored — see below) |
| `data` | Yes | Yes |
| `silent` | Yes | Yes |
| `renotify` | Yes | **No** |
| `actions` | Yes (max 2) | **No** |
| `requireInteraction` | Yes | **No** |
| `badge` | Yes | **No** |
| `image` | No (in prototype) | **No** |
| `vibrate` | Yes | **No** |
| `timestamp` | Yes | **No** |
| `setAppBadge` | Yes | Yes |
| `clearAppBadge` | Yes | Yes |
| `Notification.maxActions` | 2 | undefined |
| `new Notification()` | Yes | Yes |

## Tag Replacement

**Does showing a notification with the same `tag` as an existing one replace it?**

### Chromium — YES

```
showNotification("Msg 1", {tag: "room-A"})
  → getNotifications(): count=1

showNotification("Msg 2", {tag: "room-A"})
  → getNotifications(): count=1, title="Msg 2"
```

Tag replacement works correctly. The old notification is removed, the new one takes its place. By default the replacement is silent (no sound/vibration). With `renotify: true`, it re-alerts.

### iOS PWA — NO (WebKit Bug #258922)

```
showNotification("Msg 1", {tag: "room-A"})
  → getNotifications(): count=1

showNotification("Msg 2", {tag: "room-A"})
  → getNotifications(): count=2 (both "Msg 1" and "Msg 2" present)
```

iOS ignores the `tag` property entirely for replacement. Every `showNotification()` call creates a new notification regardless of tag. Verified with 5-second delays between pushes to rule out async processing. Two notifications with the same tag coexist in both the notification center and `getNotifications()` results.

The `tag` property IS stored — `getNotifications({tag: "room-A"})` correctly filters by it, and `notification.tag` returns the value. iOS just doesn't use it for coalescing.

WebKit Bug #258922 (filed July 2023, status P2 NEW, confirmed still unresolved July 2026).

## getNotifications()

**Can we read the list of displayed notifications?**

### Chromium — FULL SUPPORT

- From SW context: returns all displayed notifications with correct `title`, `body`, `tag`, `data`
- From page context (`navigator.serviceWorker.ready.then(reg => reg.getNotifications())`): same results
- Tag filter (`{tag: "foo"}`): returns only matching notifications
- Data readback: complex nested objects (`{roomId: "xyz", count: 42, nested: {foo: "bar"}, arr: [1,2,3]}`) survive round-trip intact

### iOS PWA — WORKS

All of the above works on iOS:
- SW context: returns correct count and data
- Page context: works (needs ~4s after push for reliable results)
- Tag filter: works correctly
- Data readback: complex nested objects intact

This was a surprise — online sources (MDN compat data, developer reports) claim `getNotifications()` always returns empty on iOS. Our testing shows it works on iOS 18.x.

## notification.close()

**Can we programmatically remove a notification?**

### Chromium — YES (instant)

```
From SW:
  getNotifications({tag: "x"}): count=1
  notification.close()
  getNotifications({tag: "x"}): count=0  ← notification removed instantly

From page:
  reg.getNotifications({tag: "x"}): count=1
  notification.close()
  [3s delay]
  reg.getNotifications({tag: "x"}): count=0  ← notification removed
```

Works from both SW and page context. Notification is removed from the notification center and from `getNotifications()` results immediately.

### iOS PWA — YES, with two critical rules

**Rule 1: The notification must be at least 30 seconds old.** `close()` on notifications younger than 30s is silently ignored. Tested at 10s, 15s, 20s, 25s — all fail. At 30s — works. At 35s — works reliably.

**Rule 2: Do NOT call `getNotifications()` after `close()` in the same handler.** Calling `getNotifications()` after `close()` cancels the pending close, regardless of notification age. The close must be fire-and-forget.

```
FAILS (notification < 30s old):
  showNotification()
  [3s delay]
  getNotifications() → close()  ← notification is 3s old → no effect

FAILS (getNotifications after close):
  showNotification()
  [35s delay]
  getNotifications() → close() → [any delay] → getNotifications()  ← cancels the close

WORKS (aged + fire-and-forget):
  showNotification()
  [35s delay]
  getNotifications() → close() → return immediately  ← notification removed
```

Works from both SW and page context when both rules are followed. Filtered close (closing a subset while others remain) also works.

These rules were discovered through extensive iterative testing — no documentation or developer report mentions either constraint. The 30-second minimum is likely iOS's anti-abuse mechanism to prevent apps from showing and immediately hiding notifications (which could be used to wake the SW without user-visible activity).

## Prune-then-show

**Can we close old notifications for a group and show a consolidated one?**

This is the core strategy for one-notification-per-room: `getNotifications()` → filter by `data.roomId` → `close()` each → `showNotification()` with updated count.

### Chromium — YES (instant)

```
Show 3 notifications for room-x (unique tags, data.roomId="room-x")
  → getNotifications(): total=3, room-x=3

pruneAndShow(roomId="room-x", title="3 new messages")
  → getNotifications(): total=1, room-x=1, title="3 new messages"
```

Works perfectly. Old notifications removed, consolidated notification shown.

### iOS PWA — YES (when notifications are ≥30s old)

```
Show 2 notifications for room-x
  [35s delay]
getNotifications() → filter roomId → close() old → showNotification("2 new messages")
  → Old notifications disappear, consolidated one stays
  → Result: exactly 1 notification visible
```

Verified working (test 5r, 5t). The prune-then-show strategy works on iOS as long as the old notifications have been displayed for ≥30 seconds. With a 60-second server-side debounce between pushes, old notifications are always ≥60s old when the next push arrives — well past the 30s threshold.

**Critical**: the handler must NOT call `getNotifications()` after the close+show sequence. Fire-and-forget.

## Per-Room Clear on Visit

**Can we clear notifications for room A without affecting room B?**

### Chromium — YES (instant)

```
Show notification for room-A and room-B (unique tags, different data.roomId)
  → getNotifications(): total=2, A=1, B=1

Close room-A only:
  reg.getNotifications() → filter data.roomId === "rc-a" → close()
  → getNotifications(): total=1, A=0, B=1
```

Room A's notification removed, room B's untouched.

### iOS PWA — YES (when notifications are ≥30s old)

```
Show notifications for room-A and room-B
  [35s delay]
getNotifications() → filter roomId === "room-a" → close() → return (fire-and-forget)
  → room-A notification disappears, room-B stays
```

Verified working (test 5s). Same 30-second age rule and fire-and-forget requirement apply. In practice, if the user visits a room more than 30 seconds after the last push, the clear works. Edge case: visiting within 30 seconds of the push — the notification stays until the user manually dismisses it.

## notificationclick

**Does the click handler fire when the user taps a notification?**

### iOS PWA — YES (with caveats)

The `notificationclick` handler fires and `event.notification.data` contains the full data object. Verified via Cache Storage breadcrumb (local write before any network call, survives SW kill).

**Caveat 1: SW update orphans notifications.** Notifications shown by a previous SW version do NOT dispatch `notificationclick` after the SW updates (`skipWaiting` + `claim`). The tap opens the app at `start_url` with no event. Always test with a freshly delivered notification after the SW activates.

**Caveat 2: local work first.** iOS can kill the SW moments after foregrounding the app. Network calls (`fetch`) placed before local work (Cache Storage write, `postMessage`) can silently abort. Our initial test handler used only `fetch` for logging — zero click events reached the server. After switching to cache-first + `sendBeacon` + `fetch`, all three delivery methods succeeded.

**Caveat 3: tag replacement kills clicks (from diag/RESULTS.md).** A notification that replaced another via tag reuse does not dispatch `notificationclick` on iOS. Since tag replacement doesn't work anyway (see above), this is moot if using unique tags — but don't attempt tag replacement as a workaround.

### Chromium — not tested via automation

`notificationclick` requires user interaction (tapping), which Playwright cannot simulate for OS-level notifications. Chromium's `notificationclick` is well-documented and reliable.

## silent Option

**Does `silent: true` suppress sound/vibration?**

### Chromium — YES

`silent` property preserved in `getNotifications()` readback. Notification shown without sound.

### iOS PWA — YES

Confirmed working. A push with `silent: true` arrived without sound. A subsequent push without `silent` produced sound. Side-by-side comparison verified the difference.

Note: `renotify` is not in the iOS prototype, so silent tag-replacement (the Chrome pattern of same-tag + `renotify: false`) is not applicable.

## actions (Buttons)

### Chromium — YES (max 2)

`Notification.maxActions = 2`. Notifications show action buttons. `event.action` in `notificationclick` reports which button was pressed.

### iOS PWA — NO

`actions` not in `Notification.prototype`. `Notification.maxActions` is `undefined`. Notifications show only the generic system "View" action.

## data Option

### Chromium — YES

Complex nested objects survive round-trip through `showNotification()` → `getNotifications()` → `notification.data` and through `notificationclick` → `event.notification.data`.

### iOS PWA — YES

Same behavior. Complex nested objects intact in both `getNotifications()` readback and `notificationclick` handler. Earlier reports of `data` being `null` in `notificationclick` on iOS were not reproduced — may have been related to SW update orphaning (see notificationclick caveats).

## notificationclose Event

**Does the SW receive an event when the user dismisses a notification?**

### iOS PWA — NO

Zero `notificationclose` events reached the server across all tests, including explicit swipe-to-dismiss. The SW handler uses the same cache-first + `sendBeacon` + `fetch` pattern as `notificationclick`, so delivery is not the issue — the event simply never fires.

Programmatic `close()` also does not fire `notificationclose` (consistent with the spec, which says it only fires for user-initiated dismissal — but on iOS, it doesn't fire for that either).

## Rapid Fire

**What happens with many notifications in quick succession?**

### Chromium

- 5 pushes, unique tags, 500ms apart → 5 separate notifications
- 5 pushes, same tag, 500ms apart → 1 notification (tag replacement works)

### iOS PWA

- 5 pushes, unique tags, 500ms apart → 4 notifications (some may be lost/batched by iOS)
- 5 pushes, same tag, 500ms apart → 3 notifications (tag ignored, some lost)
- 10 pushes, 200ms apart → 3 notifications (significant loss at high rate)

iOS appears to throttle/drop notifications when they arrive in rapid succession. The exact behavior varies — not all pushes result in displayed notifications.

## setAppBadge / clearAppBadge

### Chromium — YES

Both methods available and callable.

### iOS PWA — YES

Both methods available and callable. `setAppBadge(5)` shows a numeric badge on the app icon. `clearAppBadge()` removes it.

## showNotification from Page Context

**Can we show persistent notifications without a push event?**

### Chromium — YES

`registration.showNotification()` from page JS works. Notification appears and is visible in `getNotifications()`.

### iOS PWA — YES

Same behavior. Useful for showing notifications triggered by WebSocket messages while the app is in the foreground.

## new Notification() (Non-persistent)

### Chromium — YES

Constructor works. Creates a non-persistent notification. Not visible in `getNotifications()` (spec-correct).

### iOS PWA — YES

Constructor works (unlike Android Chrome, which throws `TypeError`). Note: non-persistent notifications are not recommended for production use — they don't survive page close and aren't visible to `getNotifications()`.

## Summary Table

| Feature | Chromium | iOS PWA |
|---|---|---|
| `showNotification()` | Pass | Pass |
| Tag replacement | **Pass** | **Fail** (ignored, WebKit Bug #258922) |
| `getNotifications()` from SW | **Pass** | **Pass** |
| `getNotifications()` from page | **Pass** | **Pass** |
| `getNotifications({tag})` filter | **Pass** | **Pass** |
| `notification.data` round-trip | **Pass** | **Pass** |
| `notification.close()` | **Pass** (instant) | **Pass** (≥30s age + fire-and-forget) |
| Prune-then-show | **Pass** (instant) | **Pass** (≥30s age + fire-and-forget) |
| Per-room clear | **Pass** (instant) | **Pass** (≥30s age + fire-and-forget) |
| `notificationclick` | Pass (documented) | **Pass** (fresh SW only) |
| `notificationclose` | Pass (documented) | **Fail** (never fires) |
| `silent: true` | **Pass** | **Pass** |
| `actions` (buttons) | **Pass** (max 2) | **Fail** (not supported) |
| `renotify` | **Pass** | **Fail** (not supported) |
| `requireInteraction` | **Pass** | **Fail** (not supported) |
| `setAppBadge` | **Pass** | **Pass** |
| `new Notification()` | **Pass** | **Pass** |
| Rapid fire (unique tags) | **Pass** (all delivered) | **Partial** (some dropped) |
| Rapid fire (same tag) | **Pass** (1 notification) | **Fail** (tag ignored) |

## iOS close() Rules (discovered July 2026)

These rules are not documented anywhere online. Discovered through iterative testing with this diagnostic tool.

1. **Minimum age: 30 seconds.** `close()` is silently ignored on notifications younger than 30 seconds. Tested at 10s, 15s, 20s, 25s (all fail), 30s (works), 35s (reliable).

2. **Fire-and-forget.** Calling `getNotifications()` after `close()` in the same handler cancels the pending close. The close must be the last notification-related API call in the handler.

3. **Both contexts work.** When both rules are followed, `close()` works from SW context (via `postMessage` handler) and page context (`reg.getNotifications()` → `close()`).

4. **Filtered close works.** Closing a subset of notifications (by tag or by data property) while others remain — works. Only the targeted notifications are removed.

5. **Combined with showNotification.** `getNotifications()` → `close()` old → `showNotification()` new — works. The `showNotification()` call does not interfere with the pending close.

## Implications for Notification Strategy

### What works on iOS

1. **`getNotifications()`** — read all displayed notifications with full data
2. **`notification.close()`** — works when notification is ≥30s old and handler doesn't read notifications afterward
3. **Prune-then-show** — close old + show consolidated, works when old notifications are ≥30s
4. **Per-room clear** — close notifications for one room, keep others
5. **`notificationclick`** — fires reliably for notifications shown by the current SW version
6. **`silent: true`** — suppresses sound
7. **`data`** — complex objects survive round-trip
8. **`setAppBadge()`** — numeric app icon badge

### What does NOT work on iOS

1. **Tag replacement** — every push creates a new notification regardless of tag
2. **`close()` on notifications < 30s old** — silently ignored
3. **`getNotifications()` after `close()`** — cancels the close
4. **`notificationclose` event** — never fires (cannot detect user dismissal)
5. **`actions`** — no custom buttons
6. **`renotify`** — cannot control re-alerting on replacement (replacement itself doesn't work)

### The strategy (works on all platforms)

With a **60-second progressive debounce**, prune-then-show works on both Chromium and iOS:

1. **Push 1** arrives → notification shown (sound)
2. **60 seconds** pass (debounce, no push during this time)
3. **Push 2** arrives → SW does prune-then-show:
   - `getNotifications()` → find old notification (it's 60s old, ≥30s threshold)
   - `close()` old
   - `showNotification("8 new messages")` → consolidated notification
   - Return immediately (fire-and-forget)
4. **Result**: 1 notification per room at all times

On **Chromium**: old notification removed instantly, new one appears. Seamless.

On **iOS**: old notification removed (it's ≥30s old, rule satisfied), new one appears. Same end result.

**Per-room clear on visit**: when user opens room X, page calls `getNotifications()` → filter by roomId → `close()` → return. Works on iOS as long as the notification is ≥30s old (which it will be — the debounce is 60s). Edge case: user opens room within 30s of the push — notification stays until manually dismissed or until it ages past 30s.

**Payload structure**:
```json
{
  "title": "#General",
  "body": "8 new messages",
  "room_id": "abc123",
  "count": 8,
  "url": "/chat/msg/first-unread-id",
  "silent": true,
  "push_index": 42
}
```

**SW push handler**:
```js
self.addEventListener('push', function(event) {
  var data = event.data.json();
  var title = data.title;
  var body = data.count === 1 ? data.body : data.count + ' new messages';
  var tag = 'chat-' + data.room_id + '-' + data.push_index;
  var options = {
    body: body,
    tag: tag,
    data: { roomId: data.room_id, url: data.url, count: data.count },
    silent: data.silent || false,
  };

  event.waitUntil(
    // prune old notifications for this room (fire-and-forget close)
    self.registration.getNotifications().then(function(list) {
      list.filter(function(n) {
        return n.data && n.data.roomId === data.room_id;
      }).forEach(function(n) { n.close(); });
      // show new consolidated notification — do NOT call getNotifications after
      return self.registration.showNotification(title, options);
    })
  );
});
```

This single code path works on all platforms. On Chromium, close is instant. On iOS, close works because the previous notification is always ≥60s old (debounce guarantees this).

**App icon badge** (`setAppBadge` / `clearAppBadge`):

The PWA app icon badge shows total unread count. Verified working on iOS PWA and desktop Chrome. Not supported on Android (Android auto-manages a dot badge based on notification presence).

```
Platform behavior:
  iOS PWA:        setAppBadge(N) → numeric "N" on app icon. clearAppBadge() → removed.
  Desktop Chrome: setAppBadge(N) → numeric badge on dock/taskbar icon.
  Android:        Not supported — OS shows dot automatically when notifications exist.
```

Update points:

1. **SW push handler** — after `showNotification()`, set badge to total unread. The server includes `total_unread` in the push payload (sum of unread across all rooms for this user).

   ```js
   // at end of push handler, after showNotification:
   if (data.total_unread && navigator.setAppBadge) {
     navigator.setAppBadge(data.total_unread);
   }
   ```

2. **Page (on `badge_update` / `badge_counts` WS event)** — recalculate total unread from `unreadByRoom` and set badge. When total is 0, clear.

   ```js
   function updateAppBadge() {
     var total = Object.values(unreadByRoom).reduce(function(s, n) { return s + n; }, 0);
     if (navigator.setAppBadge) {
       if (total > 0) navigator.setAppBadge(total);
       else navigator.clearAppBadge();
     }
   }
   ```

3. **Page (on `mark_read`)** — after clearing a room's unread, recalculate and update badge.

4. **Page (on room enter)** — if all rooms become read, `clearAppBadge()`.

The badge persists even when the app is closed — `setAppBadge` writes to the OS. It stays until explicitly cleared. On iOS, the badge survives app suspension, force-quit, and device restart.

**Payload structure** (updated with `total_unread`):
```json
{
  "title": "#General",
  "body": "8 new messages",
  "room_id": "abc123",
  "count": 8,
  "total_unread": 12,
  "url": "/chat/msg/first-unread-id",
  "silent": true,
  "push_index": 42
}
```

`total_unread` is the sum of unread messages across ALL rooms for this user — not just the room being pushed. This allows the SW to set an accurate app badge even when the page isn't open.
