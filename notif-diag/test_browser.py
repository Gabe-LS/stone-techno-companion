"""
Automated browser notification tests via Playwright + Chromium.
Requires the notif-diag server running on port 9444.

Usage:
    cd notif-diag && python test_browser.py
"""

import json
import time
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://localhost:9444"
LOG_DIR = Path(__file__).resolve().parent / "logs"
RESULTS = []


def log_result(test_id, status, detail):
    RESULTS.append({"test": test_id, "status": status, "detail": detail})
    icon = {"pass": "+", "fail": "!", "info": " ", "warn": "?"}[status]
    print(f"  [{icon}] {test_id}: {detail}")


def get_server_logs(session_filter=None):
    entries = []
    latest = LOG_DIR / "_latest.jsonl"
    if not latest.exists():
        return entries
    for line in latest.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            e = json.loads(line)
            if session_filter and e.get("session") != session_filter:
                continue
            entries.append(e)
        except json.JSONDecodeError:
            pass
    return entries


def find_log_events(entries, ev_name, src=None):
    results = []
    for e in entries:
        if e.get("ev") == ev_name:
            if src and e.get("src") != src:
                continue
            detail = e.get("detail", "")
            try:
                detail = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                pass
            results.append(detail)
    return results


def run_tests(engine="chromium"):
    # clear logs
    for f in LOG_DIR.glob("*.jsonl"):
        f.unlink()

    with sync_playwright() as p:
        if engine == "webkit":
            browser = p.webkit.launch(headless=False)
        else:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--ignore-certificate-errors",
                    "--disable-web-security",
                    "--enable-features=NotificationTriggers",
                    "--unsafely-treat-insecure-origin-as-secure=https://localhost:9444",
                    "--allow-insecure-localhost",
                ],
            )
        context = browser.new_context(
            ignore_https_errors=True,
            permissions=["notifications"],
            service_workers="allow",
        )
        page = context.new_page()
        page.goto(BASE, wait_until="networkidle")
        time.sleep(2)

        # get session ID from page
        session = page.evaluate("_session")
        platform = page.evaluate("_platform")
        print(f"\nSession: {session}")
        print(f"Platform: {platform}")
        print(f"{'=' * 60}")

        # --- Test 2: Feature Detection ---
        print("\n## Feature Detection")
        page.click("text=Detect all features")
        time.sleep(1)
        features = page.evaluate("""() => {
            var r = {};
            r['Notification'] = typeof Notification !== 'undefined';
            r['permission'] = typeof Notification !== 'undefined' ? Notification.permission : 'N/A';
            r['showNotification'] = typeof ServiceWorkerRegistration !== 'undefined' && 'showNotification' in ServiceWorkerRegistration.prototype;
            r['getNotifications'] = typeof ServiceWorkerRegistration !== 'undefined' && 'getNotifications' in ServiceWorkerRegistration.prototype;
            r['maxActions'] = typeof Notification !== 'undefined' ? Notification.maxActions : 'N/A';
            r['actions'] = typeof Notification !== 'undefined' && 'actions' in Notification.prototype;
            r['silent'] = typeof Notification !== 'undefined' && 'silent' in Notification.prototype;
            r['renotify'] = typeof Notification !== 'undefined' && 'renotify' in Notification.prototype;
            r['requireInteraction'] = typeof Notification !== 'undefined' && 'requireInteraction' in Notification.prototype;
            r['badge'] = typeof Notification !== 'undefined' && 'badge' in Notification.prototype;
            r['image'] = typeof Notification !== 'undefined' && 'image' in Notification.prototype;
            r['vibrate'] = typeof Notification !== 'undefined' && 'vibrate' in Notification.prototype;
            r['data'] = typeof Notification !== 'undefined' && 'data' in Notification.prototype;
            r['timestamp'] = typeof Notification !== 'undefined' && 'timestamp' in Notification.prototype;
            r['tag'] = typeof Notification !== 'undefined' && 'tag' in Notification.prototype;
            r['setAppBadge'] = 'setAppBadge' in navigator;
            r['clearAppBadge'] = 'clearAppBadge' in navigator;
            return r;
        }""")
        for k, v in features.items():
            log_result(
                f"feature:{k}",
                "pass" if v is True else ("fail" if v is False else "info"),
                str(v),
            )

        # --- Helper: show notification via SW (no push needed) ---
        def send_push(payload, wait=2):
            """Show notification via SW postMessage instead of push."""
            title = payload.get("title", "Test")
            options = {}
            if "body" in payload:
                options["body"] = payload["body"]
            if "tag" in payload:
                options["tag"] = payload["tag"]
            if "data" in payload:
                options["data"] = payload["data"]
            if "silent" in payload:
                options["silent"] = payload["silent"]
            if "renotify" in payload:
                options["renotify"] = payload["renotify"]
            if "requireInteraction" in payload:
                options["requireInteraction"] = payload["requireInteraction"]
            if "actions" in payload:
                options["actions"] = payload["actions"]
            if "vibrate" in payload:
                options["vibrate"] = payload["vibrate"]
            tid = f"auto-{int(time.time() * 1000)}"
            page.evaluate(
                f"waitForSW({{type:'test-showNotification',title:{json.dumps(title)},options:{json.dumps(options)},test_id:'{tid}'}}, 5000)"
            )
            time.sleep(wait)

        def get_notifs_from_sw(tag_filter=None):
            tid = f"auto-gn-{int(time.time() * 1000)}"
            msg = {"type": "test-getNotifications", "test_id": tid}
            if tag_filter:
                msg["filter_tag"] = tag_filter
            result = page.evaluate(f"waitForSW({json.dumps(msg)}, 5000)")
            return result

        def close_by_tag_sw(tag):
            tid = f"auto-close-{int(time.time() * 1000)}"
            result = page.evaluate(
                f"waitForSW({{type:'test-closeByTag',tag:'{tag}',test_id:'{tid}'}}, 8000)"
            )
            return result

        def close_all_sw():
            tid = f"auto-closeall-{int(time.time() * 1000)}"
            result = page.evaluate(
                f"waitForSW({{type:'test-closeAll',test_id:'{tid}'}}, 5000)"
            )
            return result

        def show_from_sw(title, options):
            tid = f"auto-show-{int(time.time() * 1000)}"
            result = page.evaluate(
                f"waitForSW({{type:'test-showNotification',title:'{title}',options:{json.dumps(options)},test_id:'{tid}'}}, 5000)"
            )
            return result

        # --- Test 3: Tag replacement ---
        print("\n## Tag Replacement")
        close_all_sw()
        time.sleep(1)

        send_push(
            {
                "title": "Tag A-1",
                "body": "First",
                "tag": "tag-test-a",
                "data": {"seq": 1},
            }
        )
        r1 = get_notifs_from_sw("tag-test-a")
        log_result(
            "3a:before-replace",
            "pass" if r1["count"] == 1 else "fail",
            f"count={r1['count']} (expected 1)",
        )

        send_push(
            {
                "title": "Tag A-2",
                "body": "Should replace",
                "tag": "tag-test-a",
                "data": {"seq": 2},
            }
        )
        time.sleep(1)
        r2 = get_notifs_from_sw("tag-test-a")
        log_result(
            "3a:after-replace",
            "pass" if r2["count"] == 1 else "fail",
            f"count={r2['count']} (expected 1 after replacement)",
        )
        if r2["count"] == 1 and r2["notifications"]:
            title = r2["notifications"][0].get("title", "")
            log_result(
                "3a:replaced-content",
                "pass" if "A-2" in title else "fail",
                f"title='{title}' (should be 'Tag A-2')",
            )

        # 3f: delayed verification
        close_all_sw()
        time.sleep(1)
        send_push(
            {
                "title": "Delay 1",
                "body": "First",
                "tag": "delay-tag",
                "data": {"seq": 1},
            },
            wait=5,
        )
        r3 = get_notifs_from_sw("delay-tag")
        log_result("3f:pre", "info", f"Before push 2: count={r3['count']}")
        send_push(
            {
                "title": "Delay 2",
                "body": "Replace?",
                "tag": "delay-tag",
                "data": {"seq": 2},
            },
            wait=5,
        )
        r4 = get_notifs_from_sw("delay-tag")
        log_result(
            "3f:post-5s",
            "pass" if r4["count"] == 1 else "fail",
            f"After 5s: count={r4['count']} (expected 1)",
        )

        # --- Test 4: getNotifications ---
        print("\n## getNotifications()")
        close_all_sw()
        time.sleep(1)

        send_push({"title": "GN-A", "tag": "gn-a", "data": {"marker": "a"}})
        send_push({"title": "GN-B", "tag": "gn-b", "data": {"marker": "b"}})
        time.sleep(1)

        r_all = get_notifs_from_sw()
        log_result(
            "4a:sw-no-filter",
            "pass" if r_all["count"] >= 2 else "fail",
            f"count={r_all['count']} (expected >=2)",
        )

        r_filt = get_notifs_from_sw("gn-a")
        log_result(
            "4b:sw-filter-tag",
            "pass" if r_filt["count"] == 1 else "fail",
            f"count={r_filt['count']} (expected 1)",
        )

        # page context
        page_count = page.evaluate("""async () => {
            var reg = await navigator.serviceWorker.ready;
            var list = await reg.getNotifications();
            return list.length;
        }""")
        log_result(
            "4c:page-context",
            "pass" if page_count >= 2 else "fail",
            f"count={page_count} (expected >=2)",
        )

        # data readback
        r_data = get_notifs_from_sw("gn-a")
        if r_data["count"] > 0 and r_data["notifications"][0].get("data"):
            d = r_data["notifications"][0]["data"]
            log_result(
                "4d:data-readback",
                "pass" if d.get("marker") == "a" else "fail",
                f"data={json.dumps(d)}",
            )
        else:
            log_result("4d:data-readback", "fail", "no data")

        # --- Test 5: notification.close() ---
        print("\n## notification.close()")
        close_all_sw()
        time.sleep(1)

        send_push({"title": "Close SW", "tag": "close-sw", "data": {"x": 1}})
        r_close = close_by_tag_sw("close-sw")
        log_result(
            "5a:close-from-sw",
            "pass"
            if r_close.get("remaining_3s", r_close.get("remaining", -1)) == 0
            else "fail",
            f"found={r_close.get('found')}, remaining={r_close.get('remaining_3s', r_close.get('remaining'))}",
        )

        send_push({"title": "Close Page", "tag": "close-page", "data": {"x": 1}})
        page_close = page.evaluate("""async () => {
            var reg = await navigator.serviceWorker.ready;
            var before = await reg.getNotifications({tag: 'close-page'});
            before.forEach(n => n.close());
            await new Promise(r => setTimeout(r, 3000));
            var after = await reg.getNotifications({tag: 'close-page'});
            return {before: before.length, after: after.length};
        }""")
        log_result(
            "5b:close-from-page",
            "pass" if page_close["after"] == 0 else "fail",
            f"before={page_close['before']}, after_3s={page_close['after']}",
        )

        close_all_sw()
        time.sleep(1)

        # --- Test 6: Prune-then-show ---
        print("\n## Prune-then-show")
        for i in range(3):
            send_push(
                {
                    "title": f"Room X Msg {i + 1}",
                    "tag": f"room-x-{i}-{int(time.time() * 1000)}",
                    "data": {"roomId": "room-x", "count": i + 1},
                },
                wait=1,
            )
        time.sleep(2)

        pre = get_notifs_from_sw()
        room_x_pre = [
            n
            for n in (pre.get("notifications") or [])
            if n.get("data", {}).get("roomId") == "room-x"
        ]
        log_result(
            "6a:pre-prune", "info", f"total={pre['count']}, room-x={len(room_x_pre)}"
        )

        prune_tid = f"auto-prune-{int(time.time() * 1000)}"
        prune_result = page.evaluate(f"""waitForSW({{
            type: 'test-pruneAndShow',
            test_id: '{prune_tid}',
            group_field: 'roomId',
            group_value: 'room-x',
            title: 'Room X - 3 new messages',
            options: {{
                body: '3 new messages',
                tag: 'room-x-consolidated-{int(time.time() * 1000)}',
                data: {{ roomId: 'room-x', count: 3, consolidated: true }},
            }}
        }}, 8000)""")
        time.sleep(2)
        post = get_notifs_from_sw()
        room_x_post = [
            n
            for n in (post.get("notifications") or [])
            if n.get("data", {}).get("roomId") == "room-x"
        ]
        log_result(
            "6a:post-prune",
            "pass" if len(room_x_post) == 1 else "fail",
            f"total={post['count']}, room-x={len(room_x_post)} (expected 1)",
        )

        # --- Test 7: silent ---
        print("\n## silent option")
        close_all_sw()
        time.sleep(1)
        send_push({"title": "Silent Test", "tag": "silent-test", "silent": True})
        r_silent = get_notifs_from_sw("silent-test")
        if r_silent["count"] > 0:
            s = r_silent["notifications"][0].get("silent")
            log_result(
                "7a:silent",
                "pass" if s is True else "warn",
                f"silent={s} (notification shown, silent property={s})",
            )
        else:
            log_result("7a:silent", "fail", "notification not shown")

        # --- Test 8: actions ---
        print("\n## actions")
        max_actions = page.evaluate(
            "typeof Notification !== 'undefined' ? Notification.maxActions : 'N/A'"
        )
        log_result("8b:maxActions", "info", f"Notification.maxActions={max_actions}")

        close_all_sw()
        time.sleep(1)
        send_push(
            {
                "title": "Actions",
                "tag": "action-test",
                "actions": [
                    {"action": "reply", "title": "Reply"},
                    {"action": "dismiss", "title": "Dismiss"},
                ],
            }
        )
        r_act = get_notifs_from_sw("action-test")
        if r_act["count"] > 0:
            ac = r_act["notifications"][0].get("actions", 0)
            log_result(
                "8a:actions", "pass" if ac >= 2 else "warn", f"actions_count={ac}"
            )
        else:
            log_result("8a:actions", "fail", "notification not shown")

        # --- Test 9: data ---
        print("\n## data option")
        close_all_sw()
        time.sleep(1)
        test_data = {
            "roomId": "xyz",
            "count": 42,
            "nested": {"foo": "bar"},
            "arr": [1, 2, 3],
        }
        send_push({"title": "Data", "tag": "data-test", "data": test_data})
        r_d = get_notifs_from_sw("data-test")
        if r_d["count"] > 0:
            d = r_d["notifications"][0].get("data", {})
            match = d.get("roomId") == "xyz" and d.get("count") == 42
            log_result("9a:data", "pass" if match else "fail", f"data={json.dumps(d)}")
        else:
            log_result("9a:data", "fail", "notification not shown")

        # --- Test 15: showNotification from page ---
        print("\n## showNotification from page")
        close_all_sw()
        time.sleep(1)
        page_show = page.evaluate("""async () => {
            try {
                var reg = await navigator.serviceWorker.ready;
                await reg.showNotification('From Page', {
                    body: 'Page-created notification',
                    tag: 'page-show-test',
                    data: {source: 'page'},
                });
                await new Promise(r => setTimeout(r, 1000));
                var list = await reg.getNotifications({tag: 'page-show-test'});
                return {ok: true, count: list.length};
            } catch(e) { return {ok: false, error: e.message}; }
        }""")
        log_result(
            "15a:showFromPage",
            "pass" if page_show.get("count", 0) > 0 else "fail",
            f"ok={page_show.get('ok')}, count={page_show.get('count', 0)}",
        )

        # new Notification()
        new_notif = page.evaluate("""() => {
            try {
                var n = new Notification('test');
                n.close();
                return {ok: true};
            } catch(e) { return {ok: false, error: e.message}; }
        }""")
        log_result(
            "15b:newNotification",
            "pass" if new_notif.get("ok") else "info",
            f"ok={new_notif.get('ok')}, error={new_notif.get('error', '')}",
        )

        # --- Test 17: Room clear ---
        print("\n## Room clear simulation")
        close_all_sw()
        time.sleep(1)
        send_push(
            {
                "title": "#room-A",
                "tag": f"rc-a-{int(time.time() * 1000)}",
                "data": {"roomId": "rc-a"},
            },
            wait=1,
        )
        send_push(
            {
                "title": "#room-B",
                "tag": f"rc-b-{int(time.time() * 1000)}",
                "data": {"roomId": "rc-b"},
            },
            wait=3,
        )

        room_clear = page.evaluate("""async () => {
            var reg = await navigator.serviceWorker.ready;
            var all = await reg.getNotifications();
            var roomA = all.filter(n => n.data && n.data.roomId === 'rc-a');
            var roomB = all.filter(n => n.data && n.data.roomId === 'rc-b');
            roomA.forEach(n => n.close());
            await new Promise(r => setTimeout(r, 3000));
            var after = await reg.getNotifications();
            var afterA = after.filter(n => n.data && n.data.roomId === 'rc-a');
            var afterB = after.filter(n => n.data && n.data.roomId === 'rc-b');
            return {
                before_total: all.length, before_a: roomA.length, before_b: roomB.length,
                after_total: after.length, after_a: afterA.length, after_b: afterB.length,
            };
        }""")
        log_result(
            "17a:room-clear",
            "pass"
            if room_clear["after_a"] == 0 and room_clear["after_b"] > 0
            else "fail",
            f"before: A={room_clear['before_a']} B={room_clear['before_b']} | after: A={room_clear['after_a']} B={room_clear['after_b']}",
        )

        # --- Test 16: Rapid fire ---
        print("\n## Rapid fire")
        close_all_sw()
        time.sleep(1)

        for i in range(5):
            send_push(
                {
                    "title": f"Rapid {i + 1}/5",
                    "tag": f"rapid-{i}-{int(time.time() * 1000)}",
                },
                wait=0.5,
            )
        time.sleep(2)
        rapid = get_notifs_from_sw()
        log_result(
            "16a:rapid-5-unique",
            "pass" if rapid["count"] == 5 else "warn",
            f"count={rapid['count']} (expected 5)",
        )

        close_all_sw()
        time.sleep(1)
        for i in range(5):
            send_push({"title": f"Same {i + 1}/5", "tag": "rapid-same"}, wait=0.5)
        time.sleep(2)
        rapid_same = get_notifs_from_sw("rapid-same")
        log_result(
            "16b:rapid-5-same-tag",
            "pass" if rapid_same["count"] == 1 else "fail",
            f"count={rapid_same['count']} (expected 1 after tag replacement)",
        )

        # --- Cleanup ---
        close_all_sw()
        browser.close()

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print("## Summary")
    passed = sum(1 for r in RESULTS if r["status"] == "pass")
    failed = sum(1 for r in RESULTS if r["status"] == "fail")
    total = len(RESULTS)
    print(
        f"   {passed} passed, {failed} failed, {total - passed - failed} info/warn out of {total} tests"
    )
    print()
    if failed:
        print("Failures:")
        for r in RESULTS:
            if r["status"] == "fail":
                print(f"   {r['test']}: {r['detail']}")

    # save report
    report_file = Path(__file__).resolve().parent / "logs" / f"{engine}_report.json"
    report_file.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nFull report: {report_file}")


if __name__ == "__main__":
    import sys

    engine = sys.argv[1] if len(sys.argv) > 1 else "chromium"
    run_tests(engine)
