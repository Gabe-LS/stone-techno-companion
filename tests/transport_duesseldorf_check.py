#!/usr/bin/env python3
"""Standalone browser check for the Düsseldorf <-> Essen transport view.

Loads /transport?route=duesseldorf and verifies it renders in the Zollverein
style and is bidirectional: Düsseldorf title, RE/S line badges, the direction
swap shown, real train headsigns in the destination column (not a bare "Hbf"),
the Düsseldorf itinerary nav active, walk banner shown, day tabs; then toggles
to the reverse (Essen -> Düsseldorf Airport) and checks the title + reverse
headsigns; finally confirms the default /transport (Zollverein) still works.
Deterministic (static schedule for non-today dates). Run OUTSIDE the sandbox.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests"))
import e2ee_browser_check as h  # noqa: E402

db = Path(tempfile.mkdtemp(prefix="dus_")) / "chat.db"
os.environ["CHAT_DB_PATH"] = str(db)
sys.path.insert(0, str(Path.cwd() / "server"))
import chat_db  # noqa: E402

chat_db.init_chat_db(chat_db.get_chat_db())
port = h.get_free_port()
proc, base, _ = h.start_server(db, port)
fails = []
FWD_HEADSIGNS = ("Hamm", "Osnabrück", "Minden", "Münster", "Dortmund", "Bielefeld")
REV_HEADSIGNS = ("Köln", "Düsseldorf", "Solingen", "Flughafen Köln/Bonn")
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 420, "height": 900}).new_page()
        pg.goto(base + "/transport?route=duesseldorf", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        out = pg.evaluate(
            """()=>({
              title: document.getElementById('route-title-text').textContent.trim(),
              swap: getComputedStyle(document.getElementById('dir-toggle')).display,
              badges: [...document.querySelectorAll('.line-badge')].slice(0,6).map(b=>b.textContent.trim()),
              dests: [...document.querySelectorAll('.dep-dest')].slice(0,6).map(e=>e.textContent.trim()),
              navActive: [...document.querySelectorAll('[data-route]')].map(el=>({r:el.dataset.route,a:el.classList.contains('active')})),
              banner: getComputedStyle(document.getElementById('location-banner')).display,
              tabs: document.getElementById('day-tabs').childElementCount,
              live: getComputedStyle(document.getElementById('live-indicator')).display,
            })"""
        )
        if "Düsseldorf" not in out["title"]:
            fails.append("outbound title not Düsseldorf")
        if out["swap"] == "none":
            fails.append("direction swap hidden (Düsseldorf is bidirectional)")
        if not any(x.startswith("RE") or x.startswith("S") for x in out["badges"]):
            fails.append("no RE/S line badges")
        if any(d == "Hbf" for d in out["dests"]):
            fails.append("destination shows bare 'Hbf' instead of a headsign")
        if not any(d in FWD_HEADSIGNS for d in out["dests"]):
            fails.append("no real forward headsigns")
        if not any(n["r"] == "duesseldorf" and n["a"] for n in out["navActive"]):
            fails.append("Düsseldorf nav not active")
        if any(n["r"] == "zollverein" and n["a"] for n in out["navActive"]):
            fails.append("Zollverein nav still active on Düsseldorf")
        if out["banner"] == "none":
            fails.append("walk banner hidden")
        if out["tabs"] != 3:
            fails.append("expected 3 day tabs")
        if out["live"] != "none":
            fails.append("live indicator should be off (no realtime for Düsseldorf)")

        pg.click("#dir-toggle")
        pg.wait_for_timeout(400)
        rev = pg.evaluate(
            """()=>({
              title: document.getElementById('route-title-text').textContent.trim(),
              dests: [...document.querySelectorAll('.dep-dest')].slice(0,6).map(e=>e.textContent.trim()),
            })"""
        )
        if "Essen Hbf" not in rev["title"] or "Düsseldorf" not in rev["title"]:
            fails.append("inbound title wrong")
        if not any(d in REV_HEADSIGNS for d in rev["dests"]):
            fails.append("no reverse headsigns")

        pg2 = b.new_context(viewport={"width": 420, "height": 900}).new_page()
        pg2.goto(base + "/transport", timeout=30000)
        pg2.wait_for_selector(".dep-item", timeout=15000)
        z = pg2.evaluate(
            """()=>({
              title: document.getElementById('route-title-text').textContent.trim(),
              swap: getComputedStyle(document.getElementById('dir-toggle')).display,
            })"""
        )
        if "Zollverein" not in z["title"]:
            fails.append("Zollverein default broken")
        if z["swap"] == "none":
            fails.append("Zollverein swap toggle missing")
        b.close()
finally:
    h.stop_server(proc)

print("=== transport Düsseldorf view check ===")
if fails:
    print("RESULT: FAIL " + "; ".join(fails))
    sys.exit(1)
print("  bidirectional, real headsigns, RE/S badges, nav active, "
      "walk banner, day tabs; Zollverein default intact")
print("RESULT: PASS")
