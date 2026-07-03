#!/usr/bin/env python3
"""
Chat Stress Test
================
Simulates concurrent users across multiple rooms and DMs with periodic
burst-load spikes. Logs every action. Tracks latency, throughput, system
resources, and estimated moderation costs.

Usage:
    # Local (start the server first)
    python stress_test/run.py --insecure

    # Quick smoke test
    python stress_test/run.py --users 20 --duration 120 --insecure

    # Full 200-user, 30-minute run against production
    python stress_test/run.py --url https://stonetechno.deftlab.dev \
        --db /root/services/stone-techno/server/data/chat.db

    # Without moderation (isolate chat infra from OpenAI)
    python stress_test/run.py --insecure --no-moderation

    # Clean up leftover data from an interrupted run
    python stress_test/run.py --cleanup-only

Requirements:
    pip install websockets httpx psutil
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import secrets
import sqlite3
import ssl
import statistics
import sys
import time
import uuid
from array import array
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("pip install httpx")
try:
    import websockets
except ImportError:
    sys.exit("pip install websockets")
try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MESSAGES = [
    "Anyone at the main stage?",
    "This set is incredible!",
    "Where's the water station?",
    "Meeting up at the entrance in 10",
    "The sound system here is unreal",
    "Who's playing next?",
    "Lost my friends, near the bar",
    "This DJ is killing it",
    "Anyone know if there's an afterparty?",
    "The light show is amazing",
    "Just got here, where is everyone?",
    "Best festival ever honestly",
    "Need earplugs, so loud here",
    "The bass is insane on this floor",
    "Food trucks are great this year",
    "Where do we meet after the closing?",
    "Rain is coming, bring a jacket",
    "Security is super chill this year",
    "That transition was perfect",
    "Who saw the sunrise set this morning?",
    "Charging stations near the entrance",
    "This floor has the best vibes",
    "Anyone from Berlin here?",
    "The visual effects are next level",
    "Going to grab some food, back in 20",
    "What time does the afterhours start?",
    "That was a legendary b2b set",
    "My feet are killing me but worth it",
    "Sound quality on this floor is top notch",
    "Just discovered a hidden chill-out area",
]

REACTIONS = ["thumbs_up", "heart", "laugh", "fire", "wow", "clap"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm"}

# GPT-5.4-nano cost estimate per moderated message
# ~250 input tokens x $0.075/M + ~10 output tokens x $0.30/M
COST_PER_MODERATED_MSG = 0.0000218

log = logging.getLogger("stress")


# ---------------------------------------------------------------------------
# Burst control
# ---------------------------------------------------------------------------


class BurstControl:
    __slots__ = (
        "trigger",
        "msg_slots",
        "img_slots",
        "msg_burst_results",
        "img_burst_results",
        "burst_log",
    )

    def __init__(self):
        self.trigger = asyncio.Event()
        self.msg_slots = 0
        self.img_slots = 0
        self.msg_burst_results: list[dict] = []
        self.img_burst_results: list[dict] = []
        self.burst_log: list[dict] = []


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class Metrics:
    def __init__(self):
        self.ack_latencies = array("d")
        self.broadcast_latencies = array("d")
        self.upload_latencies = array("d")
        self.image_upload_latencies = array("d")
        self.video_upload_latencies = array("d")
        self.connect_latencies = array("d")
        self.history_latencies = array("d")

        self.messages_sent = 0
        self.messages_received = 0
        self.messages_failed = 0
        self.messages_deleted = 0
        self.replies_sent = 0
        self.media_uploaded = 0
        self.media_failed = 0
        self.reactions_sent = 0
        self.meetups_created = 0
        self.meetups_joined = 0
        self.locations_sent = 0
        self.dms_opened = 0
        self.mark_reads = 0
        self.ws_errors = 0
        self.ws_reconnects = 0
        self.connections_active = 0
        self.moderated_messages = 0

        self.cpu_samples: list[float] = []
        self.ram_mb_samples: list[float] = []
        self.db_size_mb_samples: list[float] = []
        self.uploads_size_mb_samples: list[float] = []
        self.net_sent_bytes: list[int] = []
        self.net_recv_bytes: list[int] = []
        self.send_rate_samples: list[float] = []
        self.recv_rate_samples: list[float] = []
        self.ack_timeline: list[tuple[float, float]] = []

        self.start_time = 0.0
        self.end_time = 0.0

        self._send_times: dict[str, float] = {}
        self._broadcast_lookup: dict[str, float] = {}
        self._history_waits: dict[str, float] = {}
        self.recent_msg_ids: list[str] = []
        self._sent_msg_ids: set[str] = set()
        self._received_msg_ids: set[str] = set()
        self.meetup_ids: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def pct(data, ps=(50, 95, 99)):
    if not data:
        return {p: 0.0 for p in ps}
    s = sorted(data)
    n = len(s)
    return {p: s[min(int(n * p / 100), n - 1)] for p in ps}


def fmt_ms(sec):
    return f"{sec * 1000:.0f}ms"


def fmt_bytes(b):
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


def setup_logging(log_path: str):
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(ch)


# ---------------------------------------------------------------------------
# Database setup / cleanup
# ---------------------------------------------------------------------------


def setup_db(db_path: str, num_users: int, event_id: str, moderated: bool):
    """Create test users, sessions, and rooms. Returns config dict."""
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row

    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "users" not in tables:
        db.close()
        sys.exit("chat.db not initialized -- start the server first.")

    # Create test rooms (is_moderated matches production default)
    test_rooms = []
    mod = 1 if moderated else 0
    room_configs = [
        ("general", "Stress: General", mod, 1),
        ("general", "Stress: Stage A", mod, 1),
        ("general", "Stress: Stage B", mod, 1),
    ]

    for rtype, rname, is_mod, allows_media in room_configs:
        rid = f"stress-{secrets.token_hex(4)}"
        db.execute(
            "INSERT INTO rooms "
            "(id, event_id, type, name, description, is_moderated, "
            "is_read_only, auto_join, allows_media, ttl_minutes, position, created_at) "
            "VALUES (?, ?, ?, ?, 'Stress test room', ?, 0, 0, ?, NULL, 999, datetime('now'))",
            (rid, event_id, rtype, rname, is_mod, allows_media),
        )
        test_rooms.append(rid)

    # Discover existing non-test rooms
    existing_rooms = [
        r["id"]
        for r in db.execute(
            "SELECT id FROM rooms WHERE event_id = ? AND name NOT LIKE 'Stress:%' "
            "AND is_read_only = 0",
            (event_id,),
        ).fetchall()
    ]
    all_rooms = test_rooms + existing_rooms
    log.info(
        "rooms: %d test + %d existing = %d total",
        len(test_rooms),
        len(existing_rooms),
        len(all_rooms),
    )

    # Create users and sessions
    tokens: list[str] = []
    user_ids: list[str] = []

    for i in range(num_users):
        uid = uuid.uuid4().hex
        tag = secrets.token_hex(3)
        username = f"bot{i}_{tag}"
        db.execute(
            "INSERT INTO users "
            "(id, provider, provider_id, display_name, username, "
            "username_lower, country, color_index, created_at, last_seen) "
            "VALUES (?, 'stress_test', ?, ?, ?, ?, 'IT', ?, "
            "datetime('now'), datetime('now'))",
            (
                uid,
                f"stress_{i}_{tag}",
                f"StressBot {i}",
                username,
                username.lower(),
                i % 12,
            ),
        )
        token = uuid.uuid4().hex + uuid.uuid4().hex
        db.execute(
            "INSERT INTO sessions (id, user_id, token, expires_at) "
            "VALUES (?, ?, ?, datetime('now', '+1 day'))",
            (uuid.uuid4().hex, uid, token),
        )
        tokens.append(token)
        user_ids.append(uid)

    db.commit()
    db.close()

    return {
        "tokens": tokens,
        "user_ids": user_ids,
        "test_rooms": test_rooms,
        "all_rooms": all_rooms,
    }


def cleanup_db(db_path: str, test_rooms: list[str] | None, user_ids: list[str] | None):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")

    before = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]

    if user_ids:
        db.executemany(
            "DELETE FROM users WHERE id = ? AND provider = 'stress_test'",
            [(u,) for u in user_ids],
        )
    else:
        db.execute("DELETE FROM users WHERE provider = 'stress_test'")

    if test_rooms:
        db.executemany(
            "DELETE FROM rooms WHERE id = ? AND name LIKE 'Stress:%'",
            [(r,) for r in test_rooms],
        )
    else:
        db.execute("DELETE FROM rooms WHERE name LIKE 'Stress:%'")

    # Also clean up meetups created by stress test users
    db.execute("DELETE FROM meetups WHERE creator_id NOT IN (SELECT id FROM users)")

    db.commit()
    after = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    non_test = db.execute(
        "SELECT COUNT(*) FROM rooms WHERE name NOT LIKE 'Stress:%'"
    ).fetchone()[0]
    log.info(
        "Cleanup: rooms %d -> %d (non-test rooms: %d intact)", before, after, non_test
    )
    db.close()


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
}


def _make_webp(w: int, h: int) -> bytes:
    """Generate a 1500px WebP Q=80 image matching what the browser sends."""
    import pyvips

    # Gradient pattern with noise — compresses like a real photo
    r = pyvips.Image.xyz(w, h)
    r = ((r[0] * 37 + r[1] * 73) % 256).cast("uchar")
    g = pyvips.Image.xyz(w, h)
    g = ((g[0] * 59 + g[1] * 41) % 256).cast("uchar")
    b = pyvips.Image.xyz(w, h)
    b = ((b[0] * 83 + b[1] * 29) % 256).cast("uchar")
    img = r.bandjoin([g, b])
    return img.webpsave_buffer(Q=80)


def _generate_test_media(
    media_dir: Path,
    n_images: int = 5,
) -> tuple[list[Path], list[Path]]:
    """Generate test images identical to what the browser sends:
    1500px max side, WebP Q=80.  The real client runs
    resizeImage(file, 1500) + toBlob('image/webp', 0.8) before uploading."""
    media_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []

    sizes = [
        (1500, 1125),  # 4:3 landscape
        (1125, 1500),  # 4:3 portrait
        (1500, 1000),  # 3:2 landscape
        (1000, 1500),  # 3:2 portrait
        (1500, 844),  # 16:9 landscape
    ]
    for i in range(n_images):
        w, h = sizes[i % len(sizes)]
        p = media_dir / f"test_{w}x{h}.webp"
        if not p.exists():
            print(f"  generating {p.name} ...", end=" ", flush=True)
            p.write_bytes(_make_webp(w, h))
            print(f"{p.stat().st_size / 1024:.0f}KB")
        images.append(p)

    # Pre-process user-provided images the same way the browser does:
    # resize to 1500px max side, convert to WebP Q=80
    generated = {p.name for p in images}
    for f in sorted(media_dir.iterdir()):
        if (
            f.suffix.lower() in IMAGE_EXTS
            and f.name not in generated
            and not f.name.startswith(".")
        ):
            cached = media_dir / f".processed_{f.stem}.webp"
            if not cached.exists():
                print(f"  processing {f.name} ...", end=" ", flush=True)
                try:
                    import pyvips

                    try:
                        img = pyvips.Image.new_from_file(str(f))
                    except pyvips.Error:
                        img = pyvips.Image.new_from_file(str(f), unlimited=True)
                    max_side = max(img.width, img.height)
                    if max_side > 1500:
                        scale = 1500 / max_side
                        img = img.resize(scale, kernel=pyvips.enums.Kernel.LANCZOS3)
                    img.webpsave(str(cached), Q=80)
                    print(f"{cached.stat().st_size / 1024:.0f}KB")
                except Exception as e:
                    print(f"SKIP ({e})")
                    continue
            images.append(cached)

    # Generate test videos matching browser output spec:
    # MP4, H.264 (AVC) 4Mbps, AAC 128kbps, ≤1080p, ≤60s
    videos: list[Path] = []
    video_specs = [
        (1920, 1080, 10, "landscape_10s"),
        (1080, 1920, 8, "portrait_8s"),
        (1280, 720, 15, "720p_15s"),
    ]
    for vw, vh, dur, label in video_specs:
        p = media_dir / f"test_{label}.mp4"
        if not p.exists():
            print(f"  generating {p.name} ...", end=" ", flush=True)
            _generate_test_video(p, vw, vh, dur)
            if p.exists():
                print(f"{p.stat().st_size / 1024:.0f}KB")
            else:
                print("SKIPPED (ffmpeg not available)")
        if p.exists():
            videos.append(p)

    for f in media_dir.iterdir():
        if f.suffix.lower() in VIDEO_EXTS and f not in videos:
            videos.append(f)

    return images, videos


def _generate_test_video(path: Path, w: int, h: int, duration: int):
    """Generate a valid MP4 matching browser Mediabunny output:
    H.264 4Mbps, AAC 128kbps, 30fps."""
    import subprocess

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={w}x{h}:rate=30:duration={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-b:v",
                "4M",
                "-maxrate",
                "4M",
                "-bufsize",
                "8M",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(path),
            ],
            capture_output=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


async def upload_media(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    path: Path,
    kind: str,
    ulog: logging.Logger,
) -> str | None:
    url = f"{base_url}/chat/api/upload/{kind}"
    mime = MIME_MAP.get(path.suffix.lower(), f"{kind}/*")
    t0 = time.monotonic()
    try:
        with open(path, "rb") as fh:
            resp = await client.post(
                url,
                files={"file": (path.name, fh, mime)},
                cookies={"chat_session": token},
            )
        lat = time.monotonic() - t0
        if resp.status_code == 200:
            ulog.info("UPLOAD %s %s %s", kind, path.name, fmt_ms(lat))
            return json.dumps(resp.json())
        ulog.warning(
            "UPLOAD_FAIL %s %s status=%d %s",
            kind,
            path.name,
            resp.status_code,
            fmt_ms(lat),
        )
    except Exception as e:
        ulog.error("UPLOAD_ERR %s %s: %s", kind, path.name, e)
    return None


# ---------------------------------------------------------------------------
# User simulation
# ---------------------------------------------------------------------------


async def simulate_user(
    idx: int,
    token: str,
    user_id: str,
    all_user_ids: list[str],
    rooms: list[str],
    base_url: str,
    ssl_ctx: ssl.SSLContext | None,
    metrics: Metrics,
    burst: BurstControl,
    images: list[Path],
    videos: list[Path],
    duration: float,
    ramp_delay: float,
    moderated: bool,
    stop: asyncio.Event,
    http: httpx.AsyncClient,
):
    ulog = logging.getLogger(f"user.{idx:03d}")
    await asyncio.sleep(idx * ramp_delay)
    if stop.is_set():
        return

    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_base}/ws/chat/{token}"
    end_at = time.monotonic() + duration

    my_rooms = random.sample(rooms, min(len(rooms), random.randint(2, 4)))
    dm_rooms: list[str] = []
    next_dm_time = time.monotonic() + random.uniform(60, 180)

    while time.monotonic() < end_at and not stop.is_set():
        t0 = time.monotonic()
        try:
            async with websockets.connect(
                ws_url,
                ssl=ssl_ctx,
                additional_headers={"Cookie": f"chat_session={token}"},
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20,
            ) as ws:
                lat = time.monotonic() - t0
                metrics.connect_latencies.append(lat)
                metrics.connections_active += 1
                ulog.info("CONNECT %s", fmt_ms(lat))

                for rid in my_rooms:
                    metrics._history_waits[rid] = time.monotonic()
                    await ws.send(
                        json.dumps(
                            {
                                "event": "join_room",
                                "room_id": rid,
                            }
                        )
                    )
                    ulog.debug("JOIN room=%s", rid[:12])

                recv_task = asyncio.create_task(_receiver(ws, idx, metrics, stop))

                try:
                    while time.monotonic() < end_at and not stop.is_set():
                        # Check for burst trigger or wait normally
                        fired_burst = False
                        try:
                            await asyncio.wait_for(
                                burst.trigger.wait(),
                                timeout=random.uniform(5, 15),
                            )
                            fired_burst = True
                        except asyncio.TimeoutError:
                            pass

                        if stop.is_set():
                            break

                        if fired_burst and burst.msg_slots == -1:
                            continue

                        if fired_burst and burst.msg_slots > 0:
                            burst.msg_slots -= 1
                            await _send_text(
                                ws,
                                random.choice(my_rooms),
                                metrics,
                                ulog,
                                moderated,
                                burst_tag="MSG_BURST",
                            )
                            continue

                        if fired_burst and burst.img_slots > 0 and images:
                            burst.img_slots -= 1
                            await _send_image(
                                ws,
                                random.choice(my_rooms),
                                token,
                                base_url,
                                metrics,
                                images,
                                http,
                                ulog,
                                moderated,
                                burst_tag="IMG_BURST",
                            )
                            continue

                        # DM opening
                        now = time.monotonic()
                        if now >= next_dm_time and len(dm_rooms) < 3:
                            target = _pick_dm_target(idx, all_user_ids)
                            if target:
                                dm_rid = await _open_dm(
                                    http,
                                    base_url,
                                    token,
                                    target,
                                    ulog,
                                )
                                if dm_rid:
                                    dm_rooms.append(dm_rid)
                                    metrics.dms_opened += 1
                                    await ws.send(
                                        json.dumps(
                                            {
                                                "event": "join_room",
                                                "room_id": dm_rid,
                                            }
                                        )
                                    )
                            next_dm_time = now + random.uniform(120, 300)

                        # Typing indicator
                        target_room = random.choice(
                            my_rooms + dm_rooms if dm_rooms else my_rooms
                        )
                        await ws.send(
                            json.dumps(
                                {
                                    "event": "typing",
                                    "room_id": target_room,
                                    "active": True,
                                }
                            )
                        )
                        await asyncio.sleep(random.uniform(0.3, 1.5))

                        # Pick action
                        await _pick_action(
                            ws,
                            target_room,
                            token,
                            base_url,
                            metrics,
                            images,
                            videos,
                            http,
                            ulog,
                            moderated,
                        )

                finally:
                    recv_task.cancel()
                    try:
                        await recv_task
                    except asyncio.CancelledError:
                        pass
                    metrics.connections_active -= 1
                    ulog.info("DISCONNECT")

        except (
            websockets.ConnectionClosed,
            ConnectionRefusedError,
            OSError,
            asyncio.TimeoutError,
        ) as e:
            metrics.ws_errors += 1
            metrics.connections_active = max(0, metrics.connections_active - 1)
            ulog.warning("WS_ERROR %s", e)
            if time.monotonic() < end_at and not stop.is_set():
                metrics.ws_reconnects += 1
                await asyncio.sleep(random.uniform(1, 3))

        except Exception as e:
            metrics.ws_errors += 1
            metrics.connections_active = max(0, metrics.connections_active - 1)
            ulog.error("FATAL %s", e, exc_info=True)
            break


async def _receiver(ws, idx: int, metrics: Metrics, stop: asyncio.Event):
    ulog = logging.getLogger(f"recv.{idx:03d}")
    try:
        async for raw in ws:
            if stop.is_set():
                break
            data = json.loads(raw)
            evt = data.get("event")

            if evt == "message_acked":
                tid = data.get("temp_id")
                mid = data.get("id")
                if tid in metrics._send_times:
                    lat = time.monotonic() - metrics._send_times[tid]
                    metrics.ack_latencies.append(lat)
                    metrics.ack_timeline.append((time.monotonic(), lat))
                    metrics._broadcast_lookup[mid] = metrics._send_times.pop(tid)
                    metrics._sent_msg_ids.add(mid)
                    ulog.debug("ACK temp=%s msg=%s %s", tid[:8], mid[:8], fmt_ms(lat))

            elif evt == "message":
                metrics.messages_received += 1
                mid = data.get("id")
                if mid:
                    metrics._received_msg_ids.add(mid)
                if mid and mid in metrics._broadcast_lookup:
                    if random.random() < 0.02:
                        lat = time.monotonic() - metrics._broadcast_lookup[mid]
                        metrics.broadcast_latencies.append(lat)
                        ulog.debug("BCAST msg=%s %s", mid[:8], fmt_ms(lat))

                if mid and random.random() < 0.2:
                    if len(metrics.recent_msg_ids) >= 3000:
                        metrics.recent_msg_ids[random.randint(0, 2999)] = mid
                    else:
                        metrics.recent_msg_ids.append(mid)

            elif evt == "room_history":
                rid = data.get("room_id", "")
                if rid in metrics._history_waits:
                    lat = time.monotonic() - metrics._history_waits.pop(rid)
                    metrics.history_latencies.append(lat)
                    ulog.debug(
                        "HISTORY room=%s %s msgs=%d",
                        rid[:12],
                        fmt_ms(lat),
                        len(data.get("messages", [])),
                    )

            elif evt == "message_rejected":
                metrics.messages_failed += 1
                ulog.warning(
                    "REJECTED temp=%s reason=%s",
                    data.get("temp_id", "?")[:8],
                    data.get("reason", "?"),
                )

            elif evt == "message_removed":
                ulog.info(
                    "MODERATED msg=%s reason=%s",
                    data.get("id", "?")[:8],
                    data.get("reason", "?"),
                )

            elif evt == "meetup_created":
                mid = data.get("meetup", {}).get("id")
                if mid:
                    metrics.meetup_ids.append(mid)
                ulog.debug("MEETUP_CREATED id=%s", mid)

            elif evt == "presence":
                ulog.debug(
                    "PRESENCE user=%s online=%s room=%s",
                    data.get("user_id", "?")[:8],
                    data.get("online"),
                    data.get("room_id", "?")[:12],
                )

            elif evt == "reaction_updated":
                ulog.debug("REACTION_UPDATED msg=%s", data.get("message_id", "?")[:8])

            elif evt == "badge_counts":
                ulog.debug("BADGE_COUNTS rooms=%d", len(data.get("counts", [])))

            elif evt == "badge_update":
                ulog.debug(
                    "BADGE_UPDATE room=%s count=%s",
                    data.get("room_id", "?")[:12],
                    data.get("count"),
                )

            elif evt == "strike":
                ulog.warning(
                    "STRIKE count=%s reason=%s", data.get("count"), data.get("reason")
                )

            elif evt == "banned":
                ulog.error("BANNED reason=%s", data.get("reason"))

            elif evt == "muted":
                ulog.warning("MUTED reason=%s", data.get("reason"))

    except (websockets.ConnectionClosed, asyncio.CancelledError):
        pass


async def _send_text(
    ws,
    room_id: str,
    metrics: Metrics,
    ulog: logging.Logger,
    moderated: bool,
    burst_tag: str = "",
):
    tid = secrets.token_hex(6)
    tag = f" #{secrets.token_hex(3)}"
    # 5% of text messages are long (near 1K char limit) to stress DB + broadcast
    if random.random() < 0.05:
        base = random.choice(MESSAGES)
        content = (base + " ") * (900 // (len(base) + 1)) + tag
    else:
        content = random.choice(MESSAGES) + tag
    metrics._send_times[tid] = time.monotonic()
    await ws.send(
        json.dumps(
            {
                "event": "send_message",
                "room_id": room_id,
                "type": "text",
                "content": content,
                "temp_id": tid,
            }
        )
    )
    metrics.messages_sent += 1
    if moderated:
        metrics.moderated_messages += 1
    tag = f" [{burst_tag}]" if burst_tag else ""
    ulog.info("SEND text room=%s temp=%s%s", room_id[:12], tid[:8], tag)


async def _send_image(
    ws,
    room_id: str,
    token: str,
    base_url: str,
    metrics: Metrics,
    images: list[Path],
    http: httpx.AsyncClient,
    ulog: logging.Logger,
    moderated: bool,
    burst_tag: str = "",
):
    img = random.choice(images)
    t0 = time.monotonic()
    content = await upload_media(http, base_url, token, img, "image", ulog)
    if content:
        lat = time.monotonic() - t0
        metrics.upload_latencies.append(lat)
        metrics.image_upload_latencies.append(lat)
        metrics.media_uploaded += 1
        tid = secrets.token_hex(6)
        metrics._send_times[tid] = time.monotonic()
        await ws.send(
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "image",
                    "content": content,
                    "temp_id": tid,
                }
            )
        )
        metrics.messages_sent += 1
        if moderated:
            metrics.moderated_messages += 1
        tag = f" [{burst_tag}]" if burst_tag else ""
        ulog.info("SEND image room=%s temp=%s%s", room_id[:12], tid[:8], tag)
    else:
        metrics.media_failed += 1


async def _pick_action(
    ws, room_id, token, base_url, metrics, images, videos, http, ulog, moderated
):
    roll = random.random()

    # 2% video upload
    if roll < 0.02 and videos:
        vid = random.choice(videos)
        t0 = time.monotonic()
        content = await upload_media(http, base_url, token, vid, "video", ulog)
        if content:
            lat = time.monotonic() - t0
            metrics.upload_latencies.append(lat)
            metrics.video_upload_latencies.append(lat)
            metrics.media_uploaded += 1
            tid = secrets.token_hex(6)
            metrics._send_times[tid] = time.monotonic()
            await ws.send(
                json.dumps(
                    {
                        "event": "send_message",
                        "room_id": room_id,
                        "type": "video",
                        "content": content,
                        "temp_id": tid,
                    }
                )
            )
            metrics.messages_sent += 1
            if moderated:
                metrics.moderated_messages += 1
            ulog.info("SEND video room=%s temp=%s", room_id[:12], tid[:8])
        else:
            metrics.media_failed += 1

    # 8% image upload
    elif roll < 0.10 and images:
        await _send_image(
            ws, room_id, token, base_url, metrics, images, http, ulog, moderated
        )

    # 5% reaction
    elif roll < 0.15 and metrics.recent_msg_ids:
        target = random.choice(metrics.recent_msg_ids)
        emoji = random.choice(REACTIONS)
        await ws.send(
            json.dumps(
                {
                    "event": "add_reaction",
                    "message_id": target,
                    "emoji": emoji,
                }
            )
        )
        metrics.reactions_sent += 1
        ulog.debug("REACT msg=%s emoji=%s", target[:8], emoji)

    # 5% reply to recent message
    elif roll < 0.20 and metrics.recent_msg_ids:
        reply_to = random.choice(metrics.recent_msg_ids)
        tid = secrets.token_hex(6)
        metrics._send_times[tid] = time.monotonic()
        await ws.send(
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "text",
                    "content": random.choice(MESSAGES) + f" #{secrets.token_hex(3)}",
                    "temp_id": tid,
                    "reply_to_id": reply_to,
                }
            )
        )
        metrics.messages_sent += 1
        metrics.replies_sent += 1
        if moderated:
            metrics.moderated_messages += 1
        ulog.info(
            "REPLY room=%s temp=%s reply_to=%s", room_id[:12], tid[:8], reply_to[:8]
        )

    # 2% location share
    elif roll < 0.22:
        tid = secrets.token_hex(6)
        lat_v = 51.48 + random.uniform(-0.02, 0.02)
        lng_v = 7.22 + random.uniform(-0.02, 0.02)
        metrics._send_times[tid] = time.monotonic()
        await ws.send(
            json.dumps(
                {
                    "event": "send_message",
                    "room_id": room_id,
                    "type": "location",
                    "content": json.dumps({"lat": lat_v, "lng": lng_v}),
                    "temp_id": tid,
                }
            )
        )
        metrics.messages_sent += 1
        metrics.locations_sent += 1
        ulog.info("SEND location room=%s temp=%s", room_id[:12], tid[:8])

    # 1% meetup creation
    elif roll < 0.23:
        await ws.send(
            json.dumps(
                {
                    "event": "create_meetup",
                    "title": random.choice(
                        [
                            "Meet at main stage",
                            "Water station group",
                            "After-party crew",
                            "Lost and found meetup",
                            "Sunrise set gathering",
                        ]
                    ),
                    "meetup_time": "2026-07-12T22:00:00+02:00",
                    "label": "Main entrance",
                    "lat": 51.48 + random.uniform(-0.01, 0.01),
                    "lng": 7.22 + random.uniform(-0.01, 0.01),
                }
            )
        )
        metrics.meetups_created += 1
        ulog.info("CREATE_MEETUP room=%s", room_id[:12])

    # 1% join existing meetup
    elif roll < 0.24 and metrics.meetup_ids:
        mid = random.choice(metrics.meetup_ids)
        await ws.send(
            json.dumps(
                {
                    "event": "join_meetup",
                    "meetup_id": mid,
                }
            )
        )
        metrics.meetups_joined += 1
        ulog.debug("JOIN_MEETUP id=%s", mid[:8])

    # 2% delete own recent message
    elif roll < 0.26 and metrics.recent_msg_ids:
        target = random.choice(metrics.recent_msg_ids)
        await ws.send(
            json.dumps(
                {
                    "event": "delete_message",
                    "message_id": target,
                }
            )
        )
        metrics.messages_deleted += 1
        ulog.debug("DELETE msg=%s", target[:8])

    # 3% mark room as read
    elif roll < 0.29:
        await ws.send(
            json.dumps(
                {
                    "event": "mark_read",
                    "room_id": room_id,
                }
            )
        )
        metrics.mark_reads += 1

    # 71% plain text message
    else:
        await _send_text(ws, room_id, metrics, ulog, moderated)


def _pick_dm_target(my_idx: int, all_user_ids: list[str]) -> str | None:
    others = [uid for i, uid in enumerate(all_user_ids) if i != my_idx]
    return random.choice(others) if others else None


async def _open_dm(http, base_url, token, target_uid, ulog) -> str | None:
    try:
        resp = await http.post(
            f"{base_url}/chat/api/dms",
            json={"target_user_id": target_uid},
            cookies={"chat_session": token},
        )
        if resp.status_code in (200, 201):
            rid = resp.json().get("room_id")
            ulog.info("DM_OPEN target=%s room=%s", target_uid[:8], rid[:8])
            return rid
        ulog.warning("DM_FAIL status=%d", resp.status_code)
    except Exception as e:
        ulog.error("DM_ERR %s", e)
    return None


# ---------------------------------------------------------------------------
# Burst coordinator
# ---------------------------------------------------------------------------


async def burst_coordinator(
    burst: BurstControl,
    metrics: Metrics,
    stop: asyncio.Event,
    images: list[Path],
    msg_burst_size: int = 50,
    img_burst_size: int = 10,
):
    blog = logging.getLogger("burst")
    cycle = 0

    while not stop.is_set():
        # Wait before first burst
        try:
            await asyncio.wait_for(stop.wait(), timeout=random.uniform(60, 120))
            break
        except asyncio.TimeoutError:
            pass

        # --- Message burst ---
        cycle += 1
        blog.info("MSG_BURST #%d cooldown 12s for rate limit drain", cycle)
        burst.msg_slots = -1
        burst.trigger.set()
        await asyncio.sleep(0)
        burst.trigger.clear()
        await asyncio.sleep(12)

        blog.info("MSG_BURST #%d firing, %d slots", cycle, msg_burst_size)
        burst.msg_slots = msg_burst_size
        t0 = time.monotonic()
        burst.trigger.set()
        await asyncio.sleep(0)
        burst.trigger.clear()

        # Wait for slots to be consumed (max 5s)
        deadline = time.monotonic() + 5
        while burst.msg_slots > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        fired = msg_burst_size - burst.msg_slots
        elapsed = time.monotonic() - t0
        burst.msg_slots = 0
        burst.burst_log.append(
            {
                "type": "msg",
                "cycle": cycle,
                "fired": fired,
                "target": msg_burst_size,
                "elapsed_ms": elapsed * 1000,
                "time": time.monotonic() - metrics.start_time,
            }
        )
        blog.info(
            "MSG_BURST #%d done: %d/%d in %s",
            cycle,
            fired,
            msg_burst_size,
            fmt_ms(elapsed),
        )

        # Pause between bursts
        try:
            await asyncio.wait_for(stop.wait(), timeout=random.uniform(60, 90))
            break
        except asyncio.TimeoutError:
            pass

        # --- Image burst ---
        if not images:
            continue

        blog.info("IMG_BURST #%d cooldown 12s", cycle)
        burst.msg_slots = -1
        burst.trigger.set()
        await asyncio.sleep(0)
        burst.trigger.clear()
        await asyncio.sleep(12)

        blog.info("IMG_BURST #%d firing, %d slots", cycle, img_burst_size)
        burst.img_slots = img_burst_size
        t0 = time.monotonic()
        burst.trigger.set()
        await asyncio.sleep(0)
        burst.trigger.clear()

        deadline = time.monotonic() + 30
        while burst.img_slots > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        fired = img_burst_size - burst.img_slots
        elapsed = time.monotonic() - t0
        burst.img_slots = 0
        burst.burst_log.append(
            {
                "type": "img",
                "cycle": cycle,
                "fired": fired,
                "target": img_burst_size,
                "elapsed_ms": elapsed * 1000,
                "time": time.monotonic() - metrics.start_time,
            }
        )
        blog.info(
            "IMG_BURST #%d done: %d/%d in %s",
            cycle,
            fired,
            img_burst_size,
            fmt_ms(elapsed),
        )


# ---------------------------------------------------------------------------
# System metrics collector
# ---------------------------------------------------------------------------


async def collect_system_metrics(metrics: Metrics, db_path: str, stop: asyncio.Event):
    net_start = psutil.net_io_counters() if HAS_PSUTIL else None

    while not stop.is_set():
        try:
            if HAS_PSUTIL:
                metrics.cpu_samples.append(psutil.cpu_percent(interval=0.1))
                metrics.ram_mb_samples.append(
                    psutil.virtual_memory().used / 1024 / 1024
                )
                net = psutil.net_io_counters()
                metrics.net_sent_bytes.append(net.bytes_sent - net_start.bytes_sent)
                metrics.net_recv_bytes.append(net.bytes_recv - net_start.bytes_recv)

            p = Path(db_path)
            if p.exists():
                sz = p.stat().st_size
                wal = p.with_name(p.name + "-wal")
                if wal.exists():
                    sz += wal.stat().st_size
                metrics.db_size_mb_samples.append(sz / 1024 / 1024)

            uploads_dir = p.parent.parent / "chat" / "uploads"
            if uploads_dir.is_dir():
                total = sum(
                    f.stat().st_size for f in uploads_dir.iterdir() if f.is_file()
                )
                metrics.uploads_size_mb_samples.append(total / 1024 / 1024)

            now = time.monotonic()
            stale = [k for k, v in metrics._broadcast_lookup.items() if now - v > 60]
            for k in stale:
                del metrics._broadcast_lookup[k]

        except Exception:
            pass

        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Progress reporter
# ---------------------------------------------------------------------------


async def progress_reporter(metrics: Metrics, total: int, stop: asyncio.Event):
    start = time.monotonic()
    prev_sent = 0
    prev_recv = 0
    interval = 10

    while not stop.is_set():
        await asyncio.sleep(interval)
        if stop.is_set():
            break

        elapsed = time.monotonic() - start
        m, s = divmod(int(elapsed), 60)

        rate_s = (metrics.messages_sent - prev_sent) / interval
        rate_r = (metrics.messages_received - prev_recv) / interval
        metrics.send_rate_samples.append(rate_s)
        metrics.recv_rate_samples.append(rate_r)
        prev_sent = metrics.messages_sent
        prev_recv = metrics.messages_received

        ack = ""
        if metrics.ack_latencies:
            p = pct(metrics.ack_latencies, (50,))
            ack = f" | ack p50: {fmt_ms(p[50])}"

        ram = ""
        if metrics.ram_mb_samples:
            ram = f" | RAM: {metrics.ram_mb_samples[-1]:.0f}MB"

        db = ""
        if metrics.db_size_mb_samples:
            db = f" | DB: {metrics.db_size_mb_samples[-1]:.1f}MB"

        print(
            f"\r[{m:3d}:{s:02d}] {metrics.connections_active}/{total} conn"
            f" | {metrics.messages_sent:,} sent ({rate_s:.0f}/s)"
            f" | {metrics.messages_received:,} recv"
            f" | {metrics.ws_errors} err"
            f" | {metrics.dms_opened} dms{ack}{ram}{db}    ",
            end="",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def generate_report(metrics: Metrics, burst: BurstControl, args) -> str:
    dur = metrics.end_time - metrics.start_time
    m, s = divmod(int(dur), 60)

    lines = [
        "",
        "=" * 68,
        "  CHAT STRESS TEST REPORT",
        "=" * 68,
        f"  Duration:      {m}m {s}s",
        f"  Users:         {args.users}",
        f"  URL:           {args.url}",
        f"  Moderation:    {'on' if args.moderated else 'off'}",
        "",
        "--- Throughput " + "-" * 53,
        f"  Messages sent:      {metrics.messages_sent:>10,}",
        f"  Messages received:  {metrics.messages_received:>10,}",
        f"  Messages failed:    {metrics.messages_failed:>10,}",
        f"  Replies sent:       {metrics.replies_sent:>10,}",
        f"  Messages deleted:   {metrics.messages_deleted:>10,}",
        f"  Media uploaded:     {metrics.media_uploaded:>10,}",
        f"  Media failed:       {metrics.media_failed:>10,}",
        f"  Reactions sent:     {metrics.reactions_sent:>10,}",
        f"  Locations sent:     {metrics.locations_sent:>10,}",
        f"  Meetups created:    {metrics.meetups_created:>10,}",
        f"  Meetups joined:     {metrics.meetups_joined:>10,}",
        f"  DMs opened:         {metrics.dms_opened:>10,}",
        f"  Mark reads:         {metrics.mark_reads:>10,}",
        f"  WS errors:          {metrics.ws_errors:>10}",
        f"  WS reconnects:      {metrics.ws_reconnects:>10}",
    ]

    if dur > 0:
        lines.append(
            f"  Avg send rate:      {metrics.messages_sent / dur:>10.1f} msg/s"
        )
    if metrics.send_rate_samples:
        lines.append(
            f"  Peak send rate:     {max(metrics.send_rate_samples):>10.1f} msg/s"
        )
    if metrics.recv_rate_samples:
        lines.append(
            f"  Peak recv rate:     {max(metrics.recv_rate_samples):>10.1f} msg/s"
        )

    # Latency table
    lines.append("")
    lines.append("--- Latency " + "-" * 55)
    lines.append(f"  {'':16s}{'p50':>8s}{'p95':>8s}{'p99':>8s}{'max':>8s}{'n':>10s}")
    for name, data in [
        ("Ack", metrics.ack_latencies),
        ("Broadcast", metrics.broadcast_latencies),
        ("Room history", metrics.history_latencies),
        ("Upload (all)", metrics.upload_latencies),
        ("  Image", metrics.image_upload_latencies),
        ("  Video", metrics.video_upload_latencies),
        ("Connect", metrics.connect_latencies),
    ]:
        if data:
            p = pct(data, (50, 95, 99))
            lines.append(
                f"  {name:16s}{fmt_ms(p[50]):>8s}{fmt_ms(p[95]):>8s}"
                f"{fmt_ms(p[99]):>8s}{fmt_ms(max(data)):>8s}"
                f"{len(data):>10,}"
            )
        else:
            lines.append(
                f"  {name:16s}{'--':>8s}{'--':>8s}{'--':>8s}{'--':>8s}{'0':>10s}"
            )

    # Latency over time (ack, 5-minute windows)
    if metrics.ack_timeline and dur > 300:
        lines.append("")
        lines.append("--- Ack Latency Over Time " + "-" * 41)
        lines.append(f"  {'Window':16s}{'p50':>8s}{'p95':>8s}{'n':>8s}")
        window = 300
        start_t = metrics.start_time
        for w_start in range(0, int(dur), window):
            w_end = w_start + window
            bucket = [
                lat
                for t, lat in metrics.ack_timeline
                if w_start <= (t - start_t) < w_end
            ]
            if bucket:
                p = pct(bucket, (50, 95))
                label = f"{w_start // 60}-{w_end // 60}min"
                lines.append(
                    f"  {label:16s}{fmt_ms(p[50]):>8s}"
                    f"{fmt_ms(p[95]):>8s}{len(bucket):>8,}"
                )

    # Burst results
    if burst.burst_log:
        lines.append("")
        lines.append("--- Burst Tests " + "-" * 51)
        lines.append(
            f"  {'Type':8s}{'Cycle':>6s}{'Fired':>8s}"
            f"{'Target':>8s}{'Time':>10s}{'At':>10s}"
        )
        for b in burst.burst_log:
            lines.append(
                f"  {b['type']:8s}{b['cycle']:>6d}{b['fired']:>8d}"
                f"{b['target']:>8d}{b['elapsed_ms']:>9.0f}ms"
                f"{b['time']:>9.0f}s"
            )

    # System resources
    if metrics.ram_mb_samples or metrics.db_size_mb_samples:
        lines.append("")
        lines.append("--- System Resources " + "-" * 46)

        if metrics.ram_mb_samples:
            lines.append(
                f"  RAM (system):  start={metrics.ram_mb_samples[0]:.0f}MB  "
                f"peak={max(metrics.ram_mb_samples):.0f}MB  "
                f"end={metrics.ram_mb_samples[-1]:.0f}MB"
            )
        if metrics.cpu_samples:
            lines.append(
                f"  CPU:           avg={statistics.mean(metrics.cpu_samples):.1f}%  "
                f"peak={max(metrics.cpu_samples):.1f}%"
            )
        if metrics.db_size_mb_samples:
            growth = metrics.db_size_mb_samples[-1] - metrics.db_size_mb_samples[0]
            lines.append(
                f"  chat.db:       start={metrics.db_size_mb_samples[0]:.2f}MB  "
                f"end={metrics.db_size_mb_samples[-1]:.2f}MB  "
                f"delta={growth:+.2f}MB"
            )
        if metrics.uploads_size_mb_samples:
            u_growth = (
                metrics.uploads_size_mb_samples[-1] - metrics.uploads_size_mb_samples[0]
            )
            lines.append(
                f"  uploads/:      start={metrics.uploads_size_mb_samples[0]:.2f}MB  "
                f"end={metrics.uploads_size_mb_samples[-1]:.2f}MB  "
                f"delta={u_growth:+.2f}MB"
            )
        if metrics.net_sent_bytes:
            lines.append(f"  Net sent:      {fmt_bytes(metrics.net_sent_bytes[-1])}")
            lines.append(f"  Net recv:      {fmt_bytes(metrics.net_recv_bytes[-1])}")

    # Message delivery verification
    sent = len(metrics._sent_msg_ids)
    received = len(metrics._received_msg_ids & metrics._sent_msg_ids)
    if sent > 0:
        lines.append("")
        lines.append("--- Delivery Verification " + "-" * 41)
        lines.append(f"  Unique messages sent:      {sent:>8,}")
        lines.append(f"  Seen by other users:       {received:>8,}")
        lost = sent - received
        lines.append(f"  Not seen (possible loss):  {lost:>8,}")
        if lost > 0:
            lines.append(f"  Delivery rate:             {received / sent * 100:>7.1f}%")
        else:
            lines.append("  Delivery rate:                100.0%")

    # Cost estimation
    if args.moderated and metrics.moderated_messages > 0:
        lines.append("")
        lines.append("--- Estimated Moderation Cost " + "-" * 37)
        lines.append(f"  Messages moderated:  {metrics.moderated_messages:,}")
        lines.append("  omni-moderation:     free")
        cost = metrics.moderated_messages * COST_PER_MODERATED_MSG
        lines.append(f"  GPT-5.4-nano:        ${cost:.4f}")
        lines.append(f"  Total estimated:     ${cost:.4f}")

    lines.append("=" * 68)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(args):
    if args.cleanup_only:
        print("Cleaning up stress test data...")
        cleanup_db(args.db, None, None)
        print("Done.")
        return

    log_path = str(Path("stress_test") / f"debug_{int(time.time())}.log")
    setup_logging(log_path)
    args.moderated = not args.no_moderation

    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    needed = args.users * 2 + 100
    if soft < needed:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(needed, hard), hard))
            new_soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
            print(f"Raised file descriptor limit: {soft} -> {new_soft}")
        except (ValueError, OSError):
            print(
                f"WARNING: file descriptor limit is {soft}, need ~{needed} "
                f"for {args.users} users. Run: ulimit -n {needed}"
            )

    log.info(
        "Starting stress test: users=%d duration=%d url=%s moderated=%s",
        args.users,
        args.duration,
        args.url,
        args.moderated,
    )

    ssl_ctx = None
    if args.url.startswith("https://"):
        ssl_ctx = ssl.create_default_context()
        if args.insecure:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    images, videos = _generate_test_media(Path(args.media_dir))
    print(f"Media: {len(images)} images ({len(videos)} videos)")
    log.info("Media: %d images, %d videos", len(images), len(videos))

    print(f"Creating {args.users} test users and rooms...")
    config = setup_db(args.db, args.users, args.event_id, args.moderated)
    tokens = config["tokens"]
    user_ids = config["user_ids"]
    test_rooms = config["test_rooms"]
    all_rooms = config["all_rooms"]
    print(
        f"Rooms: {len(test_rooms)} test + {len(all_rooms) - len(test_rooms)} existing"
    )

    # Pre-flight check
    ws_base = args.url.replace("https://", "wss://").replace("http://", "ws://")
    try:
        async with websockets.connect(
            f"{ws_base}/ws/chat/{tokens[0]}",
            ssl=ssl_ctx,
            additional_headers={"Cookie": f"chat_session={tokens[0]}"},
            close_timeout=5,
        ):
            pass
        print("Pre-flight connection: OK")
    except Exception as e:
        print(f"Cannot connect to {args.url}: {e}")
        cleanup_db(args.db, test_rooms, user_ids)
        return

    metrics = Metrics()
    burst = BurstControl()
    stop = asyncio.Event()

    http = httpx.AsyncClient(
        verify=not args.insecure,
        timeout=60,
        limits=httpx.Limits(
            max_connections=args.users + 20,
            max_keepalive_connections=args.users + 20,
        ),
    )

    try:
        sys_task = asyncio.create_task(collect_system_metrics(metrics, args.db, stop))
        prog_task = asyncio.create_task(progress_reporter(metrics, args.users, stop))
        burst_task = asyncio.create_task(
            burst_coordinator(
                burst,
                metrics,
                stop,
                images,
                msg_burst_size=args.msg_burst,
                img_burst_size=args.img_burst,
            )
        )

        ramp_delay = args.ramp / max(args.users, 1)
        print(
            f"Launching {args.users} users for {args.duration}s "
            f"(ramp: {args.ramp}s, bursts: {args.msg_burst}msg/{args.img_burst}img)\n"
        )
        metrics.start_time = time.monotonic()

        user_tasks = [
            asyncio.create_task(
                simulate_user(
                    i,
                    tokens[i],
                    user_ids[i],
                    user_ids,
                    all_rooms,
                    args.url,
                    ssl_ctx,
                    metrics,
                    burst,
                    images,
                    videos,
                    args.duration,
                    ramp_delay,
                    args.moderated,
                    stop,
                    http,
                )
            )
            for i in range(args.users)
        ]

        done, pending = await asyncio.wait(
            user_tasks,
            timeout=args.duration + args.ramp + 60,
        )
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=10)

        metrics.end_time = time.monotonic()
        stop.set()

        for t in (sys_task, prog_task, burst_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        report = generate_report(metrics, burst, args)
        print(report)

        report_dir = Path("stress_test")
        report_dir.mkdir(exist_ok=True)
        report_path = report_dir / f"report_{int(time.time())}.txt"
        report_path.write_text(report)
        print(f"\nReport:  {report_path}")
        print(f"Log:     {log_path}")

    finally:
        stop.set()
        await http.aclose()
        print("\nCleaning up test data...")
        cleanup_db(args.db, test_rooms, user_ids)
        print("Cleanup complete.")


def main():
    p = argparse.ArgumentParser(
        description="Chat stress test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--url",
        default="https://localhost:64728",
        help="Server base URL (default: https://localhost:64728)",
    )
    p.add_argument("--db", default="server/data/chat.db", help="Path to chat.db")
    p.add_argument(
        "--users", type=int, default=200, help="Simulated users (default: 200)"
    )
    p.add_argument(
        "--duration", type=int, default=1800, help="Duration in seconds (default: 1800)"
    )
    p.add_argument(
        "--ramp", type=int, default=30, help="Connection ramp-up seconds (default: 30)"
    )
    p.add_argument(
        "--msg-burst",
        type=int,
        default=50,
        help="Messages per burst test (default: 50)",
    )
    p.add_argument(
        "--img-burst",
        type=int,
        default=10,
        help="Image uploads per burst test (default: 10)",
    )
    p.add_argument(
        "--media-dir",
        default="stress_test/media",
        help="Directory with test photos/videos",
    )
    p.add_argument(
        "--no-moderation",
        action="store_true",
        help="Disable AI moderation (default: moderation ON, matching production)",
    )
    p.add_argument(
        "--insecure", action="store_true", help="Skip TLS certificate verification"
    )
    p.add_argument(
        "--event-id",
        default="stone-techno-2026",
        help="Event ID (default: stone-techno-2026)",
    )
    p.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Remove leftover stress test data and exit",
    )
    args = p.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
