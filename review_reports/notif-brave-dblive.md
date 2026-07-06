## Verdict

**Yes — the Brave user (gabbo82, `86ad6d6d-8673-4faf-b528-6f9c60a38d31`) has a live, current push subscription row.** It was created at `2026-07-05T13:41:40.357410+00:00`, which is *before* the message that should have triggered the missing push. Subscription absence/staleness is ruled out as the cause for this specific failure.

## Subscription table dump (redacted)

**`chat.db` → `chat_push_subscriptions`**

| id | user_id | endpoint (truncated) | created_at | service |
|---|---|---|---|---|
| 29 | `5bc441dc…` (Outlook) | `https://updates.push.services.mozilla.com/wpush/v2/gAAAAABqSfsW2mr...` | 2026-07-05T13:40:17.227554+00:00 | Mozilla (Firefox/Zen) |
| 32 | `86ad6d6d…` (gabbo82) | `https://fcm.googleapis.com/fcm/send/cuUMOrPb1sI:APA...` | 2026-07-05T13:41:40.357410+00:00 | **FCM (Chrome/Brave)** |

`users` mapping:

| id | username | display_name | provider | last_seen | last_active |
|---|---|---|---|---|---|
| `5bc441dc…` | Outlook | outlook | email | 13:46:52 | 13:41:44 |
| `86ad6d6d…` | gabbo82 | Gabbo | email | **13:46:56** | 13:40:59 |

Given FCM = Chromium family and gabbo82's `last_seen` kept advancing well past the message timestamp (WS ping activity, tab open), **gabbo82 is the Brave user** in the repro. Outlook is on Firefox/Zen (Mozilla endpoint) and is user A (sender).

## Message/membership timeline for the failed notification

Room `8fcf2027-fdb8-42fe-a42e-62cfeb6d9e4d` is a DM (`is_moderated=0`, confirmed in `dm_participants` between Outlook and gabbo82; both have registered `e2ee_device_keys`, so this is an E2EE DM thread).

Relevant tail of the conversation (UTC):

| time | sender | note |
|---|---|---|
| 13:40:23 / 13:40:47 | Outlook | — |
| **13:40:17** | — | Outlook's own push sub (row 29) created |
| **13:41:01** | gabbo82 | reply |
| **13:41:40** | — | **gabbo82's push sub (row 32) created — 17s before the target message** |
| 13:41:46 | Outlook | gabbo82's `room_memberships.last_read_at` = exactly this timestamp |
| **13:41:57** | **Outlook** | **← THIS is the unread message. Sent 17s after gabbo82's subscription existed.** |

`room_memberships` for gabbo82 on this room: `last_read_at = 2026-07-05T13:41:46.538652+00:00`, which matches the *second-to-last* Outlook message exactly. Recomputing unread count directly from `messages` (`created_at > last_read_at AND user_id != gabbo82`) yields **exactly 1 unread message** — `fe6baa54` at `13:41:57.117572`, from Outlook.

Current DB read time was `13:49:24 UTC` — ~7.5 minutes after the message and ~2.5 minutes after gabbo82's `last_seen` last advanced (13:46:56). This is far past any reasonable push-dispatch delay (the documented idle model is "instant" on hide/pagehide or a 30s WS-inactivity fallback); if a push had fired it would show as a subscription/last_seen update or a dead-row prune by now, and neither happened.

Note: the symptom describes a badge showing "2" in the window title, but DB-computed unread for gabbo82 across every room they belong to is exactly **1** (only this DM has any unread rows). That discrepancy is client-side badge state, outside what the DB can explain — flagging it for whoever owns the client code.

## Evidence of push attempts

- `chat.db` has **no** push-log/audit/dedup table at all (schema dump confirms: no `sent_notifications`, no push log table exists in chat.db — only lineup's `hearts.db` has one).
- `hearts.db.sent_notifications` (lineup surface) is **empty** — 0 rows, so no lineup push was logged either, but that's a different code path.
- No table in either DB records "push attempted/skipped/failed" for chat. This means DB forensics **cannot** distinguish "server never tried to send" from "server tried, pywebpush was called, and it failed silently" — that determination requires server logs/code (out of my scope).

## What this rules in / rules out

**Ruled out** (by DB data alone):
- Missing subscription row for the Brave user — a live FCM row exists and pre-dates the message by 17 seconds.
- Stale/wrong-origin subscription *for this specific row* — it was created minutes ago in this same dev session, not a days-old cross-origin leftover.
- Message never actually persisted / room mismatch — message `fe6baa54` is correctly recorded in the correct DM room, and `dm_participants` confirms gabbo82 is a legitimate recipient.

**Ruled in / worth other angles chasing**:
- **Idle-detection logic**: gabbo82's `last_active` (user-initiated event) froze at 13:40:59 while `last_seen` (WS liveness) kept advancing — exactly the state the idle/push-eligibility check is supposed to catch via the 30s fallback, yet nothing fired 58+ seconds later. This points at the server-side idle/trigger logic, not the DB.
- **Endpoint mismatch, lineup vs. chat**: `hearts.db.push_subscriptions` has exactly one row (session `pzX4jI73fhKKdWSK19QMLQ`, FCM, created `2026-07-05 09:56:06`) whose registration token (`dgrMhB5U3bw:APA91bHYjieg`) is **different** from chat's FCM row for gabbo82 (`cuUMOrPb1sI:APA9…`). Per the documented invariant, lineup and chat must share one endpoint per origin — these differ. I can't confirm from the DB alone whether this lineup session belongs to gabbo82's Brave profile (no join key between `hearts.db` sessions and `chat.db` users), but if it does, this is a second, independent red flag worth the client-code angle chasing (possible unsubscribe/resubscribe rotation orphaning one surface).
- **No push audit trail exists in chat.db** — if this bug recurs, there is currently no DB-level way to tell "not attempted" from "attempted and failed"; that gap itself is worth noting to whoever owns `chat_ws.py`.
