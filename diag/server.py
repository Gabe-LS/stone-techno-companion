"""
Standalone push notification diagnostic server.
Completely independent from the main app.

Usage:
    cd diag && python server.py

    # With custom certs:
    python server.py --cert path/to/cert.pem --key path/to/key.pem

    # Custom port:
    python server.py --port 9999

Requires: pip install fastapi uvicorn[standard] pywebpush cryptography
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn

DIAG_DIR = Path(__file__).resolve().parent
subscriptions: list[dict] = []

private_key_path: str = ""
public_key_b64: str = ""


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


app = FastAPI()


NO_CACHE = {"Cache-Control": "no-store"}


@app.get("/")
async def serve_index():
    return FileResponse(
        DIAG_DIR / "index.html", media_type="text/html", headers=NO_CACHE
    )


@app.get("/target")
async def serve_target():
    return FileResponse(
        DIAG_DIR / "target.html", media_type="text/html", headers=NO_CACHE
    )


@app.get("/log")
async def serve_log():
    return FileResponse(DIAG_DIR / "log.html", media_type="text/html", headers=NO_CACHE)


@app.get("/sw.js")
async def serve_sw():
    return FileResponse(
        DIAG_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"},
    )


@app.get("/shared.js")
async def serve_shared():
    return FileResponse(DIAG_DIR / "shared.js", media_type="application/javascript")


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


@app.post("/api/push")
async def send_push(request: Request):
    if not subscriptions:
        return JSONResponse(
            {
                "ok": False,
                "error": "No subscriptions. Open Page A and subscribe first.",
            },
            400,
        )

    body = await request.json()
    fmt = body.get("format", "rel-target")
    strategy = body.get("strategy", "default")

    host = request.headers.get("host", "localhost")
    scheme = "https"
    base = f"{scheme}://{host}"

    url_map = {
        "rel-target": "/target",
        "abs-target": f"{base}/target",
        "rel-target-query": "/target?from=push&t="
        + str(int(__import__("time").time())),
        "rel-target-hash": "/target#from-push",
        "rel-root": "/",
        "abs-root": f"{base}/",
    }
    target_url = url_map.get(fmt, "/target")

    if strategy == "declarative":
        payload = json.dumps(
            {
                "web_push": "8-0-3-0",
                "notification": {
                    "title": "Diag: declarative",
                    "body": f"url={target_url}",
                    "navigate": target_url
                    if target_url.startswith("http")
                    else f"{base}{target_url}",
                    "tag": "diag-declarative",
                },
                "title": "Diag: declarative",
                "body": f"url={target_url}",
                "tag": "diag-declarative",
                "url": target_url,
                "strategy": strategy,
            }
        )
    else:
        payload = json.dumps(
            {
                "title": f"Diag: {fmt}",
                "body": f"strategy={strategy} url={target_url}",
                "tag": "diag-" + fmt,
                "url": target_url,
                "strategy": strategy,
            }
        )

    from pywebpush import webpush, WebPushException

    sub = subscriptions[-1]
    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=private_key_path,
            vapid_claims={"sub": "mailto:gabriele@densitymedia.com"},
        )
    except WebPushException as e:
        return JSONResponse(
            {
                "ok": False,
                "error": f"Push failed: {e}",
                "status": getattr(getattr(e, "response", None), "status_code", None),
            },
            500,
        )

    return {
        "ok": True,
        "format": fmt,
        "strategy": strategy,
        "url": target_url,
        "payload_bytes": len(payload),
        "endpoint": sub.get("endpoint", "")[:60],
    }


def find_certs():
    candidates = [
        (
            DIAG_DIR.parent / "server" / "localhost+1.pem",
            DIAG_DIR.parent / "server" / "localhost+1-key.pem",
        ),
        (Path.home() / ".local/share/mkcert/rootCA.pem", None),
    ]
    for cert, key in candidates:
        if cert and key and cert.exists() and key.exists():
            return str(cert), str(key)
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Push notification diagnostic server")
    parser.add_argument("--port", type=int, default=9443)
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
        print(f"  Page A (source):  https://localhost:{args.port}/")
        print(f"  Page B (target):  https://localhost:{args.port}/target")
        print(f"  Log viewer:       https://localhost:{args.port}/log")
        print(f"\nCerts: {cert}\n")
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
