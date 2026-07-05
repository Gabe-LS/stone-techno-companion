# Stage 2 build contract — client behavior via Playwright

Extends CONTRACT.md. Stage 2 adds a browser layer on top of the validated Stage-1 foundation
(harness.NotifServer, recorder.SignalRecorder) and a client-behavior scenario suite. Reuse the proven
patterns in tests/notif_badge_browser_check.py and tests/e2ee_browser_check.py — do not reinvent server
startup, session login, the Notification spy, or the fetch tracker.

No emojis. Clear docstrings. No new dependencies (Playwright is installed; chromium + webkit cached).
You have no Bash and cannot run anything — the orchestrator runs and debugs in real browsers.

## Reused, already-working pieces
- harness.NotifServer — isolated server (own port, scratch chat.db + hearts.db, generated VAPID, log
  capture). Use its base_url, ws_base, create_user, create_session, ensure_membership, main_room_id,
  chat_sub_count, grep_log. Start/stop it once per run; scenarios share it.
- recorder.SignalRecorder — record(source, kind, data), assert_sequence, assert_within, assert_absent.
- Spy patterns to copy from notif_badge_browser_check.py: NOTIFICATION_SPY_SCRIPT, FETCH_TRACKER_SCRIPT.
- Login: add a chat_session cookie (non-httpOnly, secure=False, sameSite=Lax) as in that file's
  add_session_cookie; the harness user already has a complete profile so /chat routes into the main room.

## Module to build: tests/notif_e2e/browser.py

Init scripts (context.add_init_script, must run before page JS):

1. SPY_SCRIPT — a single combined script installing:
   - Notification spy: window.__notifications=[]; FakeNotification records {title, body}; permission
     getter returns 'granted'; requestPermission()->resolve('granted'); window.Notification=Fake.
   - App badge spy: window.__appBadge={value:null, calls:[], cleared:0};
     navigator.setAppBadge=(n)=>{__appBadge.value=n; __appBadge.calls.push(n); return Promise.resolve()};
     navigator.clearAppBadge=()=>{__appBadge.value=null; __appBadge.cleared++; return Promise.resolve()}.
   - sendBeacon spy: window.__beacons=[]; wrap navigator.sendBeacon to push the url string then return
     true (do NOT call through — the beacon body is empty and the server endpoint is exercised separately;
     recording the call is what matters). Keep it truthy so client code paths that check the return value
     behave normally.
   - Focus control: window.__forceFocus=true; override document.hasFocus=()=> !!window.__forceFocus.
   - Hidden control: window.__forceHidden=false; redefine document.hidden via Object.defineProperty(
     Document.prototype OR document, 'hidden', {get:()=>!!window.__forceHidden, configurable:true}) and
     also document.visibilityState get ()=> __forceHidden?'hidden':'visible'. Provide a global helper
     window.__setHidden=(h)=>{window.__forceHidden=h; document.dispatchEvent(new Event('visibilitychange'));}.
   - fetch tracker: window.__fetchRequests=[] (same as FETCH_TRACKER_SCRIPT).

2. def push_subscribe_success_script(endpoint: str, p256dh_b64: str, auth_b64: str) -> str
   Returns JS overriding PushManager so the ENABLE flow succeeds headlessly (real subscribe fails — no
   push service). It must:
   - keep module state `_sub` (null until subscribed);
   - PushManager.prototype.subscribe = (opts)=> { _sub = fakeSub; return Promise.resolve(_sub); }
   - PushManager.prototype.getSubscription = ()=> Promise.resolve(_sub);
   where fakeSub = { endpoint, options:{...}, getKey(name){ return the raw bytes of p256dh/auth as an
   ArrayBuffer }, toJSON(){ return {endpoint, keys:{p256dh:p256dh_b64, auth:auth_b64}} },
   unsubscribe(){ _sub=null; return Promise.resolve(true); } }.
   getKey('p256dh')/('auth') must return an ArrayBuffer of the base64url-decoded bytes (the client does
   btoa(String.fromCharCode(...new Uint8Array(sub.getKey('p256dh')))) to build its POST body — verify the
   exact encoding chat.html._subscribePush uses and make getKey round-trip to the same p256dh_b64/auth_b64
   the server will store). The endpoint MUST be an allowlisted host so POST /chat/api/push/subscribe
   accepts it: use "https://fcm.googleapis.com/fcm/send/" + a random token.

class BrowserLab:
    def __init__(self, server: NotifServer, browser) -> None: ...   # browser: a Playwright Browser
    def new_session(self, user_id: str, *, recorder: SignalRecorder | None = None,
                    subscribe_success: bool = True, extra_init: list[str] | None = None) -> "BrowserSession"
        # create a context, add SPY_SCRIPT + (optionally) push_subscribe_success_script + any extra_init,
        # add the session cookie for user_id's session (create one via server.create_session), attach WS
        # frame capture and request capture wired to `recorder`, open base_url + "/chat", wait for
        # "#messages" and routing complete (page.evaluate("() => !_routing")). Return a BrowserSession.

class BrowserSession:
    page, context                     # Playwright handles
    user_id: str
    async def call(self, js: str): ...        # page.evaluate wrapper (accepts an expression or fn)
    def ls_get(self, key: str) -> str | None
    def ls_set(self, key: str, value: str) -> None
    def notifications(self) -> list[dict]
    def app_badge(self) -> dict
    def beacons(self) -> list[str]
    def fetches(self) -> list[str]
    def title(self) -> str
    def ws_sent(self) -> list[dict]           # parsed JSON frames the page SENT (event + fields)
    def ws_received(self) -> list[dict]       # parsed JSON frames the page RECEIVED
    def set_focus(self, focused: bool) -> None # sets window.__forceFocus and dispatches focus/blur
    def set_hidden(self, hidden: bool) -> None # calls window.__setHidden(hidden)
    def wait_ws(self, event: str, direction: str = "received", timeout: float = 5.0) -> dict
    def wait_fetch(self, substr: str, timeout: float = 5.0) -> str
    def close(self) -> None

WS capture: use page.on("websocket", ...) then ws.on("framesent")/ws.on("framereceived"); JSON-parse the
payloads; record each into the SignalRecorder as kind f"ws_sent:{event}" / f"ws_recv:{event}" and store
locally for ws_sent()/ws_received(). Network capture: context.on("request") filtered to /chat/api/push/*,
/chat/api/swlog, /chat/api/push/idle, /chat/api/push/vapid-key, /chat/api/push/subscribe — record kind
f"http:{method} {path}" with the post body where available.

Notes / gotchas:
- Real headless Chromium: pushManager.subscribe fails; that is exactly why subscribe_success installs the
  override. Notification.permission is 'denied' in headless; the spy forces 'granted'.
- The harness user's avatar (/chat/api/avatar/{id}) 404s (no blob stored) — cosmetic; scenarios should
  allowlist that 404 in any zero-error assertion.
- Do not call through the real navigator.sendBeacon; recording the call is the signal under test.

## Module to build: tests/notif_e2e/scenarios/client.py + extend run.py (browser mode)

Each scenario: async fn(lab: BrowserLab, server: NotifServer, recorder: SignalRecorder) -> list[str]
(failures; empty = pass). Scenarios (all real-browser, Chromium headless unless noted):

1. enable_success — new_session(subscribe_success=True); call _enableAllNotifications(); assert, IN ORDER:
   a fetch of /push/vapid-key, requestPermission called (Notification spy present), then a POST
   /chat/api/push/subscribe carrying {endpoint (fcm host), keys:{p256dh,auth}}; and NO unsubscribe was
   called before subscribe (shared-endpoint invariant — track a subscribe/unsubscribe order flag in the
   override); localStorage push_enabled==='1'; server.chat_sub_count(user)==1 with the endpoint stored.
2. disable_flow — after enabling, call _disableAllNotifications(); assert the endpoint was read BEFORE
   unsubscribe(), a DELETE /chat/api/push/subscribe was sent, push_enabled removed, notif_prompt_done==='1',
   server.chat_sub_count(user)==0.
3. repair_gated — reload with no push_enabled -> assert_absent a /push/vapid-key fetch; set push_enabled='1'
   with an existing subscription present -> reload -> assert a resync POST /chat/api/push/subscribe occurs
   (repair path), not a duplicate browser subscribe.
4. idle_beacon — set_hidden(true) -> assert a sendBeacon to /chat/api/push/idle was recorded; and that the
   server acted on it (a subsequently-sent message from another user reaches this now-idle user as a push,
   OR observe server.grep_log for the idle handling — pick the cleanest observable).
5. focus_gated_keepalive — set_focus(false) while visible -> assert NO 'visible' WS frame is sent over the
   next keepalive tick window; set_focus(true) -> assert a 'visible' frame IS sent. (Guards this deploy's
   document.hasFocus() gate.) Keep the wait short; if the 20s keepalive interval makes this slow, trigger
   the code path directly by evaluating the keepalive function and asserting on the WS frame, and note it.
6. badge_fanout_cross_device — user U in session A (viewing main room) and session B (same U, different
   context, treated as a second device). A third user sends a message to the main room -> assert B receives
   a badge_update and its document.title shows "(1)" and setAppBadge(1) was called; then mark_read in A ->
   assert B receives badge_update count=0 and clearAppBadge / title resets. (Cross-device clear.)
7. first_run_banner_nonblocking — fresh session with NO notif_prompt_done seeded (override the harness
   default so the banner CAN show); send the first message; wait ~1s; assert the #notif-prompt-banner
   element exists AND the composer send button is clickable (get its bounding box / click it without the
   banner intercepting — the regression guard for the old blocking modal); assert an explicit dismiss sets
   notif_prompt_done and removes the banner.

run.py: add a browser-mode runner (e.g. `python tests/notif_e2e/run.py --browser`) that launches one
Playwright chromium (headless), one NotifServer, runs the client scenarios each with a fresh
BrowserLab/session and recorder, prints a PASS/FAIL table, writes _artifacts/<scenario>.json, exits
non-zero on any failure. Keep the existing emission mode working (default runs emission; --browser runs
client; --all runs both).

End your final message listing scenarios implemented, any made SKIP with reason, and every place you had
to read chat.html to match an exact function name or subscribe encoding.
