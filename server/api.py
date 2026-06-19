from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import logging
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DB_PATH = Path(__file__).resolve().parent / "data" / "hearts.db"
STATIC_DIR = Path(__file__).resolve().parent / "static"
UUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
CODE_RE = re.compile(r"^\d{6}$")

_rate_limits: dict[str, list[tuple[float, str]]] = defaultdict(list)
_rate_lock = threading.Lock()
RATE_LIMITS = {"create": (10, 3600), "pick": (600, 3600), "load": (600, 3600)}

_sync_tokens: dict[str, tuple[str, float]] = {}
_sync_lock = threading.Lock()
SYNC_TOKEN_TTL = 300
SYNC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# WebSocket connections: edit_code -> set of websockets
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
    return db


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = _get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            edit_code   TEXT PRIMARY KEY,
            share_code  TEXT UNIQUE NOT NULL,
            picks       TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_share_code ON sessions(share_code);
    """)
    pruned = db.execute(
        "DELETE FROM sessions WHERE updated_at < datetime('now', '-90 days')"
    ).rowcount
    db.commit()
    if pruned:
        print(f"Pruned {pruned} expired session(s).")
    db.close()


def _find_session(db: sqlite3.Connection, code: str) -> tuple[str, str, str, bool]:
    row = db.execute(
        "SELECT edit_code, share_code, picks FROM sessions WHERE edit_code = ?", (code,)
    ).fetchone()
    if row:
        return row[0], row[1], row[2], False
    row = db.execute(
        "SELECT edit_code, share_code, picks FROM sessions WHERE share_code = ?",
        (code,),
    ).fetchone()
    if row:
        return row[0], row[1], row[2], True
    raise HTTPException(404, "Session not found")


async def _broadcast(
    edit_code: str, picks: list, exclude: WebSocket | None = None
) -> None:
    msg = json.dumps({"picks": picks})
    clients = [
        ws for ws in list(_ws_clients.get(edit_code, set())) if ws is not exclude
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
    if dead and edit_code in _ws_clients:
        _ws_clients[edit_code] -= dead
        if not _ws_clients[edit_code]:
            del _ws_clients[edit_code]


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
                expired = [t for t, (_, exp) in _sync_tokens.items() if now >= exp]
                for t in expired:
                    del _sync_tokens[t]
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
                db.commit()
                if pruned:
                    logger.info("Pruned %d expired session(s)", pruned)
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to prune expired sessions")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    task = asyncio.create_task(_prune_rate_limits())
    prune_task = asyncio.create_task(_prune_expired_sessions())
    yield
    task.cancel()
    prune_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await prune_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.post("/api/session", status_code=201)
def create_session(request: Request):
    _check_rate(_get_client_ip(request), "create")
    db = _get_db()
    try:
        for _ in range(50):
            # 6-digit numeric codes: short enough for users to type in the sync PIN modal
            edit_code = f"{secrets.randbelow(1000000):06d}"
            share_code = f"{secrets.randbelow(1000000):06d}"
            if edit_code == share_code:
                continue
            existing = db.execute(
                "SELECT 1 FROM sessions WHERE edit_code IN (?,?) OR share_code IN (?,?)",
                (edit_code, share_code, edit_code, share_code),
            ).fetchone()
            if existing:
                continue
            try:
                db.execute(
                    "INSERT INTO sessions (edit_code, share_code) VALUES (?, ?)",
                    (edit_code, share_code),
                )
                db.commit()
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise HTTPException(503, "Could not generate unique session codes")
    finally:
        db.close()
    return {"edit_code": edit_code, "share_code": share_code}


@app.post("/api/session/{code}/sync-token", status_code=201)
def create_sync_token(code: str, request: Request):
    if not CODE_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    _check_rate(_get_client_ip(request), "create")
    db = _get_db()
    try:
        edit_code, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
    finally:
        db.close()
    token = secrets.token_urlsafe(16)
    with _sync_lock:
        _sync_tokens[token] = (edit_code, time.monotonic() + SYNC_TOKEN_TTL)
    return {"token": token}


@app.post("/api/sync/{token}")
def exchange_sync_token(token: str, request: Request):
    if not SYNC_TOKEN_RE.match(token):
        raise HTTPException(422, "Invalid token format")
    _check_rate(_get_client_ip(request), "load")
    with _sync_lock:
        entry = _sync_tokens.pop(token, None)
    if not entry:
        raise HTTPException(404, "Token not found or expired")
    edit_code, expiry = entry
    if time.monotonic() >= expiry:
        raise HTTPException(404, "Token not found or expired")
    db = _get_db()
    try:
        _, share_code, picks_json, _ = _find_session(db, edit_code)
        return {
            "picks": json.loads(picks_json),
            "readonly": False,
            "edit_code": edit_code,
            "share_code": share_code,
        }
    finally:
        db.close()


@app.get("/api/session/{code}")
def load_session(code: str, request: Request):
    if not CODE_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    _check_rate(_get_client_ip(request), "load")
    db = _get_db()
    try:
        edit_code, share_code, picks_json, readonly = _find_session(db, code)
        return {
            "picks": json.loads(picks_json),
            "readonly": readonly,
            "edit_code": edit_code if not readonly else None,
            "share_code": share_code,
        }
    finally:
        db.close()


@app.post("/api/session/{code}/pick/{artist_id}", status_code=204)
def add_pick(
    code: str, artist_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not CODE_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(artist_id):
        raise HTTPException(422, "Invalid artist ID format")
    _check_rate(_get_client_ip(request), "pick")
    db = _get_db()
    try:
        edit_code, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET picks = (
                SELECT json_group_array(value) FROM (
                    SELECT value FROM json_each(picks)
                    UNION SELECT ?
                )
            ), updated_at = datetime('now')
            WHERE edit_code = ?""",
            (artist_id, edit_code),
        )
        db.commit()
        picks = json.loads(
            db.execute(
                "SELECT picks FROM sessions WHERE edit_code = ?", (edit_code,)
            ).fetchone()[0]
        )
    finally:
        db.close()
    background_tasks.add_task(_broadcast, edit_code, picks)
    return Response(status_code=204)


@app.delete("/api/session/{code}/pick/{artist_id}", status_code=204)
def remove_pick(
    code: str, artist_id: str, request: Request, background_tasks: BackgroundTasks
):
    if not CODE_RE.match(code):
        raise HTTPException(422, "Invalid code format")
    if not UUID_RE.match(artist_id):
        raise HTTPException(422, "Invalid artist ID format")
    _check_rate(_get_client_ip(request), "pick")
    db = _get_db()
    try:
        edit_code, _, _, readonly = _find_session(db, code)
        if readonly:
            raise HTTPException(403, "Read-only session")
        db.execute(
            """UPDATE sessions SET picks = COALESCE(
                (SELECT json_group_array(value) FROM json_each(picks)
                 WHERE value != ?),
                '[]'
            ), updated_at = datetime('now')
            WHERE edit_code = ?""",
            (artist_id, edit_code),
        )
        db.commit()
        picks = json.loads(
            db.execute(
                "SELECT picks FROM sessions WHERE edit_code = ?", (edit_code,)
            ).fetchone()[0]
        )
    finally:
        db.close()
    background_tasks.add_task(_broadcast, edit_code, picks)
    return Response(status_code=204)


MAX_WS_PER_SESSION = 20


@app.websocket("/ws/{code}")
async def ws_sync(ws: WebSocket, code: str):
    await ws.accept()
    if not CODE_RE.match(code):
        await ws.close(code=1008)
        return
    db = _get_db()
    try:
        edit_code, _, picks_json, readonly = _find_session(db, code)
    except HTTPException:
        await ws.close(code=1008)
        return
    finally:
        db.close()

    clients = _ws_clients.setdefault(edit_code, set())
    if len(clients) >= MAX_WS_PER_SESSION:
        await ws.close(code=1013)
        return
    clients.add(ws)
    try:
        await ws.send_text(
            json.dumps({"picks": json.loads(picks_json), "readonly": readonly})
        )
        while True:
            await asyncio.wait_for(ws.receive_text(), timeout=3600)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if edit_code in _ws_clients:
            _ws_clients[edit_code].discard(ws)
            if not _ws_clients[edit_code]:
                del _ws_clients[edit_code]


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(path: str):
    raise HTTPException(404, "Not found")


(STATIC_DIR / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=str(STATIC_DIR / "photos")), name="photos")


@app.get("/{path:path}")
async def serve_index(path: str):
    file_path = STATIC_DIR / "index.html"
    if file_path.exists():
        return FileResponse(file_path)
    raise HTTPException(404, "Not found")
