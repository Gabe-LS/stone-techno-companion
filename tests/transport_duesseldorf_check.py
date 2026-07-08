#!/usr/bin/env python3
"""Standalone browser check for the Düsseldorf -> Essen transport view.

Loads /transport?route=duesseldorf and verifies it renders in the Zollverein
style: Düsseldorf title, RE/S line badges, the direction swap hidden (one-way
route), the Düsseldorf itinerary nav active, walk banner shown, day tabs, and
that the default /transport (Zollverein) still works. Deterministic (static
schedule for non-today dates). Run OUTSIDE the command sandbox.
"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/"tests")); import e2ee_browser_check as h
db=Path(tempfile.mkdtemp(prefix="dv_"))/"chat.db"; os.environ["CHAT_DB_PATH"]=str(db)
sys.path.insert(0,str(Path.cwd()/"server")); import chat_db; chat_db.init_chat_db(chat_db.get_chat_db())
port=h.get_free_port(); proc,base,_=h.start_server(db,port); SS=os.environ["SS"]; fails=[]
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True)
        # DUESSELDORF view
        pg=b.new_context(viewport={"width":420,"height":900},device_scale_factor=2).new_page()
        pg.goto(base+"/transport?route=duesseldorf",timeout=30000); pg.wait_for_selector(".dep-item",timeout=15000)
        m=pg.evaluate("""()=>{
          const title=document.getElementById('route-title-text').textContent.trim();
          const dests=[...document.querySelectorAll('.dep-dest')].slice(0,6).map(e=>e.textContent);
          const badges=[...document.querySelectorAll('.line-badge')].slice(0,6).map(b=>({t:b.textContent.trim(),cls:b.className,bg:getComputedStyle(b).backgroundColor}));
          const toggleDisp=getComputedStyle(document.getElementById('dir-toggle')).display;
          const navActive=[...document.querySelectorAll('[data-route]')].map(el=>({r:el.dataset.route,a:el.classList.contains('active')}));
          const bannerDisp=getComputedStyle(document.getElementById('location-banner')).display;
          const tabs=document.getElementById('day-tabs').childElementCount;
          const live=getComputedStyle(document.getElementById('live-indicator')).display;
          return {title,dests,badges,toggleDisp,navActive,bannerDisp,tabs,live};
        }""")
        print("DUES title:", repr(m["title"]))
        print("  dests:", m["dests"])
        print("  badges:", [(x['t'],x['cls'].replace('line-badge ',''),x['bg']) for x in m['badges']])
        print("  dir-toggle display:", m["toggleDisp"], "| banner:", m["bannerDisp"], "| tabs:", m["tabs"], "| live:", m["live"])
        print("  nav active:", m["navActive"])
        if "Düsseldorf" not in m["title"]: fails.append("title not Düsseldorf")
        if not any("RE" in x["t"] or "S" in x["t"] for x in m["badges"]): fails.append("no RE/S line badges")
        if m["toggleDisp"]!="none": fails.append("dir-toggle should be hidden on Düsseldorf")
        if not any(n["r"]=="duesseldorf" and n["a"] for n in m["navActive"]): fails.append("Düsseldorf nav not active")
        if any(n["r"]=="zollverein" and n["a"] for n in m["navActive"]): fails.append("Zollverein nav still active on Düsseldorf")
        if m["bannerDisp"]=="none": fails.append("walk banner hidden")
        if m["live"]!="none": fails.append("live indicator should be off (no realtime for Düsseldorf)")
        pg.locator(".sticky-top").screenshot(path=f"{SS}/dus_header.png")
        pg.locator("#day-panels").screenshot(path=f"{SS}/dus_board.png")
        # Regression: zollverein default still works
        pg2=b.new_context(viewport={"width":420,"height":900}).new_page()
        pg2.goto(base+"/transport",timeout=30000); pg2.wait_for_selector(".dep-item",timeout=15000)
        z=pg2.evaluate("""()=>({title:document.getElementById('route-title-text').textContent.trim(), toggle:getComputedStyle(document.getElementById('dir-toggle')).display, dests:[...document.querySelectorAll('.dep-dest')].slice(0,3).map(e=>e.textContent)})""")
        print("ZOLL default title:", repr(z["title"]), "| toggle:", z["toggle"], "| dests:", z["dests"])
        if "Zollverein" not in z["title"]: fails.append("zollverein default broken")
        if z["toggle"]=="none": fails.append("zollverein swap toggle missing")
        b.close()
finally: h.stop_server(proc)
print("\nRESULT:", "PASS" if not fails else "FAIL "+"; ".join(fails))
