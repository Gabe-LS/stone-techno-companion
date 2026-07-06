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
MOCK_USER = {
    "id": "u-1",
    "display_name": "Bob",
    "username": "bob",
    "country": "IT",
    "providers": ["email"],
    "last_seen": None,
    "last_active": None,
    "is_online": False,
    "has_push": False,
    "strike_count": 0,
    "is_banned": False,
    "muted_until": None,
    "mute_count": 0,
}
MOCK_ROOM = {
    "id": "party",
    "name": "Party",
    "type": "general",
    "description": "",
    "is_main": False,
    "is_moderated": True,
    "is_read_only": False,
    "auto_join": False,
    "allows_media": True,
    "ttl_minutes": 1440,
    "online_count": 0,
    "member_count": 2,
    "message_count": 3,
    "last_message_at": None,
}
MOCK_AUDIT = {
    "id": "a-1",
    "actor": "token",
    "action": "set_main",
    "target_user_id": None,
    "target_name": None,
    "target_room_id": "party",
    "target_room_name": "Party",
    "detail": None,
    "created_at": "2026-07-06T10:00:00+00:00",
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
            if "/chat/api/admin/users" in url:
                return route.fulfill(
                    status=200, content_type="application/json", body=_json([MOCK_USER])
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


def check_persistence_and_menu():
    """Verify the last tab is restored on reload, and the Users '...' menu is grouped."""
    me = {"role": "super_admin", "kind": "cookie", "label": "alice", "email_hash": "abc"}
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def handle(route):
            url = route.request.url
            if "/shared.js" in url:
                return route.fulfill(status=200, content_type="application/javascript", body=SHARED_JS)
            if "/chat/api/admin/stats" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json(STATS))
            if "/chat/api/admin/me" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json(me))
            if "/chat/api/admin/settings" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json(SETTINGS))
            if "/chat/api/admin/rooms" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json([MOCK_ROOM]))
            if "/chat/api/admin/users" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json([MOCK_USER]))
            if "/chat/api/admin/audit" in url:
                return route.fulfill(status=200, content_type="application/json", body=_json([MOCK_AUDIT]))
            if "/chat/api/admin/" in url:
                return route.fulfill(status=200, content_type="application/json", body="[]")
            return route.fulfill(status=200, content_type="text/html", body=ADMIN_HTML)

        page.route("**/*", handle)
        page.goto("https://example.test/admin-panel")
        page.wait_for_selector(".tabs .tab", timeout=5000)
        # switch to Users, which should persist to localStorage
        page.click('.tab[data-tab="users"]')
        page.wait_for_selector("#user-tbody", timeout=5000)
        # reload: the active tab should be restored to Users, not Rooms
        page.goto("https://example.test/admin-panel")
        page.wait_for_selector(".tab.active", timeout=5000)
        active = page.query_selector(".tab.active")
        active_tab = active.get_attribute("data-tab") if active else None
        if active_tab != "users":
            problems.append(f"tab not restored on reload (active={active_tab})")
        # the Users menu should render grouped actions for a super-admin
        # (the menu is display:none until opened, so wait for it attached, not visible)
        page.wait_for_selector(".dropdown-menu", state="attached", timeout=5000)
        menu = page.query_selector(".dropdown-menu").inner_html()
        for label in ("Strike", "Mute 30 min", "Ban", "Delete user"):
            if label not in menu:
                problems.append(f"menu missing '{label}'")
        seps = menu.count("dropdown-sep")
        if seps < 2:
            problems.append(f"menu not grouped (only {seps} separators)")

        # Rooms actions should now be a "..." kebab menu with Edit / Messages.
        page.click('.tab[data-tab="rooms"]')
        page.wait_for_selector("#rooms-tbody", timeout=5000)
        room_kebab = page.query_selector("#rooms-tbody .col-actions .kebab")
        if not room_kebab:
            problems.append("rooms actions not a kebab menu")
        rmenu_el = page.query_selector("#rooms-tbody .col-actions .dropdown-menu")
        rmenu = rmenu_el.inner_html() if rmenu_el else ""
        for label in ("Edit", "Messages", "Delete room"):
            if label not in rmenu:
                problems.append(f"rooms menu missing '{label}'")

        # Filter persistence: toggle Users online-only, reload, expect it to stick.
        page.click('.tab[data-tab="users"]')
        page.wait_for_selector(".toggle", timeout=5000)
        page.click(".toggle")  # turn online-only on
        page.wait_for_selector(".toggle.on", timeout=5000)
        page.goto("https://example.test/admin-panel")
        page.wait_for_selector(".tab.active", timeout=5000)
        stored = page.evaluate("() => localStorage.getItem('admin_users_online')")
        if stored != "true":
            problems.append(f"online-only toggle not persisted (stored={stored})")
        toggle = page.query_selector(".toggle")
        if toggle and "on" not in (toggle.get_attribute("class") or ""):
            problems.append("online-only toggle not restored as active")

        # Audit entries must be humanized and never show a blank description.
        page.click('.tab[data-tab="audit"]')
        page.wait_for_selector("#audit-list table", timeout=5000)
        audit_html = page.query_selector("#audit-list table").inner_html()
        if "Set main room" not in audit_html:
            problems.append("audit action not humanized (missing 'Set main room')")
        if "Party" not in audit_html:
            problems.append("audit description empty (room name 'Party' missing)")
        # confirm no row renders a bare '--' description for this entry
        first_row = page.query_selector("#audit-list tbody tr")
        cells = [c.inner_text().strip() for c in first_row.query_selector_all("td")] if first_row else []
        if len(cells) >= 3 and cells[2] in ("", "--"):
            problems.append(f"audit description blank (cells={cells})")
        browser.close()
    return problems


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

    pm = check_persistence_and_menu()
    print("persistence + menu:", "OK" if not pm else pm)
    if pm:
        ok = False

    print("RENDER CHECK:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
