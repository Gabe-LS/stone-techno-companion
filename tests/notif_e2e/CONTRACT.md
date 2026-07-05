# Stage 1 build contract — notif_e2e foundation

Authoritative interface spec for the automated notification test harness, Stage 1 (Fake Push Service +
deterministic server-emission suite). See docs/notification-test-design.md for the overall design.

Three foundation modules are built independently against THIS contract, then the orchestrator integrates
them and builds the scenario suite. Implement your assigned module's public interface EXACTLY as written
(names, signatures, return shapes) so the pieces compose. No emojis anywhere. Add clear docstrings. You
have no Bash and cannot run anything — the orchestrator runs and debugs. Do not claim you tested it.

Python 3.14. Available libs (confirmed installed): aiohttp, websockets, httpx, http_ece, cryptography,
py_vapid, pywebpush. Do NOT add new dependencies.

Package layout (all under tests/notif_e2e/):
  fake_push_service.py   (Module A)
  harness.py             (Module B)
  recorder.py            (Module C)
  __init__.py            (empty, orchestrator creates)

---

## Shared data shapes

CapturedPush (a plain dataclass or dict) — one per WebPush the server emits:
  sub_id: str            # which injected subscription this went to
  method: str            # "POST"
  headers: dict[str,str] # lower-cased keys
  ttl: int | None        # parsed from the TTL header
  urgency: str | None
  topic: str | None
  content_encoding: str  # e.g. "aes128gcm"
  vapid: dict            # {"aud": str, "sub": str, "exp": int, "raw_jwt": str, "key": str|None}
  payload: dict | None   # the DECRYPTED JSON push body (title/body/url/room_id/push_id/count/...)
  decrypt_error: str | None
  received_at: float     # time.monotonic()

---

## Module A — fake_push_service.py

A local aiohttp server that impersonates a browser push service (FCM/Mozilla/Apple). The subscription
`endpoint` given to the app server points at this FPS; when the app calls pywebpush, the encrypted
request lands here, and the FPS decrypts and records it.

Public interface:

class FakePushService:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None: ...
        # port None -> pick a free ephemeral port. Never bind 64728.

    async def start(self) -> None: ...     # start the aiohttp server
    async def stop(self) -> None: ...      # graceful shutdown

    @property
    def origin(self) -> str: ...           # e.g. "http://127.0.0.1:53555"  (scheme://host:port)

    def endpoint_for(self, sub_id: str) -> str: ...
        # full URL the app stores as the subscription endpoint, e.g. f"{origin}/push/{sub_id}"

    def register_subscription(self, sub_id: str, private_key, auth_secret: bytes) -> None: ...
        # private_key: a cryptography EC private key object (SECP256R1) whose public point is the
        # subscription's p256dh. auth_secret: the 16 raw bytes behind the sub's base64url `auth`.
        # The FPS uses these to decrypt aes128gcm bodies sent to this sub_id.

    def set_dead(self, sub_id: str, status: int = 410) -> None: ...
        # subsequent requests to this sub_id respond with `status` (410/404) instead of 201.
        # Used to test subscription pruning.

    def requests_for(self, sub_id: str) -> list[CapturedPush]: ...
    def all_requests(self) -> list[CapturedPush]: ...
    def clear(self) -> None: ...           # drop recorded requests (between scenarios)

    async def wait_for(self, sub_id: str, count: int = 1, timeout: float = 5.0) -> list[CapturedPush]: ...
        # await until at least `count` requests for sub_id are captured, or raise TimeoutError.

Behavior:
- Route: POST /push/{sub_id}. On hit, build a CapturedPush: copy lower-cased headers; parse TTL/Urgency/
  Topic; read Content-Encoding (default "aes128gcm"); parse the VAPID JWT from the Authorization header
  (formats: "vapid t=<jwt>,k=<key>" and legacy "WebPush <jwt>") by base64url-decoding the JWT's middle
  segment into claims — do NOT require signature verification; also capture the "k=" public key if
  present. Decrypt the body:
      import http_ece
      payload_bytes = http_ece.decrypt(raw_body, private_key=priv, auth_secret=auth)  # aes128gcm default
      payload = json.loads(payload_bytes)
  Wrap decryption in try/except and record decrypt_error on failure (payload=None).
- Respond 201 Created normally; if set_dead, respond with the configured status and an empty body.
- Thread-safety: aiohttp runs in the same asyncio loop as the harness; no locking needed, but wait_for
  should poll with small asyncio.sleep, not busy-spin.
- base64url helper: pad correctly before decoding (JWT segments and keys are unpadded base64url).

Note for multi-origin VAPID-isolation testing: the harness will start THREE FakePushService instances on
three ports so each has a distinct `origin` (hence a distinct expected VAPID `aud`). Nothing special is
needed in this module for that beyond honoring the given/auto port.

---

## Module B — harness.py

Isolated server lifecycle + fixtures. Reuse the proven patterns in tests/e2ee_browser_check.py
(get_free_port, the uvicorn-in-thread startup, scratch DB via CHAT_DB_PATH, sensitive-env stripping,
wait-for-ready HTTP poll). Study that file first and mirror its server setup.

Public interface:

def get_free_port() -> int: ...

def gen_vapid_keys() -> dict: ...
    # generate a VAPID P-256 keypair. Return {"private_pem": str, "public_b64": str, "claims_email":
    # "mailto:test@example.com"}. Use py_vapid (Vapid01) or cryptography. The private_pem must be what
    # the server accepts in VAPID_PRIVATE_KEY (PEM with BEGIN, matching api.py's loader), and public_b64
    # the base64url uncompressed public key. These are set into the server process env.

class NotifServer:
    """An isolated app server (server/api.py) on a free port with scratch DBs and generated VAPID."""
    def __init__(self) -> None: ...
    def start(self) -> None: ...   # create scratch dir, scratch chat.db (CHAT_DB_PATH) + hearts.db,
                                   # strip sensitive env, inject generated VAPID_* env, launch uvicorn in
                                   # a thread/subprocess, wait until /chat/api/config (or similar) answers.
    def stop(self) -> None: ...    # shut down, remove scratch dir
    @property
    def base_url(self) -> str: ... # "http://127.0.0.1:<port>"
    @property
    def ws_base(self) -> str: ...  # "ws://127.0.0.1:<port>"
    @property
    def chat_db_path(self) -> str: ...
    @property
    def hearts_db_path(self) -> str: ...
    @property
    def log_lines(self) -> list[str]: ...  # captured server stdout/stderr lines (for [PUSH]/[PUSH-ACK])
    def grep_log(self, needle: str) -> list[str]: ...

    # --- fixtures (direct DB writes into the scratch DBs) ---
    def create_user(self, display_name: str, username: str | None = None, country: str = "US") -> str:
        ...  # returns user_id. Insert a complete users row (provider='test'), profile fields set so the
             # user can enter chat. Mirror the columns e2ee_browser_check / chat_db.create_user use.
    def create_session(self, user_id: str) -> str: ...   # returns session token (stress_test pattern)
    def ensure_membership(self, user_id: str, room_id: str) -> None: ...
    def main_room_id(self) -> str: ...   # id of the auto-created main room in the scratch chat.db

    def inject_chat_subscription(self, user_id: str, fps) -> "InjectedSub": ...
        # fps: a FakePushService. Generate a SECP256R1 keypair + 16-byte auth. Compute p256dh (uncompressed
        # public point, base64url unpadded) and auth (base64url unpadded). INSERT into chat_push_subscriptions
        # (user_id, endpoint=fps.endpoint_for(sub_id), p256dh, auth, created_at). Call
        # fps.register_subscription(sub_id, private_key, auth_bytes). Return InjectedSub with sub_id,
        # endpoint, user_id.
    def inject_lineup_subscription(self, session_id: str, fps) -> "InjectedSub": ...
        # same but into hearts.db push_subscriptions (session_id, endpoint, p256dh, auth, created_at).

    # --- introspection ---
    def chat_sub_count(self, user_id: str | None = None) -> int: ...
    def lineup_sub_count(self, session_id: str | None = None) -> int: ...
    def sent_notification_count(self, session_id: str | None = None) -> int: ...
    def last_read_at(self, user_id: str, room_id: str) -> str | None: ...

@dataclass
class InjectedSub:
    sub_id: str
    endpoint: str
    owner_id: str   # user_id or session_id

class WSClient:
    """A lightweight chat WebSocket client (uses `websockets`) for driving senders/recipients."""
    def __init__(self, ws_base: str, token: str) -> None: ...
    async def connect(self) -> None: ...   # websockets.connect(f"{ws_base}/ws/chat/{token}",
                                           #   additional_headers={"Cookie": f"chat_session={token}"})
    async def close(self) -> None: ...
    async def send_event(self, event: str, **fields) -> None: ...   # json.dumps({"event":event, **fields})
    async def join_room(self, room_id: str) -> None: ...
    async def send_message(self, room_id: str, text: str) -> str: ...  # returns the temp_id used
    async def mark_read(self, room_id: str, timestamp: str) -> None: ...
    async def visible(self) -> None: ...
    async def recv_until(self, event: str, timeout: float = 5.0) -> dict: ...  # await a specific event
    def received(self) -> list[dict]: ...   # all frames received so far (parsed)

Notes:
- The message-send WS event and its fields must match what chat_ws.py's send_message handler expects.
  Inspect chat_ws.py to get the exact event name and payload keys (room_id, content/text, temp_id, type).
- A recipient that should be "offline" simply never connects (its subscription is injected in the DB).
- A recipient that should be "idle" connects, then the harness waits >30s OR the scenario sends a
  /chat/api/push/idle beacon via httpx to zero its activity — expose a helper:
    async def post_idle_beacon(base_url, token) -> None  (module-level or on NotifServer)

---

## Module C — recorder.py

A source-agnostic signal timeline with ordered + timing-bound assertions. Pure Python, no external deps.

@dataclass(order=True) or plain: Signal { t: float (monotonic), source: str, kind: str, data: dict }

class SignalRecorder:
    def __init__(self, clock=time.monotonic) -> None: ...
    def record(self, source: str, kind: str, data: dict | None = None, t: float | None = None) -> None:
        ...  # t defaults to clock()
    def timeline(self) -> list[Signal]: ...              # sorted by t
    def of_kind(self, kind: str) -> list[Signal]: ...
    def count(self, kind: str) -> int: ...
    def first(self, kind: str) -> Signal | None: ...
    def clear(self) -> None: ...
    def dump(self, path: str) -> None: ...               # write timeline as JSON for artifacts

    def assert_sequence(self, kinds: list[str], *, strict: bool = False) -> None:
        # assert the given kinds appear IN ORDER in the timeline. strict=False allows other signals
        # interleaved; strict=True requires them to be exactly consecutive. Raise AssertionError with a
        # readable diff (expected order vs actual timeline kinds) on failure.

    def assert_within(self, before_kind: str, after_kind: str, max_seconds: float) -> None:
        # assert the first `after_kind` occurs after the first `before_kind` and within max_seconds.
        # Raise AssertionError with the measured delta on failure.

    def assert_absent(self, kind: str) -> None: ...      # assert no signal of this kind was recorded

Keep it small and dependency-free; this is the assertion backbone every scenario uses.

---

## Integration note (orchestrator will wire this)

Scenarios will: start NotifServer + one-or-more FakePushService; create users/sessions; inject
subscriptions; drive WSClients; feed FPS captures + WS frames + DB deltas + server-log greps into a
SignalRecorder; assert. Your modules must make that composition possible without further glue.
