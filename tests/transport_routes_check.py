#!/usr/bin/env python3
"""Browser check for the transport route slugs, slash dates, and compact tabs.

- The four ?route= slugs each load the right view/direction:
  zollverein-essen, essen-zollverein, duesseldorf-essen, essen-duesseldorf
  (and bare /transport defaults to zollverein-essen).
- Day-tab dates render with slashes (dd/mm/yyyy), never dots.
- Narrow viewports abbreviate the tabs ("Thu" / "09/07") without wrapping;
  wide viewports show the full day + year. Measured per-tab, so the 4-day
  airport board compacts on phones while the 3-day tram board keeps full text.
- The nav menu labels the airport itinerary "DUS Airport > Essen".
Run OUTSIDE the command sandbox (headless Chromium).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests"))
import e2ee_browser_check as h  # noqa: E402

db = Path(tempfile.mkdtemp(prefix="rt_")) / "chat.db"
os.environ["CHAT_DB_PATH"] = str(db)
sys.path.insert(0, str(Path.cwd() / "server"))
import chat_db  # noqa: E402

chat_db.init_chat_db(chat_db.get_chat_db())
port = h.get_free_port()
proc, base, _ = h.start_server(db, port)
fails = []
EXPECT = {
    "zollverein-essen": ("Zollverein", "Essen Hbf"),
    "essen-zollverein": ("Essen Hbf", "Zollverein"),
    "duesseldorf-essen": ("DUS Airport", "Essen Hbf"),
    "essen-duesseldorf": ("Essen Hbf", "DUS Airport"),
}
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        # Slug -> correct directional view + slash dates.
        for slug, (frm, to) in EXPECT.items():
            pg = b.new_context(viewport={"width": 430, "height": 900}).new_page()
            pg.goto(base + "/transport?route=" + slug, timeout=30000)
            pg.wait_for_selector(".dep-item", timeout=15000)
            m = pg.evaluate(
                """()=>({title:document.getElementById('route-title-text').textContent.replace(/\\s+/g,' ').trim(),
                  dates:[...document.querySelectorAll('.day-tab-count')].map(e=>e.textContent)})"""
            )
            title = m["title"]
            if not (frm in title and to in title and title.index(frm) < title.index(to)):
                fails.append(f"{slug}: title wrong ({title!r})")
            if any("." in d for d in m["dates"]):
                fails.append(f"{slug}: date uses dots")
            pg.close()

        pg = b.new_context().new_page()
        pg.goto(base + "/transport", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        dt = pg.evaluate(
            "()=>document.getElementById('route-title-text').textContent.replace(/\\s+/g,' ').trim()"
        )
        if "Zollverein" not in dt:
            fails.append(f"default /transport not zollverein-essen ({dt!r})")

        # Compact tabs: airport view (4 tabs) compacts on a phone; no wrapping.
        def probe(pg):
            return pg.evaluate(
                """()=>{const bar=document.getElementById('day-tabs');const t=bar.children[0];
                  const vis=e=>e&&getComputedStyle(e).display!=='none';
                  return {compact:bar.classList.contains('compact'),
                    wrap:t.scrollHeight>t.clientHeight+2,
                    day:[...t.querySelectorAll('span span')].filter(vis).map(e=>e.textContent.trim())};}"""
            )

        pg = b.new_context(viewport={"width": 360, "height": 800}).new_page()
        pg.goto(base + "/transport?route=duesseldorf-essen", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        pg.wait_for_timeout(150)
        narrow = probe(pg)
        if not narrow["compact"] or narrow["day"] != ["Thu", "09/07"]:
            fails.append(f"airport tabs not compact at 360px ({narrow})")
        if narrow["wrap"]:
            fails.append("compact tab still wraps")
        pg.close()

        pg = b.new_context(viewport={"width": 768, "height": 800}).new_page()
        pg.goto(base + "/transport?route=duesseldorf-essen", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        pg.wait_for_timeout(150)
        wide = probe(pg)
        if wide["compact"] or wide["day"] != ["Thursday", "09/07/2026"]:
            fails.append(f"airport tabs should be full at 768px ({wide})")

        # 3-day tram board never wraps at phone width.
        pg = b.new_context(viewport={"width": 360, "height": 800}).new_page()
        pg.goto(base + "/transport?route=essen-zollverein", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        pg.wait_for_timeout(150)
        tram = probe(pg)
        if tram["wrap"]:
            fails.append("tram tabs wrap at 360px")

        # Menu label.
        pg = b.new_context(viewport={"width": 1000, "height": 700}).new_page()
        pg.goto(base + "/transport?route=zollverein-essen", timeout=30000)
        pg.wait_for_selector(".dep-item", timeout=15000)
        labels = pg.evaluate(
            "()=>[...document.querySelectorAll('[data-route=\"duesseldorf\"]')].map(e=>e.textContent.trim())"
        )
        if not labels or not all("DUS Airport" in x for x in labels):
            fails.append(f"menu label not DUS Airport ({labels})")
        b.close()
finally:
    h.stop_server(proc)

print("=== transport routes / dates / tabs check ===")
if fails:
    print("RESULT: FAIL " + "; ".join(fails))
    sys.exit(1)
print("  4 route slugs, slash dates, adaptive compact tabs, DUS Airport menu label")
print("RESULT: PASS")
