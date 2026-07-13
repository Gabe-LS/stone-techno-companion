"""CLI entry point for the notif_e2e scenario suite.

Starts ONE isolated NotifServer (see harness.py) and runs every scenario in
tests/notif_e2e/scenarios/emission.py against it, each with its own fresh
FakePushService instance(s), users, and subscriptions -- so scenarios stay
independent even though they share the server process (fresh (user_id,
room_id) keys mean nothing in server-side debounce/activity state can leak
between scenarios; see emission.py's _new_room docstring for why each
scenario also gets its own room). This is the Stage-1 "emission" suite:
server -> wire, no browser/CDP, driven with asyncio.

Stage 2 adds a "browser" suite (scenarios/client.py): real headless Chromium
via Playwright's SYNC API, driving services/companion/chat/chat.html's actual client-side
notification code. Playwright's sync API cannot run inside an asyncio event
loop, so the browser suite is launched from its own plain synchronous
function (_main_browser), never from inside asyncio.run() -- the two suites
share nothing but the NotifServer *class* (each gets its own instance) and
never run in the same event loop.

Stage 3 adds a "sw" suite (scenarios/sw.py): the real services/companion/static/sw.js
source loaded into a mock service-worker environment (sw_harness.SWLab),
exercising push/notificationclick/notificationclose/pushsubscriptionchange
handler behavior. Also sync Playwright, launched from its own plain
synchronous function (_main_sw), for the same reason as the browser suite --
never from inside asyncio.run().

Usage:
    python tests/notif_e2e/run.py                     # emission suite only (default)
    python tests/notif_e2e/run.py --scenario idle_recipient_push
    python tests/notif_e2e/run.py --browser            # browser (client-behavior) suite only
    python tests/notif_e2e/run.py --sw                 # sw (service-worker) suite only
    python tests/notif_e2e/run.py --all                # emission suite, then browser, then sw
    python tests/notif_e2e/run.py --list

Writes a per-scenario JSON timeline artifact under tests/notif_e2e/_artifacts/,
prints a PASS/FAIL/SKIP table, and exits non-zero if any scenario failed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_push_service import FakePushService  # noqa: E402
from harness import NotifServer  # noqa: E402
from recorder import SignalRecorder  # noqa: E402
from scenarios.emission import SCENARIOS, ScenarioSkip  # noqa: E402

ARTIFACTS_DIR = Path(__file__).resolve().parent / "_artifacts"


async def _run_one(server: NotifServer, name: str, spec: dict) -> dict:
    """Run a single scenario against the shared server. Returns a result dict
    with status ("pass" | "fail" | "error" | "skip"), fails (list[str]),
    duration_s, and the artifact path."""
    fps_count = spec["fps_count"]
    fps_list = [FakePushService() for _ in range(fps_count)]
    recorder = SignalRecorder()
    started = time.monotonic()
    status = "fail"
    fails: list[str] = []

    print(f"\n{'=' * 70}")
    print(f"[run] scenario={name} fps_count={fps_count}")
    print(f"{'=' * 70}")

    try:
        for f in fps_list:
            await f.start()
        print(f"[run] {name}: FPS origins = {[f.origin for f in fps_list]}")

        fails = await spec["fn"](server, fps_list, recorder)
        status = "pass" if not fails else "fail"
    except ScenarioSkip as e:
        status = "skip"
        fails = [str(e)]
        print(f"[run] {name}: SKIP -- {e}")
    except Exception:
        status = "error"
        tb = traceback.format_exc()
        fails = [f"unhandled exception:\n{tb}"]
        print(f"[run] {name}: ERROR\n{tb}")
    finally:
        for f in fps_list:
            try:
                await f.stop()
            except Exception:
                pass

    duration_s = time.monotonic() - started

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACTS_DIR / f"{name}.json"
    try:
        recorder.dump(str(artifact_path))
    except Exception as e:
        print(f"[run] {name}: failed to write artifact: {e}")

    if status == "pass":
        print(f"[run] {name}: PASS ({duration_s:.2f}s)")
    elif status == "skip":
        print(f"[run] {name}: SKIP ({duration_s:.2f}s)")
    else:
        print(f"[run] {name}: {status.upper()} ({duration_s:.2f}s)")
        for f in fails:
            print(f"  - {f}")
        print(f"[run] {name}: server log tail:")
        for line in server.log_lines[-30:]:
            print("   ", line)

    return {
        "name": name,
        "status": status,
        "fails": fails,
        "duration_s": duration_s,
        "artifact": str(artifact_path),
    }


async def _main(scenario_filter: str | None) -> int:
    if scenario_filter is not None and scenario_filter not in SCENARIOS:
        print(f"[run] unknown scenario {scenario_filter!r}")
        print(f"[run] available scenarios: {', '.join(SCENARIOS.keys())}")
        return 2

    names = [scenario_filter] if scenario_filter else list(SCENARIOS.keys())

    server = NotifServer()
    print("[run] starting isolated NotifServer...")
    server.start()
    print(f"[run] server ready at {server.base_url}")

    results: list[dict] = []
    try:
        for name in names:
            result = await _run_one(server, name, SCENARIOS[name])
            results.append(result)
    finally:
        server.stop()

    print(f"\n{'=' * 70}")
    print("[run] SUMMARY")
    print(f"{'=' * 70}")
    width = max(len(r["name"]) for r in results)
    for r in results:
        marker = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skip": "SKIP"}[
            r["status"]
        ]
        print(f"  {r['name']:<{width}}  {marker:<6}  {r['duration_s']:6.2f}s")
        if r["status"] == "skip":
            print(f"    reason: {r['fails'][0]}")

    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_fail = sum(1 for r in results if r["status"] in ("fail", "error"))
    n_skip = sum(1 for r in results if r["status"] == "skip")
    print(
        f"\n[run] {n_pass} passed, {n_fail} failed, {n_skip} skipped "
        f"(of {len(results)})"
    )
    print(f"[run] artifacts written under {ARTIFACTS_DIR}")

    return 1 if n_fail else 0


def _run_one_browser(
    lab, server: NotifServer, name: str, fn, recorder: SignalRecorder
) -> dict:
    """Run a single Stage-2 client-behavior scenario against the shared lab/server.
    Mirrors _run_one's result shape (status/fails/duration_s/artifact) exactly, so
    the summary table and artifact handling below are identical to the emission
    suite's. Synchronous -- see the module docstring for why this cannot be an
    async function."""
    started = time.monotonic()
    status = "fail"
    fails: list[str] = []

    print(f"\n{'=' * 70}")
    print(f"[run:browser] scenario={name}")
    print(f"{'=' * 70}")

    try:
        fails = fn(lab, server, recorder)
        status = "pass" if not fails else "fail"
    except ScenarioSkip as e:
        status = "skip"
        fails = [str(e)]
        print(f"[run:browser] {name}: SKIP -- {e}")
    except Exception:
        status = "error"
        tb = traceback.format_exc()
        fails = [f"unhandled exception:\n{tb}"]
        print(f"[run:browser] {name}: ERROR\n{tb}")

    duration_s = time.monotonic() - started

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACTS_DIR / f"{name}.json"
    try:
        recorder.dump(str(artifact_path))
    except Exception as e:
        print(f"[run:browser] {name}: failed to write artifact: {e}")

    if status == "pass":
        print(f"[run:browser] {name}: PASS ({duration_s:.2f}s)")
    elif status == "skip":
        print(f"[run:browser] {name}: SKIP ({duration_s:.2f}s)")
    else:
        print(f"[run:browser] {name}: {status.upper()} ({duration_s:.2f}s)")
        for f in fails:
            print(f"  - {f}")
        print(f"[run:browser] {name}: server log tail:")
        for line in server.log_lines[-30:]:
            print("   ", line)

    return {
        "name": name,
        "status": status,
        "fails": fails,
        "duration_s": duration_s,
        "artifact": str(artifact_path),
    }


def _main_browser(scenario_filter: str | None) -> int:
    """Run the Stage-2 client-behavior suite: one Playwright chromium (headless),
    one NotifServer, each scenario gets a fresh BrowserLab-driven session (or
    sessions) and a fresh SignalRecorder. Plain synchronous function -- never
    called from inside asyncio.run(); Playwright's sync API would deadlock or
    raise if it were."""
    from playwright.sync_api import sync_playwright

    from browser import BrowserLab
    from scenarios.client import SCENARIOS as CLIENT_SCENARIOS

    if scenario_filter is not None and scenario_filter not in CLIENT_SCENARIOS:
        print(f"[run:browser] unknown scenario {scenario_filter!r}")
        print(
            f"[run:browser] available scenarios: {', '.join(CLIENT_SCENARIOS.keys())}"
        )
        return 2

    names = [scenario_filter] if scenario_filter else list(CLIENT_SCENARIOS.keys())

    server = NotifServer()
    print("[run:browser] starting isolated NotifServer...")
    server.start()
    print(f"[run:browser] server ready at {server.base_url}")

    results: list[dict] = []
    pw = None
    browser_instance = None
    try:
        pw = sync_playwright().start()
        browser_instance = pw.chromium.launch(headless=True)
        lab = BrowserLab(server, browser_instance)
        for name in names:
            recorder = SignalRecorder()
            result = _run_one_browser(
                lab, server, name, CLIENT_SCENARIOS[name]["fn"], recorder
            )
            results.append(result)
    finally:
        try:
            if browser_instance is not None:
                browser_instance.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
        server.stop()

    print(f"\n{'=' * 70}")
    print("[run:browser] SUMMARY")
    print(f"{'=' * 70}")
    if results:
        width = max(len(r["name"]) for r in results)
        for r in results:
            marker = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skip": "SKIP"}[
                r["status"]
            ]
            print(f"  {r['name']:<{width}}  {marker:<6}  {r['duration_s']:6.2f}s")
            if r["status"] == "skip":
                print(f"    reason: {r['fails'][0]}")

    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_fail = sum(1 for r in results if r["status"] in ("fail", "error"))
    n_skip = sum(1 for r in results if r["status"] == "skip")
    print(
        f"\n[run:browser] {n_pass} passed, {n_fail} failed, {n_skip} skipped "
        f"(of {len(results)})"
    )
    print(f"[run:browser] artifacts written under {ARTIFACTS_DIR}")

    return 1 if n_fail else 0


def _run_one_sw(
    swlab, server: NotifServer, name: str, fn, recorder: SignalRecorder
) -> dict:
    """Run a single Stage-3 service-worker scenario against the shared
    swlab/server. Mirrors _run_one_browser's result shape and control flow
    exactly (status/fails/duration_s/artifact, ScenarioSkip handling, log
    tail on failure)."""
    started = time.monotonic()
    status = "fail"
    fails: list[str] = []

    print(f"\n{'=' * 70}")
    print(f"[run:sw] scenario={name}")
    print(f"{'=' * 70}")

    try:
        fails = fn(swlab, server, recorder)
        status = "pass" if not fails else "fail"
    except ScenarioSkip as e:
        status = "skip"
        fails = [str(e)]
        print(f"[run:sw] {name}: SKIP -- {e}")
    except Exception:
        status = "error"
        tb = traceback.format_exc()
        fails = [f"unhandled exception:\n{tb}"]
        print(f"[run:sw] {name}: ERROR\n{tb}")

    duration_s = time.monotonic() - started

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACTS_DIR / f"{name}.json"
    try:
        recorder.dump(str(artifact_path))
    except Exception as e:
        print(f"[run:sw] {name}: failed to write artifact: {e}")

    if status == "pass":
        print(f"[run:sw] {name}: PASS ({duration_s:.2f}s)")
    elif status == "skip":
        print(f"[run:sw] {name}: SKIP ({duration_s:.2f}s)")
    else:
        print(f"[run:sw] {name}: {status.upper()} ({duration_s:.2f}s)")
        for f in fails:
            print(f"  - {f}")
        print(f"[run:sw] {name}: server log tail:")
        for line in server.log_lines[-30:]:
            print("   ", line)

    return {
        "name": name,
        "status": status,
        "fails": fails,
        "duration_s": duration_s,
        "artifact": str(artifact_path),
    }


def _main_sw(scenario_filter: str | None) -> int:
    """Run the Stage-3 service-worker suite: one Playwright chromium
    (headless), one NotifServer, /sw.js fetched once via httpx, one SWLab,
    each scenario gets a fresh SWHarness (new_harness per scenario) and a
    fresh SignalRecorder. Plain synchronous function -- never called from
    inside asyncio.run(), same reason as _main_browser."""
    import httpx
    from playwright.sync_api import sync_playwright

    from scenarios.sw import SCENARIOS as SW_SCENARIOS
    from sw_harness import SWLab

    if scenario_filter is not None and scenario_filter not in SW_SCENARIOS:
        print(f"[run:sw] unknown scenario {scenario_filter!r}")
        print(f"[run:sw] available scenarios: {', '.join(SW_SCENARIOS.keys())}")
        return 2

    names = [scenario_filter] if scenario_filter else list(SW_SCENARIOS.keys())

    server = NotifServer()
    print("[run:sw] starting isolated NotifServer...")
    server.start()
    print(f"[run:sw] server ready at {server.base_url}")

    results: list[dict] = []
    pw = None
    browser_instance = None
    try:
        sw_src = httpx.get(server.base_url + "/sw.js").text
        pw = sync_playwright().start()
        browser_instance = pw.chromium.launch(headless=True)
        swlab = SWLab(server, browser_instance, sw_src)
        for name in names:
            recorder = SignalRecorder()
            result = _run_one_sw(
                swlab, server, name, SW_SCENARIOS[name]["fn"], recorder
            )
            results.append(result)
    finally:
        try:
            if browser_instance is not None:
                browser_instance.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass
        server.stop()

    print(f"\n{'=' * 70}")
    print("[run:sw] SUMMARY")
    print(f"{'=' * 70}")
    if results:
        width = max(len(r["name"]) for r in results)
        for r in results:
            marker = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skip": "SKIP"}[
                r["status"]
            ]
            print(f"  {r['name']:<{width}}  {marker:<6}  {r['duration_s']:6.2f}s")
            if r["status"] == "skip":
                print(f"    reason: {r['fails'][0]}")

    n_pass = sum(1 for r in results if r["status"] == "pass")
    n_fail = sum(1 for r in results if r["status"] in ("fail", "error"))
    n_skip = sum(1 for r in results if r["status"] == "skip")
    print(
        f"\n[run:sw] {n_pass} passed, {n_fail} failed, {n_skip} skipped "
        f"(of {len(results)})"
    )
    print(f"[run:sw] artifacts written under {ARTIFACTS_DIR}")

    return 1 if n_fail else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="notif_e2e scenario suite")
    parser.add_argument(
        "--scenario",
        default=None,
        help="run only this scenario by name (matched against whichever suite(s) run)",
    )
    parser.add_argument(
        "--list", action="store_true", help="list available scenarios and exit"
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="run the Stage-2 client-behavior suite (sync Playwright) instead of the emission suite",
    )
    parser.add_argument(
        "--sw",
        action="store_true",
        help="run the Stage-3 service-worker suite (sync Playwright, mock SW) instead of the emission suite",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="run the emission suite, then the browser suite, then the sw suite",
    )
    args = parser.parse_args()

    if args.all:
        run_emission, run_browser, run_sw = True, True, True
    elif args.browser or args.sw:
        run_emission, run_browser, run_sw = False, args.browser, args.sw
    else:
        run_emission, run_browser, run_sw = True, False, False

    if args.list:
        print("[run] emission scenarios:")
        for name, spec in SCENARIOS.items():
            print(f"  {name} (fps_count={spec['fps_count']})")
        if run_browser:
            from scenarios.client import SCENARIOS as CLIENT_SCENARIOS

            print("[run] browser (client-behavior) scenarios:")
            for name in CLIENT_SCENARIOS:
                print(f"  {name}")
        if run_sw:
            from scenarios.sw import SCENARIOS as SW_SCENARIOS

            print("[run] sw (service-worker) scenarios:")
            for name in SW_SCENARIOS:
                print(f"  {name}")
        return 0

    exit_code = 0
    if run_emission:
        if args.scenario is not None and args.scenario not in SCENARIOS:
            print(f"[run] unknown scenario {args.scenario!r}")
            print(f"[run] available scenarios: {', '.join(SCENARIOS.keys())}")
            return 2
        exit_code = max(exit_code, asyncio.run(_main(args.scenario)))
    if run_browser:
        browser_filter = args.scenario
        if args.all and browser_filter is not None:
            # --scenario named an emission-suite scenario -- --all still runs
            # the full browser suite rather than erroring on a name it does
            # not recognize (--browser alone with an unknown name still errors
            # below, inside _main_browser).
            from scenarios.client import SCENARIOS as CLIENT_SCENARIOS

            if browser_filter not in CLIENT_SCENARIOS:
                browser_filter = None
        exit_code = max(exit_code, _main_browser(browser_filter))
    if run_sw:
        sw_filter = args.scenario
        if args.all and sw_filter is not None:
            # Same reasoning as the browser suite above: --all must not error
            # on a --scenario name that belongs to a different suite.
            from scenarios.sw import SCENARIOS as SW_SCENARIOS

            if sw_filter not in SW_SCENARIOS:
                sw_filter = None
        exit_code = max(exit_code, _main_sw(sw_filter))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
