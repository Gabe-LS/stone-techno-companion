# Stage 3 build contract — service-worker handler behavior

Extends CONTRACT.md / CONTRACT_STAGE2.md. Stage 3 tests the REAL sw.js push/notificationclick/
notificationclose/pushsubscriptionchange handlers. The MECHANISM IS ALREADY PROVEN and validated in
tests/notif_e2e/_smoke_sw.py -- READ THAT FILE FIRST; it is the canonical reference. Reuse its
INSTALL_MOCK_SW / FIRE_PUSH / FIRE_CLICK scripts and its mock-SW approach verbatim where possible.

Why a mock SW (not a real registered one): headless Chromium has no notification backend, so
registration.showNotification rejects and the handler's chain (swlog/ack run only AFTER it resolves)
never completes; real push delivery needs a live subscription; and Playwright's service-worker Worker
handles are terminated aggressively (they crash mid-evaluate). The mock env loads the real sw.js source
via `new Function('self','fetch','caches','navigator', swSrc)(...)` with recording mocks -- this runs OUR
real handler code deterministically. Real OS notification render + real push transport are Stage 4's
headed real-browser leg, NOT this.

No emojis. Clear docstrings. No new dependencies (Playwright + httpx already used). Synchronous
(Playwright sync API; the SW scenarios never touch asyncio). You have no Bash; the orchestrator runs and
debugs.

## Module to build: tests/notif_e2e/sw_harness.py

class SWLab:
    def __init__(self, server, browser, sw_src: str) -> None: ...  # server: NotifServer; browser: sync
        # Playwright Browser; sw_src: the /sw.js source text (orchestrator fetches once via httpx).
    def new_harness(self, *, match_client: str | None = None, recorder=None) -> "SWHarness": ...
        # Create a fresh page, goto server /line-up (domcontentloaded), install the mock SW with the real
        # sw.js and (optionally) one existing client URL (match_client) so the click path exercises the
        # postMessage-to-existing-client branch; None exercises the openWindow branch. Returns SWHarness.

class SWHarness:
    """One page running the real sw.js in a persistent mock SW env. Persistent so tag-collapse tests
    (multiple pushes sharing one notification store) work."""
    def push(self, payload: dict) -> dict: ...          # fire the push handler with json.dumps(payload),
                                                        # await waitUntil, return the recorder snapshot
                                                        # {shown, closed, fetches, badge, cachePuts,
                                                        #  postMessages, openWindows, clientsMatched}.
    def click_last(self) -> dict: ...                   # fire notificationclick on the most-recent shown
                                                        # notification; return the recorder snapshot.
    def close_last(self) -> dict: ...                   # fire notificationclose on the most-recent shown.
    def subscription_change(self, old_options: dict | None = None) -> dict: ...
                                                        # fire pushsubscriptionchange with an
                                                        # oldSubscription that has .options; return snapshot.
    def notifications(self) -> list: ...                # current notifStore contents.
    def signals(self) -> dict: ...                      # the full recorder object.
    def close(self) -> None: ...

Record EVERY handler side effect into both the JS recorder AND, when a recorder (SignalRecorder) is
given, into it (kind e.g. "sw:shown", "sw:fetch:<path>", "sw:badge", "sw:cachePut", "sw:postMessage",
"sw:openWindow", "sw:closed"). Add the FIRE_CLOSE and FIRE_SUBCHANGE evaluate scripts (mirror FIRE_CLICK):
- notificationclose event: { notification: <last>, waitUntil }.
- pushsubscriptionchange event: { oldSubscription: { options: <old_options or a default> }, waitUntil }.
  (sw.js returns early if \!event.oldSubscription, then resubscribes and POSTs /chat/api/push/subscribe.)

## Module to build: tests/notif_e2e/scenarios/sw.py

Each scenario: `def fn(swlab, server, recorder) -> list[str]` (failures; empty = pass). Reuse the emission
module's ScenarioSkip if a skip is ever needed (`from scenarios.emission import ScenarioSkip`). SCENARIOS
is a dict {name: {"fn": fn}} (same shape as scenarios/client.py). Scenarios:

1. push_shows_notification -- one push -> exactly one notification with title "#<room name from payload>",
   correct body, tag == "stc-<room_id>-<push_id>", data {url, roomId, count}; a swlog fetch with
   step "push-received" and the payload url; an ack fetch with action "delivered"; setAppBadge(total_unread).
2. tag_uniqueness_and_collapse -- push room "general" push_id p1, then room "general" push_id p2 (diff):
   the first is closed (rec.closed contains its tag), exactly one notification remains, and the two tags
   differ. Then push room "other" push_id p3: two notifications now coexist (different rooms not collapsed).
3. silent_followup -- a push with silent:true -> the shown notification has silent === true; a push with
   silent absent/false -> silent falsy.
4. click_existing_client -- new_harness(match_client set) -> push -> click_last -> assert cachePuts has
   "/_push_navigate", a postMessage {type:"navigate", url:<full url>} was sent to the client, the client
   was focused, an ack "clicked" fetch, a swlog "click-done" fetch, and NO openWindow call.
5. click_opens_window -- new_harness(match_client=None) -> push -> click_last -> assert openWindow was
   called with the full url, ack "clicked", and NO postMessage.
6. close_acks_dismissed -- push -> close_last -> assert an ack fetch with action "dismissed".
7. subscriptionchange_resubscribes -- subscription_change(old_options) -> assert a POST
   /chat/api/push/subscribe fetch whose body has endpoint (the rotated one) and keys {p256dh, auth}.

## Wire into run.py

Add a `--sw` mode: start one NotifServer, launch one sync_playwright chromium, fetch /sw.js once, build an
SWLab, run each sw scenario with a fresh SWHarness (new_harness per scenario) and a fresh SignalRecorder,
print a PASS/FAIL table, write _artifacts/<scenario>.json, exit non-zero on failure. Extend `--all` to run
emission, then browser, then sw. Keep the three paths separate (emission = asyncio; browser + sw = sync
Playwright, never inside asyncio.run). Add sw scenarios to `--list`.

End your final message listing scenarios implemented, any SKIP with reason, and confirm you reused the
_smoke_sw.py mock-SW scripts.
