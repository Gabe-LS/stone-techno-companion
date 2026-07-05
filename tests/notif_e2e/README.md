# notif_e2e — automated end-to-end notification test harness

Fully-automated observation of the notification pipeline. See
`docs/notification-test-design.md` for the full design (4 layers, 20 scenarios) and
`CONTRACT.md` for the module interfaces.

## Status

**Stages 1 and 2 are implemented and green (14 scenarios total).**

- **Stage 1 (server -> wire emission)** needs no browser: a recipient's push depends only on an injected
  DB subscription plus presence, so a WebSocket-client sender drives it deterministically. The Fake Push
  Service captures the real encrypted WebPush the server emits and decrypts it (we hold the subscription
  keys), so assertions run on the exact payload, headers, and VAPID claims.
- **Stage 2 (client behavior)** drives real headless Chromium (Playwright, sync API) against an isolated
  server, with a subscribe-success override (real headless Chromium can't reach a push service), spies
  for `Notification`/`setAppBadge`/`sendBeacon`/`document.hasFocus`, and WS + push-network capture.

Stages 3 (CDP `ServiceWorker.deliverPushMessage` delivery bridge) and 4 (real-browser FCM leg) are
designed (see `docs/notification-test-design.md`) but not yet built. See `CONTRACT_STAGE2.md` for the
Stage-2 browser-layer interface.

## Run

```bash
python tests/notif_e2e/run.py                 # emission suite (Stage 1)
python tests/notif_e2e/run.py --browser       # client-behavior suite (Stage 2, real Chromium)
python tests/notif_e2e/run.py --all           # both
python tests/notif_e2e/run.py --list
python tests/notif_e2e/run.py --browser --scenario badge_fanout_cross_device
python tests/notif_e2e/_smoke.py              # foundation smoke test
python tests/notif_e2e/_smoke_browser.py      # browser-layer smoke test
```

Each scenario runs against an isolated server (own port, scratch chat.db + hearts.db, a freshly
generated VAPID keypair, sensitive env stripped). Per-scenario signal timelines are written to
`_artifacts/<scenario>.json`. Exit code is non-zero if any scenario fails.

Notes:
- `--browser`/`--all` must run OUTSIDE the command sandbox (headless Chromium needs Mach-port access).
- `debounce_silent_escalation` takes ~60s (it waits out the server's real coalesce window); every other
  scenario runs in a few seconds.
- Stage-2 assertions read client STATE (`unreadByRoom`, title, app badge) rather than raw WS frames:
  Playwright's `framereceived` capture is unreliable for this app and misses `badge_update` frames the
  client nonetheless processes.

## Stage-2 client scenarios

| Scenario | Asserts |
|---|---|
| `enable_success` | vapid-key fetch -> subscribe (no unsubscribe first) -> POST /push/subscribe -> push_enabled=1 -> server stored the sub |
| `disable_flow` | endpoint read before unsubscribe -> DELETE /push/subscribe -> flag cleared, server row gone |
| `repair_gated` | no vapid-key fetch without push_enabled; with it + existing sub, a resync POST (not re-subscribe) |
| `idle_beacon` | hiding the tab fires sendBeacon('/chat/api/push/idle') |
| `focus_gated_keepalive` | a visible-but-unfocused window sends no 'visible' WS frame; focused it does |
| `badge_fanout_cross_device` | a message badges device B; reading on device A clears B's badge cross-device |
| `first_run_banner_nonblocking` | the first-run banner shows after the first message but never covers the send button; explicit dismiss persists |

## Stage-1 scenarios

| Scenario | Asserts |
|---|---|
| `offline_recipient_push` | offline member gets one push; payload/TTL=300/VAPID aud == FPS origin |
| `active_recipient_no_push` | a connected, recently-active viewer gets NO push |
| `idle_recipient_push` | after the idle beacon zeroes activity, the push is delivered |
| `debounce_silent_escalation` | first push loud, coalesced follow-up `silent:true` |
| `dead_endpoint_pruned` | a 410 from the push service deletes the subscription row |
| `vapid_isolation` | 3 subs on 3 origins -> 3 pushes, each VAPID `aud` == its own origin (anti-poisoning) |
| `pending_not_pushed` | a `moderation_status='pending'` message never enters a push body or the counts |

## Modules

- `fake_push_service.py` — aiohttp server impersonating FCM/Apple/Mozilla; captures + decrypts
  (aes128gcm via `http_ece`) each WebPush; parses the VAPID JWT; `set_dead` for 410 testing.
- `harness.py` — isolated `NotifServer` (reuses the `e2ee_browser_check.py` startup pattern),
  scratch DBs, `gen_vapid_keys`, subscription injection with real ECDH keypairs, and a `WSClient`.
- `recorder.py` — `SignalRecorder`: a source-agnostic timeline with ordered + timing-bound assertions.
- `scenarios/emission.py` — the Stage-1 scenarios. `run.py` — CLI runner.
