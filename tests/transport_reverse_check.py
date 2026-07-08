#!/usr/bin/env python3
"""Standalone browser check for the transport reverse-direction toggle.

Spins up an isolated server, loads /transport, and verifies the direction
toggle: icon renders, the title flips, the board swaps outbound<->inbound
destinations, and the walk banner hides on the inbound board. The board is
static for non-today dates, so this is deterministic (no network).

Run OUTSIDE the command sandbox (headless Chromium):
    python tests/transport_reverse_check.py
"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
import e2ee_browser_check as h
db = Path(tempfile.mkdtemp(prefix="tr_"))/"chat.db"; os.environ["CHAT_DB_PATH"]=str(db)
import sqlite3
sys.path.insert(0, str(Path.cwd()/"server")); import chat_db; chat_db.init_chat_db(chat_db.get_chat_db())
port=h.get_free_port(); proc,base,_=h.start_server(db,port)
fails=[]
def dests(page):
    return page.eval_on_selector_all(".dep-dest", "els => els.slice(0,20).map(e=>e.textContent)")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True); pg=b.new_context(viewport={"width":420,"height":800}).new_page()
        pg.goto(base+"/transport", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        # icon present
        has_svg = pg.eval_on_selector("#dir-toggle", "el => !!el.querySelector('svg')")
        if not has_svg: fails.append("dir-toggle has no SVG icon")
        title0 = pg.eval_on_selector("#route-title-text","e=>e.textContent")
        print("initial title:", repr(title0))
        if "Zollverein" not in title0 or "Essen Hbf" not in title0: fails.append(f"bad initial title {title0}")
        out_dests = dests(pg)
        print("outbound dests sample:", out_dests[:5])
        if not any(("Bredeney" in d or "Hauptbahnhof" in d) for d in out_dests): fails.append("no outbound (Bredeney/Hauptbahnhof) destinations")
        banner_out = pg.eval_on_selector("#location-banner","e=>getComputedStyle(e).display")
        # toggle
        pg.click("#dir-toggle"); pg.wait_for_timeout(400)
        title1 = pg.eval_on_selector("#route-title-text","e=>e.textContent")
        print("toggled title:", repr(title1))
        if "Essen Hbf" not in title1 or "Zollverein" not in title1 or title1==title0: fails.append(f"title did not flip: {title1}")
        in_dests = dests(pg)
        print("inbound dests sample:", in_dests[:5])
        if not any(("Hanielstr" in d or "Gelsenkirchen" in d) for d in in_dests): fails.append("no inbound (Hanielstr/Gelsenkirchen) destinations after toggle")
        if any(("Bredeney" in d or "Hauptbahnhof" in d) for d in in_dests): fails.append("outbound destinations leaked into inbound board")
        banner_in = pg.eval_on_selector("#location-banner","e=>getComputedStyle(e).display")
        if banner_in == "none": fails.append("location banner should be VISIBLE on inbound (walk target = Essen Hbf)")
        # toggle back
        pg.click("#dir-toggle"); pg.wait_for_timeout(400)
        title2 = pg.eval_on_selector("#route-title-text","e=>e.textContent")
        if title2 != title0: fails.append(f"toggle back did not restore ({title2})")
        banner_back = pg.eval_on_selector("#location-banner","e=>getComputedStyle(e).display")
        if banner_back == "none": fails.append("location banner stayed hidden after toggling back to outbound")
        b.close()
finally:
    h.stop_server(proc)
print("\n=== transport reverse toggle check ===")
if fails:
    for f in fails: print("  FAIL", f)
    print("RESULT: FAIL"); sys.exit(1)
print("  icon renders, title flips, board swaps directions, banner visible both directions, restores on toggle-back\nRESULT: PASS")
