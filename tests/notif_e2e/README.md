# notif_e2e — automated end-to-end notification test harness

Fully-automated observation of the notification pipeline. See
`docs/notification-test-design.md` for the full design (4 layers, 20 scenarios) and
`CONTRACT.md` for the module interfaces.

## Status

**Stage 1 (server -> wire emission) is implemented and green.** It needs no browser and no CDP:
a recipient's push depends only on an injected DB subscription plus presence, so a WebSocket-client
sender drives it deterministically. The Fake Push Service captures the real encrypted WebPush the
server emits and decrypts it (we hold the subscription keys), so assertions run on the exact payload,
headers, and VAPID claims.

Stages 2 (client behavior via Playwright), 3 (CDP `ServiceWorker.deliverPushMessage` delivery bridge),
and 4 (cross-device + real-browser FCM leg) are designed but not yet built.

## Run

```bash
python tests/notif_e2e/run.py                 # all Stage-1 scenarios
python tests/notif_e2e/run.py --scenario vapid_isolation
python tests/notif_e2e/_smoke.py              # minimal foundation smoke test
```

Each scenario runs against an isolated server (own port, scratch chat.db + hearts.db, a freshly
generated VAPID keypair, sensitive env stripped) and a fresh Fake Push Service. Per-scenario signal
timelines are written to `_artifacts/<scenario>.json`. Exit code is non-zero if any scenario fails.

Note: `debounce_silent_escalation` takes ~60s because it waits out the server's real coalesce window
to observe the silent follow-up push; every other scenario runs in under ~2s.

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
