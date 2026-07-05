# Pre-deployment review: push notifications (lineup scheduler + chat push + service worker)

You are a read-only code reviewer for a festival companion app about to deploy. Push notifications are the highest-regression-risk subsystem in this codebase and several invariants were established through painful production debugging. You CANNOT run any commands — Bash is not available and will fail. Do not claim to have run or tested anything. Cite findings as `file:line` with quoted snippets.

## Scope

- `server/api.py` — lineup push scheduler, VAPID key loading, `_check_vapid_key_consistency`, subscription endpoints
- `server/chat_ws.py` — `_do_send_push` and chat push dispatch, idle detection
- `server/static/sw.js` — push handler, notificationclick, pushsubscriptionchange
- `server/chat/chat.html` + `scraper/render.py` — client subscription code (`_repairPushSubscription`, `_enableAllNotifications`, push idle beacon) — Grep for these
- `server/chat_api.py` — push subscription REST endpoints, `/chat/api/push/ack`, `/chat/api/push/idle`, `/chat/api/swlog`

## Known invariants that MUST hold (verify each one still does — regressions here are CRITICAL)

1. pywebpush mutates the `vapid_claims` dict (stamps first endpoint's `aud`). Every pywebpush call site must pass a FRESH dict (`dict(claims)`), never a shared one — in BOTH the chat push path and the lineup scheduler.
2. Notification tags must be unique across server restarts: payload carries random `push_id` and sw.js must prefer it for the tag. iOS silently drops `notificationclick` for tag-replaced notifications.
3. sw.js notificationclick must do LOCAL work first: cache-write of target URL, postMessage+focus, openWindow — network calls (acks/logging) strictly after navigation primitives.
4. `client.navigate()` must never be combined with postMessage (racing navigations).
5. `_repairPushSubscription` must be gated by the `push_enabled` localStorage flag; `_enableAllNotifications` reports success only when subscribe + server POST both succeed.
6. Dead subscriptions (410) auto-pruned in both push paths.

## Additional focus

7. VAPID key path handling: Docker `/app/data/vapid_private.pem` vs local `data/` — consistent between scheduler and chat push?
8. Scheduler dedup (`sent_notifications`): can a notification double-send after restart or DST/timezone edge? Timezone handling from events table.
9. Push payload size limits (~4KB), error handling when a push service returns non-410 errors (does one bad subscription abort the loop for remaining users?).
10. sw.js: does `pushsubscriptionchange` re-subscribe use the correct applicationServerKey and re-POST to the right endpoint for both lineup and chat?
11. Badge counts in push payloads vs `badge_update` WS events — consistent across devices?

## Hard rules

- Read-only: Read, Glob, Grep only.
- Evidence-based findings only.

## Required final report format (this is your entire final message)

```
# Findings: push

## [SEVERITY: CRITICAL|HIGH|MEDIUM|LOW] <one-line title>
- Where: file:line
- Evidence: <short quoted snippet>
- Impact: <production consequence>
- Fix: <concrete minimal change>
```

Include an `## Invariant check` section: one line per numbered invariant above — HOLDS (with file:line) or VIOLATED (as a finding). End with `## Verified clean` for the additional focus areas found sound.
