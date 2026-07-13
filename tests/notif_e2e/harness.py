"""Module B -- isolated server lifecycle + fixtures for the notif_e2e harness.

Spins up a real, isolated copy of services/companion/api.py (uvicorn subprocess) against
scratch chat.db and hearts.db files, with a freshly generated VAPID keypair,
and exposes direct-SQL fixture helpers plus a small `websockets`-based chat
client (WSClient) for driving senders/recipients in scenarios.

Reuses the proven patterns in tests/e2ee_browser_check.py: get_free_port,
subprocess-based uvicorn startup with a stdout reader thread, sensitive-env
stripping, and a wait-for-ready HTTP poll against /chat/api/config.

One deliberate departure from that file: services/companion/api.py's hearts.db path
(`DB_PATH = Path(__file__).resolve().parent / "data" / "hearts.db"`) is
hardcoded relative to the source file, with no environment override (unlike
chat_db.CHAT_DB_PATH). tests/e2ee_browser_check.py never touches it -- it
deliberately keeps VAPID_PRIVATE_KEY unset so push code short-circuits before
touching hearts.db. This harness must exercise real push, including lineup
push, so it cannot dodge the same way. Instead, NotifServer.start() copies
services/companion/ into a scratch directory (excluding data/, chat/uploads, chat/tmp,
__pycache__, *.pem, .env*) and launches uvicorn with that directory as its
cwd, so `Path(__file__).resolve().parent / "data"` resolves inside the
scratch tree instead of the real repo's services/companion/data/. This requires no edits
to any existing file -- only copying real files into a new location this
harness creates and owns.

Python 3.14. No new dependencies: only stdlib, cryptography, websockets,
httpx (all confirmed installed per tests/notif_e2e/CONTRACT.md).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "services" / "companion"

# Stripped from the scratch server's environment so the isolated instance can
# never accidentally talk to real third-party services (OpenAI moderation,
# Maileroo email, Google OAuth) or reuse real VAPID/admin credentials. VAPID_*
# are stripped here and then re-set by NotifServer.start() to the freshly
# generated keypair for this instance.
SENSITIVE_ENV_KEYS = [
    "OPENAI_API_KEY",
    "MAILEROO_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "VAPID_PRIVATE_KEY",
    "VAPID_PUBLIC_KEY",
    "VAPID_CLAIMS_EMAIL",
    "CHAT_ADMIN_EMAILS",
    "CHAT_ADMIN_TOKEN",
]

# Copied into the scratch server dir verbatim (as symlinks, not dereferenced
# -- see _prepare_scratch_server_dir); excluded so each instance starts with
# empty runtime state and no leaked local secrets/certs.
_COPY_IGNORE = shutil.ignore_patterns(
    "data",
    "uploads",
    "tmp",
    "__pycache__",
    "*.pem",
    ".env",
    ".env.*",
    "*.pyc",
    # services/companion/static/ holds symlinks into ../../../services/data/output/ (photos, thumbs,
    # bios.json, index.html, timetable.json). In a scratch copy those dangle,
    # and a dangling `photos`/`thumbs` symlink makes api.py's
    # mkdir(exist_ok=True) raise FileExistsError. Drop them; api.py recreates
    # photos/ and thumbs/ as real dirs.
    "photos",
    "thumbs",
    "bios.json",
    "index.html",
    "timetable.json",
)


def get_free_port() -> int:
    """Return an OS-assigned free ephemeral TCP port on 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _b64url(data: bytes) -> str:
    """Unpadded base64url encoding, as used by WebPush p256dh/auth/VAPID."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    """Mirror services/companion/chat_db.py's hash_token: sessions.token stores only a
    SHA-256 hash at rest (since commit 8be87cf, "hash session and magic-link
    tokens at rest"). The harness inserts sessions directly into the scratch
    DB, so it must hash here too -- the raw token is what goes over the wire
    (cookie / WS URL path segment) and services/companion/chat_db.py's get_user_by_token
    hashes the incoming token before comparing against this column."""
    return hashlib.sha256(token.encode()).hexdigest()


def gen_vapid_keys() -> dict:
    """Generate a fresh VAPID P-256 keypair for one isolated server instance.

    Returns {"private_pem": str, "public_b64": str, "claims_email": str}.

    private_pem is a PKCS8 PEM string (unencrypted) -- accepted both by
    services/companion/api.py's `_check_vapid_key_consistency` (via py_vapid's
    `Vapid.from_pem`, which loads through `cryptography`'s
    `load_pem_private_key`, itself PEM-subtype-agnostic) and by pywebpush's
    `vapid_private_key` argument when the value contains "BEGIN" (see
    services/companion/chat_ws.py's `if "BEGIN" not in vapid_private_key and not
    os.path.isfile(...)` check -- a raw PEM string, not a file path, is
    accepted directly). public_b64 is the base64url-unpadded uncompressed
    EC point, matching what `_check_vapid_key_consistency` derives from the
    private key and compares against VAPID_PUBLIC_KEY.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "private_pem": private_pem,
        "public_b64": _b64url(raw_public),
        "claims_email": "mailto:test@example.com",
    }


def _wait_until(predicate, timeout: float, interval: float, desc: str) -> None:
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as e:  # noqa: BLE001 - keep polling, report last error on timeout
            last_exc = e
        time.sleep(interval)
    if last_exc:
        raise TimeoutError(f"timed out waiting for: {desc} (last error: {last_exc})")
    raise TimeoutError(f"timed out waiting for: {desc}")


def _prepare_scratch_server_dir(scratch_root: Path) -> Path:
    """Copy services/companion/ into scratch_root so api.py's __file__-relative hearts.db
    path resolves inside the scratch tree. symlinks=True so real symlinks
    under services/companion/static/ (pointing at output/, which may not exist in a
    fresh checkout) are recreated as symlinks rather than dereferenced --
    dereferencing a dangling symlink would raise during copytree.
    """
    scratch_server = scratch_root / "server"
    shutil.copytree(SERVER_DIR, scratch_server, symlinks=True, ignore=_COPY_IGNORE)
    (scratch_server / "data").mkdir(parents=True, exist_ok=True)
    return scratch_server


@dataclass
class InjectedSub:
    sub_id: str
    endpoint: str
    owner_id: str  # user_id (chat) or session_id (lineup)


class NotifServer:
    """An isolated services/companion/api.py instance on a free port, with scratch
    chat.db + hearts.db and a freshly generated VAPID keypair.

    start()/stop() are synchronous -- this launches uvicorn as a real
    subprocess (not an asyncio task), matching tests/e2ee_browser_check.py's
    proven pattern, so it composes cleanly with both sync test code and
    async scenario code (which talks to it over real HTTP/WS).
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._scratch_root: Path | None = None
        self._scratch_server_dir: Path | None = None
        self._port: int | None = None
        self._base_url: str | None = None
        self._chat_db_path: Path | None = None
        self._hearts_db_path: Path | None = None
        self._log_lines: list[str] = []
        self._vapid: dict | None = None

    # --- lifecycle ---------------------------------------------------

    def start(self) -> None:
        self._scratch_root = Path(tempfile.mkdtemp(prefix="notif_e2e_"))
        self._scratch_server_dir = _prepare_scratch_server_dir(self._scratch_root)
        self._chat_db_path = self._scratch_server_dir / "data" / "chat.db"
        self._hearts_db_path = self._scratch_server_dir / "data" / "hearts.db"

        self._port = get_free_port()
        self._base_url = f"http://127.0.0.1:{self._port}"
        self._vapid = gen_vapid_keys()

        env = os.environ.copy()
        for k in SENSITIVE_ENV_KEYS:
            env.pop(k, None)
        env["CHAT_DB_PATH"] = str(self._chat_db_path)
        env["CHAT_BASE_URL"] = self._base_url
        env["CHAT_EVENT_ID"] = "stone-techno-2026"
        # pywebpush uses Vapid.from_file (which handles PEM headers) only when
        # VAPID_PRIVATE_KEY is a real file path; a PEM *string* routes to
        # from_string, which cannot parse "-----BEGIN-----" headers and fails.
        # Production sets a file path, so we do the same.
        vapid_key_file = self._scratch_server_dir / "data" / "vapid_private.pem"
        vapid_key_file.write_text(self._vapid["private_pem"], encoding="ascii")
        env["VAPID_PRIVATE_KEY"] = str(vapid_key_file)
        env["VAPID_PUBLIC_KEY"] = self._vapid["public_b64"]
        env["VAPID_CLAIMS_EMAIL"] = self._vapid["claims_email"]
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._port),
            ],
            cwd=str(self._scratch_server_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def _reader():
            for line in self._proc.stdout:
                self._log_lines.append(line.rstrip())

        threading.Thread(target=_reader, daemon=True).start()

        def _ready():
            if self._proc.poll() is not None:
                raise RuntimeError(
                    "server process exited early:\n" + "\n".join(self._log_lines[-40:])
                )
            try:
                with urllib.request.urlopen(
                    self._base_url + "/chat/api/config", timeout=1
                ) as r:
                    return r.status == 200
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                return False

        try:
            _wait_until(
                _ready,
                timeout=25.0,
                interval=0.3,
                desc=f"server ready at {self._base_url}",
            )
        except TimeoutError:
            tail = "\n".join(self._log_lines[-60:])
            self.stop()
            raise RuntimeError(
                f"server did not become ready in time. Last log output:\n{tail}"
            )

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            self._proc = None
        if self._scratch_root is not None:
            shutil.rmtree(self._scratch_root, ignore_errors=True)
            self._scratch_root = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def ws_base(self) -> str:
        return self._base_url.replace("http://", "ws://").replace("https://", "wss://")

    @property
    def chat_db_path(self) -> str:
        return str(self._chat_db_path)

    @property
    def hearts_db_path(self) -> str:
        return str(self._hearts_db_path)

    @property
    def log_lines(self) -> list[str]:
        return list(self._log_lines)

    def grep_log(self, needle: str) -> list[str]:
        return [line for line in self._log_lines if needle in line]

    # --- DB connections ------------------------------------------------
    # Opened directly against the scratch files (not through the running
    # server process), per CONTRACT ("direct DB writes into the scratch
    # DBs"). foreign_keys is deliberately left OFF (SQLite's default) on
    # these harness-owned connections: inject_lineup_subscription needs to
    # be able to insert a hearts.db push_subscriptions row for a session_id
    # the harness itself did not create (hearts.db "sessions" are lineup
    # pick/schedule sessions, a concept this module has no fixture for --
    # see the end-of-file notes). The running server's own connections
    # still enforce foreign_keys=ON for its own writes; this pragma is
    # per-connection in SQLite, so it does not weaken the server's own
    # integrity checks.

    def _chat_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._chat_db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _hearts_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._hearts_db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # --- fixtures --------------------------------------------------------

    def create_user(
        self, display_name: str, username: str | None = None, country: str = "US"
    ) -> str:
        """Insert a complete chat.db users row (provider='test') and return
        user_id. Columns cover everything services/companion/chat_ws.py's handle_chat_ws
        reads off the row (username, color_index, avatar_url, country,
        device_fingerprint) plus the client's profile-complete gate fields
        (username/country/avatar_url) -- see chat.html's route(). No row is
        written to the `avatars` table: handle_chat_ws never checks it, and
        the WSClient in this module never renders/fetches an avatar image,
        so a real image blob (which would require pyvips, not an allowed
        dependency for this module) is unnecessary for WS-driven scenarios.
        """
        user_id = str(uuid.uuid4())
        if username is None:
            username = f"user_{uuid.uuid4().hex[:10]}"
        now = _iso_now()
        conn = self._chat_conn()
        try:
            conn.execute(
                "INSERT INTO users (id, provider, provider_id, display_name, "
                "username, username_lower, country, avatar_url, color_index, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    "test",
                    f"test-{user_id}",
                    display_name,
                    username,
                    username.lower(),
                    country,
                    f"/chat/api/avatar/{user_id}?v=1",
                    random.randint(0, 11),
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return user_id

    def create_session(self, user_id: str) -> str:
        """Insert a chat.db sessions row (stress_test.py pattern: a 64-hex-char
        token, 7-day expiry) and return the raw token. This is a chat WS
        session token, unrelated to hearts.db's own "sessions" table (lineup
        pick/schedule sessions, keyed by session_id/share_token).

        The DB column stores only hash_token(token) (SHA-256 hex), matching
        services/companion/chat_db.py's create_session/get_user_by_token since commit
        8be87cf -- the raw token is returned here for callers to use as the
        WS URL segment / cookie value, never written to the DB itself.
        """
        token = uuid.uuid4().hex + uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn = self._chat_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, _hash_token(token), expires),
            )
            conn.commit()
        finally:
            conn.close()
        return token

    def ensure_membership(self, user_id: str, room_id: str) -> None:
        now = _iso_now()
        conn = self._chat_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO room_memberships (user_id, room_id, joined_at, last_read_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, room_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def main_room_id(self) -> str:
        """id of the auto-created main room. services/companion/chat_api.py's mount_chat
        calls chat_db.seed_event_rooms(db, event_id, "Stone Techno 2026") at
        import time with a hardcoded room id of "general" -- queried here by
        is_main=1 rather than hardcoded, so this stays correct if that ever
        changes."""
        conn = self._chat_conn()
        try:
            row = conn.execute(
                "SELECT id FROM rooms WHERE is_main = 1 LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise RuntimeError("no main room found in scratch chat.db")
        return row["id"]

    def inject_chat_subscription(self, user_id: str, fps) -> InjectedSub:
        sub_id = f"chat-{uuid.uuid4().hex}"
        private_key = ec.generate_private_key(ec.SECP256R1())
        raw_public = private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        p256dh = _b64url(raw_public)
        auth_bytes = secrets.token_bytes(16)
        auth = _b64url(auth_bytes)
        endpoint = fps.endpoint_for(sub_id)

        conn = self._chat_conn()
        try:
            conn.execute(
                "INSERT INTO chat_push_subscriptions (user_id, endpoint, p256dh, auth, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, endpoint, p256dh, auth, _iso_now()),
            )
            conn.commit()
        finally:
            conn.close()

        fps.register_subscription(sub_id, private_key, auth_bytes)
        return InjectedSub(sub_id=sub_id, endpoint=endpoint, owner_id=user_id)

    def inject_lineup_subscription(self, session_id: str, fps) -> InjectedSub:
        sub_id = f"lineup-{uuid.uuid4().hex}"
        private_key = ec.generate_private_key(ec.SECP256R1())
        raw_public = private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        p256dh = _b64url(raw_public)
        auth_bytes = secrets.token_bytes(16)
        auth = _b64url(auth_bytes)
        endpoint = fps.endpoint_for(sub_id)

        conn = self._hearts_conn()
        try:
            conn.execute(
                "INSERT INTO push_subscriptions (session_id, endpoint, p256dh, auth, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, endpoint, p256dh, auth, _iso_now()),
            )
            conn.commit()
        finally:
            conn.close()

        fps.register_subscription(sub_id, private_key, auth_bytes)
        return InjectedSub(sub_id=sub_id, endpoint=endpoint, owner_id=session_id)

    # --- introspection ---------------------------------------------------

    def chat_sub_count(self, user_id: str | None = None) -> int:
        conn = self._chat_conn()
        try:
            if user_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM chat_push_subscriptions"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM chat_push_subscriptions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            return row["c"]
        finally:
            conn.close()

    def lineup_sub_count(self, session_id: str | None = None) -> int:
        conn = self._hearts_conn()
        try:
            if session_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM push_subscriptions"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM push_subscriptions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            return row["c"]
        finally:
            conn.close()

    def sent_notification_count(self, session_id: str | None = None) -> int:
        conn = self._hearts_conn()
        try:
            if session_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM sent_notifications"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM sent_notifications WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            return row["c"]
        finally:
            conn.close()

    def last_read_at(self, user_id: str, room_id: str) -> str | None:
        conn = self._chat_conn()
        try:
            row = conn.execute(
                "SELECT last_read_at FROM room_memberships WHERE user_id = ? AND room_id = ?",
                (user_id, room_id),
            ).fetchone()
        finally:
            conn.close()
        return row["last_read_at"] if row else None


class WSClient:
    """A lightweight chat WebSocket client for driving senders/recipients.

    Frames are collected into an internal list as they arrive (via a
    background reader task started in connect()); received() returns the
    full history, recv_until() scans forward from a private cursor so
    repeated calls for different events don't re-match frames already
    consumed by an earlier recv_until() call.
    """

    def __init__(self, ws_base: str, token: str) -> None:
        self.ws_base = ws_base
        self.token = token
        self._ws = None
        self._reader_task = None
        self._frames: list[dict] = []
        self._scan_idx = 0

    async def connect(self) -> None:
        url = f"{self.ws_base}/ws/chat/{self.token}"
        self._ws = await websockets.connect(
            url,
            additional_headers={"Cookie": f"chat_session={self.token}"},
            ping_interval=20,
            ping_timeout=10,
        )

        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    self._frames.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except websockets.ConnectionClosed:
            pass

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def send_event(self, event: str, **fields) -> None:
        await self._ws.send(json.dumps({"event": event, **fields}))

    async def join_room(self, room_id: str) -> None:
        await self.send_event("join_room", room_id=room_id)

    async def send_message(self, room_id: str, text: str) -> str:
        """Send a plaintext "text" message. Returns the temp_id used, so the
        caller can correlate the eventual message_acked frame (which carries
        temp_id, room_id, id, created_at -- see services/companion/chat_ws.py's
        send_message handler)."""
        temp_id = f"tmp-{uuid.uuid4().hex}"
        await self.send_event(
            "send_message",
            room_id=room_id,
            type="text",
            content=json.dumps({"text": text}),
            temp_id=temp_id,
        )
        return temp_id

    async def mark_read(self, room_id: str, timestamp: str) -> None:
        await self.send_event("mark_read", room_id=room_id, timestamp=timestamp)

    async def visible(self) -> None:
        await self.send_event("visible")

    async def recv_until(self, event: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while self._scan_idx < len(self._frames):
                frame = self._frames[self._scan_idx]
                self._scan_idx += 1
                if frame.get("event") == event:
                    return frame
            await asyncio.sleep(0.05)
        raise TimeoutError(f"timed out waiting for event {event!r}")

    def received(self) -> list[dict]:
        return list(self._frames)


async def post_idle_beacon(base_url: str, token: str) -> None:
    """POST /chat/api/push/idle with the chat session cookie -- mirrors the
    client's sendBeacon idle signal (see services/companion/chat_api.py's chat_push_idle,
    which zeroes manager._last_ws_activity[user_id] so the user is
    immediately push-eligible instead of waiting for the 30s fallback)."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{base_url}/chat/api/push/idle",
            cookies={"chat_session": token},
        )
