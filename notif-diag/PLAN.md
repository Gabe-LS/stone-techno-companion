# Notification System — Complete Specification & Implementation Plan

Complete reference for the notification system: existing behavior, new changes, iOS rules, and implementation details. Based on verified findings from `notif-diag/RESULTS.md`.

---

## Table of Contents

1. [Two Push Systems](#1-two-push-systems)
2. [iOS Rules](#2-ios-rules-discovered-july-2026)
3. [Chat Push: Server Side](#3-chat-push-server-side)
4. [Chat Push: Service Worker](#4-chat-push-service-worker)
5. [Chat Push: Page Side](#5-chat-push-page-side)
6. [Timetable Push](#6-timetable-push)
7. [Browser Notifications (fallback)](#7-browser-notifications-fallback)
8. [App Icon Badge](#8-app-icon-badge)
9. [Push Subscription Management](#9-push-subscription-management)
10. [Push Ack & Tracking](#10-push-ack--tracking)
11. [Idle Detection & Foreground Suppression](#11-idle-detection--foreground-suppression)
12. [Notification Click & Navigation](#12-notification-click--navigation)
13. [Cross-Device Sync](#13-cross-device-sync)
14. [Lifecycle Examples](#14-lifecycle-examples)
15. [Changes by File](#15-changes-by-file)
16. [Edge Cases & Failure Modes](#16-edge-cases--failure-modes)

---

## 1. Two Push Systems

The app has two independent push notification systems:

| System | Source | Trigger | DB | Subscribers |
|---|---|---|---|---|
| **Timetable push** | `server/api.py` | Background scheduler, every 60s | `hearts.db` (`push_subscriptions`, `sent_notifications`) | Lineup sessions (anonymous, cookie-based) |
| **Chat push** | `server/chat_ws.py` | Message broadcast, after moderation | `chat.db` (`chat_push_subscriptions`) | Chat users (authenticated) |

They share the same SW (`sw.js`) and VAPID keys but use different subscription tables, different user identity models, and different dedup mechanisms. This plan covers both.

---

## 2. iOS Rules (discovered July 2026)

Verified on iOS 18.x PWA via `notif-diag/` diagnostic tool. None of these are documented online.

### 2.1 notification.close()

Works, with two rules:

1. **30-second minimum age.** `close()` is silently ignored on notifications younger than 30 seconds. Tested at 10, 15, 20, 25 (fail), 30, 35 (work).
2. **Fire-and-forget.** Calling `getNotifications()` after `close()` in the same handler cancels the pending close. `showNotification()` after `close()` is fine (verified — RESULTS.md rule 5), so close-then-show works. The rule is: never re-query notifications after closing.

Both SW context and page context work when rules are followed.

### 2.2 Tag replacement

Completely ignored (WebKit Bug #258922, unfixed since July 2023). Every `showNotification()` creates a new notification regardless of tag. `getNotifications({tag})` filtering works — iOS stores the tag but doesn't use it for coalescing.

### 2.3 notificationclick

Fires reliably, with caveats:

- **SW update orphans notifications.** Notifications shown by a previous SW version don't fire `notificationclick`. Tap opens app at `start_url` with no event.
- **Local work first.** iOS can kill the SW moments after foregrounding. Cache write + `postMessage` + `focus()` before any `fetch`.
- **Tag replacement kills click** (from `diag/RESULTS.md`). Moot since tag replacement doesn't work anyway.

### 2.4 What works

`getNotifications()` (SW + page, with tag filter), `notification.data` (complex objects), `silent: true`, `setAppBadge`/`clearAppBadge`, `showNotification()` from page, `new Notification()`.

### 2.5 What doesn't work

`renotify`, `actions`, `requireInteraction`, `badge` (notification option), `image`, `vibrate`, `timestamp`, `notificationclose` event.

---

## 3. Chat Push: Server Side

**File**: `server/chat_ws.py`

### 3.1 Push targeting (existing, verified correct)

When a message passes moderation (`_moderate_and_broadcast`, line 789), determine who gets a push:

```python
connected_uids = set(mgr.user_conns.keys())
all_targets = _get_room_notification_targets(db, room_id, user_id)
now = time.monotonic()
push_targets = [
    uid for uid in all_targets
    if uid not in connected_uids
    or now - mgr._last_ws_activity.get(uid, 0) > 30
]
```

A user gets a push if:
- NOT connected via WebSocket, OR
- Connected but idle (no user-initiated WS event in 30s AND no `visible` keepalive)

Target resolution differs by room type:
- **Regular room**: all members except sender, minus blocked
- **DM**: the other participant, minus blocked
- **Meetup**: all attendees except sender, minus blocked

**Verified**: `_get_room_notification_targets` (lines 260-288) correctly handles all three types and excludes blocked users bidirectionally.

### 3.2 Progressive debounce with trailing flush [NEW]

**Current** (verified, line 305): flat 10s **leading-edge** debounce per user+room — first message pushes immediately, messages inside the window are *dropped* (never represented in any push unless a later message arrives after the window).

**New**: keep leading-edge (first push is immediate) but add a **trailing consolidation flush**: when a push is suppressed by the debounce, schedule one deferred flush task at window expiry. The flush re-queries unread counts and sends a consolidated push. Without this, a burst that ends inside the window leaves the user with a stale "Marco: hey!" notification and badge=1 while N messages are unread. (The browser-notification fallback in section 7 already does a 10s trailing flush — this brings the push path in line.)

Window: 10s before the first push, 60s after it. Reset on `mark_read`.

```python
_push_debounce: dict[str, float] = {}          # last push time per user:room (existing, line 291)
_push_sent: dict[str, bool] = {}               # NEW: audible push sent since last mark_read?
_push_flush_tasks: dict[str, asyncio.Task] = {}  # NEW: pending trailing flush per user:room
_push_counter: int = 0                          # NEW: monotonic counter for unique tag generation
```

```python
async def _push_or_defer(user_id, room_id, room_type, room_name, sender_name, text_preview, msg_id):
    global _push_counter
    key = f"{user_id}:{room_id}"
    now = time.monotonic()
    last = _push_debounce.get(key, 0)
    if now - last > 1800:
        # Long silence (30 min): a new conversation burst re-alerts with sound.
        _push_sent.pop(key, None)
    window = 60 if _push_sent.get(key) else 10
    if now - last < window:
        # Suppressed — schedule ONE trailing flush at window expiry.
        if key not in _push_flush_tasks:
            delay = last + window - now
            _push_flush_tasks[key] = asyncio.create_task(
                _flush_push_later(key, delay, user_id, room_id, room_type, room_name))
        return
    _push_debounce[key] = now
    _push_sent[key] = True
    _push_counter += 1
    await _do_send_push(user_id, room_id, room_type, room_name,
                        sender_name, text_preview, msg_id,
                        silent=bool(_push_sent.get(key) and last > 0 and now - last < 1800),
                        push_index=_push_counter)

async def _flush_push_later(key, delay, user_id, room_id, room_type, room_name):
    await asyncio.sleep(delay)
    _push_flush_tasks.pop(key, None)
    # Re-enter: window has expired, so this will send
    await _push_or_defer(user_id, room_id, room_type, room_name, "", "", None)
```

**Correction from code review**: The current `_send_chat_push` (line 294) is called directly from `_moderate_and_broadcast` (line 808) via `asyncio.create_task`. The new `_push_or_defer` replaces `_send_chat_push` as the entry point. The actual sending logic (pywebpush call, VAPID setup, dead-sub cleanup) moves to `_do_send_push`.

**Silent flag logic** (corrected): The first push after a 30-min silence or after a `mark_read` reset has `silent=False` (sound). Every subsequent push within the same conversation burst has `silent=True`. The trailing flush always has `silent=True` (it consolidates, not alerts).

The 60s window guarantees every old notification is >=60s old when the next push (including a trailing flush) arrives — safely past the iOS 30s close threshold. The trailing flush fires exactly at window expiry, so the prune-then-show age guarantee holds for it too.

**Memory**: `_push_debounce`, `_push_sent`, and `_push_flush_tasks` grow unboundedly (one entry per user:room). Prune entries older than 2 hours in the existing purge loop (section 3.8).

### 3.3 Unread count query [NEW]

**Current**: sends latest message preview, no count.

**New**: reuse the existing `get_unread_counts(db, user_id)` (`chat_db.py:634`) — do NOT hand-roll new SQL. The existing function already handles three things a naive query gets wrong:

- **DMs**: recipients may have no `room_memberships` row — it unions `dm_participants` with `COALESCE(last_read_at, '1970-01-01')`
- **Expired messages**: filters `m.expires_at > now` (expired-but-not-yet-purged messages would inflate counts)
- **Own messages**: excludes `m.user_id != ?` (consistent with the in-memory badge counter)

```python
counts = get_unread_counts(db, user_id)  # existing — chat_db.py:634
room = counts.get(room_id)
count = room["count"] if room else 0
total_unread = sum(c["count"] for c in counts.values())
if count == 0:
    return  # mark_read from another device raced this push task — nothing to notify

# Only new query needed: first unread message ID (for the push URL)
last_read = room["last_read_at"] if room else "1970-01-01"
row = db.execute(
    "SELECT id FROM messages WHERE room_id = ? AND created_at > ? "
    "AND user_id != ? AND expires_at > ? ORDER BY created_at LIMIT 1",
    (room_id, last_read, user_id, now_iso),
).fetchone()
first_msg_id = row["id"] if row else None
```

Known mismatch (accepted): blocked senders' messages count toward `count`/`total_unread` — same as the existing in-memory badge path. The client filters blocked messages visually, so the icon badge can be slightly higher than what the user sees. Consistent with current behavior.

**DB connection**: `get_unread_counts` requires a `db` connection. Currently `_send_chat_push` opens and closes its own `get_chat_db()` (line 308-310). The new `_do_send_push` should open one db, do the count query + first_msg_id query + get_push_subscriptions, then close — one connection per push, not three.

### 3.4 Payload construction [NEW]

**Current payload** (verified, line 328-334):
```json
{"title": "#General", "body": "Marco: hey!", "tag": "chat-abc", "url": "/chat/msg/123"}
```

Note: The `tag` field in the payload is **dead** — the SW (line 35) ignores it and builds its own tag from `data.url`. This was the fix for the iOS tag-replacement click bug. The new payload drops this dead field.

**New payload**:
```json
{
  "title": "#General",
  "body": "8 new messages",
  "room_id": "abc123",
  "room_type": "general",
  "count": 8,
  "total_unread": 12,
  "url": "/chat/msg/first-unread-id",
  "silent": true,
  "push_index": 42
}
```

New fields:
- `room_id` — for SW-side prune filtering
- `count` — unread count for this room
- `total_unread` — sum across all rooms, for app badge
- `silent` — `true` for subsequent pushes (first push has sound)
- `push_index` — monotonic counter (NOT timestamp), guaranteed unique, used by SW for tag

Body logic:
- count = 1: preview (triage-friendly)
  - DM: `"hey everyone!"` — bare preview, title is already the sender (matches current code at line 321)
  - Room: `"Marco: hey everyone!"`
  - Meetup: `"Marco: hey everyone!"`
- count > 1: `"N new messages"` (accumulation signal)
- count = 0: never pushed — guarded in 3.3 (mark_read race)

Title logic (unchanged from current, lines 319-327):
- DM: sender name
- Room: `"#room_name"`
- Meetup: meetup name

**Trailing flush body**: Always queries fresh count. If count=1 (user read all but one while flush was pending), still shows preview. `sender_name` and `text_preview` are empty strings for flushes — need to query the latest unread message for preview when count=1:

```python
if count == 1 and not sender_name:
    msg_row = db.execute(
        "SELECT m.content, u.display_name, u.username FROM messages m "
        "JOIN users u ON u.id = m.user_id "
        "WHERE m.room_id = ? AND m.created_at > ? AND m.user_id != ? AND m.expires_at > ? "
        "ORDER BY m.created_at LIMIT 1",
        (room_id, last_read, user_id, now_iso),
    ).fetchone()
    if msg_row:
        sender_name = msg_row["display_name"] or msg_row["username"]
        text_preview = msg_row["content"]
```

### 3.5 Debounce reset on mark_read [NEW]

In the `mark_read` WS handler (after line 1031):

```python
elif event == "mark_read":
    room_id = data.get("room_id")
    timestamp = data.get("timestamp")
    if room_id:
        mark_room_read(db, user_id, room_id, timestamp)
        if user_id in manager.user_unread:
            manager.user_unread[user_id].pop(room_id, None)
        room_meta = manager._room_meta.get(room_id, {})
        await manager.send_to_user(
            user_id,
            {
                "event": "badge_update",
                "room_id": room_id,
                "count": 0,
                "type": room_meta.get("type", "general"),
                "name": room_meta.get("name", ""),
            },
        )
        # NEW: Reset push state — next push is immediate with sound
        key = f"{user_id}:{room_id}"
        _push_sent.pop(key, None)
        _push_debounce.pop(key, None)
        # Cancel any pending trailing flush — user already read the messages
        task = _push_flush_tasks.pop(key, None)
        if task:
            task.cancel()
```

### 3.6 Visible event handler [NEW]

In the WS event dispatch (after the activity-reset block at line 953):

```python
elif event == "visible":
    manager._last_ws_activity[user_id] = time.monotonic()
```

**Critical**: `visible` must NOT be in the `should_update_last_active` list (line 944) — it's a keepalive, not engagement. It only refreshes `_last_ws_activity` to prevent false idle detection.

### 3.7 Entry point change

Replace the current direct call pattern (lines 806-817):

```python
# OLD:
for uid in push_targets:
    asyncio.create_task(
        _send_chat_push(uid, room_id, room_type, room_name, display_name, text_preview, msg_id=msg["id"])
    )

# NEW:
for uid in push_targets:
    asyncio.create_task(
        _push_or_defer(uid, room_id, room_type, room_name, display_name, text_preview, msg["id"])
    )
```

### 3.8 Stale state pruning [NEW]

Add to purge loop (after line 1612), running every 240 cycles (2 hours):

```python
if _purge_cycle % 240 == 0:
    cutoff = time.monotonic() - 7200  # 2 hours
    stale_keys = [k for k, v in _push_debounce.items() if v < cutoff]
    for k in stale_keys:
        _push_debounce.pop(k, None)
        _push_sent.pop(k, None)
        task = _push_flush_tasks.pop(k, None)
        if task:
            task.cancel()
    # Also prune disconnected users from _last_ws_activity
    connected = set(manager.user_conns.keys())
    stale_activity = [uid for uid in manager._last_ws_activity if uid not in connected]
    for uid in stale_activity:
        if time.monotonic() - manager._last_ws_activity.get(uid, 0) > 7200:
            manager._last_ws_activity.pop(uid, None)
```

**Rationale**: `_last_ws_activity` is never cleaned on disconnect (verified — `disconnect()` at line 429 pops `_last_active_ts` but NOT `_last_ws_activity`). Over a multi-day festival with hundreds of users connecting/disconnecting, this leaks. The 2h threshold ensures we only prune truly stale entries.

---

## 4. Chat Push: Service Worker

**File**: `server/static/sw.js`

### 4.1 Push handler [REWRITE]

**Current** (verified, line 25-42): shows notification immediately with unique tag from `data.url`, no prune, no badge.

**New**: prune-then-show + app badge.

```js
self.addEventListener('push', function(event) {
  var raw = event.data ? event.data.text() : '';
  var data = {};
  try { data = JSON.parse(raw); } catch (e) { data = {}; }

  var title = data.title || 'Stone Techno Companion';
  var body = data.body || '';
  // Tag must be unique per notification (iOS rule 2.3).
  // push_index is a server-side monotonic counter, guaranteed unique.
  var tag = 'stc-' + (data.room_id || '') + '-' + (data.push_index || Math.random().toString(36).slice(2));
  var options = {
    body: body,
    icon: '/favicon.png',
    badge: '/favicon.png',
    tag: tag,
    data: { url: data.url || '/', roomId: data.room_id, count: data.count },
    silent: data.silent || false,
  };

  event.waitUntil(
    // Step 1: Prune old notifications for this room (iOS 30s rule satisfied by 60s server debounce)
    self.registration.getNotifications().then(function(list) {
      if (data.room_id) {
        list.filter(function(n) {
          return n.data && n.data.roomId === data.room_id;
        }).forEach(function(n) { n.close(); });
      }
      // Step 2: Show new notification — do NOT call getNotifications() after this (iOS rule 2.1)
      return self.registration.showNotification(title, options);
    }).then(function() {
      // Step 3: App badge (after showNotification — must be in waitUntil)
      if (data.total_unread && navigator.setAppBadge) {
        navigator.setAppBadge(data.total_unread);
      }
      // Step 4: Network calls last (iOS rule 2.3 — SW may be killed after foregrounding)
      return Promise.all([swlog('push-received', data.url), ackPush('delivered', data.url)]);
    })
  );
});
```

Key design:
- `getNotifications()` before `showNotification()` — finds old notifications for this room
- `close()` on old ones — fire-and-forget, no re-query (iOS rule 2.1.2)
- Unique tag via `room_id + push_index` — iOS ignores tags for replacement, but Chromium uses them to avoid duplicates in notification center
- `silent` — server sets this to `true` for subsequent pushes
- `setAppBadge` — total unread across all rooms
- Ack calls last — after all local work

**Fallback for timetable pushes**: timetable pushes carry no `room_id`, `total_unread`, or `push_index`. So: prune step is skipped (no `data.room_id`), badge step is skipped (no `data.total_unread`), tag falls back to `'stc--' + random`. All correct — timetable pushes are one-off per slot, never consolidated.

### 4.2 Click handler [KEEP — already correct]

Verified (lines 45-73). Order: close notification → cache write → matchAll → postMessage+focus (or openWindow) → ack. Exactly per iOS rules. No changes needed.

**One minor issue in current code**: `event.notification.close()` is at line 47 (BEFORE cache write), not at the end as the old plan stated. This is actually fine — the notification is already dismissed by the tap, `close()` here is just cleanup. The critical rule is "no network before local work", and close-at-top doesn't violate that. Keep as-is.

### 4.3 Close handler [KEEP, add waitUntil]

**Current** (line 76-78):
```js
self.addEventListener('notificationclose', function(event) {
  ackPush('dismissed');
});
```

**Issue**: no `event.waitUntil()`. The ack fetch may be killed before completing. Add waitUntil:

```js
self.addEventListener('notificationclose', function(event) {
  event.waitUntil(ackPush('dismissed'));
});
```

Only fires on Chromium (iOS never fires it). Best-effort ack.

**Chromium programmatic close()**: Per Web Notifications spec, `notificationclose` fires only on *user* dismissal, not programmatic `close()`. Chromium follows spec here. So the prune in the push handler does NOT trigger spurious `dismissed` acks. Verified via Chromium source (no test needed).

### 4.4 pushsubscriptionchange handler [KEEP]

Verified (lines 80-97). Correct implementation. No changes.

### 4.5 Version bump

Increment `SW_VERSION` from `'v8'` to `'v9'`. This is just for logging/debugging via `swlog` and `ackPush` — it does NOT affect notification click behavior (SW update orphan issue is about the browser's internal SW version tracking, not this string).

---

## 5. Chat Push: Page Side

**File**: `server/chat/chat.html`

### 5.1 Per-room notification clear on room enter [NEW]

```js
function _clearRoomNotifications(roomId) {
  if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
  navigator.serviceWorker.ready.then(function(reg) {
    reg.getNotifications().then(function(list) {
      list.filter(function(n) {
        return n.data && n.data.roomId === roomId;
      }).forEach(function(n) { n.close(); });
      // fire-and-forget: do NOT call getNotifications() after (iOS rule 2.1.2)
    });
  });
}
```

Call in `openRoom()` after setting `currentRoom` (after line ~1627). Works on iOS if notification is >=30s old (guaranteed by 60s debounce for subsequent pushes). The first push notification (at T=0) may be <30s old when the user opens the room — `close()` is silently ignored and the notification persists cosmetically (see lifecycle 14.3 and section 16.1 for mitigation).

### 5.2 Retry clear after 35s [NEW — addresses iOS stale notification]

When `_clearRoomNotifications` fails on a <30s notification, schedule a retry:

```js
function _clearRoomNotifications(roomId) {
  if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
  navigator.serviceWorker.ready.then(function(reg) {
    reg.getNotifications().then(function(list) {
      var roomNotifs = list.filter(function(n) {
        return n.data && n.data.roomId === roomId;
      });
      roomNotifs.forEach(function(n) { n.close(); });
      // Retry after 35s for any that were <30s old (iOS ignores close on young notifications)
      if (roomNotifs.length > 0) {
        setTimeout(function() {
          if (currentRoom !== roomId) return;  // user left the room
          reg.getNotifications().then(function(list2) {
            list2.filter(function(n) {
              return n.data && n.data.roomId === roomId;
            }).forEach(function(n) { n.close(); });
          });
        }, 35000);
      }
    });
  });
}
```

The 35s retry guarantees the notification is now >=35s old (>=30s threshold). The `currentRoom !== roomId` guard prevents closing notifications for a room the user already left. If the user left and came back within 35s, the new `openRoom` call will schedule its own retry.

### 5.3 Push navigation (existing, keep)

Verified (lines 3792-3841). Two mechanisms:
1. **postMessage from SW** — `navigator.serviceWorker.addEventListener('message', ...)` → `window.location.href = url`
2. **Cache fallback** — `_checkPushNavigate()` reads `stc-push/_push_navigate` from Cache Storage on `focus`/`pageshow` with retries at 0ms, 300ms, 1s. 3s navigation latch prevents double-navigation.

No changes needed.

### 5.4 Push re-sync on load (existing, keep)

Verified (lines 3486-3521). On page load, if push is subscribed, re-send subscription to server. No changes.

### 5.5 Clear app badge on full read [NEW]

When all rooms are read, clear the app icon badge:

```js
function _updateAppBadge() {
  if (!navigator.setAppBadge) return;
  var total = Object.values(unreadByRoom).reduce(function(s, n) { return s + n; }, 0) + _hiddenUnread;
  if (total > 0) navigator.setAppBadge(total);
  else navigator.clearAppBadge();
}
```

Called from:
- `badge_counts` handler (line 1284) — after `refreshAllBadges()`
- `badge_update` handler (line 1298) — after `updateTabBadges()`
- `_debouncedMarkRead()` — after sending mark_read to server

This keeps the app icon badge in sync with the in-app title badge (`_updateTitleBadge` at line 1544 uses the same formula).

---

## 6. Timetable Push

**File**: `server/api.py`, function `_push_notification_scheduler()`

Background task, runs every 60s. No changes needed.

### 6.1 How it works

1. Load `timetable.json` (slot UUID → artist + time mapping)
2. Find slots starting in 9:30–10:30 from now (the "10 min before" window)
3. Query `sessions` table for users who scheduled that slot
4. Check `sent_notifications` table for dedup
5. Send push via `pywebpush`

### 6.2 Payload

```json
{
  "title": "Artist Name starts in 10 min",
  "body": "Floor Name, 23:00-00:30",
  "tag": "stc-{slot_id}",
  "url": "/?view=timetable"
}
```

Uses a per-slot tag (`stc-{slot_id}`). Tag replacement doesn't matter here — each slot is a unique event, sent once.

**Shared SW handler**: timetable pushes go through the same rewritten push handler (section 4.1). They carry no `room_id` or `total_unread`, so the prune and app-badge steps are skipped and the tag falls back to `'stc--' + random` — all fine, but it's a shared code path, not a separate one. Note: the `tag` field in this payload is ignored by the SW (the SW builds its own tag from `push_index` or random) — it's a dead field; remove from `api.py` to avoid confusion.

### 6.3 Dedup

`sent_notifications` table (session_id + slot_id). Prevents re-sending on subsequent scheduler cycles. Pruned after 7 days.

### 6.4 Dead subscription cleanup

On 404/410 from push service, delete the subscription from `push_subscriptions`.

---

## 7. Browser Notifications (fallback)

**File**: `server/chat/chat.html`

When push is NOT subscribed (`_pushSubscribed = false`), the page shows browser notifications via `new Notification()` for messages arriving via WebSocket while the tab is hidden.

### 7.1 How it works (verified, lines 3375-3434)

1. `_queueBrowserNotification(data)` — called from `badge_update` handler when `document.hidden && !_pushSubscribed && Notification.permission === 'granted'`
2. DMs → `_sendDmNotification()` — instant `new Notification(sender, {body, tag: 'chat-dm-{room_id}', renotify: true})`
3. Rooms/meetups → batched in `_nw` accumulator, flushed after 10s by `_flushBrowserNotification()`
4. Flush logic: 1 message → sender + preview, same room → "N new messages in #room", multi-room → "N new messages"

### 7.2 Limitations

- Only works while tab is open (just hidden)
- `new Notification()` creates non-persistent notifications (not in `getNotifications()`)
- On mobile browsers: `new Notification()` throws `TypeError` — push is the only option
- `tag: 'chat-dm-{room_id}'` with `renotify: true` — on Chromium, replaces per room. On iOS, tag ignored.

### 7.3 No changes needed

This is the fallback for users who haven't enabled push. It works as-is. The push improvements don't affect this path.

---

## 8. App Icon Badge [NEW]

The numeric badge on the PWA app icon.

### 8.1 Platform support

| Platform | API | Behavior |
|---|---|---|
| iOS PWA | `navigator.setAppBadge(N)` | Numeric badge on home screen icon |
| Desktop Chrome | `navigator.setAppBadge(N)` | Numeric badge on dock/taskbar |
| Android | Not supported | OS auto-shows dot when notifications exist |

### 8.2 Update points

**SW push handler** — set badge after showing notification:
```js
if (data.total_unread && navigator.setAppBadge) {
  navigator.setAppBadge(data.total_unread);
}
```

**Page — `_updateAppBadge()` function** [NEW]:
```js
function _updateAppBadge() {
  if (!navigator.setAppBadge) return;
  var total = Object.values(unreadByRoom).reduce(function(s, n) { return s + n; }, 0) + _hiddenUnread;
  if (total > 0) navigator.setAppBadge(total);
  else navigator.clearAppBadge();
}
```

Called from:
- `badge_counts` WS handler (initial load)
- `badge_update` WS handler (incremental update)
- `_debouncedMarkRead()` (after clearing a room's unread)

### 8.3 Server payload

`total_unread` field in every chat push payload — sum of unread messages across ALL rooms for this user. Allows the SW to set an accurate badge even when the page isn't open.

---

## 9. Push Subscription Management

### 9.1 Chat push (authenticated)

**Subscribe**: `POST /chat/api/push/subscribe` — saves `{endpoint, p256dh, auth}` to `chat_push_subscriptions` table, keyed by `user_id`. Upserts on `endpoint` uniqueness (verified, `chat_db.py` line 1511 — `ON CONFLICT(endpoint) DO UPDATE`).

**Unsubscribe**: `DELETE /chat/api/push/subscribe` — removes by `user_id + endpoint`.

**Status**: `GET /chat/api/push/status` — returns `{subscribed: true/false}`.

**VAPID key**: `GET /chat/api/push/vapid-key` — returns public key for `PushManager.subscribe()`.

**Page flow** (verified, lines 3486-3521):
1. User taps "Enable notifications" in settings
2. `Notification.requestPermission()` → must be `granted`
3. Fetch VAPID key → unsubscribe existing → re-subscribe with `userVisibleOnly: true`
4. POST subscription JSON to server
5. Set `_pushSubscribed = true`

### 9.2 Timetable push (anonymous)

**Subscribe**: `POST /api/session/{code}/push/subscribe` — saves to `push_subscriptions` table, keyed by `session_id`.

**Unsubscribe**: `DELETE /api/session/{code}/push/unsubscribe` — removes by `session_id + endpoint`.

### 9.3 Re-sync on load

On page load, if push is subscribed, the client re-POSTs the subscription to the server. Recovers from:
- Server DB purge (container rebuild)
- Silent subscription loss (iOS)

### 9.4 pushsubscriptionchange

SW handler re-subscribes via `PushManager.subscribe()` and POSTs new keys. Handles browser-side key rotation.

### 9.5 Dead subscription cleanup

On 404/410 response from push service: delete subscription from DB (verified, `chat_ws.py` line 362-364). Prevents wasting resources on expired endpoints.

### 9.6 Stale subscription pruning

`purge_stale_push_subscriptions()` (verified, `chat_db.py` line 1463) — removes subscriptions older than 90 days whose user also hasn't been seen in that period. Runs every 24 hours (purge loop cycle 2880).

---

## 10. Push Ack & Tracking

**Endpoint**: `POST /chat/api/push/ack` (verified, `chat_api.py` line 1484)

SW sends ack for each notification lifecycle event:

| Action | When | Server effect |
|---|---|---|
| `delivered` | After `showNotification()` | Updates `last_seen` |
| `clicked` | After `notificationclick` | Updates `last_seen` + `last_active` |
| `dismissed` | After `notificationclose` | Updates `last_seen` |

Ack payload: `{endpoint, action, v (SW version), url}`.

Server finds user by push endpoint (`find_user_by_push_endpoint`, `chat_db.py` line 1217), updates timestamps. `last_seen` used for membership counting (reachable users). `last_active` used for engagement metrics.

Note: `dismissed` never fires on iOS (`notificationclose` event is broken). `notificationclose` does NOT fire for programmatic `close()` on Chromium either (spec: user-initiated only) — so the prune in section 4.1 doesn't produce spurious acks.

---

## 11. Idle Detection & Foreground Suppression

### 11.1 Idle detection (existing, verified)

Two-layer approach to detect when a user is no longer viewing the app:

**Layer 1 — Instant (primary):** Client sends `POST /chat/api/push/idle` via `sendBeacon` on `visibilitychange(hidden)` and `pagehide` (verified, `chat.html` lines 3843-3850). Server sets `_last_ws_activity[user_id] = 0` (verified, `chat_api.py` line 1470), making user immediately push-eligible.

**Layer 2 — 30-second fallback:** If `sendBeacon` fails, server considers user idle if no user-initiated WS event in 30s. Only engagement events reset the timer (verified, `chat_ws.py` lines 944-953): `send_message`, `typing`, `add_reaction`, `remove_reaction`, `create_meetup`, `open_dm`, `delete_message`.

### 11.2 Foreground suppression [NEW]

**Problem**: user reading in room X without interacting for 30s becomes push-eligible for room Y, causing push banners on top of the open app.

**Solution**: client sends `visible` WS event on load and every 20s while `!document.hidden`. This keeps `_last_ws_activity` fresh. 20s (not 25s) against the 30s idle threshold — a keepalive delayed by a WS reconnect must not tip the user into push-eligible while the app is on screen.

```js
// Page side — add near the idle beacon code (after line 3850)
var _visibleInterval = null;
function _startVisibleKeepalive() {
  if (_visibleInterval || document.hidden) return;
  if (ws && ws.readyState === 1) wsSend('visible', {});
  _visibleInterval = setInterval(function() {
    if (ws && ws.readyState === 1) wsSend('visible', {});
    else if (document.hidden) { clearInterval(_visibleInterval); _visibleInterval = null; }
  }, 20000);
}
document.addEventListener('visibilitychange', function() {
  if (!document.hidden) {
    _startVisibleKeepalive();
  } else {
    if (_visibleInterval) { clearInterval(_visibleInterval); _visibleInterval = null; }
    // existing idle beacon fires in the separate listener (line 3843)
  }
});
// visibilitychange does NOT fire on initial page load.
_startVisibleKeepalive();
```

```python
# Server side (chat_ws.py WS handler, after line 953 activity check)
elif event == "visible":
    manager._last_ws_activity[user_id] = time.monotonic()
```

Result: no push while app is in foreground. Badge updates arrive via WebSocket instead.

**WS reconnect gap**: if the WS drops and reconnects (exponential backoff, max 30s), the interval fires but `ws.readyState !== 1` so no message is sent. The user is temporarily push-eligible during the reconnect window. This is acceptable — the reconnect typically takes 3-5s, and the user is likely aware the app is disconnected (no new messages appearing). A push during this window is mildly redundant, not harmful.

**Multiple tabs**: each tab runs its own keepalive. The server tracks per-user (not per-connection), so any tab keeping the user fresh suppresses push for all tabs. If all tabs are hidden, all idle beacons fire, and the user becomes push-eligible. Correct behavior.

---

## 12. Notification Click & Navigation

### 12.1 SW notificationclick handler (verified, no changes)

Order (from current code, lines 45-73):

1. **Close notification** — `event.notification.close()` (already dismissed by tap, this is cleanup)
2. **Cache write** — `caches.open('stc-push').put('/_push_navigate', targetUrl)` (~1ms, survives SW kill)
3. **Find client** — `clients.matchAll({type: 'window', includeUncontrolled: true})`
4. **postMessage** — `client.postMessage({type: 'navigate', url})` (page receives and navigates)
5. **focus** — `client.focus()` (brings app to foreground)
6. **openWindow** — only if no client exists (cold start / app fully closed)
7. **ack** — `swlog` + `ackPush('clicked')` (best-effort, last)

### 12.2 Page navigation receiver (verified, lines 3837-3841)

```js
navigator.serviceWorker.addEventListener('message', function(e) {
  if (e.data && e.data.type === 'navigate') {
    window.location.href = e.data.url;
  }
});
```

### 12.3 Cache fallback (verified, lines 3792-3835)

For cases where postMessage doesn't arrive (iOS edge cases):

- `_checkPushNavigate()` reads from Cache Storage
- Called on `focus`, `pageshow` with retries at 0ms, 300ms, 1s
- 3s navigation latch (`_pushNavigating`) prevents double-navigation
- Deletes cache entry before navigating

### 12.4 Rules (from diag/RESULTS.md)

- Do NOT combine `client.navigate()` with `postMessage` — they race and abort each other
- `openWindow()` returns null when a window already exists on iOS — fallback only
- Network calls (`swlog`, `ackPush`) ALWAYS last — iOS kills SW after foregrounding

---

## 13. Cross-Device Sync

### 13.1 Badge sync (existing, verified)

`mark_read` (line 1022) broadcasts `badge_update` with `count=0` to ALL of the user's WebSocket connections via `send_to_user`. Reading on phone clears badge on desktop.

**Known limitation — app icon badge staleness**: this only reaches *connected* devices. If the user reads on desktop while the phone is locked, the iOS icon badge set by an earlier push stays stale — it can't be cleared remotely (iOS requires every push to show a notification via `userVisibleOnly`, so no silent badge-clearing push is possible). It self-heals on next app open: `badge_counts` arrives on WS connect → `_updateAppBadge()`. Accepted.

### 13.2 Push subscription per device

Each device has its own push subscription. A user can have multiple subscriptions (phone + desktop). The server sends push to ALL of them (verified — `get_push_subscriptions` returns all rows for a user, `_send_chat_push` loops over all subs at line 344).

### 13.3 Favorites/schedule sync (existing)

Cross-device sync via ephemeral 6-digit PINs (5-min TTL) and WebSocket real-time sync. Not notification-related but shares the same session model.

---

## 14. Lifecycle Examples

### 14.1 Busy room, user backgrounded

```
T=0:00  Message 1 in #General
        Server: push_sent=false, window=10s, last push long ago → send NOW
          title: "#General"
          body: "Marco: heading to stage 2"
          count: 1, total_unread: 1, silent: false
        Server: push_sent[user:room] = true → window = 60s
        SW: getNotifications → 0 old → showNotification (sound) → badge(1)
        User sees: 1 notification with sound (immediately — leading edge)

T=0:05–0:50  Messages 2-15 arrive → all suppressed (60s window)
             First suppressed message schedules ONE trailing flush at T=1:00

T=1:00  Trailing flush fires (window expiry)
        Server: queries get_unread_counts → count=15
          body: "15 new messages"
          count: 15, total_unread: 15, silent: true
        SW: getNotifications → finds push 1 (60s old, >=30s) → close → show → badge(15)
        User sees: old notification removed, "15 new messages" appears (silent)

T=1:20  Message 16 → within 60s of T=1:00 → suppressed, flush scheduled at T=2:00
T=2:00  Flush → count=16, "16 new messages" replaces "15 new messages" (silent)

(If the burst had ended at message 15 with no further traffic, the T=1:00
flush still fires — no message is ever left unrepresented.)

T=any   User taps notification
        → notificationclick → navigate to /chat/msg/first-unread
        → user reads messages → mark_read → server resets debounce,
          cancels pending flush
        → _updateAppBadge → clearAppBadge (if all read)
```

### 14.2 Multiple rooms

```
T=0:00  Push for #General → notification shown (sound)
T=0:30  Push for DM from Lisa → second notification shown (sound, different room → new burst)
T=1:00  Trailing flush for #General (60s debounce from T=0:00)
        → prunes old #General notification (60s old, >=30s) → consolidated
        Lisa DM notification untouched (different room_id)
        User sees: 2 notifications (1 per room, both most recent state)

T=any   User opens #General
        → _clearRoomNotifications('general-id') → close #General notification
        → Lisa DM notification stays
        → mark_read for #General → debounce reset
```

### 14.3 User opens room within 30s of push

```
T=0:00  Push arrives → notification shown (sound)
T=0:15  User opens that room
        → _clearRoomNotifications() → close() on 15s-old notification → silently ignored (iOS)
        → notification stays in notification center (cosmetically stale)
        → mark_read → server resets debounce + cancels pending flush → no more pushes
        → 35s retry scheduled
T=0:50  Retry fires (T=0:15 + 35s = T=0:50) → notification is now 50s old (>=30s)
        → close() succeeds → notification removed
```

### 14.4 Foreground suppression

```
User reading in room X (app open, visible)
  → 'visible' WS event on page load + every 20s → _last_ws_activity stays fresh
  → push_targets filter: now - _last_ws_activity < 30 AND uid in connected_uids → excluded
  → no push for any room while app is visible
  → badge updates arrive via WS → in-app badges update in real-time

User backgrounds app
  → visibilitychange(hidden) → clearInterval (no more visible keepalives)
  → sendBeacon /push/idle → _last_ws_activity = 0
  → immediately push-eligible
  → next message (after debounce) → push sent
```

### 14.5 Multiple devices, one foreground

```
User has phone (foreground, sending 'visible' keepalive) and laptop (locked, push-eligible)

Message arrives in #General:
  push_targets includes user (laptop is push-eligible) — but wait:
  → user IS in connected_uids (phone has active WS)
  → _last_ws_activity[user] is fresh (phone's 'visible' keepalive)
  → now - _last_ws_activity < 30 → user excluded from push_targets
  → NO push to either device
  → phone gets message via WS in real-time

User locks phone:
  → idle beacon → _last_ws_activity = 0
  → laptop was already not sending keepalive (locked)
  → next message: user in connected_uids (phone WS still alive briefly), but idle > 30 → push-eligible
  → push sent to ALL subscriptions (phone + laptop)
  → phone: notification shown but user's phone is locked → notification center
  → laptop: notification shown

Note: there is a brief window (~30s) after phone locks where the phone WS is
still alive but no keepalive is sent. During this window, the idle beacon
already set _last_ws_activity=0, so the user is immediately push-eligible.
Push is sent to both devices — correct behavior (user is not actively viewing).
```

### 14.6 Timetable push

```
T=23:50  Scheduler runs → finds "DJ Name" starts at 00:00
         → checks sessions with this slot scheduled
         → checks sent_notifications → not sent yet
         → sends push: "DJ Name starts in 10 min — Floor, 00:00–01:30"
         → records in sent_notifications (won't re-send)

T=23:51  Next scheduler cycle → sent_notifications exists → skip

T=00:00  DJ starts playing → notification irrelevant, user already there (or not)
```

### 14.7 Server restart mid-debounce

```
T=0:00  First push sent for user:room (window = 60s)
T=0:30  Server restarts (deploy, crash, etc.)
        → all in-memory state lost: _push_debounce, _push_sent, _push_flush_tasks = empty
T=0:35  Message arrives
        → _push_debounce.get(key, 0) = 0 → now - 0 > 10 → send immediately
        → User gets an extra push with sound (leading-edge behavior)
        → Correct: fail-open toward alerting. Better to over-notify than miss messages.
```

### 14.8 Mark_read races trailing flush

```
T=0:00   First push sent
T=0:05   Messages 2-10 arrive → suppressed, flush scheduled at T=1:00
T=0:45   User opens room on another device → mark_read
         → server: _push_sent.pop(key), _push_debounce.pop(key)
         → server: _push_flush_tasks[key].cancel() → flush at T=1:00 cancelled
         → no stale push delivered — correct

T=0:45   (Alternative) User opens room on SAME device → mark_read
         → same as above, plus _clearRoomNotifications removes T=0:00 notification
           (45s old, >=30s — close succeeds on iOS)
```

---

## 15. Changes by File

### server/chat_ws.py

| Change | Lines (est.) |
|---|---|
| Add `_push_sent`, `_push_flush_tasks`, `_push_counter` dicts | 3 |
| New `_push_or_defer()` function (debounce logic, trailing schedule) | 20 |
| New `_flush_push_later()` function | 5 |
| Refactor `_send_chat_push()` → `_do_send_push()` with new params (`silent`, `push_index`) | 15 |
| Unread counts query (reuse `get_unread_counts` + first_msg_id + count=0 guard) | 12 |
| Build new payload (room_id, count, total_unread, silent, push_index, smart body) | 20 |
| Trailing flush preview query (count=1 case) | 8 |
| Replace call site: `_send_chat_push` → `_push_or_defer` | 2 |
| Reset `_push_sent`/`_push_debounce` + cancel flush task in `mark_read` handler | 6 |
| Add `visible` event handler (2 lines in event dispatch) | 2 |
| Prune stale debounce/sent/flush/activity entries in purge loop | 10 |
| Remove dead `tag` field from payload | -1 |

### server/static/sw.js

| Change | Lines (est.) |
|---|---|
| Rewrite push handler: prune-then-show + badge | 28 |
| Add `event.waitUntil` to notificationclose handler | 1 |
| Bump SW_VERSION to 'v9' | 1 |
| Keep click handler (already correct) | 0 |
| Keep pushsubscriptionchange handler | 0 |

### server/chat/chat.html

| Change | Lines (est.) |
|---|---|
| Add `_clearRoomNotifications(roomId)` with 35s retry | 18 |
| Call it in `openRoom()` | 1 |
| Add `_updateAppBadge()` function | 5 |
| Call from `badge_counts`, `badge_update`, `_debouncedMarkRead` | 3 |
| Add `visible` WS keepalive (startup + 20s interval + visibilitychange) | 15 |

### No changes needed

| File | Reason |
|---|---|
| `server/api.py` | Timetable push is independent, no changes (optionally remove dead `tag` field) |
| `server/chat_api.py` | Push endpoints unchanged, idle endpoint unchanged |
| `server/chat_db.py` | Schema unchanged, `get_unread_counts` reused as-is |
| `server/chat/chat.html` (browser notif) | Fallback path unchanged |
| `server/chat/chat.html` (push nav) | Already correct from diag work |
| `server/chat/chat.html` (subscription) | No changes |

---

## 16. Edge Cases & Failure Modes

### 16.1 iOS notification persists after room open (<30s)

**Scenario**: User taps notification at T=0:10. App opens to the room. Notification is 10s old — `close()` silently fails.

**Mitigation**: 35s retry in `_clearRoomNotifications` (section 5.2). At T=0:45, retry fires and successfully closes the notification.

**Residual**: if the user opens the room via app (not via notification click), the notification persists for up to 35s before the retry removes it. Cosmetic only — the user is already reading the messages.

### 16.2 Trailing flush fires for deleted/expired room

**Scenario**: Room is deleted (admin action) or meetup expires between message and flush.

**Mitigation**: `get_unread_counts` returns nothing for non-existent rooms → `count = 0` → guard returns without sending. No crash, no push.

### 16.3 User banned/deleted between message and flush

**Scenario**: User banned, messages purged, flush fires.

**Mitigation**: `get_push_subscriptions` returns empty (user cascaded) → function returns early. If subscriptions survive (ban doesn't cascade subscriptions), `get_unread_counts` returns 0 (messages deleted) → guard returns. Either way, no push.

### 16.4 asyncio.Task leaked on shutdown

**Scenario**: Server shuts down while trailing flush tasks are sleeping.

**Mitigation**: asyncio cancels all pending tasks on loop shutdown. The `await asyncio.sleep(delay)` raises `CancelledError` → task exits cleanly. No explicit cleanup needed.

### 16.5 DB locked during flush

**Scenario**: `get_unread_counts` hits WAL contention during heavy writes.

**Mitigation**: SQLite WAL mode allows concurrent reads. Writes (mark_read from another path) may briefly block, but reads never block on writes in WAL. The flush query is read-only — always succeeds.

### 16.6 Multiple push subscriptions (phone + desktop + tablet)

**Scenario**: User has 3 devices. All get the same push simultaneously.

**Behavior**: Each device shows the notification independently. The prune-then-show in the SW handler works per-device (each device's notification center is independent). Badge is set to the same `total_unread` on all devices. First device to open and `mark_read` triggers `badge_update` via WS to connected devices — their badges clear. Disconnected devices: badge clears on next app open (badge_counts on WS connect).

### 16.7 Push payload exceeds 4KB limit

**Scenario**: Very long sender name + room name.

**Mitigation**: Body is already truncated (preview capped at 80-100 chars in current code, line 321-324). The new payload adds ~50 bytes of metadata (`room_id`, `count`, `total_unread`, `silent`, `push_index`). Total payload stays well under 4KB even with max-length fields. If somehow exceeded, `webpush` raises `WebPushException` with status 413 — caught and logged, not fatal.

### 16.8 Clock skew in `get_unread_counts`

**Scenario**: Server clock jumps backward. `_now()` returns a past time. Messages with `expires_at > now` that should be expired are still counted.

**Mitigation**: `_now()` uses `datetime.now(timezone.utc).isoformat()` — server clock only. If the clock jumps, counts are temporarily inflated until the next purge cycle (30s) cleans up truly expired messages. Negligible impact — the purge loop is the authoritative expiry mechanism.

### 16.9 Rapid mark_read / new-message race

**Scenario**: User reads in room → mark_read fires → resets debounce → new message arrives 1ms later → immediate push (because debounce was just reset).

**Behavior**: Correct. The user backgrounded (or switched rooms), so they should be notified immediately. The mark_read reset is intentional — it means "user is caught up, alert again on next message."

**But**: if the user is still in the room (just triggered mark_read via scroll), the `visible` keepalive prevents push. The user is still in `connected_uids` with fresh `_last_ws_activity`, so they're excluded from push_targets. Only disconnected/idle users receive the push.

### 16.10 _push_counter overflow

**Scenario**: After millions of pushes, `_push_counter` becomes very large.

**Mitigation**: Python integers have no overflow. JSON serialization handles arbitrarily large ints. The SW uses it only as a string suffix for the tag. No practical limit.

### 16.11 Concurrent _push_or_defer calls for same user:room

**Scenario**: Two messages pass moderation near-simultaneously. Both spawn `asyncio.create_task(_push_or_defer(...))` for the same user:room.

**Behavior**: asyncio is single-threaded cooperative. The first task runs to its first `await` (which is inside `_do_send_push`). Before that, it sets `_push_debounce[key] = now`. The second task then checks and finds `now - last < window` → suppressed. No double-push. The critical section (reading and writing `_push_debounce`) is synchronous — no race.

### 16.12 _last_ws_activity stale entry after long disconnect

**Scenario**: User disconnects. `_last_ws_activity[user_id]` retains the last timestamp. Hours later, push targeting checks this stale value.

**Behavior**: `now - stale_time > 30` is always true (it's hours old). But the user is also NOT in `connected_uids` (popped on disconnect, line 441). So they're pushed via the first condition (`uid not in connected_uids`) regardless of the stale entry. The stale `_last_ws_activity` entry is harmless but wastes memory — pruned by section 3.8.

---

## iOS-Specific Rules Checklist

When implementing, verify each of these:

- [ ] SW push handler: `getNotifications()` before `close()` + `showNotification()` — never after
- [ ] SW push handler: unique tag per notification (`room_id + push_index`, not room-scoped)
- [ ] SW push handler: `setAppBadge` inside `waitUntil` (after showNotification)
- [ ] SW click handler: cache write + postMessage + focus BEFORE fetch/ack (already correct)
- [ ] SW notificationclose: wrapped in `event.waitUntil`
- [ ] Page clear: `getNotifications()` → filter → `close()` → return (fire-and-forget)
- [ ] Page clear: 35s retry for <30s notifications (covers first-push case)
- [ ] Page clear: `currentRoom !== roomId` guard on retry (user may have left)
- [ ] Server: 60s progressive debounce guarantees >=30s notification age (trailing flush fires exactly at window expiry)
- [ ] Server: trailing flush — a burst that ends inside the window still produces a consolidated push
- [ ] Server: unread counts via existing `get_unread_counts()` (DMs, expired messages, own messages) — no hand-rolled SQL
- [ ] Server: count=0 guard (mark_read race) — never push "0 new messages"
- [ ] Server: `silent: true` on subsequent pushes (first push has sound)
- [ ] Server: trailing flush count=1 case queries latest unread message for preview
- [ ] Server: reset debounce + cancel pending flush on `mark_read`
- [ ] Server: `_push_sent` staleness reset (30 min) — a new burst after long silence re-alerts with sound
- [ ] Server: `visible` event updates `_last_ws_activity` but NOT `_last_active_ts` (not engagement)
- [ ] Server: `_push_counter` is module-level monotonic int (not timestamp)
- [ ] Server: stale state pruning (2h) in purge loop, including `_last_ws_activity` for disconnected users
- [ ] Page: visible keepalive starts on page load AND on `visibilitychange(visible)` (both needed)
- [ ] Page: visible interval cleared on `visibilitychange(hidden)` (don't send while backgrounded)
- [ ] Page: idle beacon on `visibilitychange(hidden)` and `pagehide` (existing, verified)
- [ ] Page: `_updateAppBadge` uses same formula as `_updateTitleBadge` (includes `_hiddenUnread`)
- [ ] Payload: no dead `tag` field (SW ignores it)
- [ ] Payload: `push_index` from monotonic counter (not timestamp)
- [ ] SW version bump: `v8` → `v9`
