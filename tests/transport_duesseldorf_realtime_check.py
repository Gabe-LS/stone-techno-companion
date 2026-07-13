#!/usr/bin/env python3
"""Browser check for the DUS Airport realtime overlay + row extras.

Part A (static): DUS Airport title, the Thursday day tab (airport-only),
the arrival time on every row, and the platform sub-line.
Part B (realtime): forces a festival "now" (?date=&time=) and mocks the
/api/transport/departures response with a delayed entry, then asserts the
matching row shows the struck+red delayed departure, the live train number
and platform, the delayed arrival, the realtime dot, and the LIVE badge.
Run OUTSIDE the command sandbox (headless Chromium).
"""
import os, sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/"tests")); import e2ee_browser_check as h
db=Path(tempfile.mkdtemp(prefix="rt_"))/"chat.db"; os.environ["CHAT_DB_PATH"]=str(db)
sys.path.insert(0,str(Path.cwd()/"services"/"companion")); import chat_db; chat_db.init_chat_db(chat_db.get_chat_db())
# pick a target Friday DUS departure >= 14:00
tt=json.load(open("services/companion/static/timetable-transport.json"))
fri=[d for d in tt["duesseldorf"]["days"] if d["day"]=="Friday"][0]
tgt=next(x for x in fri["departures"] if x["dep"]>="14:00")
def plus(hhmm,m):
    H,M=map(int,hhmm.split(":")); M+=m; H+=M//60; M%=60; return f"{H%24:02d}:{M:02d}"
mock={"departures":[{"line":tgt["line"],"scheduled":tgt["dep"],"scheduledDate":"10.07.2026",
  "realtime":True,"real":plus(tgt["dep"],7),"delay":7,"platform":"11","trainNumber":"99999",
  "arr":tgt["arr"],"arrReal":plus(tgt["arr"],7),"arrDelay":7}],"ts":"2026-07-10T12:00:00Z"}
print(f"target: {tgt['dep']} {tgt['line']} -> arr {tgt['arr']}  (mock delay +7, #99999)")
port=h.get_free_port(); proc,base,_=h.start_server(db,port); SS=os.environ.get("SS") or tempfile.mkdtemp(); fails=[]
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True)
        # PART A: static rendering
        pg=b.new_context(viewport={"width":430,"height":950},device_scale_factor=2).new_page()
        pg.goto(base+"/transport?route=duesseldorf",timeout=30000); pg.wait_for_selector(".dep-item",timeout=15000)
        A=pg.evaluate("""()=>({title:document.getElementById('route-title-text').textContent.trim(),
          tabs:[...document.querySelectorAll('.day-tab')].map(t=>t.textContent.replace(/\\s+/g,' ').trim().split(' ')[0]),
          arr:[...document.querySelectorAll('.dep-arr')].length, sub:[...document.querySelectorAll('.dep-sub')].slice(0,3).map(e=>e.textContent.trim())})""")
        print("A title:",repr(A["title"]),"| tabs:",A["tabs"],"| arr els:",A["arr"],"| sub:",A["sub"])
        if "DUS Airport" not in A["title"]: fails.append("title not DUS Airport")
        if not A["tabs"][0].startswith("Thursday") or len(A["tabs"])!=4: fails.append("Thursday tab missing/order")
        if A["arr"]==0: fails.append("no arrival times on rows")
        if not any("Pl." in s for s in A["sub"]): fails.append("no platform sub-line")
        pg.locator("#day-panels").screenshot(path=f"{SS}/dus_rt_static.png")
        # PART B: realtime overlay (mock endpoint + forced festival 'now')
        ctx=b.new_context(viewport={"width":430,"height":950},device_scale_factor=2)
        ctx.route("**/api/transport/departures*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(mock)))
        pg2=ctx.new_page()
        pg2.goto(base+"/transport?route=duesseldorf&date=10.07.2026&time=14:00",timeout=30000)
        pg2.wait_for_selector(".dep-item",timeout=15000); pg2.wait_for_timeout(800)
        # The shipped markup has one .dep-time span per row: on a delay it gets
        # the "delayed" class and its text is replaced by the real (live) time,
        # so the matching row is found by that real time, not the scheduled one.
        real_dep=plus(tgt["dep"],7)
        B=pg2.evaluate("""(real)=>{
          const rows=[...document.querySelectorAll('.dep-item')];
          const row=rows.find(r=>{const t=r.querySelector('.dep-time.delayed');return t && t.textContent.trim()===real;});
          if(!row) return {found:false};
          return {found:true, real:(row.querySelector('.dep-time.delayed')||{}).textContent,
            sub:(row.querySelector('.dep-sub')||{}).textContent,
            arrReal:(row.querySelector('.dep-arr.delayed')||{}).textContent,
            dot:!!row.querySelector('.rt-dot.red'),
            live:getComputedStyle(document.getElementById('live-indicator')).visibility};
        }""", real_dep)
        print("B overlay:",B)
        if not B.get("found"): fails.append("delayed row not rendered (no delayed departure time)")
        else:
            if not B.get("real"): fails.append("no real (delayed) departure time")
            if "#99999" not in (B.get("sub") or ""): fails.append("train number not shown")
            if plus(tgt["arr"],7) not in (B.get("arrReal") or ""): fails.append("delayed arrival not shown")
            if not B.get("dot"): fails.append("no realtime dot")
            if B.get("live") != "visible": fails.append("live indicator not shown")
        pg2.locator("#day-panels").screenshot(path=f"{SS}/dus_rt_live.png")
        b.close()
finally: h.stop_server(proc)
print("RESULT:", "PASS" if not fails else "FAIL "+"; ".join(fails))
