"""Stage 2 browser layer -- drives real Chromium against a NotifServer and
records client-side notification behavior into a SignalRecorder.

Uses Playwright's SYNC API throughout (matching the proven patterns in
tests/notif_badge_browser_check.py: context/page setup, wait_until polling,
add_session_cookie, and driving _enableAllNotifications via page.evaluate).
The one exception is BrowserSession.call(), which is declared `async def` so
it composes naturally with the async scenario functions CONTRACT_STAGE2
specifies for tests/notif_e2e/scenarios/client.py (`async fn(lab, server,
recorder) -> list[str]`). That coroutine does not use asyncio internally --
it just calls the synchronous `page.evaluate()` and returns the result
immediately. Every other method on BrowserLab/BrowserSession is a plain
synchronous call, exactly like notif_badge_browser_check.py.

No new dependencies: only Playwright (already installed per CONTRACT_STAGE2)
plus stdlib (base64, json, secrets, time, urllib.parse).
"""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse

# --- SPY_SCRIPT -------------------------------------------------------------
# A single combined init script installing every client-side spy/override
# scenarios need. Added via context.add_init_script() so it runs before any
# page JS on every navigation (initial load AND reloads).
#
# Individually modeled on tests/notif_badge_browser_check.py's
# NOTIFICATION_SPY_SCRIPT and FETCH_TRACKER_SCRIPT, extended per
# CONTRACT_STAGE2 with app-badge, sendBeacon, focus, and hidden spies that
# file did not need.
SPY_SCRIPT = """
(() => {
  // --- Notification spy ---
  // Headless Chromium reports Notification.permission === 'denied'
  // regardless of context.grant_permissions(); this override makes the
  // app's permission checks and `new Notification(...)` calls behave as if
  // the user had already granted permission, and records every constructed
  // notification for assertions.
  window.__notifications = [];
  function FakeNotification(title, options) {
    window.__notifications.push({ title: title, body: options && options.body });
    this.onclick = null;
  }
  FakeNotification.prototype.close = function () {};
  Object.defineProperty(FakeNotification, 'permission', { get: () => 'granted' });
  FakeNotification.requestPermission = () => Promise.resolve('granted');
  window.Notification = FakeNotification;

  // --- App badge spy ---
  window.__appBadge = { value: null, calls: [], cleared: 0 };
  navigator.setAppBadge = (n) => {
    window.__appBadge.value = n;
    window.__appBadge.calls.push(n);
    return Promise.resolve();
  };
  navigator.clearAppBadge = () => {
    window.__appBadge.value = null;
    window.__appBadge.cleared++;
    return Promise.resolve();
  };

  // --- sendBeacon spy ---
  // Records the call but does NOT call through to the real sendBeacon: the
  // beacon body chat.html sends is empty and the /chat/api/push/idle
  // endpoint is exercised directly by scenarios over HTTP -- the call
  // itself (and that it fires from the right event) is the signal under
  // test. Stays truthy so client code that branches on the return value
  // behaves exactly as it would with a real, successful beacon.
  window.__beacons = [];
  navigator.sendBeacon = (url) => {
    window.__beacons.push(String(url));
    return true;
  };

  // --- Focus control ---
  window.__forceFocus = true;
  document.hasFocus = () => !!window.__forceFocus;

  // --- Hidden control ---
  window.__forceHidden = false;
  Object.defineProperty(document, 'hidden', {
    get: () => !!window.__forceHidden,
    configurable: true,
  });
  Object.defineProperty(document, 'visibilityState', {
    get: () => (window.__forceHidden ? 'hidden' : 'visible'),
    configurable: true,
  });
  window.__setHidden = (h) => {
    window.__forceHidden = h;
    document.dispatchEvent(new Event('visibilitychange'));
  };

  // --- fetch tracker ---
  // Same shape as notif_badge_browser_check.py's FETCH_TRACKER_SCRIPT.
  window.__fetchRequests = [];
  var _origFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      var u = typeof input === 'string' ? input
            : (input && typeof input === 'object' && 'url' in input) ? input.url
            : String(input);
      window.__fetchRequests.push(u);
    } catch (e) {}
    return _origFetch.apply(this, arguments);
  };
})();
"""

# Suppresses the first-run notification banner by default, mirroring
# notif_badge_browser_check.py's add_session_cookie -- most scenarios are
# not testing the banner and it must never overlap the flow under test.
# BrowserLab.new_session() always injects this. Scenarios that DO want the
# banner to show (CONTRACT_STAGE2 scenario 7, first_run_banner_nonblocking)
# must override it by passing ALLOW_FIRST_RUN_BANNER_SCRIPT in extra_init --
# init scripts run in registration order, so a later script can undo an
# earlier one's localStorage write before any page JS reads it.
SUPPRESS_FIRST_RUN_BANNER_SCRIPT = (
    "() => { try { localStorage.setItem('notif_prompt_done', '1'); } catch (e) {} }"
)

# Pass this in BrowserLab.new_session(..., extra_init=[ALLOW_FIRST_RUN_BANNER_SCRIPT])
# to undo the default suppression above for a session that must exercise the
# first-run banner.
ALLOW_FIRST_RUN_BANNER_SCRIPT = (
    "() => { try { localStorage.removeItem('notif_prompt_done'); } catch (e) {} }"
)


def _b64url(data: bytes) -> str:
    """Unpadded base64url encoding, matching harness.py's WebPush encoding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def push_subscribe_success_script(endpoint: str, p256dh_b64: str, auth_b64: str) -> str:
    """Build an init script overriding PushManager so the app's enable flow
    succeeds headlessly (a real pushManager.subscribe() call fails in
    headless Chromium -- there is no push service to talk to).

    The fake PushSubscription's toJSON() returns {endpoint, keys: {p256dh,
    auth}} with p256dh_b64/auth_b64 verbatim -- this is the ONLY encoding
    chat.html's _subscribePush()/_repairPushSubscription()/
    _disableAllNotifications() actually use (`const subJson = sub.toJSON()`,
    then `JSON.stringify({endpoint: subJson.endpoint, keys: subJson.keys})`);
    chat.html never calls getKey() itself. getKey('p256dh')/('auth') is
    still implemented to return an ArrayBuffer of the raw base64url-decoded
    bytes, matching the real PushSubscription interface and CONTRACT_STAGE2's
    explicit round-trip requirement, so any code path that does call it
    (or a future one) sees the same bytes toJSON() reports as base64.

    endpoint MUST be an allowlisted host for POST /chat/api/push/subscribe
    to accept it (server/chat_api.py's _is_valid_push_endpoint checks
    scheme == "https" and hostname suffix in {.googleapis.com, ...}) --
    callers should pass an "https://fcm.googleapis.com/fcm/send/<token>" URL.

    Subscription state is persisted in localStorage (under an internal key),
    not a plain JS closure variable, so it survives page reloads within the
    same browser context -- a bare closure variable would silently reset to
    "not subscribed" on every navigation, since Playwright re-runs init
    scripts from scratch on each one. This is required for CONTRACT_STAGE2's
    repair_gated scenario, which depends on a subscription still being
    present across a reload.

    window.__pushCalls accumulates 'getSubscription' / 'subscribe' /
    'unsubscribe' in call order, so scenarios can assert invariants like
    "no unsubscribe before subscribe" (the shared-endpoint invariant).
    """
    return """
(() => {{
  var STORAGE_KEY = '__fake_push_sub_v1';
  var ENDPOINT = {endpoint_json};
  var P256DH_B64 = {p256dh_json};
  var AUTH_B64 = {auth_json};

  function _b64urlToBytes(b64url) {{
    var b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }}
  var P256DH_BYTES = _b64urlToBytes(P256DH_B64);
  var AUTH_BYTES = _b64urlToBytes(AUTH_B64);

  window.__pushCalls = window.__pushCalls || [];

  function _makeFakeSub() {{
    return {{
      endpoint: ENDPOINT,
      options: {{ userVisibleOnly: true }},
      getKey: function (name) {{
        var bytes = name === 'p256dh' ? P256DH_BYTES
                  : name === 'auth' ? AUTH_BYTES
                  : new Uint8Array(0);
        return bytes.buffer;
      }},
      toJSON: function () {{
        return {{ endpoint: ENDPOINT, keys: {{ p256dh: P256DH_B64, auth: AUTH_B64 }} }};
      }},
      unsubscribe: function () {{
        window.__pushCalls.push('unsubscribe');
        try {{ localStorage.removeItem(STORAGE_KEY); }} catch (e) {{}}
        return Promise.resolve(true);
      }},
    }};
  }}

  function _hasPersistedSub() {{
    try {{ return localStorage.getItem(STORAGE_KEY) === '1'; }} catch (e) {{ return false; }}
  }}

  if (typeof PushManager !== 'undefined') {{
    PushManager.prototype.subscribe = function () {{
      window.__pushCalls.push('subscribe');
      try {{ localStorage.setItem(STORAGE_KEY, '1'); }} catch (e) {{}}
      return Promise.resolve(_makeFakeSub());
    }};
    PushManager.prototype.getSubscription = function () {{
      window.__pushCalls.push('getSubscription');
      return Promise.resolve(_hasPersistedSub() ? _makeFakeSub() : null);
    }};
  }}
}})();
""".format(
        endpoint_json=json.dumps(endpoint),
        p256dh_json=json.dumps(p256dh_b64),
        auth_json=json.dumps(auth_b64),
    )


def _wait_until(predicate, timeout: float, interval: float, desc: str) -> None:
    """Poll `predicate` until it returns truthy or `timeout` elapses.

    Mirrors the wait_until() helper in notif_badge_browser_check.py and
    harness.py -- swallows exceptions raised by `predicate` while polling
    (useful when it evaluates page JS that may not exist yet) and surfaces
    the last one on timeout.
    """
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as e:  # noqa: BLE001 - keep polling, report on timeout
            last_exc = e
        time.sleep(interval)
    if last_exc:
        raise TimeoutError(f"timed out waiting for: {desc} (last error: {last_exc})")
    raise TimeoutError(f"timed out waiting for: {desc}")


def _gen_fake_push_identity() -> tuple[str, str, str]:
    """Generate a fresh (endpoint, p256dh_b64, auth_b64) triple for a fake
    browser push subscription. The endpoint uses an FCM host so the server's
    endpoint allowlist accepts it. p256dh MUST be a real uncompressed P-256
    point: the server encrypts outbound pushes to it via http_ece, which
    validates the point (a random 0x04+64 blob raises "Invalid EC key" and
    crashes the server's push send for any offline holder of this sub).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    endpoint = "https://fcm.googleapis.com/fcm/send/" + secrets.token_hex(16)
    priv = ec.generate_private_key(ec.SECP256R1())
    raw_public = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    p256dh_b64 = _b64url(raw_public)
    auth_b64 = _b64url(secrets.token_bytes(16))
    return endpoint, p256dh_b64, auth_b64


# Paths captured by the context-level network tracker (see
# BrowserLab.new_session): every push-related endpoint plus the SW debug
# log endpoint, per CONTRACT_STAGE2's "Network capture" note.
_TRACKED_PATH_PREFIXES = ("/chat/api/push/",)
_TRACKED_EXACT_PATHS = ("/chat/api/swlog",)


def _tracked_path(path: str) -> bool:
    if path in _TRACKED_EXACT_PATHS:
        return True
    return any(path.startswith(p) for p in _TRACKED_PATH_PREFIXES)


def _make_request_handler(recorder, http_log: list, user_id: str):
    """Build a context.on("request") handler recording tracked requests into
    `http_log` (for wait_fetch()) and, if given, into `recorder` as kind
    f"http:{method} {path}" with the POST body where available.
    """

    def handler(request) -> None:
        try:
            path = urllib.parse.urlparse(request.url).path
        except ValueError:
            path = request.url
        if not _tracked_path(path):
            return
        post_data = None
        try:
            post_data = request.post_data
        except Exception:  # noqa: BLE001 - post_data access can raise post-navigation
            pass
        entry = {
            "method": request.method,
            "path": path,
            "url": request.url,
            "post_data": post_data,
        }
        http_log.append(entry)
        if recorder is not None:
            recorder.record(
                source=f"http:{user_id}",
                kind=f"http:{request.method} {path}",
                data={"url": request.url, "post_data": post_data},
            )

    return handler


def _make_websocket_handler(recorder, ws_sent: list, ws_received: list, user_id: str):
    """Build a page.on("websocket", ...) handler wiring frame capture for one
    WebSocket connection: JSON-parses each frame, appends the parsed dict to
    `ws_sent`/`ws_received`, and (if given) records it into `recorder` as
    kind f"ws_sent:{event}" / f"ws_recv:{event}" where `event` is the frame's
    top-level "event" field (chat_ws.py's WS protocol always frames as
    {"event": ..., ...}; see the client's `switch (data.event)` dispatch).
    """

    def _record(payload, direction: str) -> None:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        event = data.get("event", "?")
        (ws_sent if direction == "sent" else ws_received).append(data)
        if recorder is not None:
            kind = f"ws_sent:{event}" if direction == "sent" else f"ws_recv:{event}"
            recorder.record(source=f"ws:{user_id}", kind=kind, data=data)

    def on_websocket(ws) -> None:
        ws.on("framesent", lambda payload: _record(payload, "sent"))
        ws.on("framereceived", lambda payload: _record(payload, "received"))

    return on_websocket


class BrowserSession:
    """One logged-in browser context/page driving server/chat/chat.html,
    with every client-side spy/override from SPY_SCRIPT installed and WS +
    push-related network traffic captured.

    Constructed by BrowserLab.new_session() -- not meant to be instantiated
    directly.
    """

    def __init__(
        self,
        page,
        context,
        user_id: str,
        ws_sent: list,
        ws_received: list,
        http_log: list,
        push_identity: dict | None,
    ) -> None:
        self.page = page
        self.context = context
        self.user_id = user_id
        self._ws_sent = ws_sent
        self._ws_received = ws_received
        self._http_log = http_log
        # Not part of CONTRACT_STAGE2's required interface, but convenient
        # for scenarios that want to cross-check the endpoint the server
        # stored (e.g. via NotifServer's chat.db) against what the fake
        # subscription actually presented.
        self.push_identity = push_identity

    def call(self, js: str):
        """Evaluate `js` (an expression or a function) in the page and
        return the result. Synchronous: Playwright's sync API cannot run
        inside an asyncio loop, so the whole Stage-2 browser layer and its
        scenarios are synchronous (see the module docstring).
        """
        return self.page.evaluate(js)

    def ls_get(self, key: str) -> str | None:
        """Read a localStorage key from the page."""
        return self.page.evaluate("(k) => localStorage.getItem(k)", key)

    def ls_set(self, key: str, value: str) -> None:
        """Write a localStorage key in the page."""
        self.page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, value])

    def notifications(self) -> list:
        """All notifications constructed via the FakeNotification spy so far."""
        return self.page.evaluate("() => window.__notifications || []")

    def app_badge(self) -> dict:
        """Current state of the navigator.setAppBadge/clearAppBadge spy:
        {value, calls, cleared}."""
        return self.page.evaluate(
            "() => window.__appBadge || {value: null, calls: [], cleared: 0}"
        )

    def beacons(self) -> list:
        """URLs passed to navigator.sendBeacon so far (see SPY_SCRIPT: the
        spy records but does not call through to the real sendBeacon)."""
        return self.page.evaluate("() => window.__beacons || []")

    def fetches(self) -> list:
        """URLs passed to window.fetch() so far (client-side tracker, all
        fetch() calls page-wide -- not filtered to push-related paths; see
        wait_fetch() for the filtered, recorder-integrated equivalent)."""
        return self.page.evaluate("() => window.__fetchRequests || []")

    def title(self) -> str:
        """The page's current document.title (badge count prefix, e.g. "(1) Chat")."""
        return self.page.title()

    def ws_sent(self) -> list:
        """Parsed JSON frames the page SENT over its chat WebSocket, in
        arrival order."""
        return list(self._ws_sent)

    def ws_received(self) -> list:
        """Parsed JSON frames the page RECEIVED over its chat WebSocket, in
        arrival order."""
        return list(self._ws_received)

    def set_focus(self, focused: bool) -> None:
        """Set document.hasFocus()'s return value and dispatch a matching
        focus/blur event on window, so the page's own focus/blur listeners
        (e.g. the keepalive's window.addEventListener('focus', ...)) run."""
        self.page.evaluate(
            """(f) => {
              window.__forceFocus = f;
              window.dispatchEvent(new Event(f ? 'focus' : 'blur'));
            }""",
            focused,
        )

    def set_hidden(self, hidden: bool) -> None:
        """Set document.hidden/visibilityState and dispatch visibilitychange,
        via the window.__setHidden helper installed by SPY_SCRIPT."""
        self.page.evaluate("(h) => window.__setHidden(h)", hidden)

    def wait_ws(
        self, event: str, direction: str = "received", timeout: float = 5.0
    ) -> dict:
        """Block until a WS frame with data.event == `event` has been
        captured in the given direction ("sent" or "received"), and return
        it. Does not track a per-call cursor -- a frame already captured
        before this call satisfies it immediately, and repeated calls for
        the same event will keep returning the same (earliest) match."""
        source = self.ws_received if direction == "received" else self.ws_sent
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in source():
                if frame.get("event") == event:
                    return frame
            time.sleep(0.05)
        raise TimeoutError(
            f"timed out waiting for ws {direction} event {event!r} "
            f"(user={self.user_id})"
        )

    def wait_fetch(self, substr: str, timeout: float = 5.0) -> str:
        """Block until a tracked network request (see _TRACKED_PATH_PREFIXES
        / _TRACKED_EXACT_PATHS -- /chat/api/push/* and /chat/api/swlog) whose
        URL contains `substr` has been captured, and return its full URL."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for entry in self._http_log:
                if substr in entry["url"]:
                    return entry["url"]
            time.sleep(0.05)
        raise TimeoutError(
            f"timed out waiting for a tracked request containing {substr!r} "
            f"(user={self.user_id})"
        )

    def close(self) -> None:
        """Close the page and its context. Safe to call more than once."""
        try:
            self.page.close()
        except Exception:  # noqa: BLE001 - already closed / crashed page
            pass
        try:
            self.context.close()
        except Exception:  # noqa: BLE001 - already closed context
            pass


class BrowserLab:
    """Factory for BrowserSession instances sharing one Playwright Browser
    and one NotifServer.

    One BrowserLab per test run; call new_session() once per logged-in
    browser context a scenario needs (e.g. twice for a "two devices, one
    user" scenario).
    """

    def __init__(self, server, browser) -> None:
        self.server = server
        self.browser = browser

    def new_session(
        self,
        user_id: str,
        *,
        recorder=None,
        subscribe_success: bool = True,
        extra_init: list | None = None,
    ) -> BrowserSession:
        """Create a new browser context + page logged in as `user_id`.

        - Creates a chat session via server.create_session(user_id) and adds
          it as the chat_session cookie (non-httpOnly, secure=False,
          sameSite=Lax), same as notif_badge_browser_check.py's
          add_session_cookie.
        - Always installs SPY_SCRIPT and SUPPRESS_FIRST_RUN_BANNER_SCRIPT.
        - If subscribe_success (default True), also installs
          push_subscribe_success_script() with a freshly generated fake push
          identity so the app's enable-notifications flow succeeds
          headlessly; the identity is exposed on the returned session as
          `.push_identity` (None when subscribe_success is False).
        - Any scripts in extra_init are added last, in order, so a scenario
          can override an earlier init script's effect (e.g. pass
          ALLOW_FIRST_RUN_BANNER_SCRIPT to un-suppress the first-run banner).
        - Wires WS frame capture and (push-related) network request capture,
          both feeding `recorder` when given.
        - Navigates to base_url + "/chat", waits for #messages and for
          routing to finish (page.evaluate("() => !_routing")).
        """
        token = self.server.create_session(user_id)

        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(SPY_SCRIPT)
        context.add_init_script(SUPPRESS_FIRST_RUN_BANNER_SCRIPT)

        push_identity: dict | None = None
        if subscribe_success:
            endpoint, p256dh_b64, auth_b64 = _gen_fake_push_identity()
            push_identity = {
                "endpoint": endpoint,
                "p256dh": p256dh_b64,
                "auth": auth_b64,
            }
            context.add_init_script(
                push_subscribe_success_script(endpoint, p256dh_b64, auth_b64)
            )

        for script in extra_init or []:
            context.add_init_script(script)

        context.add_cookies(
            [
                {
                    "name": "chat_session",
                    "value": token,
                    "url": self.server.base_url,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ]
        )

        ws_sent: list = []
        ws_received: list = []
        http_log: list = []
        context.on("request", _make_request_handler(recorder, http_log, user_id))

        page = context.new_page()
        page.on(
            "websocket",
            _make_websocket_handler(recorder, ws_sent, ws_received, user_id),
        )

        page.goto(self.server.base_url + "/chat", timeout=30000)
        page.wait_for_selector("#messages", timeout=20000)
        _wait_until(
            lambda: page.evaluate("() => !_routing"),
            timeout=15.0,
            interval=0.1,
            desc=f"routing complete (user={user_id})",
        )

        return BrowserSession(
            page=page,
            context=context,
            user_id=user_id,
            ws_sent=ws_sent,
            ws_received=ws_received,
            http_log=http_log,
            push_identity=push_identity,
        )


# --- Synchronous WS sender helper -------------------------------------------
# Scenarios that need a message sent "by another user" (badge fan-out, idle
# push) run in the synchronous Stage-2 world, where the async harness.WSClient
# cannot be awaited. This drives a chat WebSocket synchronously via
# websockets.sync.client, mirroring harness.WSClient.send_message's payload
# (event=send_message, type=text, content=json.dumps({"text": ...})).
def send_message_as(
    server, user_id: str, room_id: str, text: str, settle: float = 0.4
) -> None:
    """Connect as `user_id`, join `room_id`, send one text message, close.

    Synchronous (no asyncio), so it composes with sync Playwright scenarios.
    `settle` gives the server a moment to broadcast/push before disconnecting.
    """
    import uuid as _uuid

    from websockets.sync.client import connect as _sync_connect

    token = server.create_session(user_id)
    url = f"{server.ws_base}/ws/chat/{token}"
    with _sync_connect(
        url, additional_headers={"Cookie": f"chat_session={token}"}
    ) as ws:
        ws.send(json.dumps({"event": "join_room", "room_id": room_id}))
        time.sleep(0.2)
        ws.send(
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "text",
                    "content": json.dumps({"text": text}),
                    "temp_id": f"tmp-{_uuid.uuid4().hex}",
                }
            )
        )
        time.sleep(settle)
