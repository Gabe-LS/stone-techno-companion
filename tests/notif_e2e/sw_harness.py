"""Stage 3 mock service-worker harness (real Chromium, synchronous).

Loads the REAL server/static/sw.js source into a mock service-worker
environment (self/fetch/caches/navigator all stubbed with recording mocks)
and fires synthetic push/notificationclick/notificationclose/
pushsubscriptionchange events at it, so the actual push/click/close/resub
handler code runs deterministically -- no real registered service worker,
no real OS notification, no real push transport. See _smoke_sw.py (the
proven reference this module is built on) and CONTRACT_STAGE3.md for why a
mock SW is required instead of a real one.

INSTALL_MOCK_SW, FIRE_PUSH, and FIRE_CLICK below are reused verbatim from
_smoke_sw.py. FIRE_CLOSE and FIRE_SUBCHANGE are new, mirroring FIRE_CLICK's
shape for the two handlers _smoke_sw.py did not exercise.

Synchronous only (Playwright sync API) -- the SW scenarios never touch
asyncio. No new dependencies.
"""

from __future__ import annotations

import json

# --- mock SW install + event-firing scripts ---------------------------------
# Reused verbatim from tests/notif_e2e/_smoke_sw.py (the validated reference).

INSTALL_MOCK_SW = r"""
([swSrc, matchClient]) => {
  const rec = { shown: [], closed: [], fetches: [], badge: [],
                cachePuts: [], postMessages: [], openWindows: [], clientsMatched: 0 };
  const notifStore = [];
  function FakeNotif(title, options) {
    const n = { title, body: options.body, tag: options.tag, data: options.data,
                silent: options.silent,
                close(){ rec.closed.push(this.tag);
                  const i = notifStore.indexOf(this); if (i >= 0) notifStore.splice(i, 1); } };
    return n;
  }
  const fakeClients = matchClient
    ? [{ url: matchClient, visibilityState: 'visible', focused: false,
         focus(){ this.focused = true; },
         postMessage(m){ rec.postMessages.push(m); } }]
    : [];
  const self = {
    _handlers: {},
    addEventListener(type, fn){ (self._handlers[type] = self._handlers[type] || []).push(fn); },
    skipWaiting(){},
    // Real SW origin == the page origin (the page is loaded at the server
    // base_url), so notificationclick's `new URL(url, self.location.origin)`
    // resolves against the actual server, matching what production does.
    location: { origin: location.origin },
    registration: {
      showNotification(title, options){ const n = FakeNotif(title, options); notifStore.push(n);
        rec.shown.push({ title, body: options.body, tag: options.tag, data: options.data,
                         silent: options.silent }); return Promise.resolve(); },
      getNotifications(){ return Promise.resolve(notifStore.slice()); },
      pushManager: { getSubscription(){ return Promise.resolve(
        { endpoint: 'https://fcm.googleapis.com/fcm/send/existing',
          options: { userVisibleOnly: true, applicationServerKey: new Uint8Array([9]).buffer },
          getKey(name){ return new Uint8Array(name === 'p256dh' ? [1,2,3] : [4,5]).buffer; } }); },
        subscribe(opts){ return Promise.resolve(
        { endpoint: 'https://fcm.googleapis.com/fcm/send/rotated', options: opts,
          getKey(name){ return new Uint8Array(name === 'p256dh' ? [7,8,9] : [6,5]).buffer; } }); } },
    },
    clients: { matchAll(){ rec.clientsMatched++; return Promise.resolve(fakeClients); },
               claim(){ return Promise.resolve(); },
               openWindow(u){ rec.openWindows.push(u); return Promise.resolve({ url: u }); } },
  };
  const fetchMock = (url, opts) => { rec.fetches.push({ url,
      body: opts && opts.body ? JSON.parse(opts.body) : null }); return Promise.resolve({ ok: true }); };
  const cachesMock = { open(){ return Promise.resolve({
      put(key, resp){ rec.cachePuts.push(String(key)); return Promise.resolve(); } }); } };
  const navigatorMock = { setAppBadge(n){ rec.badge.push(n); return Promise.resolve(); },
                          clearAppBadge(){ rec.badge.push(null); return Promise.resolve(); } };
  new Function('self', 'fetch', 'caches', 'navigator', swSrc)(self, fetchMock, cachesMock, navigatorMock);
  window.__sw = { self, rec, notifStore, fakeClients };
  return true;
}
"""

FIRE_PUSH = r"""
(payload) => {
  const { self } = window.__sw;
  const waits = [];
  const evt = { data: { text: () => payload, json: () => JSON.parse(payload) },
                waitUntil: (p) => waits.push(p) };
  (self._handlers['push'] || []).forEach(h => h(evt));
  return Promise.all(waits).then(() => window.__sw.rec);
}
"""

FIRE_CLICK = r"""
() => {
  const { self, notifStore } = window.__sw;
  const n = notifStore[notifStore.length - 1];
  const waits = [];
  const evt = { notification: n, preventDefault(){}, waitUntil: (p) => waits.push(p) };
  (self._handlers['notificationclick'] || []).forEach(h => h(evt));
  return Promise.all(waits).then(() => window.__sw.rec);
}
"""

# New for Stage 3, mirroring FIRE_CLICK's shape for the two handlers
# _smoke_sw.py did not exercise.

FIRE_CLOSE = r"""
() => {
  const { self, notifStore } = window.__sw;
  const n = notifStore[notifStore.length - 1];
  const waits = [];
  const evt = { notification: n, waitUntil: (p) => waits.push(p) };
  (self._handlers['notificationclose'] || []).forEach(h => h(evt));
  return Promise.all(waits).then(() => window.__sw.rec);
}
"""

FIRE_SUBCHANGE = r"""
(oldOptions) => {
  const { self } = window.__sw;
  const waits = [];
  const evt = { oldSubscription: { options: oldOptions || {} }, waitUntil: (p) => waits.push(p) };
  (self._handlers['pushsubscriptionchange'] || []).forEach(h => h(evt));
  return Promise.all(waits).then(() => window.__sw.rec);
}
"""

# Reads the fake client(s)' live state (e.g. .focused, mutated in place by the
# notificationclick handler's list[0].focus() call) straight out of the mock
# env's closure -- INSTALL_MOCK_SW's fakeClients array is not itself part of
# `rec`, so this is a separate read rather than a change to that verbatim
# script.
CLIENT_STATES = r"""
() => window.__sw.fakeClients.map(c => ({ url: c.url, focused: c.focused, visibilityState: c.visibilityState }))
"""

# rec array keys that accumulate signals over the harness's lifetime -> the
# "sw:<kind>" prefix recorded for each, per CONTRACT_STAGE3. "fetches" is
# handled separately (its kind embeds the fetched path).
_DELTA_KEYS = {
    "shown": "sw:shown",
    "closed": "sw:closed",
    "badge": "sw:badge",
    "cachePuts": "sw:cachePut",
    "postMessages": "sw:postMessage",
    "openWindows": "sw:openWindow",
}


class SWHarness:
    """One page running the real sw.js in a persistent mock SW env.

    Persistent (one page, one mock SW install, reused across calls) so
    tag-collapse tests -- multiple pushes sharing one notification store --
    work, matching CONTRACT_STAGE3.
    """

    def __init__(self, page, recorder=None) -> None:
        self.page = page
        self.recorder = recorder
        # Cursor per rec array so repeated calls only record NEW entries into
        # the SignalRecorder -- the JS-side rec arrays are cumulative across
        # every push/click/close/subchange fired on this harness.
        self._seen: dict[str, int] = {k: 0 for k in _DELTA_KEYS}
        self._seen["fetches"] = 0

    def _record_delta(self, rec: dict) -> None:
        if self.recorder is None:
            return
        for key, kind in _DELTA_KEYS.items():
            items = rec.get(key, [])
            start = self._seen[key]
            for item in items[start:]:
                data = item if isinstance(item, dict) else {"value": item}
                self.recorder.record("sw", kind, data)
            self._seen[key] = len(items)
        fetches = rec.get("fetches", [])
        start = self._seen["fetches"]
        for f in fetches[start:]:
            self.recorder.record("sw", f"sw:fetch:{f.get('url', '')}", f)
        self._seen["fetches"] = len(fetches)

    def push(self, payload: dict) -> dict:
        """Fire the push handler with json.dumps(payload); return the
        recorder snapshot (shown/closed/fetches/badge/cachePuts/
        postMessages/openWindows/clientsMatched)."""
        rec = self.page.evaluate(FIRE_PUSH, json.dumps(payload))
        self._record_delta(rec)
        return rec

    def click_last(self) -> dict:
        """Fire notificationclick on the most-recently shown notification."""
        rec = self.page.evaluate(FIRE_CLICK)
        self._record_delta(rec)
        return rec

    def close_last(self) -> dict:
        """Fire notificationclose on the most-recently shown notification."""
        rec = self.page.evaluate(FIRE_CLOSE)
        self._record_delta(rec)
        return rec

    def subscription_change(self, old_options: dict | None = None) -> dict:
        """Fire pushsubscriptionchange with an oldSubscription carrying
        .options (sw.js reads event.oldSubscription.options to re-subscribe
        with the same params)."""
        rec = self.page.evaluate(FIRE_SUBCHANGE, old_options or {})
        self._record_delta(rec)
        return rec

    def notifications(self) -> list:
        """Current notifStore contents (still-visible notifications), as
        plain dicts -- not the raw JS objects, which carry a close()
        function evaluate() cannot serialize."""
        return self.page.evaluate(
            "() => window.__sw.notifStore.map(n => "
            "({title: n.title, body: n.body, tag: n.tag, data: n.data, silent: n.silent}))"
        )

    def client_states(self) -> list:
        """Live state of the mock window client(s) installed via
        match_client -- in particular .focused, mutated by the
        notificationclick handler's list[0].focus() call."""
        return self.page.evaluate(CLIENT_STATES)

    def signals(self) -> dict:
        """The full recorder object as it currently stands."""
        return self.page.evaluate("() => window.__sw.rec")

    def close(self) -> None:
        try:
            self.page.close()
        except Exception:
            pass


class SWLab:
    """Builds fresh SWHarness instances against one shared server + browser."""

    def __init__(self, server, browser, sw_src: str) -> None:
        self.server = server
        self.browser = browser
        self.sw_src = sw_src

    def new_harness(
        self, *, match_client: str | None = None, recorder=None
    ) -> SWHarness:
        """New page, navigate to /line-up, install the mock SW loaded with
        the real sw.js source. match_client, if given, seeds one fake window
        client at that URL so the click path exercises the
        postMessage-to-existing-client branch; None exercises openWindow."""
        page = self.browser.new_page()
        page.goto(self.server.base_url + "/line-up", wait_until="domcontentloaded")
        page.evaluate(INSTALL_MOCK_SW, [self.sw_src, match_client])
        return SWHarness(page, recorder=recorder)
