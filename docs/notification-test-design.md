# Automated End-to-End Notification Test — Design

Goal: a fully automated harness that drives multiple real browser instances, exercises every
notification path (chat push, lineup push, badges, idle detection, click navigation, subscription
lifecycle), and **observes every signal in and out** — asserting not just that each thing happens, but
that it happens *in the right order, at the right time, with the right payload*.

This complements the existing nets rather than replacing them:
- `tests/e2ee_browser_check.py` — E2EE DM correctness (21 checks)
- `tests/notif_badge_browser_check.py` — badge bookkeeping + truthful enable + gated repair
- `tests/test_notifications.py` — SW/version/tag unit assertions (Playwright infra)
- `server/verify_push_both.py` — real-endpoint liveness (manual, needs a live FCM subscription)

None of them close the full loop *message-send → server push logic → wire → service-worker → client
navigation → ack* while watching every hop. That is what this harness does.

---

## 1. The core challenge and how we solve it

A real WebPush travels: **server → push service (FCM / Mozilla / Apple) → browser SW**. Headless
Chromium has no connection to a real push service, so `pushManager.subscribe()` fails and no real push
can be delivered. We cannot rely on real FCM in an automated test.

Solution: split the loop into independently observable segments and bridge the un-reachable hop with a
**Fake Push Service (FPS)** plus **Chrome DevTools Protocol (CDP) push injection**. Every code path that
runs in production runs here too; only the opaque push-service transport is substituted.

```
  message send (WS)                                   real sw.js handlers
        │                                                     ▲
        ▼                                                     │  CDP ServiceWorker.deliverPushMessage
  server push logic  ──►  pywebpush  ──►  Fake Push Service ──┘   (decrypted payload injected)
  (_push_or_defer,          (real           (captures + decrypts
   _do_send_push,            encrypt)         the real WebPush,
   scheduler)                                 then bridges to SW)
        │                                            │
        ▼                                            ▼
   [PUSH] server log                         swlog + ack POSTs, showNotification,
   DB subscription rows                      setAppBadge, cache write, postMessage nav
```

Every segment is asserted against captured signals:

| Segment | Substituted? | How it is observed |
|---|---|---|
| WS trigger → push decision | No (real code) | `[PUSH]` server log, captured WS frames |
| push decision → encrypted wire | No (real pywebpush) | FPS captures the raw request; we decrypt it (we hold the sub keys) |
| wire → SW `push` handler | Bridged via CDP | real sw.js runs; `swlog('push-received')`, `getNotifications()` |
| SW → client navigation | No (real code) | `swlog('click-done')`, Cache Storage read, page `postMessage`/`location` |
| SW/client → server ack | No (real code) | `[PUSH-ACK]` log, captured `POST /chat/api/push/ack` |

---

## 2. Observability layers

The harness is organized into four layers. Layers 1–3 are **fully automated headless**; Layer 4 adds
**real-browser legs** (headed Chrome + Brave) for the handful of behaviors that only a genuine push
subscription exercises.

### Layer 1 — Server → wire (deterministic, no real browser needed)
The highest-value invariants live here and are 100% automatable.

Subscriptions are injected directly into the DB with endpoints pointing at the FPS, using **real ECDH
keypairs the harness generates** (so we can decrypt). Then the harness drives the real server (send
messages, seed due timetable slots) and the FPS captures each emitted WebPush.

Asserted from captured requests:
- **Payload** decrypts to the expected JSON: `title`, `body`, `url`, `room_id`, `count`,
  `total_unread`, `silent`, `push_id` (present + unique per push), `push_index`.
- **Headers**: `TTL: 300` (not 0), `Urgency`, `Topic`; the **VAPID JWT** `aud` equals the endpoint's own
  origin and `sub` equals `VAPID_CLAIMS_EMAIL`.
- **Per-service VAPID isolation** (the invariant that cost "a full afternoon"): register three subs on
  three distinct FPS origins standing in for FCM/Apple/Mozilla; assert each request's JWT `aud` matches
  its *own* origin — proving the per-call `dict(vapid_claims)` copy holds and one service's `aud` never
  poisons another.
- **Targeting**: offline user → push; idle-30s user → push; actively-connected user → no push. Driven by
  `manager._last_ws_activity` and connection state.
- **Debounce / silent escalation**: first push within 10s window is loud; a second within 60s is
  `silent:true`; `_push_sent` only sets after a real send (the fix from this round).
- **410/404 pruning**: FPS returns 410 for one endpoint; assert the row is deleted from
  `chat_push_subscriptions` (chat) / `push_subscriptions` (lineup).
- **Timetable dedup**: scheduler sends once per `(session_id, slot_id)`; a second scheduler tick with the
  row present in `sent_notifications` emits nothing.
- **Pending-moderation gating** (this round's fix): a message still `moderation_status='pending'` never
  appears in a push body or inflates `total_unread`.

### Layer 2 — Wire → service worker (CDP-bridged)
The FPS, on receiving a captured push, decrypts it and calls
`CDP ServiceWorker.deliverPushMessage(origin, registrationId, payload)` to fire the **real sw.js** `push`
event in a live (headless) Chromium context.

Asserted:
- `swlog('push-received', url)` reaches the server → the handler ran with the right URL.
- Page-side `navigator.serviceWorker.ready → reg.getNotifications()` shows exactly one notification with
  the expected `title`/`body`/`tag`; a second push with the same `room_id` **replaces** it (tag collapse)
  and a different `room_id` does not.
- **Tag uniqueness**: two pushes with distinct `push_id` never collide even across a simulated server
  restart (reset `_push_counter`), guarding the iOS `notificationclick`-drop bug.
- `navigator.setAppBadge(total_unread)` was called (spied — see §3).
- Click path: trigger `notificationclick` (CDP `Runtime.evaluate` in the SW target dispatching the
  handler, or a real click in Layer 4). Assert Cache Storage `stc-push/_push_navigate` holds the target
  URL, the page received `postMessage({type:'navigate'})`, `swlog('click-done', ...)` fired, and
  `POST /chat/api/push/ack {action:'clicked'}` reached the server.
- Dismiss path: `notificationclose` → `ack {action:'dismissed'}`.

### Layer 3 — Client behavior (deterministic, page-observable)
Real chat.html / render.py JS, all signals captured page-side.

Asserted:
- **Enable flow**: `GET /push/vapid-key` → `Notification.requestPermission` (spied granted) →
  `pushManager.subscribe` (spied) → `POST /push/subscribe` with `{endpoint,keys}` → `push_enabled='1'`.
  Critically assert **no `unsubscribe()` precedes `subscribe()`** (shared-endpoint invariant).
- **Repair on load**: gated by `push_enabled==='1'` + `permission==='granted'`; resyncs via
  `POST /push/subscribe`; never runs when the flag is unset (verified by absence of the vapid-key fetch).
- **Disable flow**: endpoint captured *before* `unsubscribe()`; `DELETE /push/subscribe`;
  `push_enabled` removed; `notif_prompt_done='1'` set.
- **Idle detection**: on `visibilitychange(hidden)`/`pagehide`, `sendBeacon('/chat/api/push/idle')` fires
  (spied). On focus, `wsSend('visible')` resumes; a **visible-but-unfocused** window sends **no** visible
  signal (the `document.hasFocus()` gate — this round's fix).
- **Badges**: `badge_counts` on connect and `badge_update` on message populate `unreadByRoom`; title
  shows `(N)`; `setAppBadge(N)` called; `mark_read` clears to 0 across the user's connections (cross
  -device). Assert `_hiddenUnread` is gone (no double count).
- **First-run banner** (this round's redesign): arms only after the first sent message, is a non-blocking
  top banner (never intercepts the composer — assert the send button is clickable while it is shown),
  auto-dismiss after 12s does not persist `notif_prompt_done`, explicit answer does.

### Layer 4 — Multi-instance & real-browser legs
- **Cross-device badge sync**: user on context A + context B; read in A broadcasts `badge_update count=0`
  to B. Assert B's title/app-badge clear.
- **Sender vs recipients**: 1 sender + N recipients across contexts; assert the sender is never a push
  target and each recipient's state matches its presence (active/idle/offline).
- **Shared subscription per origin**: enable notifications on **both** lineup and chat in one context;
  assert the lineup record (`push_subscriptions`) and chat record (`chat_push_subscriptions`) hold the
  **same endpoint** and neither enable rotated the other's endpoint — the automated analogue of
  `verify_push_both.py`.
- **Real-browser legs (headed Chrome channel + Brave via executablePath)**: the only leg that produces a
  genuine FCM subscription. Runs the enable flow for real and, if a live subscription results, drives one
  real send through `verify_push_both.py`'s assertion (same-endpoint, LIVE). Brave is mandatory here
  because FCM strictly validates the signing key and Brave's GCM socket is the historical failure point;
  Firefox/WebKit passing proves nothing about Chromium-family push.

---

## 3. How every signal is captured (the recorder)

A single `SignalRecorder` timestamps every observed signal from all sources into one ordered timeline per
scenario. Assertions are expressed as **ordered sequence + timing-bound** matches against that timeline,
which is what lets us verify "when and as it should," not just "did it happen."

| Source | Mechanism |
|---|---|
| HTTP requests (subscribe, idle, ack, swlog, vapid-key) | Playwright `context.on("request")` filtered by path |
| WebSocket frames (`visible`, `mark_read`, `send_message`, `badge_update`, `badge_counts`) | `page.on("websocket")` → `ws.on("framesent"/"framereceived")`, JSON-parsed |
| Browser push APIs (`Notification`, `requestPermission`, `pushManager.subscribe/getSubscription/unsubscribe`, `setAppBadge`, `clearAppBadge`, `sendBeacon`) | `context.add_init_script` installs spies that push `{api, args, t}` into `window.__signals`; read via `page.evaluate` |
| Service-worker actions | The SW already POSTs `swlog` (`push-received`, `click-done`, `postmessage-received`) and `ack` (`delivered`/`clicked`/`dismissed`) — captured as HTTP requests above; no SW instrumentation needed |
| Shown notifications | `reg.getNotifications()` from the page (title/body/tag/data) |
| Cache navigation | `caches.open('stc-push').match('/_push_navigate')` from the page |
| Server-side push decisions | tail the server log for `[PUSH] targets=.. all=.. connected=..`, `[PUSH-ACK]`, `[SWLOG]`, `[MOD]` |
| Emitted WebPush wire | FPS records method/headers/body per request; decrypts body with the sub's private key |
| DB state | direct sqlite reads: `chat_push_subscriptions`, `push_subscriptions`, `sent_notifications`, `room_memberships.last_read_at`, `users.last_seen/last_active`, `messages.moderation_status` |
| In-memory server state | a tiny test-only introspection endpoint (guarded by a test flag) exposing `_push_debounce`/`_push_sent`/`_push_counter`/`_last_ws_activity` sizes, or assert them indirectly via behavior |

The recorder writes a per-scenario JSON timeline artifact so a failure shows the exact observed vs
expected sequence.

---

## 4. Scenario matrix

Each scenario is `trigger → expected ordered signal sequence (with timing bounds) → assertions`.

1. **Cold enable (chat)** — click Enable → vapid-key GET, requestPermission=granted, subscribe (no prior
   unsubscribe), subscribe POST, `push_enabled=1`. FPS now holds the sub.
2. **Offline recipient push** — recipient disconnects; sender sends → FPS receives one push within ~1s;
   payload decrypts to `{title:sender, body:preview, url:/chat/msg/<id>, count:1}`; `[PUSH] targets=1`.
3. **Idle recipient push** — recipient connected but silent >30s → push sent; active recipient (<30s) →
   no push.
4. **Debounce + silent escalation** — two rapid sends → first push loud, second `silent:true`; assert the
   60s window and that `_push_sent` set only after the first real send.
5. **Delivery → notification** — CDP-deliver the captured push → `swlog('push-received')`; exactly one
   notification with correct title/body/tag.
6. **Tag collapse vs uniqueness** — two pushes same `room_id` → one notification (old closed); different
   `room_id` → two; distinct `push_id` across a `_push_counter` reset → no tag reuse.
7. **Click → navigate → ack** — notificationclick → cache write, `postMessage navigate`, page navigates
   to `/chat/msg/<id>`, `swlog('click-done')`, `ack clicked`.
8. **Dismiss → ack** — notificationclose → `ack dismissed`.
9. **Badge fan-out + cross-device clear** — message → `badge_update` to B not A(current); title `(1)`,
   `setAppBadge(1)`; read in A → `badge_update count=0` to B; B clears.
10. **Idle beacon** — hide tab → `sendBeacon('/chat/api/push/idle')`; server zeroes activity → immediate
    push eligibility on next send.
11. **Focus gating** — visible-but-unfocused window sends no `visible` frame; focusing sends one
    immediately then every 20s.
12. **410 pruning** — FPS returns 410 → subscription row deleted (chat and lineup).
13. **VAPID isolation** — 3 subs on 3 FPS origins → 3 pushes each with a correct per-origin `aud`.
14. **Timetable push + dedup** — seed a due slot + a session schedule → one lineup push
    (`url:/?view=timetable`, has `push_id`); second tick emits nothing (`sent_notifications`).
15. **Pending moderation gating** — a `pending` message never enters a push body or `total_unread`.
16. **Shared endpoint (lineup+chat)** — enable both in one context → identical endpoint in both tables,
    no rotation.
17. **Disable + no-repair** — disable → DELETE, flag cleared; reload → repair does not run (no vapid-key
    fetch).
18. **First-run banner non-blocking** — after first message the banner shows; the send button is still
    clickable (regression guard for the modal that blocked clicks); auto-dismiss does not persist.
19. **pushsubscriptionchange** — simulate via CDP/`dispatchEvent` → resubscribe → `POST /push/subscribe`
    with the new endpoint.
20. **Real-browser FCM leg (Chrome + Brave, headed)** — genuine enable; if a live sub results, one real
    send asserts same-endpoint + LIVE (the automated `verify_push_both`).

---

## 5. Isolation & reset

Per the server signal inventory, before each scenario the harness resets:
- In-memory: `_push_debounce`, `_push_sent`, `_push_flush_tasks` (cancel), `_push_counter=0`,
  `manager._last_ws_activity` — via a test-only reset hook or a fresh server process.
- DB (scratch `CHAT_DB_PATH` + scratch hearts.db): clear `chat_push_subscriptions`,
  `push_subscriptions`, `sent_notifications`; reset `room_memberships.last_read_at`;
  null `users.last_seen/last_active`; prune test messages.
- Browser: fresh `context` per user/device; `add_init_script` seeds spies and (where a scenario is not
  about onboarding) `notif_prompt_done=1`, matching the existing harnesses.

Isolated server: own uvicorn on a free ephemeral port (never 64728), sensitive env stripped, its own
VAPID keypair generated for the run, `CHAT_DB_PATH` at a temp dir — reusing the setup helpers already in
`tests/e2ee_browser_check.py`.

---

## 6. What is and isn't fully automated

- **Fully automated (Layers 1–3, most of 4)**: every server push decision, wire payload/headers/VAPID,
  SW handler behavior, notification content/tag, click/dismiss/ack wiring, client enable/disable/repair,
  idle/focus, badges, cross-device sync, shared-endpoint, timetable, moderation gating. This is the bulk
  of the risk surface and runs headless in CI.
- **Needs a headed real browser (Layer 4 FCM leg)**: a genuine FCM subscription and delivery. Automatable
  with headed Chrome/Brave on a workstation but not in a stock headless CI runner; gated behind a
  `--real-push` flag and skipped (with a loud `log()`) when unavailable, so the suite never silently
  claims coverage it didn't run.
- **Genuinely out of scope (physical)**: real iOS lock-screen delivery/tap and real APNs. Documented as a
  manual pre-deploy check; the harness covers the code paths, not Apple's transport.

---

## 7. Harness layout & build stages

```
tests/notif_e2e/
  __init__.py
  fake_push_service.py   # aiohttp FPS: capture, decrypt (aes128gcm via http_ece), 410 mode, CDP bridge
  recorder.py            # SignalRecorder: unified timeline + sequence/timing assertions
  spies.js               # add_init_script: Notification/pushManager/setAppBadge/sendBeacon spies
  harness.py             # server lifecycle, context factory, DB reset, CDP session helpers
  scenarios/             # one module per scenario group (emission, delivery, client, cross_device)
  run.py                 # CLI: --browsers, --real-push, --scenario, writes JSON timelines + summary
```

Build order (each stage independently valuable and shippable):
1. **FPS + Layer 1** — deterministic server-emission suite (payload, VAPID isolation, targeting,
   debounce, 410, timetable, moderation gating). Highest value, no CDP needed.
2. **Recorder + Layer 3** — client enable/disable/repair, idle/focus, badges, banner. Pure Playwright.
3. **CDP bridge + Layer 2** — delivery → notification → click → ack.
4. **Layer 4** — cross-device, shared-endpoint, and the gated real-browser FCM leg.

Dependencies: `aiohttp` (FPS), `http_ece` + `cryptography` (decrypt the WebPush payload), `py-vapid`
already present via pywebpush, Playwright (installed; chromium + webkit cached; Chrome channel + Brave
executablePath for Layer 4). No new production dependencies.
```
