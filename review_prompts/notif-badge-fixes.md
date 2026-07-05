# Fix spec: DM notification + unread badge defects

You are an IMPLEMENTATION agent. You have Read, Glob, Grep, Edit, Write. You
CANNOT run anything (no Bash, no tests, no server) -- the orchestrator executes
all tests and verification. Do not claim any verification you did not do by
reading code. Implement EXACTLY this spec. If you believe a spec item is wrong,
implement it anyway and flag the concern in your final report.

This spec is the arbitrated result of a read-only investigation plus runtime
DB evidence. Root causes are confirmed unless marked otherwise. Do not
re-diagnose.

## Style rules (mandatory)

- No emojis anywhere.
- New/changed JS functions and branches must keep the existing `dbg()` logging
  convention (every action logs; match nearby density and tag style, e.g.
  `[BADGE]`, `[NOTIFY]`).
- Match surrounding code style exactly (chat.html is vanilla JS, 2-space
  indent; chat_ws.py follows the existing module conventions).
- Comments only where the code cannot express a constraint (as the existing
  code does).

## Fix 1 -- loadRooms() wipes DM/meetup unread state (cold-start badge bug)

File: `server/chat/chat.html`, function `loadRooms()` (~line 1547).

Confirmed root cause of "no red dot / unread badge on DM row after PWA cold
start": server sends `badge_counts` (which correctly includes DM rooms)
immediately on WS connect; `route()` then awaits `loadBlockedUsers()` and
`loadRooms()`; `loadRooms()` does:

```js
roomTypeLookup = {};
rooms.forEach(r => { roomTypeLookup[r.id] = { type: r.type, name: r.name }; });
const validIds = new Set(rooms.map(r => r.id));
for (const rid of Object.keys(unreadByRoom)) {
  if (!validIds.has(rid)) delete unreadByRoom[rid];
}
```

`GET /chat/api/rooms` returns only stage/general rooms -- DM and meetup room
ids are NEVER in `validIds`, so their unread entries (and their
`roomTypeLookup` types, which `updateTabBadges()` needs) are destroyed.

Required changes in `loadRooms()`:

1. Do NOT reassign `roomTypeLookup = {}`. Instead merge: for each room in the
   response set `roomTypeLookup[r.id] = { type: r.type, name: r.name }`, and
   additionally remove from `roomTypeLookup` only entries whose type is
   `'stage'` or `'general'` and whose id is no longer in the response (rooms
   deleted server-side). DM/meetup/unknown entries must survive.
2. Restrict the `unreadByRoom` prune to the same condition: delete
   `unreadByRoom[rid]` only when `rid` is not in `validIds` AND
   `roomTypeLookup[rid]?.type` is `'stage'` or `'general'`. Entries with type
   `'dm'`, `'meetup'`, or unknown type must survive (they are cleaned up by
   `mark_read` / badge_update count 0 / room deletion events).
3. Keep the original purpose of the prune (commit "prune stale unread badges"):
   a deleted group room's badge must still disappear.
4. Add a `dbg('[BADGE] ...')` line when entries are pruned, logging which ids
   were removed.

## Fix 2 -- refreshAllBadges() never updates DM rows

File: `server/chat/chat.html`, function `refreshAllBadges()` (~line 1748).

Currently only `document.querySelectorAll('.room-item[data-room-id]')`. DM
rows are rendered by `loadDMs()` as `.member-item[data-room-id]` (~line 1857),
so a `badge_counts` bulk sync (initial or reconnect) can never repair a DM row
badge already on screen.

Required change: also select `.member-item[data-room-id]` rows and call
`updateBadge()` for them. Check how meetup list rows are rendered (search for
where the meetup list builds items with `data-room-id`); if they use another
class, include that selector too. Verify `updateBadge(roomId)` locates the
badge element in DM rows -- it queries by `[data-room-id]` so it should work
unchanged; if it is scoped to `.room-item`, generalize it.

## Fix 3 -- push notification tag collides across server restarts (iOS tap dead)

Files: `server/chat_ws.py` (`_do_send_push`, ~line 436-449) and
`server/static/sw.js` (push handler, line 32; `SW_VERSION`, line 4).

Confirmed mechanism: the SW builds `tag = 'stc-' + room_id + '-' + push_index`.
`push_index` comes from the in-process counter `_push_counter`, which RESETS to
0 on every server restart. After a restart, new pushes for the same room reuse
low indices; if an older notification with the same `stc-<room>-<n>` tag is
still in iOS Notification Center, the new notification REPLACES it, and iOS
silently drops `notificationclick` for replaced notifications (documented
project bug: tap opens the PWA at start_url = Line-up with no navigation --
exactly the reported symptom). CLAUDE.md's own rule requires a unique tag per
notification; the counter implementation violates it across restarts.

Required changes:

1. `server/chat_ws.py`: in `_do_send_push`, add a `"push_id"` field to the
   payload dict: a fresh random hex string per push (use
   `secrets.token_hex(8)`; add the import if missing). Keep `push_index`
   (used by logs/acks).
2. `server/static/sw.js`: build the tag from `data.push_id` when present:
   `'stc-' + (data.room_id || '') + '-' + (data.push_id || data.push_index || Math.random().toString(36).slice(2))`.
   Keep the existing close-previous-same-room logic unchanged.
3. Bump `SW_VERSION` to `'v10'`.
4. Check `tests/` for assertions on the push payload shape or tag
   (grep for `push_index`, `push_id`, `stc-`). Update any affected test to
   include the new field. Do not weaken existing assertions.

## Fix 4 -- push subscribe failure reported as success (Brave silently has no push)

File: `server/chat/chat.html`, `_enableAllNotifications()` (~line 3857) and
`_checkPushStatus()` (~line 3808).

Runtime DB evidence: the affected user has push subscriptions ONLY from iOS
(`web.push.apple.com`) -- the Brave browser never registered an endpoint, yet
the UI said "Notifications enabled". Cause: in `_enableAllNotifications()`, the
`catch` around `pushManager.subscribe()` only logs `dbg(...)` and execution
falls through to `showToast('Notifications enabled')`. In Brave,
`pushManager.subscribe()` rejects when "Use Google services for push
messaging" is disabled (its default is off for many installs), so the user
believes push is on while only in-app alerts work.

Required changes:

1. In `_enableAllNotifications()`, track whether the subscribe block succeeded.
   On failure: set `_pushSubscribed = false` and show a DIFFERENT toast:
   `In-app alerts enabled. Push registration failed - if you use Brave, enable "Use Google services for push messaging" in brave://settings/privacy and try again.`
   Only show `Notifications enabled` when the subscription was actually
   registered with the server (POST returned ok). Treat a non-ok POST
   response as failure too.
2. Add a silent subscription repair on chat load: after the existing
   `_checkPushStatus()` call in `route()`, if `Notification.permission ===
   'granted'` and there is no current push subscription, attempt the same
   VAPID subscribe + POST flow once, silently (dbg logging only, no toasts).
   On success set `_pushSubscribed = true`. This mirrors the lineup page's
   re-sync-on-load behavior and repairs server-side DB purges. Extract the
   shared subscribe logic into a helper function (e.g. `_subscribePush()`)
   used by both `_enableAllNotifications()` and the repair path, returning
   true/false, so the two paths cannot drift. Guard the repair so it runs at
   most once per page load.

## Out of scope (do NOT implement)

- Per-device (per-connection) `_last_ws_activity` tracking. The per-user model
  was NOT the cause of the observed failure (the iOS push was delivered in the
  same repro, proving the user was push-eligible).
- Any iOS `openWindow()` retry logic in sw.js.
- Any change to E2EE code paths, moderation, or the `badge_update` server
  logic.

## Required final report format

```
# Implementation report

## Changes
- <file>: <what changed, function names, approx line ranges>

## Deviations from spec
- <none, or each deviation with reason>

## Concerns
- <anything you believe is wrong or risky in the spec, with evidence>

## Tests touched
- <files/tests updated and why>
```
