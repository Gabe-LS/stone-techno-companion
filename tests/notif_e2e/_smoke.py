"""Integration smoke test for the notif_e2e foundation.

Exercises the core loop end to end: start an isolated app server + a Fake Push
Service, inject a chat push subscription for an OFFLINE recipient, connect a
sender over the real chat WebSocket, send a message, and assert the FPS
captured and DECRYPTED exactly one WebPush with the expected payload, TTL, and
VAPID audience. Run directly:  python tests/notif_e2e/_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_push_service import FakePushService  # noqa: E402
from harness import NotifServer, WSClient  # noqa: E402


async def main() -> int:
    server = NotifServer()
    fps = FakePushService()
    fails: list[str] = []
    try:
        server.start()
        await fps.start()
        print(f"[smoke] server {server.base_url}  fps {fps.origin}")

        room_id = server.main_room_id()
        sender_id = server.create_user("Sender", username="sender")
        recipient_id = server.create_user("Recipient", username="recipient")
        server.ensure_membership(sender_id, room_id)
        server.ensure_membership(recipient_id, room_id)
        sub = server.inject_chat_subscription(recipient_id, fps)
        print(f"[smoke] injected recipient sub {sub.sub_id}")

        sender_token = server.create_session(sender_id)
        sender = WSClient(server.ws_base, sender_token)
        await sender.connect()
        await sender.join_room(room_id)
        await asyncio.sleep(0.3)

        await sender.send_message(room_id, "smoke test hello")
        print("[smoke] message sent; awaiting push at FPS...")

        captured = await fps.wait_for(sub.sub_id, count=1, timeout=8.0)
        push = captured[0]

        # --- assertions ---
        if push.decrypt_error:
            fails.append(f"decrypt_error: {push.decrypt_error}")
        if push.payload is None:
            fails.append("payload did not decrypt")
        else:
            if not push.payload.get("title"):
                fails.append(f"missing title in payload: {push.payload}")
            if not push.payload.get("body"):
                fails.append(f"missing body in payload: {push.payload}")
            if not push.payload.get("push_id"):
                fails.append(f"missing push_id in payload: {push.payload}")
            if "smoke test hello" not in (push.payload.get("body") or ""):
                fails.append(
                    f"body did not carry the message text: {push.payload.get('body')!r}"
                )
        if push.ttl != 300:
            fails.append(f"TTL expected 300, got {push.ttl}")
        if push.vapid.get("aud") != fps.origin:
            fails.append(
                f"VAPID aud expected {fps.origin}, got {push.vapid.get('aud')}"
            )
        if not push.vapid.get("sub"):
            fails.append("VAPID sub missing")

        print(f"[smoke] captured payload: {push.payload}")
        print(
            f"[smoke] ttl={push.ttl} aud={push.vapid.get('aud')} sub={push.vapid.get('sub')}"
        )
        print("[smoke] server [PUSH] log:", server.grep_log("[PUSH]")[-3:])

        await sender.close()
    except Exception:
        fails.append("exception: " + traceback.format_exc())
    finally:
        try:
            await fps.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass

    if fails:
        print("\n[smoke] FAIL")
        for f in fails:
            print("  -", f)
        print("\n[smoke] --- server log tail ---")
        for line in server.log_lines[-45:]:
            print("   ", line)
        return 1
    print("\n[smoke] PASS - full decrypt loop works")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
