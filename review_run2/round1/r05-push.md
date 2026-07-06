## Push Notification Stack Audit

I read all seven target files in full (or in relevant sections) against the six invariants and the additional checks requested. Summary of what's clean, followed by concrete findings.

**Verified clean (no issues found):**
- **Invariant 1 (fresh `vapid_claims` dict per call)**: `server/api.py:398` and `server/chat_ws.py:544` both do `vapid_claims=dict(vapid_claims)`.
- **Invariant 2 (VAPID key consistency check)**: `server/api.py:447-481` (`_check_vapid_key_consistency`) correctly derives the public key from the private key and compares — unchanged and correct.
- **Invariant 4/5 (no unsubscribe-before-subscribe; repair gated by flag)**: `scraper/render.py:2182-2209` (`enableNotifications`), `scraper/render.py:2618-2631` (lineup resync-on-load), and `server/chat/chat.html:3899-3971` (`_subscribePush`/`_repairPushSubscription`) all call `getSubscription()` first and only `subscribe()` if none exists; repair is correctly gated by `push_enabled`/`stc_push` before touching the browser subscription.
- **Invariant 6 (TTL=300)**: both senders — `server/api.py:402` and `server/chat_ws.py:549` — pass `ttl=300`. `server/verify_push_both.py:67-75` omits `ttl` (defaults to 0), but that script is a manual liveness probe ("is this endpoint alive right now"), not a production sender, so TTL=0 (discard if undeliverable) is actually the correct behavior there.
- **410 pruning**: both `server/api.py:407-411` and `server/chat_ws.py:553-561` delete by `endpoint` (globally unique), never by user/session, so pruning can't remove the wrong row.
- **Focus-gated keepalive / idle interplay**: `server/chat/chat.html:4695-4727` — the 20s `visible` keepalive is gated by `document.hasFocus()`, correctly distinguishing "visible but not focused" (should still go idle) from "actively focused" (never idle). The `sendBeacon` idle signal on `visibilitychange`/`pagehide` correctly zeroes `_last_ws_activity` server-side (`server/chat_api.py:1661-1668`) for instant idle detection.
- **Badge double-count fix**: confirmed complete — `_hiddenUnread` was fully removed from `chat.html` (no dangling references), and the title/tab/app badges (`chat.html:1817-1841, 4751-4755`) are now driven solely by server-authoritative `unreadByRoom` counts from `badge_update`/`badge_counts`.

---

### HIGH

**[HIGH] server/chat_ws.py:443-464, server/chat_db.py:765-795 — badge counts and push previews include not-yet-moderated (and possibly soon-to-be-rejected) messages.**
`get_unread_counts()` (chat_db.py:765-795) and the fallback queries inside `_do_send_push()` (chat_ws.py:451-464, used for `first_msg_id` and for the message content/sender preview when the caller passes no text) select from `messages` filtering only on `created_at`, `expires_at`, and `user_id != user_id` — they never exclude `moderation_status = 'pending'`. This is the same gap TRIAGE.md flagged as H11/H15 ("room_history serves not-yet-moderated/rejected messages") and which commit 1a6aaca fixed for `get_room_messages` (chat_db.py:899 does have `AND m.moderation_status != 'pending'`), but the fix was never propagated to the unread-count/push-preview path.

Concretely:
- `_push_or_defer`'s deferred flush path (chat_ws.py:414-424) calls `_do_send_push` with blank `sender_name`/`text_preview`, forcing the DB fallback query at chat_ws.py:458-464 to pull the latest message's raw `content` and use it as the push notification body — even if that message is still awaiting AI moderation or will be deleted moments later for violating the word filter/AI checks. A recipient can see the flagged content in an OS push notification/lock-screen banner before (or even though) it's rejected and purged from the DB.
- The same untfiltered count feeds `total_unread` (chat_ws.py:446), which is sent both in the WS `badge_counts` event on connect (chat_ws.py:1192-1211) and in the push payload consumed by `sw.js:54-56` (`navigator.setAppBadge`), so the OS app badge can also over-count and won't self-correct until the client is opened and a `mark_read`/room-open event recomputes it client-side.

Fix: add `AND m.moderation_status != 'pending'` (or join/exclude accordingly) to both the `get_unread_counts` query and the two ad-hoc queries in `_do_send_push`.

---

### LOW

**[LOW] server/api.py:371-377 — lineup push payload never includes `push_id`; tag uniqueness on iOS currently holds only by accident via `sw.js`'s fallback branch.**
The scheduler's payload (`title`/`body`/`url`) has no `push_id` or `push_index`, unlike `server/chat_ws.py:502-517` which explicitly sets `"push_id": secrets.token_hex(8)`. `sw.js:35` computes `tag = 'stc-' + (room_id||'') + '-' + (push_id || push_index || Math.random()...)`; since lineup sends neither `push_id` nor `push_index`, every lineup notification currently falls into the `Math.random()` branch, which is re-evaluated per push event and therefore happens to produce a fresh tag each time — so the "unique tag" invariant holds today, but not through the mechanism CLAUDE.md documents ("the payload carries a random `push_id`... and sw.js prefers it for the tag"). Any future refactor that adds a stable per-slot identifier to this payload (e.g. keying by `slot_id` for tag-based collapsing, mirroring the chat `room_id` collapsing at `sw.js:47-51`) would silently reintroduce the exact iOS `notificationclick`-drop bug this invariant exists to prevent, since nothing here enforces `push_id` by contract. Recommend adding `"push_id": secrets.token_hex(8)` to the lineup payload for defense-in-depth and to match the documented invariant.

---

**Summary**: The core hard-won invariants (VAPID claims isolation, key-pair consistency, TTL=300 in both senders, endpoint-scoped 410 pruning, no-unsubscribe-before-subscribe, focus-gated idle detection, and the badge double-count fix) all check out correctly in the current code. The one HIGH finding is a real gap: the moderation-pending status filter added to fix room-history leakage (TRIAGE H11/H15) was never extended to the unread-count/push-preview code path, so a message still awaiting or about to fail moderation can appear in a push notification's body text and inflate badge counts. The LOW finding is a latent fragility in the lineup scheduler's payload, not a live bug.