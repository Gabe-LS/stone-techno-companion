"""Fake Push Service (FPS) for the notification E2E harness.

Stands in for a real browser push service (FCM / Mozilla / Apple) so the app server's real
pywebpush calls land somewhere we control. Each registered subscription carries the real EC
private key and auth secret the harness generated for it, so the FPS can decrypt the aes128gcm
body the same way a real client-side push subscription would, and expose the decrypted payload
plus headers and VAPID claims for assertions.

See tests/notif_e2e/CONTRACT.md for the authoritative interface this module implements.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

import http_ece
from aiohttp import web

# Never bind the real dev server's port.
_FORBIDDEN_PORT = 64728


@dataclass
class CapturedPush:
    """One WebPush request captured by the FPS, decrypted where possible."""

    sub_id: str
    method: str
    headers: dict[str, str]
    ttl: int | None
    urgency: str | None
    topic: str | None
    content_encoding: str
    vapid: dict[str, Any]
    payload: dict[str, Any] | None
    decrypt_error: str | None
    received_at: float = field(default_factory=time.monotonic)


def _b64url_decode(segment: str) -> bytes:
    """Decode an unpadded base64url string (JWT segments, VAPID/ECDH keys)."""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _parse_vapid(auth_header: str | None) -> dict[str, Any]:
    """Parse a VAPID Authorization header without verifying the JWT signature.

    Accepts both current ("vapid t=<jwt>,k=<key>") and legacy ("WebPush <jwt>") schemes.
    """
    result: dict[str, Any] = {
        "aud": None,
        "sub": None,
        "exp": None,
        "raw_jwt": None,
        "key": None,
    }
    if not auth_header:
        return result

    stripped = auth_header.strip()
    lowered = stripped.lower()
    jwt = None
    key = None

    if lowered.startswith("webpush "):
        jwt = stripped[len("WebPush ") :].strip()
    elif lowered.startswith("vapid "):
        rest = stripped[len("vapid ") :]
        for part in rest.split(","):
            part = part.strip()
            if part.startswith("t=") or part.startswith("t ="):
                jwt = part.split("=", 1)[1].strip()
            elif part.startswith("k=") or part.startswith("k ="):
                key = part.split("=", 1)[1].strip()

    result["raw_jwt"] = jwt
    result["key"] = key

    if jwt:
        try:
            parts = jwt.split(".")
            claims_bytes = _b64url_decode(parts[1])
            claims = json.loads(claims_bytes)
            result["aud"] = claims.get("aud")
            result["sub"] = claims.get("sub")
            result["exp"] = claims.get("exp")
        except Exception:
            # Malformed JWT: leave aud/sub/exp as None but keep raw_jwt/key for inspection.
            pass

    return result


class FakePushService:
    """A local aiohttp server that impersonates a browser push service.

    Subscription endpoints handed to the app point back at this service. When the app calls
    pywebpush, the encrypted request lands on POST /push/{sub_id}, gets captured and (if a
    matching subscription was registered) decrypted.
    """

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        self._requested_port = port
        self._port: int | None = None
        self._app = web.Application()
        self._app.router.add_post("/push/{sub_id}", self._handle_push)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        # sub_id -> (private_key, auth_secret)
        self._subs: dict[str, tuple[Any, bytes]] = {}
        # sub_id -> status code to respond with instead of 201
        self._dead: dict[str, int] = {}
        self._captured: list[CapturedPush] = []

    async def start(self) -> None:
        """Start the aiohttp server, picking a free ephemeral port if none was given."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        requested = self._requested_port if self._requested_port is not None else 0
        while True:
            site = web.TCPSite(self._runner, self._host, requested)
            await site.start()
            bound_port = self._runner.addresses[-1][1]
            if requested != 0 or bound_port != _FORBIDDEN_PORT:
                self._site = site
                self._port = bound_port
                return
            # Vanishingly unlikely ephemeral collision with the dev server port: retry.
            await site.stop()

    async def stop(self) -> None:
        """Gracefully shut down the server."""
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    @property
    def origin(self) -> str:
        """The scheme://host:port this service is listening on."""
        if self._port is None:
            raise RuntimeError("FakePushService has not been started")
        return f"http://{self._host}:{self._port}"

    def endpoint_for(self, sub_id: str) -> str:
        """The full push endpoint URL to hand out for a given subscription id."""
        return f"{self.origin}/push/{sub_id}"

    def register_subscription(
        self, sub_id: str, private_key: Any, auth_secret: bytes
    ) -> None:
        """Register the keys needed to decrypt pushes sent to this sub_id.

        private_key: a cryptography EC private key object (SECP256R1) whose public point is the
        subscription's p256dh. auth_secret: the 16 raw bytes behind the subscription's auth value.
        """
        self._subs[sub_id] = (private_key, auth_secret)

    def set_dead(self, sub_id: str, status: int = 410) -> None:
        """Make subsequent requests to this sub_id respond with `status` instead of 201."""
        self._dead[sub_id] = status

    def requests_for(self, sub_id: str) -> list[CapturedPush]:
        """All captured pushes for a given sub_id, in receipt order."""
        return [c for c in self._captured if c.sub_id == sub_id]

    def all_requests(self) -> list[CapturedPush]:
        """All captured pushes across every sub_id, in receipt order."""
        return list(self._captured)

    def clear(self) -> None:
        """Drop all recorded requests (call between scenarios)."""
        self._captured.clear()

    async def wait_for(
        self, sub_id: str, count: int = 1, timeout: float = 5.0
    ) -> list[CapturedPush]:
        """Poll until at least `count` requests for sub_id have been captured.

        Raises TimeoutError if the count is not reached within `timeout` seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            matches = self.requests_for(sub_id)
            if len(matches) >= count:
                return matches
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out after {timeout}s waiting for {count} push(es) to "
                    f"sub_id={sub_id!r}; got {len(matches)}"
                )
            await asyncio.sleep(0.05)

    async def _handle_push(self, request: web.Request) -> web.Response:
        sub_id = request.match_info["sub_id"]
        raw_body = await request.read()
        headers = {k.lower(): v for k, v in request.headers.items()}

        ttl: int | None = None
        if "ttl" in headers:
            try:
                ttl = int(headers["ttl"])
            except ValueError:
                ttl = None

        urgency = headers.get("urgency")
        topic = headers.get("topic")
        content_encoding = headers.get("content-encoding", "aes128gcm")
        vapid = _parse_vapid(headers.get("authorization"))

        payload: dict[str, Any] | None = None
        decrypt_error: str | None = None
        registered = self._subs.get(sub_id)
        if registered is None:
            decrypt_error = f"no registered subscription for sub_id={sub_id!r}"
        else:
            private_key, auth_secret = registered
            try:
                payload_bytes = http_ece.decrypt(
                    raw_body, private_key=private_key, auth_secret=auth_secret
                )
                payload = json.loads(payload_bytes)
            except Exception as exc:
                decrypt_error = f"{type(exc).__name__}: {exc}"

        self._captured.append(
            CapturedPush(
                sub_id=sub_id,
                method=request.method,
                headers=headers,
                ttl=ttl,
                urgency=urgency,
                topic=topic,
                content_encoding=content_encoding,
                vapid=vapid,
                payload=payload,
                decrypt_error=decrypt_error,
            )
        )

        status = self._dead.get(sub_id)
        if status is not None:
            return web.Response(status=status, body=b"")
        return web.Response(status=201)
