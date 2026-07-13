# Post-event retrospective: Stone Techno 2026

Event window: Thursday 2026-07-09 through Sunday 2026-07-12. Retrospective run: 2026-07-13, against production (VPS `root@209.38.244.136`, container `stone-techno`), read-only.

## Summary

- The app container ran the entire festival with zero restarts (`RestartCount=0`), healthy status throughout, VAPID key pair verified at both startups.
- 2,152 total log lines for the container's lifetime; only 51 were ERROR-level and 0 were WARNING-level. 48 of the 51 errors are one repeating, effectively benign WebSocket-disconnect race (22 of 43 registered users hit it at least once) that is currently logged as a full traceback instead of a clean disconnect: a log-hygiene bug, not a user-facing one.
- Both `hearts.db` and `chat.db` pass `PRAGMA quick_check`. Disk and memory headroom on the shared VPS are adequate, though swap is 98.6 percent committed (shared with about 15 unrelated containers on the box, not specific to this app).
- Push delivery was clean: 0 WARNING-level push failures or dead-subscription prunes logged across the whole event. Lineup holds 9 subscriptions (5 FCM, 4 Apple), chat holds 4 (2 FCM, 2 Apple), 0 Mozilla in either.
- Registered users: 43 (25 email, 18 Google). Lineup (no-login) sessions: 87 lifetime, 852 picks, 113 saved schedule entries, 11 push reminders sent.
- Real chat usage was light: about 81 messages passed through the full two-layer AI moderation pipeline over four days, 8 word-filter/content-detection strikes issued (max strike level reached: 2 of 4), 0 mutes, 0 bans, 0 blocks, 1 report still pending admin review.
- Message/DM/meetup/reaction row counts in `chat.db` are almost entirely gone by design: the 24-hour TTL purge already ran, so per-day, per-room message counts had to be reconstructed from log proxies rather than the DB.
- The QNAP-hosted `monitor.sh` hourly log could not be read (no NAS host/credentials are recorded in this repo, and none were guessed, per instructions); VPS-side evidence was used instead as a substitute signal.
- The two "real problems": (1) the WebSocket-disconnect logging bug above, cheap to fix; (2) the retrospective's own message-volume data is unrecoverable from the DB post-purge, so any future post-event usage analysis needs a log- or aggregate-count-based plan made in advance, not attempted after the fact.

## Reliability

### Container health

| Metric | Value |
|---|---|
| Container name | `stone-techno` |
| RestartCount | 0 |
| Started at | 2026-07-09T15:39:04Z |
| Health status (at check time) | healthy |
| VAPID startup line | present, both container starts (`VAPID key pair verified: private key matches public key`) |
| OPENAI_API_KEY startup line | present (`OPENAI_API_KEY present, AI moderation enabled`) |

A second, earlier `VAPID key pair verified` line appears at 2026-07-09T11:17:14Z, about 4h22m before the current container's start time. This is a clean deploy transition (a new container replacing the old one, both with `RestartCount=0`), not a crash: no traceback or error sits around that boundary in the log.

### Log volume and errors

| Level | Count |
|---|---|
| INFO | 1,764 |
| WARNING | 0 |
| ERROR | 51 |
| Total lines | 2,152 |

ERROR breakdown:

| Cause | Count | Unique users | Notes |
|---|---|---|---|
| `RuntimeError: WebSocket is not connected. Need to call "accept" first.` at `ws.receive_text()` (`chat_ws.py:1392`) | 48 | 22 | See below |
| `[MOD] Content detection error:` (empty exception message) | 2 | n/a | GPT-5.4-nano Responses API call failed (likely a timeout given the empty `str()`); both cases correctly failed closed with "Message could not be verified. Please try again." |
| `[MOD] OpenAI moderation error: Client error '400 Bad Request'` | 1 | n/a | One-off transient upstream error, also failed closed |

WebSocket error distribution by date: 2026-07-09: 13, 2026-07-10: 28, 2026-07-11: 5, 2026-07-12: 1, 2026-07-13: 1.

Root cause of the 48 WebSocket errors: `handle_chat_ws`'s main loop (`server/chat_ws.py`) wraps `await ws.receive_text()` in `try: ... except WebSocketDisconnect: pass / except Exception: logger.exception(...)`. Starlette raises `RuntimeError('WebSocket is not connected. Need to call "accept" first.')` instead of `WebSocketDisconnect` for a specific disconnect-timing race, which the broad `except Exception` catches and logs as a full ERROR traceback. Given CLAUDE.md already documents that iOS does not send a clean WebSocket close frame when a PWA is killed (backgrounded, screen-locked, force-closed), and this event ran on phones at a live festival with intermittent connectivity, this condition is an expected, frequent disconnect path, not an edge case: it hit roughly half of all registered users (22 of 43) at least once. It has no evidence of user-facing impact (no correlated report or reproducible complaint), it is purely a log-hygiene issue that buried the two genuinely interesting ERROR lines (the moderation failures) among 48 near-duplicates.

### Database integrity

| Database | `PRAGMA quick_check` |
|---|---|
| `hearts.db` | ok |
| `chat.db` | ok |

### Disk and memory (at check time, 2026-07-13)

| Resource | Value |
|---|---|
| Root filesystem | 77G total, 32G used, 46G available (42%) |
| Memory | 7,941 MB total, 504 MB free, 4,362 MB buff/cache, 4,453 MB available |
| Swap | 1,023 MB total, 1,009 MB used (98.6%) |
| `server/data/` | 736 KB |
| `server/chat-uploads/` | 392 KB |

Disk and app-data footprint are trivial and not a concern. Swap is almost fully committed, but this VPS also runs roughly 15 unrelated containers (n8n, Seafile + MySQL, NocoDB + Postgres, Caroster + Postgres, Umami + Postgres, cloudflared, watchtower, etc.); nothing in the `stone-techno` container's own logs or resource footprint points to it as the cause. Worth keeping in view given ADR 0007 already plans to add more services to this box.

## Usage

### Chat (`chat.db`)

Important caveat before the numbers: `chat_settings` confirms `room_ttl_minutes = 1440` and `dm_ttl_minutes = 1440` (24 hours), and `meetup_ttl_minutes = 60`. Because today (2026-07-13) is more than 24 hours after the last message in every room, the TTL purge loop has already deleted essentially all `messages`, `message_reactions`, `meetups`, and `meetup_attendees` rows by design (this is the ephemeral-by-design privacy model, not data loss). Only 1 leftover message row and 0 reactions/meetups remained at query time. Per-day, per-room message counts below are therefore reconstructed from container log proxies, not the `messages` table.

| Metric | Value |
|---|---|
| Total users | 43 |
| Users by provider | email: 25, google: 18 |
| Users created per day | 07-08: 6, 07-09: 24, 07-10: 13 |
| Group rooms | 3 (`general` "Stone Techno 2026", `rideshare`, `lost-and-found`) |
| DM rooms | 8, with `last_message_at` timestamps spanning 07-10T08:34Z to 07-10T22:04Z |
| Meetup rooms remaining | 0 (any created meetups have expired and cascade-deleted per `meetup_ttl_minutes`) |
| Reports | 1, status `pending`, created 2026-07-11T19:54:15Z, reason "Reported by user" (**still awaiting admin review**) |
| Strikes (table, post-4h-TTL) | 0 rows remain (4-hour TTL has expired all of them) |
| Bans | 0 |
| Blocks | 0 |
| Admin actions logged | 6 total: `clear_warnings` x3, `delete_user` x2, `update_room` x1 |
| E2EE device keys registered | 41 (across 43 users, near 1:1, low multi-device adoption) |
| `chat_push_subscriptions` | 4 total: 2 FCM (`fcm.googleapis.com`), 2 Apple (`web.push.apple.com`), 0 Mozilla |

Log-derived proxy for real chat volume (since the `messages` table itself is purged):

| Metric (log-derived) | Value |
|---|---|
| `[MOD] result` outcomes logged (moderated group-room sends) | 85 (74 allowed, 6 strike level 1, 2 strike level 2, 3 "could not be verified": the 2 content-detection failures plus 1 moderation 400) |
| Omni-moderation calls that scored above the 0.1 log threshold on any category | 0 (`FLAGGED` never printed) |
| Max strike level reached | 2 of 4 (no mute, no ban triggered by strikes) |

Since the harassment/hate/violence AI layer (omni-moderation) never flagged anything (0 `FLAGGED` lines), the 8 strikes issued during the event came from the word filter and/or the GPT-5.4-nano content-detection layer (drugs/spam/external links), not from the harassment category.

### Lineup (`hearts.db`)

| Metric | Value |
|---|---|
| Total sessions (lifetime, since 2026-06-19 soft-launch) | 87 |
| Sessions with non-empty picks | 67 |
| Sessions with non-empty saved schedule | 18 |
| Total picks (sum across sessions) | 852 |
| Total saved schedule entries | 113 |
| Sessions created 07-08 to 07-11 (festival window) | 6 + 15 + 2 = 23 (about 26% of lifetime sessions) |
| `push_subscriptions` | 9 total: 5 FCM, 4 Apple, 0 Mozilla |
| `sent_notifications` | 11 total: 10 on 07-10, 1 on 07-11 |

## Push health

| Signal | Value |
|---|---|
| WARNING-level push send failures logged (`Push failed for ...` / `Chat push failed for ...`) | 0, across the entire event |
| 410/404 dead-subscription prunes (only logged as part of the WARNING line above) | 0 evidence found |
| Lineup subscriptions | 9 (5 FCM, 4 Apple, 0 Mozilla) |
| Chat subscriptions | 4 (2 FCM, 2 Apple, 0 Mozilla) |
| `[PUSH-ACK]` events | 160 total: 116 delivered, 41 clicked, 3 dismissed |
| `[SWLOG]` steps | 116 push-received, 48 cache-hit-nav, 41 click-done, 30 postmessage-received, 2 nav-go |

**Shared-endpoint invariant (INV-3) could not be positively confirmed from this data.** Comparing the exact endpoint strings in `hearts.db.push_subscriptions` (9 rows) against `chat.db.chat_push_subscriptions` (4 rows), zero endpoints matched. This is not necessarily a violation: with only 4 chat subscribers, it is plausible none of them also happen to be a lineup subscriber, or vice versa, so the sample is too small to exercise the invariant either way. It is not evidence the invariant is broken; it just means production traffic didn't happen to test it this cycle. The correct way to actually verify it remains `tests/verify_push_both.py` against a single Chromium profile with both surfaces enabled.

## Moderation and cost grounding

Grounding data for ADR 0005 (AI agent model and cost ceiling), per its own request to estimate "messages in moderated rooms" as the AI call volume driver:

| Metric | Value |
|---|---|
| Omni-moderation-latest POST calls (`/v1/moderations`) | 147 |
| GPT-5.4-nano Responses API calls (`/v1/responses`) | 81 |
| Real chat-message-driven moderated pipeline runs (proxy) | about 81 |

The gap between 147 and 81 omni-moderation calls is not messages: omni-moderation alone (without the paired GPT-nano content-detection call) also runs on avatar images, display names, and meetup photos, none of which go through the drugs/spam/external-link layer. The 81 GPT-nano calls are the cleaner proxy for "messages that went through the full two-AI-call moderated-room pipeline," since `moderate_message` always calls both layers in parallel via `asyncio.gather` for message text.

**Explicit computation requested by this retrospective:**

- Messages through moderated rooms observed this event: about 81
- AI calls per moderated message: 2 (one `omni-moderation-latest` call, one GPT-5.4-nano `/v1/responses` call)
- Observed AI call volume this event: 81 x 2 = 162 calls, across 43 registered users over 4 days
- Per 1,000 messages, the same ratio implies: 1,000 x 2 = 2,000 AI calls (1,000 omni-moderation + 1,000 GPT-5.4-nano), linear in message count since the pipeline runs unconditionally on every moderated-room send that passes the word filter

At the scale actually observed (162 AI calls total across the whole festival), any plausible per-event daily budget ceiling ADR 0005 proposes would not have come close to binding. This is useful, low-risk grounding for the "Leaning" section of ADR 0005 (reuse OpenAI, option B): the near-term cost/coupling risk of adding an AI support agent alongside the existing moderation calls is small at this user scale. The budget ceiling matters as a circuit breaker for future scale (more events, higher attendance, per the roadmap's Stage 2+ multi-event plans), not because current traffic is anywhere near expensive. Exact dollar pricing is still deliberately left out here, consistent with ADR 0005's own decision to pin pricing at implementation time.

## Monitor (QNAP) reachability

`monitoring/qnap/` runs `monitor.sh` hourly from an always-on QNAP NAS via Container Station (see its `README.md`). No NAS hostname, IP, or credentials are recorded anywhere in this repository, so per the read-only/no-credential-guessing constraint on this retrospective, the NAS itself was not reached and its hourly log could not be read. This is stated honestly rather than substituted with a guess.

As a substitute signal, the following VPS-side evidence was checked instead:

- Container `RestartCount=0` and `healthy` status throughout the event: nothing suggests an outage the hourly monitor would have flagged.
- 0 WARNING-level lines in the whole container log: no push failures, no missing-API-key warnings, no rate-limit warnings.
- Both databases pass `PRAGMA quick_check`.
- No evidence of an evenly-spaced, once-per-hour call pattern to `api.openai.com/v1/moderations` with a "ping" body (the pattern `monitor.sh`'s in-container OpenAI-reachability check would produce via `docker exec`). All 147 moderation POSTs in the log cluster tightly with real user activity (07-09 13:00-17:00, 07-10 06:00-15:00) rather than firing evenly across all 96 hours of the event. **This means it could not be confirmed from VPS-side evidence that the QNAP monitor's hourly in-container moderation check actually ran against this container during the event window**: only that nothing else in the container's own logs points to a problem it would have caught.

## Recommendations

1. **Code fix candidate.** In `server/chat_ws.py`'s `handle_chat_ws` receive loop (around line 1391-2311), catch the specific Starlette `RuntimeError('WebSocket is not connected. Need to call "accept" first.')` alongside `WebSocketDisconnect` as a clean disconnect, rather than letting the broad `except Exception: logger.exception(...)` log a full traceback. Evidence: 48 of the 51 total ERROR lines this event (94%), affecting 22 of 43 registered users, all consistent with the already-documented iOS-PWA-kill-without-close-frame behavior rather than a real failure. Low risk, cheap, directly improves signal-to-noise in production logs for the next event.

2. **Code fix candidate (minor).** `server/chat_moderation.py`'s `moderate_message` logs `logger.error("[MOD] Content detection error: %s", drug_result)` and the equivalent for `ai_result`; both printed empty during this event because the underlying exception's `str()` was empty (consistent with a client-side timeout). Log `repr(drug_result)` (or the exception type name) instead so a future occurrence is diagnosable without re-deriving it from context, as had to be done here.

3. **Fold into docs/invariants.md.** Add a note under INV-3 (one origin, one shared push subscription) that production endpoint overlap between `hearts.db.push_subscriptions` and `chat.db.chat_push_subscriptions` could not be exercised this event (0 of 9 lineup and 4 chat endpoints matched, sample too small to be conclusive either way), and that `tests/verify_push_both.py` remains the correct way to actually verify the invariant rather than incidental production overlap.

4. **Fold into docs/invariants.md.** Record explicitly that the QNAP-hosted `monitor.sh` hourly log is NAS-local and has no remote-readable copy: no host/credentials for the NAS are stored in this repo, so a retrospective run from a different machine cannot read it. If this needs to be fixed rather than just documented, the cheapest option is having `monitor.sh` also append to (or the NAS periodically push) a small log artifact somewhere already reachable read-only (e.g., a repo-adjacent path this Mac can read, since `monitor.sh`'s primary cron already runs from this Mac per its header comment, separately from the QNAP copy), but that is a design decision, not made here.

5. **No action.** 1 report (`d550a8dd-9245-4f91-b4d9-4d5438515c90`, "Reported by user", created 2026-07-11T19:54:15Z) is still `pending` in the admin queue. This is an operational follow-up for a human admin via `/chat/admin`, not a code or doc change.

6. **No action.** Swap at 98.6% utilization on the shared VPS is noted for awareness only; nothing in the `stone-techno` container's own footprint (736 KB + 392 KB data, healthy status, no OOM evidence in its logs) points to it as the cause, and the box runs roughly 15 other unrelated services. Revisit only if ADR 0007's planned VPS resize surfaces this as a blocker.

7. **Feed into ADR 0005.** Use the explicit computation above (about 81 real moderated messages -> 162 AI calls this event; 2,000 AI calls per 1,000 messages at the same ratio) as the grounding data point for the AI-agent cost model: current traffic is far below where any reasonable per-event budget ceiling would bind, supporting the ADR's lean toward reusing OpenAI (option B) without urgent cost pressure, while the budget-ceiling circuit breaker is still worth building before any agent is public, for when traffic scales past this single-event, 43-user baseline.

8. **No action.** Container reliability itself (0 restarts, healthy throughout, both DBs pass `quick_check`, 0 WARNING-level lines) needs no follow-up: this is a clean result worth keeping as the baseline to compare the next event against.
