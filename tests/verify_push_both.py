"""Verify that lineup and chat push subscriptions both point at a live endpoint.

Background: both the lineup page and the chat page register /sw.js at the root
scope, so a browser has exactly ONE push subscription per origin. The lineup
record lives in push_subscriptions (hearts.db) and the chat record lives in
chat_push_subscriptions (chat.db). The pre-deploy fix stopped each "enable"
flow from unsubscribe()+subscribe() (which rotated the shared endpoint and left
the OTHER surface's stored record dead). This script proves the fix: after you
enable notifications on BOTH surfaces in the same Chromium profile, it sends a
real WebPush to each stored subscription and reports whether each is live (201)
or dead (410/404).

Usage (from the repo root, with server/.env loaded for VAPID keys):

    set -a && source server/.env && set +a && python tests/verify_push_both.py

Expect: both surfaces report LIVE, and the two endpoints are IDENTICAL. A dead
record or two different endpoints means the collision is back.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from pywebpush import WebPushException, webpush

_SERVER_DATA = Path(__file__).resolve().parent.parent / "server" / "data"
HEARTS_DB = _SERVER_DATA / "hearts.db"
CHAT_DB = Path(os.environ.get("CHAT_DB_PATH", _SERVER_DATA / "chat.db"))


def _vapid_claims() -> dict:
    return {"sub": os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:noreply@example.com")}


def _load(db_path: Path, table: str, key_col: str) -> list[dict]:
    if not db_path.exists():
        print(f"  (no db at {db_path})")
        return []
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            f"SELECT {key_col}, endpoint, p256dh, auth FROM {table}"
        ).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


def _send(sub: dict, title: str, body: str) -> str:
    private = os.environ.get("VAPID_PRIVATE_KEY")
    if not private:
        return "NO_VAPID_KEY"
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": "/",
            "push_id": os.urandom(8).hex(),  # unique tag (iOS invariant)
        }
    )
    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=payload,
            vapid_private_key=private,
            vapid_claims=dict(_vapid_claims()),  # fresh copy per call (aud poisoning)
        )
        return "LIVE"
    except WebPushException as e:
        code = e.response.status_code if e.response is not None else "?"
        return f"DEAD ({code})"


def main() -> int:
    if not os.environ.get("VAPID_PRIVATE_KEY"):
        print("VAPID_PRIVATE_KEY not set — run with .env loaded (see docstring).")
        return 2

    print("=== Lineup subscriptions (push_subscriptions @ hearts.db) ===")
    lineup = _load(HEARTS_DB, "push_subscriptions", "session_id")
    for s in lineup:
        status = _send(s, "Lineup push test", "If you see this, lineup push is live.")
        print(f"  session={s['session_id'][:8]}  {s['endpoint'][:48]}...  -> {status}")

    print("=== Chat subscriptions (chat_push_subscriptions @ chat.db) ===")
    chat = _load(CHAT_DB, "chat_push_subscriptions", "user_id")
    for s in chat:
        status = _send(s, "Chat push test", "If you see this, chat push is live.")
        print(f"  user={s['user_id'][:8]}  {s['endpoint'][:48]}...  -> {status}")

    lineup_eps = {s["endpoint"] for s in lineup}
    chat_eps = {s["endpoint"] for s in chat}
    shared = lineup_eps & chat_eps
    print("=== Verdict ===")
    if not lineup or not chat:
        print(
            "  INCOMPLETE — enable notifications on BOTH surfaces first, then re-run."
        )
        return 1
    if shared:
        print(
            f"  OK: {len(shared)} endpoint(s) shared across both surfaces (fix holds)."
        )
        return 0
    print(
        "  WARNING: lineup and chat store DIFFERENT endpoints — the collision may be back."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
