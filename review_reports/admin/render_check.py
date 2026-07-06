"""Isolated render check for the admin panel (no server needed).

Fulfills the admin.html document, /shared.js, and every /chat/api/admin/* call via
Playwright route-mocking, then asserts role-gated tab rendering and the identity chip.
Verifies the riskiest new UI logic (Stage B/C) without touching the user's running server.
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
ADMIN_HTML = (ROOT / "server/chat/admin.html").read_text()
SHARED_JS = (ROOT / "server/static/shared.js").read_text()

STATS = {
    "total_users": 3,
    "online_count": 1,
    "reachable_count": 2,
    "pending_reports": 0,
    "active_bans": 0,
    "active_strikes": 0,
    "total_messages_active": 5,
    "total_rooms": 2,
}
SETTINGS = {
    "room_sort": "auto",
    "msg_char_limit": 1000,
    "dm_ttl_minutes": 1440,
    "room_ttl_minutes": 1440,
    "meetup_ttl_minutes": 60,
}


def run_for_role(role):
    me = {
        "role": role,
        "kind": "cookie",
        "label": "alice",
        "email_hash": "abc123def456",
    }
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def handle(route):
            url = route.request.url
            if "/shared.js" in url:
                return route.fulfill(
                    status=200, content_type="application/javascript", body=SHARED_JS
                )
            if "/chat/api/admin/stats" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body=_json(STATS)
                )
            if "/chat/api/admin/me" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body=_json(me)
                )
            if "/chat/api/admin/settings" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body=_json(SETTINGS)
                )
            if "/chat/api/admin/rooms" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body="[]"
                )
            if "/chat/api/admin/" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body="[]"
                )
            if url.rstrip("/").endswith("admin-panel"):
                return route.fulfill(
                    status=200, content_type="text/html", body=ADMIN_HTML
                )
            return route.fulfill(status=200, content_type="text/html", body=ADMIN_HTML)

        page.route("**/*", handle)
        errors = []
        page.on(
            "console", lambda m: errors.append(m.text) if m.type == "error" else None
        )
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("https://example.test/admin-panel")
        page.wait_for_selector(".tabs .tab", timeout=5000)
        tabs = [t.inner_text().strip() for t in page.query_selector_all(".tabs .tab")]
        chip = page.query_selector(".admin-me")
        chip_txt = chip.inner_text().strip() if chip else ""
        browser.close()
        return tabs, chip_txt, errors


def _json(obj):
    import json

    return json.dumps(obj)


def main():
    ok = True
    tabs, chip, errs = run_for_role("super_admin")
    print("super_admin tabs:", tabs)
    print("super_admin chip:", repr(chip))
    for t in (
        "Rooms",
        "Users",
        "Reports",
        "Banned",
        "Logs",
        "Audit",
        "Settings",
        "Admins",
    ):
        if t not in tabs:
            print(f"  FAIL: super-admin missing tab {t}")
            ok = False
    if "super-admin" not in chip.lower():
        print("  FAIL: identity chip missing super-admin role")
        ok = False
    if errs:
        print("  console errors (super):", errs[:5])
        ok = False

    tabs2, chip2, errs2 = run_for_role("admin")
    print("admin tabs:", tabs2)
    for t in ("Rooms", "Users", "Reports", "Banned", "Logs", "Audit"):
        if t not in tabs2:
            print(f"  FAIL: admin missing tab {t}")
            ok = False
    for t in ("Settings", "Admins"):
        if t in tabs2:
            print(f"  FAIL: admin should NOT see {t} tab")
            ok = False
    if errs2:
        print("  console errors (admin):", errs2[:5])
        ok = False

    print("RENDER CHECK:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
