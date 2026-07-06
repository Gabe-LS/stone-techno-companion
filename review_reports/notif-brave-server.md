Confirmed: main/group rooms default `is_moderated=1`. Now writing the report.

## Verdict

The server-side push path is structurally intact and commit `1a6aaca` ("hold messages pending until moderation clears them") is **not** the culprit — it only adds a `moderation_status` column flip and never touches the broadcast/push branch. The real defect is a pre-existing (Jul 4, not part of the last 3 commits) idle-eligibility bug: the client's `_startVisibleKeepalive()` heartbeat sends a `visible` WS event every 20s **while the tab is not `document.hidden`**, and the server treats that heartbeat exactly like genuine user activity (`_last_ws_activity` reset). A Brave tab that is merely the active tab of a backgrounded/unfocused browser window keeps `document.visibilityState === 'visible'` — so the keepalive never stops, `_last_ws_activity` is refreshed every 20s, and the server's `idle > 30s` push-eligibility check can never fire, even though the user is genuinely not looking at the screen and badges are piling up. This exactly matches the symptom: badge reaches 2 (proves `_moderate_and_broadcast` ran and the badge_update path fired), but push never fires (proves the separate `push_targets` condition never evaluated true).

## Push trigger flow (file:line)

1. `server/chat_ws.py:1319` `send_message` received → `create_message()` (chat_db.py) inserts row, `moderation_status='pending'` for moderated rooms (`chat_ws.py:1500-1512`)
2. `chat_ws.py:1516` optimistic `message_acked` sent to sender
3. `chat_ws.py:1554` `asyncio.create_task(_moderate_and_broadcast(...))` — fire-and-forget
4. `chat_ws.py:884-888` `moderate_message()` runs (word filter + OpenAI); on pass:
5. `chat_ws.py:958-962` message flipped to `moderation_status='approved'` (the 1a6aaca addition — no-op path structurally)
6. `chat_ws.py:994` `broadcast_to_room` → connected members get the `message` event
7. `chat_ws.py:1039-1055` badge_update loop → every member in `user_badge_rooms` gets `send_to_user(badge_update)` **unconditionally** — this is what produced the "2" the user saw
8. `chat_ws.py:1057-1064` — the **push eligibility gate**: `push_targets = [uid for uid in all_targets if uid not in connected_uids or now - mgr._last_ws_activity.get(uid, 0) > 30]`
9. `chat_ws.py:1074-1085` for each `push_target`, `asyncio.create_task(_push_or_defer(...))` → `_do_send_push` (`chat_ws.py:423`) → `pywebpush` (vapid claims copied per-call at `chat_ws.py:539` — intact)

Step 8 is where an idle-but-visible user is silently excluded, because step 9 never even gets the user's id.

## Findings ranked by likelihood

1. **`chat_ws.py:1316-1317` (`elif event == "visible": manager._last_ws_activity[user_id] = time.monotonic()`) combined with `chat/chat.html:4646-4655` (`_startVisibleKeepalive`, 20s interval, gated only on `!document.hidden`).** Condition that fails: `now - mgr._last_ws_activity.get(uid, 0) > 30` at `chat_ws.py:1063` never becomes true because the client resends `visible` every 20s as long as `document.visibilityState !== 'hidden'` — which is true for a backgrounded-but-not-minimized/not-tab-switched-away window. This is a foreground-suppression feature (intentional since commit `894888e`, "Push notifications: progressive debounce... foreground suppression") whose activity signal (`document.hidden`) is a weaker proxy for "user is actually looking at the app" than intended. Predates the 3 commits under suspicion by ~16 hours — not a regression from `1a6aaca`/`b6048b1`/`3f8701a`.
2. **Low-probability secondary risk from `1a6aaca` itself**: `chat_ws.py:958-962` adds a synchronous `db.execute(UPDATE...); db.commit()` inside the same `try` block that guards the broadcast/push section. If this `UPDATE` ever raises (e.g., `sqlite3.OperationalError: database is locked` under concurrent writers, since chat.db has no busy_timeout override visible here), the `except Exception` at `chat_ws.py:1087` swallows it, deletes the message, and returns **before reaching the broadcast/badge/push code at all** — meaning badge would NOT have fired either. Since the user's badge did reach "2", this path is ruled out for this specific occurrence, but it's worth flagging as a latent risk under write contention (stress test load, moderation backlog).
3. **Ruled out**: `is_moderated` distinction (task 4) — the push branch (`chat_ws.py:1057-1085`) runs identically regardless of `is_moderated`; only the preceding `mod_result` check differs. Main/group rooms default `is_moderated=1` (`chat_db.py:101`), DMs are created `is_moderated=False` (`chat_db.py:1148`), but both flow through the same push-eligibility code.
4. **Ruled out**: `_do_send_push` internals (task 5) — VAPID claims dict is still copied per-endpoint (`chat_ws.py:539`), 404/410 cleanup is correct, and generic `Exception` is `logger.exception`'d rather than silently swallowed (`chat_ws.py:553-554`).
5. **Ruled out**: `b6048b1` and `3f8701a` — neither touches `_last_ws_activity`, connection registries, `_do_send_push`, or push eligibility. `b6048b1` only adds a profile-identity re-fetch before broadcast; `3f8701a` is client-only E2EE encrypt-path logic with zero server diff.

## What to test to confirm

- Reproduce with logging: the existing `logger.info("[PUSH] targets=%d all=%d connected=%d sender=%s", ...)` at `chat_ws.py:1065-1071` will show `connected=1` (or however many are connected) but `targets=0` for the affected message if hypothesis #1 is correct. Grep server logs for `[PUSH] targets=0` at the time of user B's missed notification.
- In Brave's DevTools on user B's tab, watch the WS frames (Network → WS) while backgrounding the window (switch to another app, don't minimize/switch tabs) — confirm `{"event":"visible"}` keeps being sent every 20s and no `document.visibilitychange → hidden` fires, so no `sendBeacon('/chat/api/push/idle')` is ever sent.
- Query chat.db (read-only) for the affected user's session window: there is no direct table for `_last_ws_activity` (it's in-memory), so instead correlate via server logs: timestamps of the two messages that produced badge count 2, and check whether `>30s` elapsed between the last `visible`/user-event log and the message broadcast — if the gap is always <30s, that confirms the keepalive kept resetting it.
- Sanity check on finding #2: grep server logs around the incident for `sqlite3.OperationalError` or `Moderation task error for message` (`chat_ws.py:1088`) — if present, that's finding #2 firing instead; if absent (expected, since badge fired), finding #2 is ruled out for this incident.
