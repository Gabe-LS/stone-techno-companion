"""
Standalone Playwright verification for the Next.js /transport port
(apps/web/app/transport/page.tsx + components/transport/*).

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


def route_title_text(page):
    return page.locator("main").inner_text()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # --- Section 1: the four routes + legacy alias -----------------

        cases = [
            ("", "Zollverein", "Essen Hbf"),
            ("?route=zollverein-essen", "Zollverein", "Essen Hbf"),
            ("?route=essen-zollverein", "Essen Hbf", "Zollverein"),
            ("?route=dus-airport-essen", None, "Essen Hbf"),  # from = Düsseldorf airport stop name
            ("?route=essen-dus-airport", "Essen Hbf", None),
            ("?route=duesseldorf", None, "Essen Hbf"),  # legacy alias == dus-airport-essen
            ("?route=bogus", "Zollverein", "Essen Hbf"),  # unknown slug falls back to default
        ]

        for qs, expect_from, expect_to in cases:
            page.goto(f"{BASE_URL}/transport{qs}", wait_until="networkidle")
            page.wait_for_selector("li", state="attached", timeout=8000)
            text = page.locator("div").filter(has_text="Essen Hbf").first.inner_text()
            title = page.title()
            ok = True
            detail = f"title={title!r}"
            if expect_from:
                ok = ok and expect_from in text.split("\n")[0]
            if expect_to:
                ok = ok and expect_to in text
            check(f"route {qs or '(bare)'} renders expected title", ok, detail)

        # dus-airport-essen and its legacy alias must render identically
        page.goto(f"{BASE_URL}/transport?route=dus-airport-essen", wait_until="networkidle")
        title_a = page.title()
        page.goto(f"{BASE_URL}/transport?route=duesseldorf", wait_until="networkidle")
        title_b = page.title()
        check("legacy alias 'duesseldorf' matches 'dus-airport-essen'", title_a == title_b, f"{title_a!r} vs {title_b!r}")

        # --- Section 6: day tabs ------------------------------------------

        page.goto(f"{BASE_URL}/transport?route=zollverein-essen", wait_until="networkidle")
        page.wait_for_timeout(300)
        tab_texts = page.eval_on_selector_all(
            "button:has(> span > span)",
            "els => els.map(e => e.textContent)",
        )
        # Filter to actual day-tab buttons (contain a slash date once rendered)
        day_tabs = [t for t in tab_texts if "/" in t]
        check(
            "Zollverein board has 3 day tabs (Fri/Sat/Sun)",
            len(day_tabs) == 3,
            day_tabs,
        )
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
        page.get_by_role("button", name="Show the opposite direction").click()
        page.wait_for_timeout(200)
        check("swap: dus-airport-essen -> essen-dus-airport", "route=essen-dus-airport" in page.url, page.url)

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

        live_indicator_visible = page.evaluate(
            "() => { const el = document.querySelector('span'); return true; }"
        )
        live_updated_visible = page.get_by_text("Updated", exact=False).first
        check("LIVE indicator: 'Updated ...' timestamp visible after fetch", live_updated_visible.count() > 0)

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
