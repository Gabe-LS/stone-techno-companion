from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
import logging
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

DB_PATH = Path(__file__).resolve().parent / "data" / "hearts.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"
UUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
PIN_RE = re.compile(r"^\d{6}$")

_rate_limits: dict[str, list[tuple[float, str]]] = defaultdict(list)
_rate_lock = threading.Lock()
RATE_LIMITS = {
    "create": (10, 3600),
    "pick": (600, 3600),
    "schedule": (600, 3600),
    "load": (600, 3600),
    "sync_pin": (12, 3600),
}

_sync_pins: dict[str, tuple[str, float]] = {}
_sync_lock = threading.Lock()
SYNC_PIN_TTL = 300
SESSION_COOKIE = "stc_session"
SESSION_COOKIE_MAX_AGE = 7776000

logger = logging.getLogger(__name__)


class _RedactTokensFilter(logging.Filter):
    # Keep access logs but strip secret tokens that ride in the request path:
    # the session token in the WebSocket URL and the magic-link token in the
    # verify URL. Without this they land in uvicorn's access log in plaintext.
    _PATTERNS = [
        re.compile(r"(/ws/)(?!chat/)[^/\s?]+"),
        re.compile(r"(/ws/chat/)[^/\s?]+"),
        re.compile(r"(/chat/v/)[^/\s?]+"),
        re.compile(r"([?&](?:token|code|id_token)=)[^&\s]+"),
    ]

    def _redact(self, value):
        if isinstance(value, str):
            for pat in self._PATTERNS:
                value = pat.sub(r"\1[REDACTED]", value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(self._redact(a) for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactTokensFilter())


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# WebSocket connections: session_id -> set of websockets
_ws_clients: dict[str, set[WebSocket]] = {}


def _check_rate(ip: str, key: str) -> None:
    limit, window = RATE_LIMITS[key]
    now = time.monotonic()
    with _rate_lock:
        entries = _rate_limits[ip]
        _rate_limits[ip] = [
            (t, k) for t, k in entries if now - t < RATE_LIMITS.get(k, (0, 3600))[1]
        ]
        count = sum(1 for t, k in _rate_limits[ip] if k == key)
        if count >= limit:
            raise HTTPException(
                429, "Rate limit exceeded", headers={"Retry-After": "60"}
            )
        _rate_limits[ip].append((now, key))


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = _get_db()
    # Migrate from old 6-digit code schema (must run before index creation)
    try:
        db.execute("ALTER TABLE sessions RENAME COLUMN edit_code TO session_id")
        db.commit()
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE sessions RENAME COLUMN share_code TO share_token")
        db.commit()
    except sqlite3.OperationalError:
        pass
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            share_token  TEXT UNIQUE NOT NULL,
            picks        TEXT NOT NULL DEFAULT '[]',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_share_token ON sessions(share_token);
    """)
    # Migrate: add schedule column if missing
    try:
        db.execute(
            "ALTER TABLE sessions ADD COLUMN schedule TEXT NOT NULL DEFAULT '[]'"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass
    db.executescript("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            endpoint    TEXT NOT NULL UNIQUE,
            p256dh      TEXT NOT NULL,
            auth        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_push_sub_session ON push_subscriptions(session_id);
        CREATE TABLE IF NOT EXISTS sent_notifications (
            session_id  TEXT NOT NULL,
            slot_id     TEXT NOT NULL,
            sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, slot_id)
        );
    """)
    pruned = db.execute(
        "DELETE FROM sessions WHERE updated_at < datetime('now', '-90 days')"
    ).rowcount
    db.commit()
    if pruned:
        logger.info("Pruned %d expired session(s)", pruned)
    db.close()


def _find_session(db: sqlite3.Connection, code: str) -> tuple[str, str, str, str, bool]:
    row = db.execute(
        "SELECT session_id, share_token, picks, schedule FROM sessions WHERE session_id = ?",
        (code,),
    ).fetchone()
    if row:
        return row[0], row[1], row[2], row[3], False
    row = db.execute(
        "SELECT session_id, share_token, picks, schedule FROM sessions WHERE share_token = ?",
        (code,),
    ).fetchone()
    if row:
        return row[0], row[1], row[2], row[3], True
    raise HTTPException(404, "Session not found")


async def _broadcast(
    session_id: str,
    picks: list,
    schedule: list | None = None,
    exclude: WebSocket | None = None,
) -> None:
    payload: dict = {"picks": picks}
    if schedule is not None:
        payload["schedule"] = schedule
    msg = json.dumps(payload)
    clients = [
        ws for ws in list(_ws_clients.get(session_id, set())) if ws is not exclude
    ]
    if not clients:
        return

    async def _send(ws: WebSocket) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_text(msg), timeout=5.0)
            return None
        except Exception:
            try:
                await ws.close()
            except Exception:
                pass
            return ws

    results = await asyncio.gather(*[_send(ws) for ws in clients])
    dead = {ws for ws in results if ws is not None}
    if dead and session_id in _ws_clients:
        _ws_clients[session_id] -= dead
        if not _ws_clients[session_id]:
            del _ws_clients[session_id]


async def _broadcast_sync_complete(session_id: str) -> None:
    msg = json.dumps({"sync_complete": True})
    clients = list(_ws_clients.get(session_id, set()))
    for ws in clients:
        try:
            await asyncio.wait_for(ws.send_text(msg), timeout=5.0)
        except Exception:
            pass


async def _prune_rate_limits() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            now = time.monotonic()
            with _rate_lock:
                stale = [
                    ip
                    for ip, entries in _rate_limits.items()
                    if all(now - t >= 3600 for t, _ in entries)
                ]
                for ip in stale:
                    del _rate_limits[ip]
            with _sync_lock:
                expired = [pin for pin, (_, exp) in _sync_pins.items() if now >= exp]
                for pin in expired:
                    del _sync_pins[pin]
        except Exception:
            logger.exception("Failed to prune rate limits")


async def _prune_expired_sessions() -> None:
    while True:
        await asyncio.sleep(86400)
        try:
            db = _get_db()
            try:
                pruned = db.execute(
                    "DELETE FROM sessions WHERE updated_at < datetime('now', '-90 days')"
                ).rowcount
                db.execute(
                    "DELETE FROM sent_notifications WHERE sent_at < datetime('now', '-7 days')"
                )
                db.commit()
                if pruned:
                    logger.info("Pruned %d expired session(s)", pruned)
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to prune expired sessions")


TIMETABLE_PATH = STATIC_DIR / "timetable.json"
_timetable: dict | None = None
_timetable_mtime: float = 0


def _load_timetable() -> dict | None:
    global _timetable, _timetable_mtime
    if not TIMETABLE_PATH.exists():
        return None
    mtime = TIMETABLE_PATH.stat().st_mtime
    if mtime != _timetable_mtime:
        _timetable = json.loads(TIMETABLE_PATH.read_text())
        _timetable_mtime = mtime
    return _timetable


async def _push_notification_scheduler() -> None:
    while True:
        await asyncio.sleep(60)
        if not os.environ.get("VAPID_PRIVATE_KEY"):
            continue
        try:
            timetable = _load_timetable()
            if not timetable:
                continue

            tz = ZoneInfo(timetable["timezone"])
            now = datetime.now(tz)
            window_start = now + timedelta(minutes=9, seconds=30)
            window_end = now + timedelta(minutes=10, seconds=30)

            due_slots: list[tuple[str, dict]] = []
            for slot_id, slot in timetable["slots"].items():
                start_dt = datetime.fromisoformat(slot["start"])
                start = (
                    start_dt.replace(tzinfo=tz)
                    if start_dt.tzinfo is None
                    else start_dt.astimezone(tz)
                )
                if window_start <= start <= window_end:
                    due_slots.append((slot_id, slot))

            if not due_slots:
                continue

            db = _get_db()
            try:
                slot_ids = [s[0] for s in due_slots]
                placeholders = ",".join("?" * len(slot_ids))
                rows = db.execute(
                    f"SELECT DISTINCT s.session_id, je.value as slot_id "
                    f"FROM sessions s, json_each(s.schedule) je "
                    f"WHERE je.value IN ({placeholders})",
                    slot_ids,
                ).fetchall()

                if not rows:
                    continue

                to_send: list[tuple[str, str]] = []
                for session_id, slot_id in rows:
                    sent = db.execute(
                        "SELECT 1 FROM sent_notifications WHERE session_id = ? AND slot_id = ?",
                        (session_id, slot_id),
                    ).fetchone()
                    if not sent:
                        to_send.append((session_id, slot_id))

                if not to_send:
                    continue

                from pywebpush import WebPushException, webpush

                slot_map = dict(due_slots)
                for session_id, slot_id in to_send:
                    try:
                        subs = db.execute(
                            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE session_id = ?",
                            (session_id,),
                        ).fetchall()
                        if not subs:
                            continue

                        slot = slot_map[slot_id]
                        artists = " b2b ".join(slot["artists"])
                        payload = json.dumps(
                            {
                                "title": f"{artists} starts in 10 min",
                                "body": f"{slot['floor']}, {slot['start_hhmm']}–{slot['end_hhmm']}",
                                "url": "/?view=timetable",
                                "push_id": secrets.token_hex(8),
                            }
                        )
                        vapid_claims = {
                            "sub": os.environ.get(
                                "VAPID_CLAIMS_EMAIL", "mailto:noreply@example.com"
                            )
                        }
                        any_sent = False
                        for endpoint, p256dh, auth in subs:
                            try:
                                await asyncio.to_thread(
                                    webpush,
                                    subscription_info={
                                        "endpoint": endpoint,
                                        "keys": {"p256dh": p256dh, "auth": auth},
                                    },
                                    data=payload,
                                    vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                                    # Fresh copy per endpoint: pywebpush mutates
                                    # the claims dict (sets aud from the first
                                    # endpoint), which breaks pushes to any other
                                    # push service in the same loop.
                                    vapid_claims=dict(vapid_claims),
                                    # pywebpush defaults to TTL=0 (deliver
                                    # instantly or discard) -- a briefly
                                    # disconnected browser loses the push.
                                    ttl=300,
                                    timeout=10,
                                )
                                any_sent = True
                            except WebPushException as e:
                                if e.response and e.response.status_code in (404, 410):
                                    db.execute(
                                        "DELETE FROM push_subscriptions WHERE endpoint = ?",
                                        (endpoint,),
                                    )
                                    db.commit()
                                logger.warning(
                                    "Push failed for %s: %s", endpoint[:60], e
                                )
                            except Exception as e:
                                # Raw network errors (timeout, ConnectionError) are
                                # not wrapped in WebPushException. Swallow per-endpoint
                                # so one endpoint's failure can't abort the block and
                                # skip the dedup INSERT below (causing re-sends).
                                logger.warning(
                                    "Push error for %s: %s", endpoint[:60], e
                                )

                        if any_sent:
                            db.execute(
                                "INSERT OR IGNORE INTO sent_notifications (session_id, slot_id) VALUES (?, ?)",
                                (session_id, slot_id),
                            )
                            db.commit()
                            logger.info(
                                "Sent push for %s to session %s",
                                artists,
                                session_id[:8],
                            )
                    except Exception:
                        logger.exception(
                            "push: failed for session %s slot %s", session_id, slot_id
                        )
                        continue
            finally:
                db.close()
        except Exception:
            logger.exception("Push notification scheduler error")


def _check_vapid_key_consistency() -> None:
    # FCM rejects pushes (403) when the signing key differs from the
    # applicationServerKey clients subscribed with; Apple does not enforce the
    # binding, so a mismatch breaks Chromium-family push while iOS keeps
    # working -- invisible unless checked here.
    private = os.environ.get("VAPID_PRIVATE_KEY")
    public = os.environ.get("VAPID_PUBLIC_KEY")
    if not private or not public:
        return
    try:
        from py_vapid import Vapid
        from py_vapid.utils import b64urlencode
        from cryptography.hazmat.primitives import serialization

        v = (
            Vapid.from_file(private)
            if os.path.isfile(private)
            else Vapid.from_pem(private.encode())
        )
        raw = v.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        derived = b64urlencode(raw)
        if derived != public.strip().rstrip("="):
            logger.error(
                "VAPID key mismatch: VAPID_PRIVATE_KEY does not pair with "
                "VAPID_PUBLIC_KEY (derived %s..., configured %s...). Push to "
                "FCM endpoints will fail with 403.",
                derived[:16],
                public[:16],
            )
        else:
            logger.info("VAPID key pair verified: private key matches public key")
    except Exception:
        logger.exception("VAPID key consistency check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    _check_vapid_key_consistency()
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning(
            "OPENAI_API_KEY not set — AI moderation layers 2 and 3 are DISABLED; "
            "only the local word filter is active for moderated rooms"
        )
    else:
        logger.info("OPENAI_API_KEY present — AI moderation enabled")
    task = asyncio.create_task(_prune_rate_limits())
    prune_task = asyncio.create_task(_prune_expired_sessions())
    push_task = asyncio.create_task(_push_notification_scheduler())
    chat_purge_task = None
    if _chat_purge_coro:
        chat_purge_task = asyncio.create_task(_chat_purge_coro())
    yield
    task.cancel()
    prune_task.cancel()
    push_task.cancel()
    if chat_purge_task:
        chat_purge_task.cancel()
    for t in [task, prune_task, push_task] + (
        [chat_purge_task] if chat_purge_task else []
    ):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)

# Hard ceiling above the largest legitimate upload (100MB video). Rejects
# oversized bodies by Content-Length before FastAPI parses/spools them, so a
# multi-GB body can't exhaust disk/memory. Per-endpoint limits still enforce
# exact sizes on the actual bytes.
_MAX_BODY_BYTES = 110 * 1024 * 1024


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _MAX_BODY_BYTES:
        return Response("Payload too large", status_code=413)
    return await call_next(request)


@app.post("/api/session", status_code=201)
def create_session(request: Request, response: Response):
    _check_rate(_get_client_ip(request), "create")
    session_id = secrets.token_urlsafe(16)
    share_token = secrets.token_urlsafe(16)
    db = _get_db()
    try:
        db.execute(
            "INSERT INTO sessions (session_id, share_token) VALUES (?, ?)",
            (session_id, share_token),
        )
        db.commit()
    finally:
        db.close()
    _set_session_cookie(response, session_id)
    return {"session_id": session_id, "share_token": share_token}


@app.post("/api/session/{code}/sync-pin", status_code=201)
def create_sync_pin(code: str, request: Request):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    _check_rate(_get_client_ip(request), "load")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
    finally:
        db.close()
    now = time.monotonic()
    with _sync_lock:
        old = [p for p, (sid, _) in _sync_pins.items() if sid == session_id]
        for p in old:
            del _sync_pins[p]
        for _ in range(50):
            pin = f"{secrets.randbelow(1000000):06d}"
            existing = _sync_pins.get(pin)
            if existing and now < existing[1]:
                continue
            _sync_pins[pin] = (session_id, now + SYNC_PIN_TTL)
            return {"pin": pin}
    raise HTTPException(503, "Could not generate unique sync PIN")


@app.post("/api/sync/{pin}")
def exchange_sync_pin(
    pin: str, request: Request, response: Response, background_tasks: BackgroundTasks
):
    if not PIN_RE.match(pin):
        raise HTTPException(422, "Invalid PIN format")
    _check_rate(_get_client_ip(request), "sync_pin")
    with _sync_lock:
        entry = _sync_pins.pop(pin, None)
    if not entry:
        raise HTTPException(404, "PIN not found or expired")
    session_id, expiry = entry
    if time.monotonic() >= expiry:
        raise HTTPException(404, "PIN not found or expired")
    db = _get_db()
    try:
        _, share_token, picks_json, schedule_json, _ = _find_session(db, session_id)
        _set_session_cookie(response, session_id)
        background_tasks.add_task(_broadcast_sync_complete, session_id)
        return {
            "picks": json.loads(picks_json),
            "schedule": json.loads(schedule_json),
            "readonly": False,
            "session_id": session_id,
            "share_token": share_token,
        }
    finally:
        db.close()


@app.get("/api/me")
def get_me(request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id or not TOKEN_RE.match(session_id):
        raise HTTPException(401, "No session")
    _check_rate(_get_client_ip(request), "load")
    db = _get_db()
    try:
        _, share_token, picks_json, schedule_json, _ = _find_session(db, session_id)
        _set_session_cookie(response, session_id)
        return {
            "picks": json.loads(picks_json),
            "schedule": json.loads(schedule_json),
            "readonly": False,
            "session_id": session_id,
            "share_token": share_token,
        }
    finally:
        db.close()


@app.get("/api/session/{code}")
def load_session(code: str, request: Request, response: Response):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    _check_rate(_get_client_ip(request), "load")
    db = _get_db()
    try:
        session_id, share_token, picks_json, schedule_json, readonly = _find_session(
            db, code
        )
        if not readonly:
            _set_session_cookie(response, session_id)
        return {
            "picks": json.loads(picks_json),
            "schedule": json.loads(schedule_json),
            "readonly": readonly,
            "session_id": session_id if not readonly else None,
            "share_token": share_token,
        }
    finally:
        db.close()


@app.post("/api/session/{code}/pick/{artist_id}", status_code=204)
def add_pick(
    code: str, artist_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(artist_id):
        raise HTTPException(422, "Invalid artist ID format")
    _check_rate(_get_client_ip(request), "pick")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET picks = (
                SELECT json_group_array(value) FROM (
                    SELECT value FROM json_each(picks)
                    UNION SELECT ?
                )
            ), updated_at = datetime('now')
            WHERE session_id = ?""",
            (artist_id, session_id),
        )
        db.commit()
        picks = json.loads(
            db.execute(
                "SELECT picks FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        )
    finally:
        db.close()
    background_tasks.add_task(_broadcast, session_id, picks)
    return Response(status_code=204)


@app.delete("/api/session/{code}/pick/{artist_id}", status_code=204)
def remove_pick(
    code: str, artist_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(artist_id):
        raise HTTPException(422, "Invalid artist ID format")
    _check_rate(_get_client_ip(request), "pick")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET picks = COALESCE(
                (SELECT json_group_array(value) FROM json_each(picks)
                 WHERE value != ?),
                '[]'
            ), updated_at = datetime('now')
            WHERE session_id = ?""",
            (artist_id, session_id),
        )
        db.commit()
        picks = json.loads(
            db.execute(
                "SELECT picks FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        )
    finally:
        db.close()
    background_tasks.add_task(_broadcast, session_id, picks)
    return Response(status_code=204)


@app.post("/api/session/{code}/schedule/{slot_id}", status_code=204)
def add_schedule(
    code: str, slot_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(slot_id):
        raise HTTPException(422, "Invalid slot ID format")
    _check_rate(_get_client_ip(request), "schedule")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET schedule = (
                SELECT json_group_array(value) FROM (
                    SELECT value FROM json_each(schedule)
                    UNION SELECT ?
                )
            ), updated_at = datetime('now')
            WHERE session_id = ?""",
            (slot_id, session_id),
        )
        db.commit()
        row = db.execute(
            "SELECT picks, schedule FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        picks = json.loads(row[0])
        schedule = json.loads(row[1])
    finally:
        db.close()
    background_tasks.add_task(_broadcast, session_id, picks, schedule)
    return Response(status_code=204)


@app.delete("/api/session/{code}/schedule/{slot_id}", status_code=204)
def remove_schedule(
    code: str, slot_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(slot_id):
        raise HTTPException(422, "Invalid slot ID format")
    _check_rate(_get_client_ip(request), "schedule")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET schedule = COALESCE(
                (SELECT json_group_array(value) FROM json_each(schedule)
                 WHERE value != ?),
                '[]'
            ), updated_at = datetime('now')
            WHERE session_id = ?""",
            (slot_id, session_id),
        )
        db.commit()
        row = db.execute(
            "SELECT picks, schedule FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        picks = json.loads(row[0])
        schedule = json.loads(row[1])
    finally:
        db.close()
    background_tasks.add_task(_broadcast, session_id, picks, schedule)
    return Response(status_code=204)


MAX_WS_PER_SESSION = 20


@app.websocket("/ws/{code}")
async def ws_sync(ws: WebSocket, code: str):
    await ws.accept()
    if not TOKEN_RE.match(code):
        await ws.close(code=1008)
        return
    db = _get_db()
    try:
        session_id, _, picks_json, schedule_json, readonly = _find_session(db, code)
    except HTTPException:
        await ws.close(code=1008)
        return
    finally:
        db.close()

    clients = _ws_clients.setdefault(session_id, set())
    if len(clients) >= MAX_WS_PER_SESSION:
        await ws.close(code=1013)
        return
    clients.add(ws)
    try:
        await ws.send_text(
            json.dumps(
                {
                    "picks": json.loads(picks_json),
                    "schedule": json.loads(schedule_json),
                    "readonly": readonly,
                }
            )
        )
        while True:
            await asyncio.wait_for(ws.receive_text(), timeout=3600)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if session_id in _ws_clients:
            _ws_clients[session_id].discard(ws)
            if not _ws_clients[session_id]:
                del _ws_clients[session_id]


@app.get("/api/push/vapid-key")
def get_vapid_key():
    key = os.environ.get("VAPID_PUBLIC_KEY")
    if not key:
        raise HTTPException(501, "Push notifications not configured")
    return {"public_key": key}


_ALLOWED_PUSH_HOST_SUFFIXES = (
    ".googleapis.com",  # FCM (Chrome / Brave / Chromium Edge)
    ".push.services.mozilla.com",  # Firefox
    ".push.apple.com",  # Safari / iOS
    ".notify.windows.com",  # WNS (legacy Edge)
)


def _is_valid_push_endpoint(endpoint: str) -> bool:
    from urllib.parse import urlparse

    try:
        p = urlparse(endpoint)
    except ValueError:
        return False
    if p.scheme != "https" or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host.endswith(s) for s in _ALLOWED_PUSH_HOST_SUFFIXES)


@app.post("/api/session/{code}/push/subscribe", status_code=204)
async def push_subscribe(code: str, request: Request):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    _check_rate(_get_client_ip(request), "pick")
    body = await request.json()
    endpoint = body.get("endpoint", "")
    keys = body.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        raise HTTPException(422, "Missing subscription fields")
    if not _is_valid_push_endpoint(endpoint):
        raise HTTPException(422, "Invalid push endpoint")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            "INSERT INTO push_subscriptions (session_id, endpoint, p256dh, auth) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET session_id=excluded.session_id, "
            "p256dh=excluded.p256dh, auth=excluded.auth, created_at=datetime('now')",
            (session_id, endpoint, p256dh, auth),
        )
        db.commit()
    finally:
        db.close()
    return Response(status_code=204)


@app.delete("/api/session/{code}/push/subscribe", status_code=204)
async def push_unsubscribe(code: str, request: Request):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    body = await request.json()
    endpoint = body.get("endpoint", "")
    if not endpoint:
        raise HTTPException(422, "Missing endpoint")
    db = _get_db()
    try:
        session_id, _, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            "DELETE FROM push_subscriptions WHERE session_id = ? AND endpoint = ?",
            (session_id, endpoint),
        )
        db.commit()
    finally:
        db.close()
    return Response(status_code=204)


@app.get("/api/session/{code}/push/status")
def push_status(code: str, request: Request):
    if not TOKEN_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    db = _get_db()
    try:
        session_id, _, _, _, _ = _find_session(db, code)
        count = db.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    finally:
        db.close()
    return {"subscribed": count > 0}


@app.get("/ics/{slot_id}")
def generate_ics(slot_id: str):
    timetable = _load_timetable()
    if not timetable:
        raise HTTPException(404, "Timetable not available")
    slot = timetable.get("slots", {}).get(slot_id)
    if not slot:
        raise HTTPException(404, "Slot not found")
    name = " b2b ".join(slot["artists"])
    floor = slot["floor"]
    start = slot["start"]
    end = slot["end"]

    tz_name = timetable.get("timezone") or "Europe/Berlin"
    try:
        event_tz = ZoneInfo(tz_name)
    except Exception:
        event_tz = ZoneInfo("Europe/Berlin")

    def _ics_esc(s: str) -> str:
        return (
            s.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\r", "\\n")
            .replace("\n", "\\n")
        )

    def to_utc_ics_dt(iso: str) -> str:
        # Parse the naive local wall-clock slot time, attach the event's
        # configured timezone, and convert to UTC — avoids hardcoding a
        # VTIMEZONE that only covers Europe/Berlin.
        clean = iso.replace("-", "").replace(":", "")
        clean = re.sub(r"[+-]\d{4}$", "", clean).rstrip("Z")
        t_idx = clean.find("T")
        if not (t_idx >= 0 and len(clean) - t_idx - 1 >= 6):
            clean = clean + "00"
        naive = datetime.strptime(clean, "%Y%m%dT%H%M%S")
        local_dt = naive.replace(tzinfo=event_tz)
        utc_dt = local_dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")

    dt_start = to_utc_ics_dt(start)
    dt_end = to_utc_ics_dt(end)
    uid = (
        dt_start + "-" + re.sub(r"[^a-zA-Z0-9]", "", name) + "@stonetechno.deftlab.dev"
    )
    stamp = datetime.now(tz=ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Stone Techno Companion//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"DTSTART:{dt_start}",
            f"DTEND:{dt_end}",
            f"DTSTAMP:{stamp}",
            f"UID:{uid}",
            f"SUMMARY:{_ics_esc(name)}",
            f"LOCATION:{_ics_esc(floor)}\\, Stone Techno 2026",
            "BEGIN:VALARM",
            "TRIGGER:-PT10M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_esc(name)} starts in 10 minutes",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    filename = re.sub(r"[^a-zA-Z0-9 ]", "", name).replace(" ", "_") + ".ics"
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Public transport (Tram 107 / NE2 departure board at /transport) ---
# The VRR EFA API has no CORS headers, so the page polls this proxy for
# realtime data. The stop is pinned server-side (never client-controlled), so
# this cannot be used as an open proxy to EFA. A short shared cache collapses
# all concurrent clients into ~1 upstream call per minute bucket: on festival
# nights hundreds of phones poll every 30s and must not hammer public infra.

_EFA_BASE = "https://efa.vrr.de/vrr/XSLT_DM_REQUEST"
_TRANSPORT_STOP = "20009206"  # Essen Zollverein
# Stop coordinates, matching timetable-transport.json (walk-route destination)
_TRANSPORT_STOP_LAT = 51.486095
_TRANSPORT_STOP_LNG = 7.046062
_TRANSPORT_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TRANSPORT_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_transport_cache: dict[tuple[str, str], tuple[float, list]] = {}
_transport_rate: dict[str, list[float]] = {}


def _check_transport_rate(request: Request, limit: int, window: float = 60.0) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = [t for t in _transport_rate.get(ip, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many requests")
    hits.append(now)
    _transport_rate[ip] = hits
    if len(_transport_rate) > 5000:
        for k in [
            k for k, v in _transport_rate.items() if all(now - t >= window for t in v)
        ]:
            del _transport_rate[k]


@app.get("/api/transport/departures")
async def transport_departures(request: Request, date: str):
    t = request.query_params.get("time") or ""
    if not _TRANSPORT_DATE_RE.match(date) or not _TRANSPORT_TIME_RE.match(t):
        raise HTTPException(400, "date (DD.MM.YYYY) and time (HH:MM) required")
    _check_transport_rate(request, limit=30)

    key = (date, t)
    now = time.monotonic()
    cached = _transport_cache.get(key)
    if cached and now - cached[0] < 55:
        return {"departures": cached[1], "ts": datetime.now(timezone.utc).isoformat()}

    import httpx

    params = {
        "outputFormat": "JSON",
        "language": "de",
        "stateless": "1",
        "type_dm": "stop",
        "name_dm": _TRANSPORT_STOP,
        "mode": "direct",
        "useRealtime": "1",
        "itdDateDayMonthYear": date,
        "itdTime": t,
        "limit": "30",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                _EFA_BASE, params=params, headers={"User-Agent": "Mozilla/5.0"}
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        # Serve a stale cache entry rather than nothing if upstream hiccups
        if cached:
            return {
                "departures": cached[1],
                "ts": datetime.now(timezone.utc).isoformat(),
                "stale": True,
            }
        raise HTTPException(502, "Departure service unavailable")

    deps = []
    for d in data.get("departureList") or []:
        line = d.get("servingLine", {})
        num = line.get("number")
        if num not in ("107", "NE2"):
            continue
        direction = line.get("direction") or ""
        if "Bredeney" not in direction and "Hauptbahnhof" not in direction:
            continue
        dt = d.get("dateTime") or {}
        try:
            entry = {
                "line": num,
                "direction": direction,
                "platform": d.get("platform"),
                "scheduled": f"{int(dt['hour']):02d}:{int(dt['minute']):02d}",
                "scheduledDate": f"{int(dt['day']):02d}.{int(dt['month']):02d}.{dt['year']}",
                "realtime": line.get("realtime") == "1",
            }
        except (KeyError, ValueError, TypeError):
            continue
        rt = d.get("realDateTime")
        if rt:
            try:
                entry["real"] = f"{int(rt['hour']):02d}:{int(rt['minute']):02d}"
            except (KeyError, ValueError, TypeError):
                pass
        if line.get("delay") is not None:
            try:
                entry["delay"] = int(line["delay"])
            except (ValueError, TypeError):
                pass
        if d.get("realtimeTripStatus"):
            entry["status"] = d["realtimeTripStatus"]
        if d.get("countdown") is not None:
            try:
                entry["countdown"] = int(d["countdown"])
            except (ValueError, TypeError):
                pass
        deps.append(entry)

    _transport_cache[key] = (now, deps)
    if len(_transport_cache) > 50:
        oldest = min(_transport_cache, key=lambda k: _transport_cache[k][0])
        del _transport_cache[oldest]
    return {"departures": deps, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/transport/walk")
async def transport_walk(request: Request, lat: float, lng: float):
    """Foot-routing proxy to OSRM so the client never sends GPS coordinates to
    a third party directly (OSRM sees only the VPS IP). Coordinates are used
    transiently and never stored or logged. Bounded to the Essen area so this
    cannot be abused as a generic routing proxy."""
    if abs(lat - _TRANSPORT_STOP_LAT) > 0.35 or abs(lng - _TRANSPORT_STOP_LNG) > 0.5:
        raise HTTPException(400, "Out of service area")
    _check_transport_rate(request, limit=10)

    import httpx

    # FOSSGIS OSRM with a REAL foot profile. The project-osrm.org demo server
    # only hosts the car dataset and silently routes "foot" as driving
    # (5.5 km / 9 min for a 50-minute walk), so never use it here.
    url = (
        f"https://routing.openstreetmap.de/routed-foot/route/v1/foot/"
        f"{lng},{lat};{_TRANSPORT_STOP_LNG},{_TRANSPORT_STOP_LAT}?overview=false"
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            d = r.json()
    except Exception:
        raise HTTPException(502, "Routing service unavailable")
    if d.get("code") != "Ok" or not d.get("routes"):
        raise HTTPException(502, "No route found")
    return {
        "distanceM": d["routes"][0]["distance"],
        "durationS": d["routes"][0]["duration"],
    }


@app.get("/public-transport")
async def public_transport_redirect():
    # Old URL of the transport page; permanent redirect keeps old links alive
    return RedirectResponse("/transport", status_code=301)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(path: str):
    raise HTTPException(404, "Not found")


(STATIC_DIR / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=str(STATIC_DIR / "photos")), name="photos")
(STATIC_DIR / "thumbs").mkdir(parents=True, exist_ok=True)
app.mount("/thumbs", StaticFiles(directory=str(STATIC_DIR / "thumbs")), name="thumbs")
# Self-hosted third-party assets (e.g. Leaflet for the meetup map picker) so the
# app has no external CDN dependency and works on venue wifi / offline.
(STATIC_DIR / "vendor").mkdir(parents=True, exist_ok=True)
app.mount("/vendor", StaticFiles(directory=str(STATIC_DIR / "vendor")), name="vendor")


@app.get("/favicon.svg")
async def serve_favicon_svg():
    file_path = STATIC_DIR / "favicon.svg"
    if file_path.exists():
        return FileResponse(file_path, media_type="image/svg+xml")
    raise HTTPException(404, "Not found")


@app.get("/favicon.png")
async def serve_favicon_png():
    file_path = STATIC_DIR / "favicon.png"
    if file_path.exists():
        return FileResponse(file_path, media_type="image/png")
    raise HTTPException(404, "Not found")


@app.get("/manifest.json")
async def serve_manifest():
    file_path = STATIC_DIR / "manifest.json"
    if file_path.exists():
        return FileResponse(
            file_path,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )
    raise HTTPException(404, "Not found")


@app.get("/icon-chat.png")
async def serve_icon_chat():
    # Home-screen icon for installs made from the chat page. Same single app
    # (chat links the shared /manifest.json, start_url '/'), just a prettier
    # icon for that install path.
    file_path = STATIC_DIR / "icon-chat.png"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(404, "Not found")


@app.get("/sw.js")
async def serve_sw():
    file_path = STATIC_DIR / "sw.js"
    if file_path.exists():
        return FileResponse(
            file_path,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )
    raise HTTPException(404, "Not found")


@app.get("/bios.json")
async def serve_bios():
    file_path = STATIC_DIR / "bios.json"
    if file_path.exists():
        return FileResponse(file_path, media_type="application/json")
    raise HTTPException(404, "Not found")


@app.get("/timetable-transport.json")
async def serve_timetable_transport():
    # Tram 107 / NE2 schedule for /transport. no-cache so a schedule
    # regeneration (pipeline/transport/capture-api.mjs + git pull) is picked
    # up without a container rebuild.
    file_path = STATIC_DIR / "timetable-transport.json"
    if file_path.exists():
        return FileResponse(
            file_path,
            media_type="application/json",
            headers={"Cache-Control": "no-cache"},
        )
    raise HTTPException(404, "Not found")


@app.get("/shared.css")
async def serve_shared_css():
    file_path = STATIC_DIR / "shared.css"
    if file_path.exists():
        return FileResponse(
            file_path, media_type="text/css", headers={"Cache-Control": "no-cache"}
        )
    raise HTTPException(404, "Not found")


@app.get("/shared.js")
async def serve_shared_js():
    file_path = STATIC_DIR / "shared.js"
    if file_path.exists():
        return FileResponse(
            file_path,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )
    raise HTTPException(404, "Not found")


_DOP_CACHE = STATIC_DIR / "vendor" / "dop-cache"


@app.get("/dop/{z}/{x}/{y}.jpg")
async def dop_tile(z: int, x: int, y: int):
    """XYZ raster proxy for the NRW open aerial (Geobasis NRW WMS, dl-de/by-2-0).
    Renders each web-mercator tile via WMS and caches it to disk, so MapLibre can
    use the aerial as a normal tile source and repeat views are instant/offline.
    Guarded to NRW zoom/extent so it can't be abused to mirror all of Germany."""
    import math

    n = 2**z
    if not (12 <= z <= 20 and 0 <= x < n and 0 <= y < n):
        raise HTTPException(404, "Out of range")
    # tile centre lat/lng, restrict to the NRW region (outside = blank anyway)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    if not (50.0 <= lat <= 52.7 and 5.7 <= lon <= 9.6):
        raise HTTPException(404, "Outside NRW")

    fpath = _DOP_CACHE / str(z) / str(x) / f"{y}.jpg"
    cache_headers = {"Cache-Control": "public, max-age=2592000"}
    if fpath.is_file():
        return FileResponse(fpath, media_type="image/jpeg", headers=cache_headers)

    world = 20037508.342789244
    tile_m = (2 * world) / n
    minx = -world + x * tile_m
    maxy = world - y * tile_m
    maxx = minx + tile_m
    miny = maxy - tile_m
    url = (
        "https://www.wms.nrw.de/geobasis/wms_nw_dop?SERVICE=WMS&VERSION=1.3.0"
        "&REQUEST=GetMap&LAYERS=nw_dop_rgb&STYLES=&CRS=EPSG:3857"
        f"&BBOX={minx},{miny},{maxx},{maxy}&WIDTH=256&HEIGHT=256&FORMAT=image/jpeg"
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image"):
                raise HTTPException(502, "Tile source error")
            data = r.content
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Tile fetch failed")
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(data)
    return FileResponse(fpath, media_type="image/jpeg", headers=cache_headers)


from chat_api import mount_chat

_chat_purge_coro = mount_chat(app)


@app.get("/line-up")
@app.get("/timetable")
@app.get("/{path:path}")
async def serve_index(path: str = ""):
    if path.startswith("chat"):
        raise HTTPException(404, "Not found")
    # Clean-URL static pages: /transport -> static/pages/transport.html.
    # The slug regex forbids slashes and dots, so the lookup is traversal-safe;
    # anything without a matching page file falls through to the SPA below.
    if path and re.fullmatch(r"[a-z0-9][a-z0-9-]*", path):
        page = STATIC_DIR / "pages" / f"{path}.html"
        if page.is_file():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
    file_path = STATIC_DIR / "index.html"
    if file_path.exists():
        return FileResponse(file_path, headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "Not found")
