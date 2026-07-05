"""Smoke test / reference for the Stage-3 service-worker harness.

Headless Chromium cannot run the notification pipeline through a real
registered service worker: showNotification has no backend (so the push
handler's chain, which runs swlog/ack only after showNotification resolves,
never completes), real push delivery needs a live subscription, and
Playwright's service-worker Worker handles are terminated aggressively. So
Stage 3 runs the REAL sw.js source inside a controlled mock service-worker
environment (the standard way to unit-test a service worker) and dispatches
synthetic push/notificationclick/etc. events, asserting on every signal.

This smoke test proves the mechanism end to end. Run unsandboxed:
    python tests/notif_e2e/_smoke_sw.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from harness import NotifServer  # noqa: E402

# The mock service-worker environment. Loads the real sw.js with mocked self/
# fetch/caches/navigator, records every side effect, and exposes handlers so
# the harness can fire synthetic events. Stored on window.__sw so multiple
# events (e.g. tag-collapse tests) share one persistent notification store.
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
  window.__sw = { self, rec, notifStore };
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


def main() -> int:
    server = NotifServer()
    fails: list[str] = []
    pw = browser = None
    try:
        server.start()
        sw_src = httpx.get(server.base_url + "/sw.js").text
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(server.base_url + "/line-up", wait_until="domcontentloaded")
        # Install with an existing client so the click path exercises postMessage.
        page.evaluate(INSTALL_MOCK_SW, [sw_src, server.base_url + "/chat"])

        p1 = json.dumps(
            {
                "title": "#general",
                "body": "A: hi",
                "url": "/chat/msg/m1",
                "room_id": "general",
                "push_id": "p1",
                "total_unread": 3,
                "count": 1,
            }
        )
        rec = page.evaluate(FIRE_PUSH, p1)
        print("[swsmoke] shown:", rec["shown"])
        print(
            "[swsmoke] fetches:",
            [
                (f["url"], f["body"].get("step") or f["body"].get("action"))
                for f in rec["fetches"]
            ],
        )
        print("[swsmoke] badge:", rec["badge"])

        if len(rec["shown"]) != 1:
            fails.append(f"expected 1 notification, got {len(rec['shown'])}")
        elif rec["shown"][0]["tag"] != "stc-general-p1":
            fails.append(f"tag != stc-general-p1: {rec['shown'][0]['tag']}")
        steps = [f["body"].get("step") for f in rec["fetches"] if "swlog" in f["url"]]
        acks = [
            f["body"].get("action") for f in rec["fetches"] if "push/ack" in f["url"]
        ]
        if "push-received" not in steps:
            fails.append(f"no swlog push-received: {steps}")
        if "delivered" not in acks:
            fails.append(f"no ack delivered: {acks}")
        if 3 not in rec["badge"]:
            fails.append(f"setAppBadge(3) not called: {rec['badge']}")

        # Click the notification -> cache write + postMessage navigate + click ack.
        rec2 = page.evaluate(FIRE_CLICK)
        if "/_push_navigate" not in rec2["cachePuts"]:
            fails.append(f"click did not cache /_push_navigate: {rec2['cachePuts']}")
        nav = [m for m in rec2["postMessages"] if m.get("type") == "navigate"]
        if not nav:
            fails.append(f"click did not postMessage navigate: {rec2['postMessages']}")
        if "clicked" not in [
            f["body"].get("action") for f in rec2["fetches"] if "push/ack" in f["url"]
        ]:
            fails.append("click did not ack 'clicked'")
        print(
            "[swsmoke] click cachePuts:",
            rec2["cachePuts"],
            "postMessages:",
            rec2["postMessages"],
        )
    except Exception:
        fails.append("exception: " + traceback.format_exc())
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        server.stop()

    if fails:
        print("\n[swsmoke] FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print(
        "\n[swsmoke] PASS - real sw.js runs in the mock env; push + click signals observed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
