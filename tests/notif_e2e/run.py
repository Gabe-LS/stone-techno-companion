"""CLI entry point for the Stage-1 notif_e2e scenario suite.

Starts ONE isolated NotifServer (see harness.py) and runs every scenario in
tests/notif_e2e/scenarios/emission.py against it, each with its own fresh
FakePushService instance(s), users, and subscriptions -- so scenarios stay
independent even though they share the server process (fresh (user_id,
room_id) keys mean nothing in server-side debounce/activity state can leak
between scenarios; see emission.py's _new_room docstring for why each
scenario also gets its own room).

Usage:
    python tests/notif_e2e/run.py                     # run all scenarios
    python tests/notif_e2e/run.py --scenario idle_recipient_push
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage-1 notif_e2e scenario suite")
    parser.add_argument(
        "--scenario", default=None, help="run only this scenario by name"
    )
    parser.add_argument(
        "--list", action="store_true", help="list available scenarios and exit"
    )
    args = parser.parse_args()

    if args.list:
        for name, spec in SCENARIOS.items():
            print(f"{name} (fps_count={spec['fps_count']})")
        return 0

    return asyncio.run(_main(args.scenario))


if __name__ == "__main__":
    sys.exit(main())
