"""
Notification API diagnostic server.
Tests every notification feature across platforms.

Usage:
    cd notif-diag && python server.py

    # With custom certs:
    python server.py --cert path/to/cert.pem --key path/to/key.pem

    # Custom port:
    python server.py --port 9444

Requires: pip install fastapi uvicorn[standard] pywebpush cryptography
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

DIAG_DIR = Path(__file__).resolve().parent
LOG_DIR = DIAG_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

subscriptions: list[dict] = []
private_key_path: str = ""
public_key_b64: str = ""

_push_counter = 0


def generate_vapid_keys():
    global private_key_path, public_key_b64
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file = DIAG_DIR / ".vapid_key.pem"
    key_file.write_bytes(pem)
    private_key_path = str(key_file)
    pub = key.public_key().public_numbers()
    raw = b"\x04" + pub.x.to_bytes(32, "big") + pub.y.to_bytes(32, "big")
    public_key_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    print(f"VAPID public key: {public_key_b64[:40]}...")


def _log_to_file(session_id: str, entry: dict):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{session_id}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    latest = LOG_DIR / "_latest.jsonl"
    with open(latest, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


app = FastAPI()

NO_CACHE = {"Cache-Control": "no-store"}


@app.get("/ca.pem")
async def serve_ca():
    ca_file = DIAG_DIR / "rootCA.pem"
    if not ca_file.exists():
        return JSONResponse({"error": "rootCA.pem not found"}, 404)
    return FileResponse(
        ca_file,
        media_type="application/x-pem-file",
        filename="mkcert-rootCA.pem",
        headers=NO_CACHE,
    )


@app.get("/")
async def serve_index():
    return FileResponse(
        DIAG_DIR / "index.html", media_type="text/html", headers=NO_CACHE
    )


@app.get("/sw.js")
async def serve_sw():
    return FileResponse(
        DIAG_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(DIAG_DIR / "manifest.json", media_type="application/json")


@app.get("/api/vapid-key")
async def vapid_key():
    return {"key": public_key_b64}


@app.post("/api/subscribe")
async def subscribe(request: Request):
    data = await request.json()
    endpoint = data.get("endpoint", "")
    for s in subscriptions:
        if s["endpoint"] == endpoint:
            s.update(data)
            return {"ok": True, "msg": "updated"}
    subscriptions.append(data)
    print(f"Subscription stored: {endpoint[:60]}... (total: {len(subscriptions)})")
    return {"ok": True, "msg": "added"}


@app.post("/api/log")
async def receive_log(request: Request):
    data = await request.json()
    session_id = data.get("session", "unknown")
    entry = {
        "server_ts": datetime.now(timezone.utc).isoformat(),
        "client_ts": data.get("ts", ""),
        "src": data.get("src", "?"),
        "ev": data.get("ev", "?"),
        "detail": data.get("detail", ""),
        "platform": data.get("platform", ""),
        "test_id": data.get("test_id", ""),
    }
    _log_to_file(session_id, entry)
    return {"ok": True}


@app.post("/api/log/batch")
async def receive_log_batch(request: Request):
    data = await request.json()
    session_id = data.get("session", "unknown")
    entries = data.get("entries", [])
    for e in entries:
        entry = {
            "server_ts": datetime.now(timezone.utc).isoformat(),
            "client_ts": e.get("ts", ""),
            "src": e.get("src", "?"),
            "ev": e.get("ev", "?"),
            "detail": e.get("detail", ""),
            "platform": e.get("platform", ""),
            "test_id": e.get("test_id", ""),
        }
        _log_to_file(session_id, entry)
    return {"ok": True, "count": len(entries)}


@app.get("/api/logs/{session_id}")
async def get_logs(session_id: str):
    log_file = LOG_DIR / f"{session_id}.jsonl"
    if not log_file.exists():
        return {"entries": []}
    entries = []
    for line in log_file.read_text().strip().split("\n"):
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"entries": entries}


@app.get("/api/logs")
async def list_logs():
    files = sorted(
        LOG_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    result = []
    for f in files[:20]:
        if f.name == "_latest.jsonl":
            continue
        line_count = sum(1 for _ in open(f))
        result.append(
            {
                "session": f.stem,
                "lines": line_count,
                "modified": datetime.fromtimestamp(
                    f.stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    return {"sessions": result}


@app.post("/api/logs/clear")
async def clear_logs():
    for f in LOG_DIR.glob("*.jsonl"):
        f.unlink()
    return {"ok": True}


@app.post("/api/push")
async def send_push(request: Request):
    global _push_counter
    if not subscriptions:
        return JSONResponse({"ok": False, "error": "No subscriptions."}, 400)

    body = await request.json()
    test_id = body.get("test_id", "manual")
    _push_counter += 1

    payload = json.dumps(
        {
            "test_id": test_id,
            "push_index": _push_counter,
            **body.get("payload", {}),
        }
    )

    from pywebpush import webpush, WebPushException

    sub = subscriptions[-1]
    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=private_key_path,
            vapid_claims={"sub": "mailto:diag@example.com"},
        )
    except WebPushException as e:
        return JSONResponse(
            {
                "ok": False,
                "error": str(e),
                "status": getattr(getattr(e, "response", None), "status_code", None),
            },
            500,
        )

    return {
        "ok": True,
        "push_index": _push_counter,
        "test_id": test_id,
        "payload_bytes": len(payload),
    }


@app.post("/api/push/sequence")
async def send_push_sequence(request: Request):
    """Send multiple pushes with configurable delays."""
    global _push_counter
    if not subscriptions:
        return JSONResponse({"ok": False, "error": "No subscriptions."}, 400)

    body = await request.json()
    test_id = body.get("test_id", "sequence")
    pushes = body.get("pushes", [])
    results = []

    from pywebpush import webpush, WebPushException

    sub = subscriptions[-1]
    vapid_claims = {"sub": "mailto:diag@example.com"}

    for i, p in enumerate(pushes):
        delay = p.get("delay_ms", 0)
        if delay > 0 and i > 0:
            time.sleep(delay / 1000.0)

        _push_counter += 1
        payload = json.dumps(
            {
                "test_id": test_id,
                "push_index": _push_counter,
                "seq_index": i,
                "seq_total": len(pushes),
                **p.get("payload", {}),
            }
        )

        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=private_key_path,
                vapid_claims=vapid_claims,
            )
            results.append({"ok": True, "push_index": _push_counter, "seq_index": i})
        except WebPushException as e:
            results.append({"ok": False, "error": str(e), "seq_index": i})

    return {"ok": True, "test_id": test_id, "results": results}


def find_certs():
    candidates = [
        (
            DIAG_DIR.parent / "server" / "localhost+1.pem",
            DIAG_DIR.parent / "server" / "localhost+1-key.pem",
        ),
        (
            DIAG_DIR.parent / "192.168.0.100+1.pem",
            DIAG_DIR.parent / "192.168.0.100+1-key.pem",
        ),
    ]
    for cert, key in candidates:
        if cert and key and cert.exists() and key.exists():
            return str(cert), str(key)
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Notification diagnostic server")
    parser.add_argument("--port", type=int, default=9444)
    parser.add_argument("--cert", help="SSL cert file")
    parser.add_argument("--key", help="SSL key file")
    parser.add_argument(
        "--no-ssl", action="store_true", help="Run without SSL (push won't work on iOS)"
    )
    args = parser.parse_args()

    generate_vapid_keys()

    cert, key = args.cert, args.key
    if not cert and not args.no_ssl:
        cert, key = find_certs()

    if cert and key:
        print(f"\nServing on https://localhost:{args.port}")
        print(f"  Test page:   https://localhost:{args.port}/")
        print(f"  Logs dir:    {LOG_DIR}")
        print(f"  Certs:       {cert}\n")
        uvicorn.run(
            app, host="0.0.0.0", port=args.port, ssl_certfile=cert, ssl_keyfile=key
        )
    elif args.no_ssl:
        print(
            f"\nServing on http://localhost:{args.port} (no SSL - push won't work on iOS)"
        )
        uvicorn.run(app, host="0.0.0.0", port=args.port)
    else:
        print("\nNo SSL certs found. Options:")
        print(
            "  1. brew install mkcert && mkcert -install && mkcert localhost 127.0.0.1"
        )
        print("  2. python server.py --cert cert.pem --key key.pem")
        print("  3. python server.py --no-ssl  (push won't work)")
        sys.exit(1)


if __name__ == "__main__":
    main()
