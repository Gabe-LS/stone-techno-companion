"""
Standalone Playwright verification for the Next.js /transport port
(apps/web/app/transport/page.tsx + components/transport/*).

Covers the unified method-picker layout (docs/getting-there-design.md,
"Decision: unified method layout"): ONE top-level tab bar (Train | Plane |
Car | Bus | Local transit) replaces the old two-section page (live boards on
top, a separate collapsible "Getting there" section below). Train/Car/Bus
render curated rows; Plane renders curated rows with the Duesseldorf airport
row expanding inline into the live airport board; Local transit renders the
live tram board full-panel, unchanged in behavior.

Not part of the pytest suite (the Next.js app has no pytest integration);
run directly, same convention as tests/transport_*_check.py for the legacy
page. Must run OUTSIDE the command sandbox (headless Chromium needs Mach-port
access) -- see CLAUDE.md.

Prerequisites (both already running, this script does not start them):
  - the companion FastAPI backend on https://localhost:64728 (or set
    BACKEND_ORIGIN / STC_BACKEND_URL)
  - `next dev` for apps/web on a free port, with BACKEND_ORIGIN pointing at
    the backend above, and either a trusted TLS cert or
    NODE_EXTRA_CA_CERTS="$(mkcert -CAROOT)/rootCA.pem" set so the dev
    server's /timetable-transport.json and /api/transport/* rewrites can
    reach the backend over its mkcert-signed HTTPS cert.

The festival window (docs/getting-there-design.md smart-default rule) is
derived from timetable-transport.json's own day dates -- currently
08.07.2026 (the day before the first day present, 09.07.2026) through
12.07.2026 inclusive. The checks below use ?date=/&time= overrides to probe
both sides of that window deterministically, plus one live check against the
real wall clock (today, outside the window, so smart-default-picks-Train is
directly observable without any override).

Usage:
  STC_WEB_BASE_URL=http://localhost:3100 python tests/web/transport_nextjs_check.py
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("STC_WEB_BASE_URL", "http://localhost:3100")

PASS = []
FAIL = []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print(f"OK   {label}")
    else:
        FAIL.append(label)
        print(f"FAIL {label}  {detail}")


def active_tab_label(page):
    el = page.locator('[role="tab"][aria-selected="true"]')
    return el.first.inner_text().strip() if el.count() > 0 else None


def board_text(page):
    """Text of the live board's route-title span, if a board is mounted.

    Scoped to [class*="routeTitleMain"] rather than a generic "div with
    Essen Hbf" filter: in the unified layout, outer ancestor divs (the page
    wrapper, the panel) also contain "Essen Hbf" text (the h1 "Transport" or
    a curated Plane row's summary mentioning "Essen Hbf"), so a broad
    has_text filter's .first no longer reliably lands on the board's own
    title.
    """
    loc = page.locator('[class*="routeTitleMain"]')
    return loc.first.inner_text() if loc.count() > 0 else ""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # --- Section 1: route slugs select the right method tab -----------

        route_cases = [
            ("?route=zollverein-essen", "Local transit", "Zollverein", "Essen Hbf"),
            ("?route=essen-zollverein", "Local transit", "Essen Hbf", "Zollverein"),
            ("?route=dus-airport-essen", "Plane", None, "Essen Hbf"),
            ("?route=essen-dus-airport", "Plane", "Essen Hbf", None),
            ("?route=duesseldorf", "Plane", None, "Essen Hbf"),  # legacy alias == dus-airport-essen
        ]
        for qs, expect_tab, expect_from, expect_to in route_cases:
            page.goto(f"{BASE_URL}/transport{qs}", wait_until="networkidle")
            page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
            page.wait_for_timeout(200)
            tab = active_tab_label(page)
            check(f"route {qs} selects the {expect_tab} tab", tab == expect_tab, tab)
            text = board_text(page)
            ok = True
            if expect_from:
                ok = ok and expect_from in text.split("\n")[0]
            if expect_to:
                ok = ok and expect_to in text
            check(f"route {qs} renders the expected board title", ok, text)

        # dus-airport-essen and its legacy alias must render identically
        page.goto(f"{BASE_URL}/transport?route=dus-airport-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        title_a = page.title()
        page.goto(f"{BASE_URL}/transport?route=duesseldorf", wait_until="networkidle")
        page.wait_for_timeout(300)
        title_b = page.title()
        check("legacy alias 'duesseldorf' matches 'dus-airport-essen'", title_a == title_b, f"{title_a!r} vs {title_b!r}")

        # Plane tab's Duesseldorf row starts expanded when reached via a route slug
        page.goto(f"{BASE_URL}/transport?route=dus-airport-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        dus_toggle = page.get_by_role("button", name="Live departure board (Airport to Essen)")
        check(
            "route=dus-airport-essen: airport row is pre-expanded (aria-expanded=true)",
            dus_toggle.get_attribute("aria-expanded") == "true",
            dus_toggle.get_attribute("aria-expanded"),
        )
        check("route=dus-airport-essen: live board departure rows are visible", page.locator("li").count() > 0)

        # --- Smart default: outside the festival window (real wall clock) -

        page.goto(f"{BASE_URL}/transport", wait_until="networkidle")
        page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
        page.wait_for_timeout(300)
        tab = active_tab_label(page)
        check(
            "smart default: bare /transport today (outside the festival window) opens Train",
            tab == "Train",
            tab,
        )

        # An unrecognized ?route= is treated exactly like no param at all
        # (docs/parity/transport.md #26) -- it too falls through to the
        # smart default, not to a hardcoded board.
        page.goto(f"{BASE_URL}/transport?route=bogus", wait_until="networkidle")
        page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
        page.wait_for_timeout(300)
        tab = active_tab_label(page)
        check("smart default: unrecognized ?route= falls back like no param (opens Train today)", tab == "Train", tab)

        # --- Smart default: inside the festival window (date override) ----

        page.goto(f"{BASE_URL}/transport?date=10.07.2026&time=14:00", wait_until="networkidle")
        page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
        page.wait_for_timeout(300)
        tab = active_tab_label(page)
        check(
            "smart default: a festival-window date (10.07.2026) opens Local transit",
            tab == "Local transit",
            tab,
        )
        check("smart default in-window: live board is mounted", "Essen Hbf" in board_text(page))

        # --- ?method= is shareable, and reload restores it -----------------

        for method_id, expect_tab, expect_marker in [
            ("train", "Train", "Cologne"),
            ("car", "Car", "UNESCO Welterbe Zollverein"),
            ("bus", "Bus", "FlixBus"),
            ("local-transit", "Local transit", "Essen Hbf"),
        ]:
            page.goto(f"{BASE_URL}/transport?method={method_id}", wait_until="networkidle")
            page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
            page.wait_for_timeout(300)
            tab = active_tab_label(page)
            check(f"?method={method_id} selects the {expect_tab} tab", tab == expect_tab, tab)
            check(f"?method={method_id} panel shows expected content", expect_marker in page.locator("main").inner_text())

        # route slug wins over ?method= when both are present
        page.goto(f"{BASE_URL}/transport?route=zollverein-essen&method=train", wait_until="networkidle")
        page.wait_for_selector('[role="tab"][aria-selected="true"]', timeout=8000)
        page.wait_for_timeout(300)
        tab = active_tab_label(page)
        check("route slug wins over ?method= when both are present", tab == "Local transit", tab)

        # Clicking a tab writes ?method= (shareable), and reload restores it
        page.goto(f"{BASE_URL}/transport", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.get_by_role("tab", name="Bus", exact=True).click()
        page.wait_for_timeout(200)
        check("tab click writes ?method=bus to the URL", "method=bus" in page.url, page.url)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(300)
        check("reload restores the Bus tab from ?method=", active_tab_label(page) == "Bus")

        # Switching tabs shows exactly one panel at a time
        page.goto(f"{BASE_URL}/transport?method=train", wait_until="networkidle")
        page.wait_for_timeout(300)
        check("Train tab: Cologne row visible", "Cologne" in page.locator("main").inner_text())
        page.get_by_role("tab", name="Car", exact=True).click()
        page.wait_for_timeout(200)
        main_text = page.locator("main").inner_text()
        check("switching to Car tab hides the Train panel", "Cologne" not in main_text)
        check("Car tab shows its own content", "UNESCO Welterbe Zollverein" in main_text)

        # --- Plane tab: DUS row expands inline into the live board --------

        page.goto(f"{BASE_URL}/transport?method=plane", wait_until="networkidle")
        page.wait_for_timeout(300)
        dus_toggle = page.get_by_role("button", name="Live departure board (Airport to Essen)")
        check("Plane tab: DUS row starts collapsed", dus_toggle.get_attribute("aria-expanded") == "false")
        check("Plane tab: no live board rows before expanding", page.locator('[class*="depList"] li').count() == 0)
        check("Plane tab: CGN row still a coarse (non-expanding) row", "Cologne Bonn Airport" in page.locator("main").inner_text())

        dus_toggle.click()
        page.wait_for_timeout(300)
        dus_toggle = page.get_by_role("button", name="Live departure board (Airport to Essen)")
        check("Plane tab: DUS row expands on click (aria-expanded=true)", dus_toggle.get_attribute("aria-expanded") == "true")
        check("Plane tab: expanding DUS shows live board rows", page.locator('[class*="depList"] li').count() > 0)
        check("Plane tab: expanding DUS updates the URL to the route slug", "route=dus-airport-essen" in page.url, page.url)

        dus_toggle.click()
        page.wait_for_timeout(200)
        check("Plane tab: collapsing DUS removes the route slug, restores ?method=plane", "method=plane" in page.url and "route" not in page.url, page.url)

        # --- Section 6: day tabs (Local transit / Plane's embedded board) -

        page.goto(f"{BASE_URL}/transport?route=zollverein-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        tab_texts = page.eval_on_selector_all(
            "button:has(> span > span)",
            "els => els.map(e => e.textContent)",
        )
        day_tabs = [t for t in tab_texts if "/" in t]
        check("Zollverein board has 3 day tabs (Fri/Sat/Sun)", len(day_tabs) == 3, day_tabs)
        check(
            "day tab dates use slashes, never dots",
            all("." not in t.split("2026")[0][-3:] for t in day_tabs) and all("." not in t for t in day_tabs),
            day_tabs,
        )
        check(
            "day tabs include Friday/Saturday/Sunday",
            any("Friday" in t or "Fri" in t for t in day_tabs)
            and any("Saturday" in t or "Sat" in t for t in day_tabs)
            and any("Sunday" in t or "Sun" in t for t in day_tabs),
            day_tabs,
        )

        page.goto(f"{BASE_URL}/transport?route=dus-airport-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        tab_texts = page.eval_on_selector_all(
            "button:has(> span > span)",
            "els => els.map(e => e.textContent)",
        )
        day_tabs = [t for t in tab_texts if "/" in t]
        check("Duesseldorf board has 4 day tabs (Thu-Sun)", len(day_tabs) == 4, day_tabs)
        check("Duesseldorf board's first tab is Thursday", bool(day_tabs) and ("Thursday" in day_tabs[0] or "Thu" in day_tabs[0]), day_tabs)

        # --- Section 1: swap icon flips direction + rewrites the URL, no reload ---

        page.goto(f"{BASE_URL}/transport?route=zollverein-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.evaluate("window.__stc_test_marker = true")
        swap_btn = page.get_by_role("button", name="Show the opposite direction")
        swap_btn.click()
        page.wait_for_timeout(200)
        check("swap: URL becomes essen-zollverein", "route=essen-zollverein" in page.url, page.url)
        check("swap: no full reload (window marker survived)", page.evaluate("window.__stc_test_marker === true"))
        swap_btn.click()
        page.wait_for_timeout(200)
        check("swap: round-trips back to zollverein-essen", "route=zollverein-essen" in page.url, page.url)

        page.goto(f"{BASE_URL}/transport?route=dus-airport-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.get_by_role("button", name="Show the opposite direction").click()
        page.wait_for_timeout(200)
        check("swap: dus-airport-essen -> essen-dus-airport (embedded board)", "route=essen-dus-airport" in page.url, page.url)

        # --- Section 4/7: mocked realtime -- delay (red) + canceled (struck) + LIVE ---

        page.goto(f"{BASE_URL}/transport?route=zollverein-essen&date=10.07.2026&time=14:00", wait_until="networkidle")
        page.wait_for_selector("li", timeout=8000)

        def fulfill_departures(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"departures": ['
                    '{"line": "107", "direction": "Essen Bredeney", "scheduled": "14:09", '
                    '"scheduledDate": "10.07.2026", "realtime": true, "status": "CANCELED", "platform": "1"},'
                    '{"line": "107", "direction": "Essen Hauptbahnhof", "scheduled": "14:11", '
                    '"scheduledDate": "10.07.2026", "realtime": true, "real": "14:20", "delay": 9, "platform": "1"}'
                    '], "ts": "2026-07-10T12:00:00Z"}'
                ),
            )

        page.route("**/api/transport/departures**", fulfill_departures)
        # Trigger a fresh fetch deterministically instead of waiting up to 90s:
        # clicking the currently-active day tab re-triggers fetchRealtime().
        page.locator("button", has_text="Friday").first.click()
        page.wait_for_timeout(500)

        canceled_row = page.locator("li", has_text="14:09").first
        canceled_time = canceled_row.locator("span").first
        text_decoration = canceled_time.evaluate("el => getComputedStyle(el).textDecorationLine")
        check("canceled row: scheduled time is struck through", "line-through" in text_decoration, text_decoration)

        delayed_row = page.locator("li", has_text="14:20").first
        check("delayed row: shows the realtime (not scheduled) time", delayed_row.count() > 0)
        delayed_time_el = delayed_row.locator("span").first
        delayed_color = delayed_time_el.evaluate("el => getComputedStyle(el).color")
        check("delayed row: time text is red (--color-delay)", delayed_color == "rgb(185, 28, 28)", delayed_color)

        live_updated_visible = page.get_by_text("Updated", exact=False).first
        check("LIVE indicator: 'Updated ...' timestamp visible after fetch", live_updated_visible.count() > 0)

        # --- Train/Car/Bus/Plane curated content (docs/getting-there-design.md) ---

        page.goto(f"{BASE_URL}/transport?method=train", wait_until="networkidle")
        page.wait_for_timeout(300)

        first_item_name = page.locator("li", has_text="Direct regional express").first
        check("Train tab: Cologne row present (data file's own first row)", first_item_name.count() > 0)

        nsi_link = page.get_by_role("link", name="Book via NS International")
        check(
            "Train tab: Amsterdam row links to NS International with the expected href",
            nsi_link.get_attribute("href") == "https://www.nsinternational.com/en/germany/train-essen",
            nsi_link.get_attribute("href"),
        )
        check(
            "Train tab: external link opens in a new tab with rel=noopener noreferrer",
            nsi_link.get_attribute("target") == "_blank" and "noopener" in (nsi_link.get_attribute("rel") or ""),
            (nsi_link.get_attribute("target"), nsi_link.get_attribute("rel")),
        )

        # --- Language-based ordering boost (docs/getting-there-design.md #7) ---

        ctx_nl = browser.new_context(locale="nl-NL")
        page_nl = ctx_nl.new_page()
        page_nl.goto(f"{BASE_URL}/transport?method=train", wait_until="networkidle")
        page_nl.wait_for_timeout(400)
        first_row_nl = page_nl.locator('[class*="itemName"]').first
        check(
            "Getting there: nl-NL locale boosts Amsterdam to the top of the Train panel",
            first_row_nl.inner_text() == "Amsterdam",
            first_row_nl.inner_text(),
        )
        ctx_nl.close()

        ctx_us = browser.new_context(locale="en-US")
        page_us = ctx_us.new_page()
        page_us.goto(f"{BASE_URL}/transport?method=train", wait_until="networkidle")
        page_us.wait_for_timeout(400)
        first_row_us = page_us.locator('[class*="itemName"]').first
        check(
            "Getting there: en-US locale applies no boost (Cologne, the data file's own first row, stays first)",
            first_row_us.inner_text() == "Cologne",
            first_row_us.inner_text(),
        )
        ctx_us.close()

        browser.close()

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
