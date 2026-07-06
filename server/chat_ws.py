"""Chat WebSocket server: rooms, messaging, presence, typing indicators."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from fastapi import WebSocket, WebSocketDisconnect

from chat_db import (
    DEFAULT_MESSAGE_TTL_MIN,
    get_chat_db,
    get_user,
    get_user_by_token,
    get_room,
    get_main_room,
    create_message,
    get_room_messages,
    get_reactions_for_messages,
    add_reaction,
    remove_reaction,
    get_message_reactions,
    create_meetup,
    join_meetup,
    leave_meetup,
    get_meetup_attendees,
    find_or_create_dm,
    block_user,
    unblock_user,
    is_blocked,
    is_banned,
    create_report,
    update_last_seen,
    update_last_active,
    delete_user_messages,
    purge_expired_messages,
    purge_expired_meetups,
    purge_expired_sessions,
    purge_old_reports,
    purge_expired_strikes,
    purge_stale_push_subscriptions,
    sweep_stuck_pending,
    join_room_membership,
    leave_room_membership,
    mark_room_read,
    get_user_memberships,
    get_room_members,
    get_unread_counts,
    get_push_subscriptions,
    delete_push_subscription_by_endpoint,
    get_setting,
)
from chat_moderation import moderate_message, check_ban_mute

logger = logging.getLogger(__name__)

_UPLOADS_DIR = Path(__file__).resolve().parent / "chat" / "uploads"
_UPLOAD_URL_RE = re.compile(r"^/chat/uploads/[a-f0-9]{32}\.(webp|mp4)$")

# Holds references to fire-and-forget asyncio tasks so they aren't garbage
# collected mid-flight (a bare asyncio.create_task() with no held reference
# can be GC'd before completion).
_bg_tasks: set = set()


def _spawn_bg_task(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


def _is_e2ee_content(content: str) -> bool:
    try:
        c = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(c, dict) and c.get("e2ee") is True


def _dm_preview(sender_name: str) -> tuple[str, str]:
    return sender_name, "Sent you a message"


def _preview_from_content(msg_type: str, content: str) -> str:
    # Type-aware push preview text, mirroring the live-broadcast preview
    # branching in _moderate_and_broadcast. content is the raw JSON envelope
    # stored in messages.content -- for E2EE messages the server cannot read
    # it, so this returns "" and the generic DM preview is used instead.
    if _is_e2ee_content(content):
        return ""
    if msg_type == "text":
        try:
            return (json.loads(content).get("text", "") or "")[:100]
        except (json.JSONDecodeError, AttributeError, TypeError):
            return (content or "")[:100]
    if msg_type == "image":
        return "Sent a photo"
    if msg_type == "video":
        return "Sent a video"
    if msg_type == "location":
        return "Shared a location"
    if msg_type == "meetup_invite":
        return "Shared a meetup"
    return ""


def _image_to_data_uri(rel_url: str) -> str | None:
    filename = rel_url.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    mod_path = _UPLOADS_DIR / f"{stem}_mod.webp"
    if not mod_path.is_file():
        return None
    try:
        b64 = base64.b64encode(mod_path.read_bytes()).decode()
        return f"data:image/webp;base64,{b64}"
    except Exception:
        logger.exception("Failed to read moderation image: %s", mod_path)
        return None


def _video_mod_frames(rel_url: str) -> list[str]:
    filename = rel_url.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    uris = []
    for i in range(3):
        path = _UPLOADS_DIR / f"{stem}_mod{i}.webp"
        if path.is_file():
            try:
                b64 = base64.b64encode(path.read_bytes()).decode()
                uris.append(f"data:image/webp;base64,{b64}")
            except Exception:
                pass
    return uris


_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
_OG_RE = re.compile(
    r'<meta\s+(?:property|name)=["\']og:(\w+)["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_OG_RE2 = re.compile(
    r'<meta\s+content=["\']([^"\']*?)["\']\s+(?:property|name)=["\']og:(\w+)["\']',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _extract_first_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;:!?)") if m else None


_OEMBED_HOSTS = {
    "www.youtube.com": "https://www.youtube.com/oembed?format=json&url=",
    "youtube.com": "https://www.youtube.com/oembed?format=json&url=",
    "youtu.be": "https://www.youtube.com/oembed?format=json&url=",
    "soundcloud.com": "https://soundcloud.com/oembed?format=json&url=",
    "www.soundcloud.com": "https://soundcloud.com/oembed?format=json&url=",
}


async def _is_safe_preview_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    if (
        hostname in ("localhost",)
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        return False
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        for family, _, _, _, sockaddr in infos:
            addr = ipaddress.ip_address(sockaddr[0])
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
            ):
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


async def _resolve_safe_ips(hostname: str) -> list[str]:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except (socket.gaierror, ValueError):
        return []
    safe = []
    for _family, _, _, _, sockaddr in infos:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return []
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return []
        safe.append(sockaddr[0])
    return safe


async def _pinned_preview_get(client, url: str, headers: dict):
    # Resolve + validate the host, then connect to the validated IP literal with
    # Host header + TLS SNI preserved. The OS never re-resolves, so the address
    # can't be rebound to an internal/metadata target between check and connect
    # (SSRF DNS-rebinding TOCTOU).
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname
    if (
        not host
        or host == "localhost"
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        return None
    safe_ips = await _resolve_safe_ips(host)
    if not safe_ips:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    req_headers = dict(headers)
    req_headers["Host"] = host if port in (80, 443) else f"{host}:{port}"
    # Every candidate address is already SSRF-validated (private/loopback/
    # link-local/reserved rejected in _resolve_safe_ips); this loop only
    # decides which validated address is reachable, it never widens the
    # validation.
    for ip in safe_ips:
        ip_host = f"[{ip}]" if ":" in ip else ip
        pinned_url = f"{parsed.scheme}://{ip_host}:{port}{path}"
        try:
            return await asyncio.wait_for(
                client.get(
                    pinned_url,
                    headers=req_headers,
                    follow_redirects=False,
                    extensions={"sni_hostname": host},
                ),
                timeout=3.0,
            )
        except Exception:
            continue
    return None


async def _fetch_link_preview(url: str) -> dict | None:
    if not await _is_safe_preview_url(url):
        return None
    try:
        from chat_moderation import _get_http_client

        client = _get_http_client()
        parsed = urlparse(url)
        oembed_base = _OEMBED_HOSTS.get(parsed.netloc)
        if oembed_base:
            return await _fetch_oembed_preview(client, url, oembed_base)
        return await _fetch_og_preview(client, url)
    except Exception:
        logger.debug("Link preview fetch failed for %s", url[:60])
        return None


async def _fetch_oembed_preview(client, url: str, oembed_base: str) -> dict | None:
    from urllib.parse import quote

    resp = await asyncio.wait_for(
        client.get(oembed_base + quote(url, safe=""), follow_redirects=True),
        timeout=3.0,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    title = data.get("title", "")
    if not title:
        return None
    image = data.get("thumbnail_url", "")
    if image and "ytimg.com" in image:
        image = re.sub(r"/[a-z]*default\.jpg", "/hq720.jpg", image)
    return {
        "url": url,
        "title": title[:200],
        "description": data.get("author_name", ""),
        "image": image,
        "domain": data.get("provider_name", urlparse(url).netloc),
    }


async def _fetch_og_preview(client, url: str) -> dict | None:
    _og_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; StoneCompanionBot/1.0)",
        "Accept": "text/html",
    }
    resp = await _pinned_preview_get(client, url, _og_headers)
    if resp is None:
        return None
    if resp.is_redirect:
        location = resp.headers.get("location", "")
        if not location:
            return None
        location = urljoin(url, location)
        resp = await _pinned_preview_get(client, location, _og_headers)
        if resp is None:
            return None
    if resp.status_code != 200:
        return None
    cl = resp.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 1_000_000:
        return None
    ct = resp.headers.get("content-type", "")
    if "text/html" not in ct and "application/xhtml" not in ct:
        return None
    body = resp.text
    head_end = body.find("</head>")
    if head_end > 0:
        body = body[:head_end]
    else:
        body = body[:100000]
    og = {}
    for m in _OG_RE.finditer(body):
        og.setdefault(m.group(1).lower(), m.group(2))
    for m in _OG_RE2.finditer(body):
        og.setdefault(m.group(2).lower(), m.group(1))
    title = og.get("title", "")
    if not title:
        tm = _TITLE_RE.search(body)
        title = tm.group(1).strip() if tm else ""
    if not title:
        return None
    description = og.get("description", "")
    if not description:
        dm = _DESC_RE.search(body)
        description = dm.group(1).strip() if dm else ""
    image = og.get("image", "")
    if image and image.startswith("/"):
        parsed = urlparse(url)
        image = f"{parsed.scheme}://{parsed.netloc}{image}"
    domain = og.get("site_name", "") or urlparse(url).netloc
    return {
        "url": url,
        "title": title[:200],
        "description": description[:300],
        "image": image,
        "domain": domain,
    }


def _get_room_notification_targets(db, room_id: str, sender_id: str) -> list[str]:
    room = get_room(db, room_id)
    if not room:
        return []
    room_type = room["type"]
    if room_type == "dm":
        rows = db.execute(
            "SELECT user_id FROM dm_participants WHERE room_id = ? AND user_id != ?",
            (room_id, sender_id),
        ).fetchall()
        return [
            r["user_id"] for r in rows if not is_blocked(db, r["user_id"], sender_id)
        ]
    elif room_type == "meetup":
        rows = db.execute(
            "SELECT user_id FROM meetup_attendees WHERE meetup_id = ? AND user_id != ?",
            (room_id, sender_id),
        ).fetchall()
        return [
            r["user_id"] for r in rows if not is_blocked(db, r["user_id"], sender_id)
        ]
    else:
        rows = db.execute(
            "SELECT user_id FROM room_memberships WHERE room_id = ? AND user_id != ?",
            (room_id, sender_id),
        ).fetchall()
        return [
            r["user_id"] for r in rows if not is_blocked(db, r["user_id"], sender_id)
        ]


_push_debounce: dict[str, float] = {}
_push_sent: dict[str, bool] = {}
_push_flush_tasks: dict[str, asyncio.Task] = {}
_push_counter: int = 0


async def _push_or_defer(
    user_id: str,
    room_id: str,
    room_type: str,
    room_name: str,
    sender_name: str,
    text_preview: str,
    msg_id: str | None = None,
) -> None:
    global _push_counter
    key = f"{user_id}:{room_id}"
    now = time.monotonic()
    last = _push_debounce.get(key, 0)
    if now - last > 1800:
        _push_sent.pop(key, None)
    window = 60 if _push_sent.get(key) else 10
    if now - last < window:
        if key not in _push_flush_tasks:
            delay = last + window - now
            _push_flush_tasks[key] = _spawn_bg_task(
                _flush_push_later(key, delay, user_id, room_id, room_type, room_name)
            )
        return
    _push_debounce[key] = now
    silent = bool(_push_sent.get(key))
    _push_counter += 1
    sent = await _do_send_push(
        user_id,
        room_id,
        room_type,
        room_name,
        sender_name,
        text_preview,
        msg_id,
        silent=silent,
        push_index=_push_counter,
    )
    # Only escalate to silent follow-ups after a push actually went out.
    # Marking before the send poisoned the flag when the user had no
    # subscription yet: their first real push then arrived silent.
    if sent:
        _push_sent[key] = True


async def _flush_push_later(
    key: str,
    delay: float,
    user_id: str,
    room_id: str,
    room_type: str,
    room_name: str,
) -> None:
    await asyncio.sleep(delay)
    _push_flush_tasks.pop(key, None)
    await _push_or_defer(user_id, room_id, room_type, room_name, "", "", None)


async def _do_send_push(
    user_id: str,
    room_id: str,
    room_type: str,
    room_name: str,
    sender_name: str,
    text_preview: str,
    msg_id: str | None,
    silent: bool,
    push_index: int,
) -> bool:
    db = get_chat_db()
    try:
        subs = get_push_subscriptions(db, user_id)
        if not subs:
            return False
        counts = get_unread_counts(db, user_id)
        room_counts = counts.get(room_id)
        count = room_counts["count"] if room_counts else 0
        total_unread = sum(c["count"] for c in counts.values())
        if count == 0:
            return False
        last_read = room_counts["last_read_at"] if room_counts else "1970-01-01"
        now_iso = datetime.now(timezone.utc).isoformat()
        row = db.execute(
            "SELECT id FROM messages WHERE room_id = ? AND created_at > ? "
            "AND user_id != ? AND expires_at > ? AND moderation_status != 'pending' "
            "ORDER BY created_at LIMIT 1",
            (room_id, last_read, user_id, now_iso),
        ).fetchone()
        first_msg_id = row["id"] if row else msg_id
        if not sender_name:
            msg_row = db.execute(
                "SELECT m.content, m.type, u.display_name, u.username FROM messages m "
                "JOIN users u ON u.id = m.user_id "
                "WHERE m.room_id = ? AND m.created_at > ? AND m.user_id != ? AND m.expires_at > ? "
                "AND m.moderation_status != 'pending' "
                "ORDER BY m.created_at DESC LIMIT 1",
                (room_id, last_read, user_id, now_iso),
            ).fetchone()
            if msg_row:
                sender_name = msg_row["display_name"] or msg_row["username"]
                if count == 1:
                    if room_type == "dm":
                        _, text_preview = _dm_preview(sender_name)
                    else:
                        text_preview = _preview_from_content(
                            msg_row["type"], msg_row["content"]
                        )
    finally:
        db.close()

    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY")
    if not vapid_private_key:
        return False
    if "BEGIN" not in vapid_private_key and not os.path.isfile(vapid_private_key):
        logger.warning("VAPID_PRIVATE_KEY file not found: %s", vapid_private_key)
        return False

    if count == 1:
        if room_type == "dm":
            title = sender_name
            body = text_preview[:100]
        elif room_type == "meetup":
            title = room_name
            body = f"{sender_name}: {text_preview[:80]}"
        else:
            title = f"#{room_name}"
            body = f"{sender_name}: {text_preview[:80]}"
    else:
        if room_type == "dm":
            title = sender_name
        elif room_type == "meetup":
            title = room_name
        else:
            title = f"#{room_name}"
        body = f"{count} new messages"

    url = f"/chat/msg/{first_msg_id}" if first_msg_id else f"/chat/r/{room_id}"
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "room_id": room_id,
            "room_type": room_type,
            "count": count,
            "total_unread": total_unread,
            "url": url,
            "silent": silent,
            "push_index": push_index,
            # push_index resets to 0 on every server restart, so it alone can
            # collide with a still-visible notification's tag and cause iOS to
            # silently drop notificationclick on the replaced notification.
            "push_id": secrets.token_hex(8),
        }
    )

    vapid_claims = {
        "sub": os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:noreply@example.com")
    }
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed, skipping chat push")
        return False
    sent_any = False
    for sub in subs:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                # Fresh copy per endpoint: pywebpush mutates the claims dict,
                # stamping the FIRST endpoint's origin as `aud` -- a shared
                # dict poisons every later push to a different push service
                # (FCM rejects an apple aud with 403; mixed-service users
                # only ever reached the first service).
                vapid_claims=dict(vapid_claims),
                # pywebpush defaults to TTL=0 ("deliver this instant or
                # discard") -- any momentary push-service disconnect on the
                # client (Brave's GCM socket idles aggressively) silently
                # drops the push. Let the service hold it for 5 minutes.
                ttl=300,
                timeout=10,
            )
            sent_any = True
        except WebPushException as e:
            if (
                hasattr(e, "response")
                and e.response is not None
                and e.response.status_code in (404, 410)
            ):
                cleanup_db = get_chat_db()
                delete_push_subscription_by_endpoint(cleanup_db, sub["endpoint"])
                cleanup_db.close()
            else:
                logger.warning("Chat push failed for %s: %s", sub["endpoint"][:60], e)
        except Exception:
            logger.exception("Unexpected push error")
    return sent_any


class ChatRoom:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.conn_users: dict[str, str] = {}
        self.user_names: dict[str, str] = {}
        self.user_info: dict[str, dict] = {}

    async def broadcast(self, event: dict, exclude_conn: str | None = None) -> None:
        payload = json.dumps(event)
        disconnected = []
        for conn_id, ws in list(self.connections.items()):
            if conn_id == exclude_conn:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(conn_id)
        for cid in disconnected:
            uid = self.conn_users.pop(cid, None)
            self.connections.pop(cid, None)
            if uid and not any(u == uid for u in self.conn_users.values()):
                self.user_names.pop(uid, None)
                self.user_info.pop(uid, None)


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, ChatRoom] = {}
        self.user_conns: dict[str, dict[str, WebSocket]] = {}
        self.conn_user: dict[str, str] = {}
        self.conn_rooms: dict[str, set[str]] = {}
        self._rate_buckets: dict[str, list[float]] = {}
        self._broadcast_buckets: dict[str, list[float]] = {}
        self.user_badge_rooms: dict[str, set[str]] = {}
        self.user_unread: dict[str, dict[str, int]] = {}
        self._room_meta: dict[str, dict] = {}
        self._recent_msgs: dict[str, list] = {}
        self._last_active_ts: dict[str, float] = {}
        self._last_ws_activity: dict[str, float] = {}

    def should_update_last_active(self, user_id: str, interval: float = 60.0) -> bool:
        now = time.monotonic()
        last = self._last_active_ts.get(user_id, 0)
        if now - last >= interval:
            self._last_active_ts[user_id] = now
            return True
        return False

    def _get_room(self, room_id: str) -> ChatRoom:
        if room_id not in self.rooms:
            self.rooms[room_id] = ChatRoom()
        return self.rooms[room_id]

    def _user_rooms(self, user_id: str) -> set[str]:
        rooms: set[str] = set()
        for c in self.user_conns.get(user_id, {}):
            rooms |= self.conn_rooms.get(c, set())
        return rooms

    async def connect(self, ws: WebSocket, user_id: str, conn_id: str) -> None:
        self.user_conns.setdefault(user_id, {})[conn_id] = ws
        self.conn_user[conn_id] = user_id
        self.conn_rooms[conn_id] = set()
        self._last_ws_activity[user_id] = time.monotonic()

    def disconnect(self, conn_id: str) -> tuple[str | None, set[str]]:
        user_id = self.conn_user.pop(conn_id, None)
        if not user_id:
            return None, set()
        conns = self.user_conns.get(user_id, {})
        conns.pop(conn_id, None)
        this_conn_rooms = self.conn_rooms.pop(conn_id, set())
        left: set[str] = set()
        for room_id in this_conn_rooms:
            room = self.rooms.get(room_id)
            if not room:
                continue
            room.connections.pop(conn_id, None)
            room.conn_users.pop(conn_id, None)
            if not any(u == user_id for u in room.conn_users.values()):
                room.user_names.pop(user_id, None)
                room.user_info.pop(user_id, None)
                left.add(room_id)
        if not conns:
            self.user_conns.pop(user_id, None)
            self.user_badge_rooms.pop(user_id, None)
            self.user_unread.pop(user_id, None)
            self._rate_buckets.pop(user_id, None)
            self._broadcast_buckets.pop(user_id, None)
            self._recent_msgs.pop(user_id, None)
            self._last_active_ts.pop(user_id, None)
        return user_id, left

    async def join_room(
        self,
        room_id: str,
        user_id: str,
        conn_id: str,
        display_name: str,
        username: str = "",
        color_index: int = 0,
        avatar_url: str = "",
        country: str = "",
    ) -> None:
        room = self._get_room(room_id)
        ws = self.user_conns.get(user_id, {}).get(conn_id)
        if not ws:
            return
        already_in_room = any(u == user_id for u in room.conn_users.values())
        room.connections[conn_id] = ws
        room.conn_users[conn_id] = user_id
        room.user_names[user_id] = display_name
        room.user_info[user_id] = {
            "display_name": display_name,
            "username": username,
            "color_index": color_index,
            "avatar_url": avatar_url,
            "country": country,
        }
        self.conn_rooms.setdefault(conn_id, set()).add(room_id)
        if not already_in_room:
            await room.broadcast(
                {
                    "event": "presence",
                    "room_id": room_id,
                    "user_id": user_id,
                    "display_name": display_name,
                    "username": username,
                    "color_index": color_index,
                    "avatar_url": avatar_url,
                    "country": country,
                    "online": True,
                },
                exclude_conn=conn_id,
            )

    async def leave_room(self, room_id: str, conn_id: str) -> None:
        room = self.rooms.get(room_id)
        if not room:
            return
        user_id = room.conn_users.pop(conn_id, None)
        room.connections.pop(conn_id, None)
        if user_id and not any(u == user_id for u in room.conn_users.values()):
            room.user_names.pop(user_id, None)
            room.user_info.pop(user_id, None)
            await room.broadcast(
                {
                    "event": "presence",
                    "room_id": room_id,
                    "user_id": user_id,
                    "online": False,
                },
            )
        self.conn_rooms.get(conn_id, set()).discard(room_id)

    async def broadcast_to_room(
        self,
        room_id: str,
        event: dict,
        exclude_conn: str | None = None,
    ) -> None:
        room = self.rooms.get(room_id)
        if room:
            await room.broadcast(event, exclude_conn=exclude_conn)

    async def broadcast_profile_update(self, user_id: str, identity: dict) -> None:
        # Called when a user edits their profile: refresh the cached identity in
        # every room they're in and tell connected peers so already-rendered
        # messages and the member list update without a reconnect.
        for room_id in list(self._user_rooms(user_id)):
            room = self.rooms.get(room_id)
            if room and user_id in room.user_info:
                room.user_info[user_id].update(identity)
                if identity.get("display_name"):
                    room.user_names[user_id] = identity["display_name"]
            await self.broadcast_to_room(
                room_id,
                {"event": "profile_updated", "user_id": user_id, **identity},
            )

    async def send_to_user(self, user_id: str, event: dict) -> None:
        conns = self.user_conns.get(user_id, {})
        payload = json.dumps(event)
        for ws in list(conns.values()):
            try:
                await ws.send_text(payload)
            except Exception:
                pass

    async def broadcast_to_all(self, event: dict) -> None:
        payload = json.dumps(event)
        for conns in list(self.user_conns.values()):
            for ws in list(conns.values()):
                try:
                    await ws.send_text(payload)
                except Exception:
                    pass

    def get_online_users(self, room_id: str) -> list[dict]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        return [
            {"user_id": uid, **room.user_info.get(uid, {"display_name": name})}
            for uid, name in room.user_names.items()
        ]

    def check_rate_limit(
        self, user_id: str, max_msgs: int = 10, window_secs: int = 10
    ) -> bool:
        now = time.monotonic()
        bucket = self._rate_buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < window_secs]
        if len(bucket) >= max_msgs:
            return False
        bucket.append(now)
        return True

    def check_broadcast_rate(
        self, user_id: str, max_events: int = 30, window_secs: int = 10
    ) -> bool:
        # Separate, lenient bucket for high-frequency room fan-out events
        # (typing, reactions, join/leave) so they can't be spammed into a
        # broadcast-storm DoS, without consuming the send-message budget.
        now = time.monotonic()
        bucket = self._broadcast_buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < window_secs]
        if len(bucket) >= max_events:
            return False
        bucket.append(now)
        return True

    def check_duplicate(self, user_id: str, text: str, window_secs: int = 120) -> bool:
        if len(text) <= 4:
            return False
        now = time.monotonic()
        history = self._recent_msgs.get(user_id, [])
        history[:] = [(t, msg) for t, msg in history if now - t < window_secs]
        normalized = text.strip().lower()
        is_dup = any(msg == normalized for _, msg in history[-3:])
        history.append((now, normalized))
        self._recent_msgs[user_id] = history
        return is_dup


manager = ConnectionManager()


def _build_reply_snippet(
    db, reply_to_id: str | None, room_id: str | None = None
) -> dict | None:
    if not reply_to_id:
        return None
    orig = db.execute(
        "SELECT m.content, m.type, u.display_name FROM messages m "
        "JOIN users u ON u.id = m.user_id WHERE m.id = ? AND (? IS NULL OR m.room_id = ?)",
        (reply_to_id, room_id, room_id),
    ).fetchone()
    if not orig:
        return None
    reply_text = ""
    if _is_e2ee_content(orig["content"]):
        reply_text = ""
    elif orig["type"] == "text":
        try:
            reply_text = json.loads(orig["content"]).get("text", "")[:80]
        except Exception:
            reply_text = orig["content"][:80]
    return {"id": reply_to_id, "display_name": orig["display_name"], "text": reply_text}


def _format_message_for_history(m, reactions_map: dict) -> dict:
    keys = m.keys()
    d = {
        "id": m["id"],
        "room_id": m["room_id"],
        "user_id": m["user_id"],
        "display_name": m["display_name"],
        "username": m["username"] if "username" in keys else "",
        "color_index": m["color_index"] if "color_index" in keys else 0,
        "avatar_url": m["avatar_url"] if "avatar_url" in keys else "",
        "type": m["type"],
        "content": m["content"],
        "created_at": m["created_at"],
    }
    if m["reply_to_id"] and m["reply_display_name"]:
        reply_text = ""
        if _is_e2ee_content(m["reply_content"]):
            reply_text = ""
        elif m["reply_type"] == "text":
            try:
                reply_text = json.loads(m["reply_content"]).get("text", "")[:80]
            except Exception:
                reply_text = (m["reply_content"] or "")[:80]
        d["reply_to"] = {
            "id": m["reply_to_id"],
            "display_name": m["reply_display_name"],
            "text": reply_text,
        }
    if m["id"] in reactions_map:
        d["reactions"] = reactions_map[m["id"]]
    lp = m["link_preview"] if "link_preview" in keys else None
    if lp:
        try:
            d["link_preview"] = json.loads(lp) if isinstance(lp, str) else lp
        except (json.JSONDecodeError, TypeError):
            pass
    return d


async def _moderate_and_broadcast(
    mgr,
    room_id,
    user_id,
    conn_id,
    display_name,
    username,
    color_index,
    avatar_url,
    msg,
    msg_type,
    content,
    text,
    image_url,
    reply_to_id,
    ws,
    is_moderated=True,
):
    logger.info("[MOD] len=%d is_moderated=%s", len(text or ""), is_moderated)
    db = get_chat_db()
    try:
        if is_moderated:
            mod_result = await moderate_message(db, user_id, text, image_url)
            logger.info("[MOD] result: %s", mod_result)
        else:
            mod_result = await check_ban_mute(db, user_id)

        if not mod_result["allowed"]:
            if msg_type in ("image", "video"):
                try:
                    _u = msg.get("media_url") or json.loads(content).get("url", "")
                    if _u:
                        _fn = _u.rsplit("/", 1)[-1]
                        _stem = _fn.rsplit(".", 1)[0]
                        (_UPLOADS_DIR / _fn).unlink(missing_ok=True)
                        (_UPLOADS_DIR / f"{_stem}_mod.webp").unlink(missing_ok=True)
                        for _i in range(3):
                            (_UPLOADS_DIR / f"{_stem}_mod{_i}.webp").unlink(
                                missing_ok=True
                            )
                except Exception:
                    pass
            db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))
            db.commit()
            await mgr.send_to_user(
                user_id,
                {
                    "event": "message_removed",
                    "id": msg["id"],
                    "room_id": room_id,
                    "reason": mod_result["reason"],
                },
            )
            if mod_result["action"] in ("ban", "mute"):
                removed = delete_user_messages(db, user_id)
                for batch in removed:
                    await mgr.broadcast_to_room(
                        batch["room_id"],
                        {
                            "event": "messages_expired",
                            "room_id": batch["room_id"],
                            "message_ids": batch["message_ids"],
                        },
                    )
            if mod_result["action"] == "ban":
                await mgr.send_to_user(
                    user_id, {"event": "banned", "reason": mod_result["reason"]}
                )
                for _cid, _ws in list(mgr.user_conns.get(user_id, {}).items()):
                    try:
                        await _ws.close(code=4003, reason="Banned")
                    except Exception:
                        pass
            elif mod_result["action"] == "mute":
                await mgr.send_to_user(
                    user_id,
                    {
                        "event": "muted",
                        "reason": mod_result["reason"],
                        "message": mod_result.get("message", ""),
                    },
                )
            elif mod_result.get("strike_count"):
                await mgr.send_to_user(
                    user_id,
                    {
                        "event": "strike",
                        "count": mod_result["strike_count"],
                        "reason": mod_result["reason"],
                    },
                )
            return

        # Moderation passed: mark the message approved so room history serves
        # it (moderated messages start 'pending'; unmoderated are already
        # 'approved', so this is a no-op for them).
        db.execute(
            "UPDATE messages SET moderation_status = 'approved' WHERE id = ?",
            (msg["id"],),
        )
        db.commit()
        still_exists = db.execute(
            "SELECT 1 FROM messages WHERE id = ?", (msg["id"],)
        ).fetchone()
        if not still_exists:
            # Message was deleted (user delete) or purged (TTL) while
            # moderation was in flight. Do not resurrect it: skip broadcast,
            # link preview, badge fan-out and push.
            logger.info(
                "[MOD] message %s gone before approve; skipping broadcast", msg["id"]
            )
            return

        # A concurrent task's moderation (or an admin action) may have
        # banned/muted this user while this message's own content scan was
        # still in flight. Re-check ban/mute status right before broadcast so
        # a message from an already-banned/muted user cannot slip into the
        # room ahead of the other task's delete_user_messages cleanup.
        recheck = await check_ban_mute(db, user_id)
        if not recheck["allowed"]:
            if msg_type in ("image", "video"):
                try:
                    _u = msg.get("media_url") or json.loads(content).get("url", "")
                    if _u:
                        _fn = _u.rsplit("/", 1)[-1]
                        _stem = _fn.rsplit(".", 1)[0]
                        (_UPLOADS_DIR / _fn).unlink(missing_ok=True)
                        (_UPLOADS_DIR / f"{_stem}_mod.webp").unlink(missing_ok=True)
                        for _i in range(3):
                            (_UPLOADS_DIR / f"{_stem}_mod{_i}.webp").unlink(
                                missing_ok=True
                            )
                except Exception:
                    pass
            db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))
            db.commit()
            await mgr.send_to_user(
                user_id,
                {
                    "event": "message_removed",
                    "id": msg["id"],
                    "room_id": room_id,
                    "reason": recheck["reason"],
                },
            )
            return

        # Re-fetch the sender's identity so a profile edit made after this
        # connection's handshake is reflected in the live broadcast, not the
        # frozen handshake values.
        sender = get_user(db, user_id)
        if sender:
            _sk = sender.keys()
            display_name = sender["display_name"]
            if "username" in _sk:
                username = sender["username"]
            if "color_index" in _sk:
                color_index = sender["color_index"]
            if "avatar_url" in _sk:
                avatar_url = sender["avatar_url"]

        event_data = {
            "event": "message",
            "id": msg["id"],
            "room_id": room_id,
            "user_id": user_id,
            "display_name": display_name,
            "username": username,
            "color_index": color_index,
            "avatar_url": avatar_url,
            "type": msg_type,
            "content": content,
            "created_at": msg["created_at"],
        }
        reply_snippet = _build_reply_snippet(db, reply_to_id, room_id)
        if reply_snippet:
            event_data["reply_to"] = reply_snippet
        await mgr.broadcast_to_room(room_id, event_data, exclude_conn=conn_id)

        if msg_type == "text" and text and not _is_e2ee_content(content):
            link_url = _extract_first_url(text)
            if link_url:
                preview = await _fetch_link_preview(link_url)
                if preview:
                    db.execute(
                        "UPDATE messages SET link_preview = ? WHERE id = ?",
                        (json.dumps(preview), msg["id"]),
                    )
                    db.commit()
                    await mgr.broadcast_to_room(
                        room_id,
                        {
                            "event": "link_preview",
                            "message_id": msg["id"],
                            "room_id": room_id,
                            "link_preview": preview,
                        },
                    )

        meta = mgr._room_meta.get(room_id, {"type": "general", "name": ""})

        text_preview = ""
        if msg_type == "text":
            text_preview = (text or "")[:100]
        elif msg_type == "image":
            text_preview = "Sent a photo"
        elif msg_type == "video":
            text_preview = "Sent a video"
        elif msg_type == "location":
            text_preview = "Shared a location"
        elif msg_type == "meetup_invite":
            text_preview = "Shared a meetup"

        if meta.get("type") == "dm":
            _, text_preview = _dm_preview(display_name)

        # badge_update goes to EVERY member except the sender, including users
        # with a connection currently viewing this room: viewing is a
        # per-CONNECTION state the server cannot attribute (a second device in
        # another room must still get its badge), so the foreground viewer's
        # client ignores updates for its open room and mark_read broadcasts
        # the cross-device clear.
        for uid, badge_rooms in list(mgr.user_badge_rooms.items()):
            if room_id not in badge_rooms or uid == user_id:
                continue
            if is_blocked(db, uid, user_id):
                continue
            unread = mgr.user_unread.setdefault(uid, {})
            unread[room_id] = unread.get(room_id, 0) + 1
            await mgr.send_to_user(
                uid,
                {
                    "event": "badge_update",
                    "room_id": room_id,
                    "count": unread[room_id],
                    "type": meta.get("type", "general"),
                    "name": meta.get("name", ""),
                    "sender_name": display_name,
                    "preview": text_preview,
                },
            )

        connected_uids = set(mgr.user_conns.keys())
        all_targets = _get_room_notification_targets(db, room_id, user_id)
        now = time.monotonic()
        push_targets = [
            uid
            for uid in all_targets
            if uid not in connected_uids or now - mgr._last_ws_activity.get(uid, 0) > 30
        ]
        logger.info(
            "[PUSH] targets=%d all=%d connected=%d sender=%s",
            len(push_targets),
            len(all_targets),
            len(connected_uids),
            user_id[:8],
        )
        room_type = meta.get("type", "general")
        room_name = meta.get("name", "")
        for uid in push_targets:
            _spawn_bg_task(
                _push_or_defer(
                    uid,
                    room_id,
                    room_type,
                    room_name,
                    display_name,
                    text_preview,
                    msg_id=msg["id"],
                )
            )

    except Exception:
        logger.exception("Moderation task error for message %s", msg["id"])
        try:
            db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))
            db.commit()
        except Exception:
            pass
        await mgr.send_to_user(
            user_id,
            {
                "event": "message_removed",
                "id": msg["id"],
                "room_id": room_id,
                "reason": "Message could not be verified. Please try again.",
            },
        )
    finally:
        db.close()
        if msg_type in ("image", "video"):
            try:
                rel_url = msg.get("media_url") or json.loads(content).get("url", "")
                stem = rel_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if stem:
                    for suffix in (
                        "_mod.webp",
                        "_mod0.webp",
                        "_mod1.webp",
                        "_mod2.webp",
                    ):
                        p = _UPLOADS_DIR / f"{stem}{suffix}"
                        p.unlink(missing_ok=True)
            except Exception:
                pass


ALLOWED_REACTIONS = {"thumbs_up", "heart", "laugh", "fire", "wow", "clap"}
SENDABLE_MSG_TYPES = {"text", "image", "video", "location"}


async def handle_chat_ws(ws: WebSocket, token: str, event_id: str) -> None:
    db = get_chat_db()
    user = get_user_by_token(db, token)
    if not user:
        db.close()
        await ws.close(code=4001, reason="Invalid session")
        return

    # Reject a banned user before accepting the socket. Must run before
    # ws.accept()/the try block: closing db and returning from inside the try
    # would make the outer finally touch an already-closed connection and a
    # conn_id that was never registered.
    if is_banned(
        db,
        user["provider"],
        user["provider_id"],
        user["device_fingerprint"] if "device_fingerprint" in user.keys() else None,
    ):
        logger.info("[WS] rejecting banned user %s at connect", user["id"])
        db.close()
        await ws.close(code=4003, reason="Banned")
        return

    await ws.accept()
    try:
        msg_char_limit = int(get_setting(db, "msg_char_limit", "1000"))
    except (ValueError, TypeError):
        msg_char_limit = 1000
    user_id = user["id"]
    conn_id = secrets.token_hex(8)
    display_name = user["display_name"]
    ukeys = user.keys()
    username = user["username"] if "username" in ukeys else ""
    color_index = user["color_index"] if "color_index" in ukeys else 0
    avatar_url = user["avatar_url"] if "avatar_url" in ukeys else ""
    country = user["country"] if "country" in ukeys else ""

    await manager.connect(ws, user_id, conn_id)
    update_last_seen(db, user_id)

    memberships = get_user_memberships(db, user_id)
    dm_rooms = db.execute(
        "SELECT room_id FROM dm_participants WHERE user_id = ?", (user_id,)
    ).fetchall()
    manager.user_badge_rooms[user_id] = {m["room_id"] for m in memberships} | {
        d["room_id"] for d in dm_rooms
    }
    for d in dm_rooms:
        other = db.execute(
            "SELECT u.display_name FROM dm_participants dp "
            "JOIN users u ON u.id = dp.user_id "
            "WHERE dp.room_id = ? AND dp.user_id != ?",
            (d["room_id"], user_id),
        ).fetchone()
        dm_name = other["display_name"] if other else ""
        manager._room_meta[d["room_id"]] = {"type": "dm", "name": dm_name}
    manager.user_unread[user_id] = {}

    auto_join_rooms = db.execute(
        "SELECT id, type, name FROM rooms WHERE event_id = ? AND (auto_join = 1 OR is_main = 1)",
        (event_id,),
    ).fetchall()
    for aj_room in auto_join_rooms:
        join_room_membership(db, user_id, aj_room["id"])
        manager.user_badge_rooms[user_id].add(aj_room["id"])
        manager._room_meta[aj_room["id"]] = {
            "type": aj_room["type"],
            "name": aj_room["name"],
        }

    counts = get_unread_counts(db, user_id)
    if counts:
        await manager.send_to_user(
            user_id,
            {
                "event": "badge_counts",
                "counts": [
                    {
                        "room_id": rid,
                        "count": v["count"],
                        "type": v["type"],
                        "name": v["name"],
                        "last_read_at": v.get("last_read_at", ""),
                    }
                    for rid, v in counts.items()
                ],
            },
        )
        for rid, v in counts.items():
            manager.user_unread[user_id][rid] = v["count"]

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = data.get("event")
            if not event:
                continue
            if event in (
                "send_message",
                "typing",
                "add_reaction",
                "remove_reaction",
                "create_meetup",
                "open_dm",
                "delete_message",
            ):
                manager._last_ws_activity[user_id] = time.monotonic()

            if event == "join_room":
                if not manager.check_broadcast_rate(user_id):
                    continue
                room_id = data.get("room_id")
                room_row = get_room(db, room_id) if room_id else None
                if room_id and room_row:
                    if room_row["type"] == "dm":
                        if not db.execute(
                            "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                            (room_id, user_id),
                        ).fetchone():
                            continue
                    elif room_row["type"] == "meetup":
                        if not db.execute(
                            "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                            (room_id, user_id),
                        ).fetchone():
                            continue
                    # Re-fetch the sender's identity so a profile edit made
                    # after the WS handshake is reflected in the room seed
                    # and presence broadcast, not the frozen handshake
                    # values. Local-only: does not change the handshake
                    # locals used elsewhere in the loop.
                    _jr_display_name, _jr_username = display_name, username
                    _jr_color_index, _jr_avatar_url = color_index, avatar_url
                    sender = get_user(db, user_id)
                    if sender:
                        _sk = sender.keys()
                        _jr_display_name = sender["display_name"]
                        if "username" in _sk:
                            _jr_username = sender["username"]
                        if "color_index" in _sk:
                            _jr_color_index = sender["color_index"]
                        if "avatar_url" in _sk:
                            _jr_avatar_url = sender["avatar_url"]
                    await manager.join_room(
                        room_id,
                        user_id,
                        conn_id,
                        _jr_display_name,
                        _jr_username,
                        _jr_color_index,
                        _jr_avatar_url,
                        country,
                    )
                    if room_row["type"] in ("dm", "meetup"):
                        manager.user_badge_rooms.setdefault(user_id, set()).add(room_id)
                    else:
                        is_member = db.execute(
                            "SELECT 1 FROM room_memberships WHERE user_id = ? AND room_id = ?",
                            (user_id, room_id),
                        ).fetchone()
                        if is_member:
                            manager.user_badge_rooms.setdefault(user_id, set()).add(
                                room_id
                            )
                    manager._room_meta[room_id] = {
                        "type": room_row["type"],
                        "name": room_row["name"],
                    }
                    messages = get_room_messages(db, room_id, limit=50)
                    msg_ids = [m["id"] for m in messages]
                    reactions_map = get_reactions_for_messages(db, msg_ids)
                    members = get_room_members(db, room_id)
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "room_history",
                            "room_id": room_id,
                            "messages": [
                                _format_message_for_history(m, reactions_map)
                                for m in reversed(messages)
                            ],
                            "online": manager.get_online_users(room_id),
                            "members": members,
                        },
                    )

            elif event == "leave_room":
                if not manager.check_broadcast_rate(user_id):
                    continue
                room_id = data.get("room_id")
                if room_id:
                    await manager.leave_room(room_id, conn_id)

            elif event == "mark_read":
                room_id = data.get("room_id")
                timestamp = data.get("timestamp")
                if room_id:
                    mark_room_read(db, user_id, room_id, timestamp)
                    if user_id in manager.user_unread:
                        manager.user_unread[user_id].pop(room_id, None)
                    room_meta = manager._room_meta.get(room_id, {})
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "badge_update",
                            "room_id": room_id,
                            "count": 0,
                            "type": room_meta.get("type", "general"),
                            "name": room_meta.get("name", ""),
                        },
                    )
                    key = f"{user_id}:{room_id}"
                    _push_sent.pop(key, None)
                    _push_debounce.pop(key, None)
                    task = _push_flush_tasks.pop(key, None)
                    if task:
                        task.cancel()

            elif event == "visible":
                manager._last_ws_activity[user_id] = time.monotonic()

            elif event == "send_message":
                room_id = data.get("room_id")
                msg_type = data.get("type", "text")
                content = data.get("content", "")
                temp_id = data.get("temp_id")
                reply_to_id = data.get("reply_to_id")

                if not room_id or not content:
                    continue

                if not isinstance(content, str) or not isinstance(room_id, str):
                    continue
                if reply_to_id is not None:
                    if not isinstance(reply_to_id, str):
                        reply_to_id = None
                    else:
                        _rt = db.execute(
                            "SELECT 1 FROM messages WHERE id = ? AND room_id = ?",
                            (reply_to_id, room_id),
                        ).fetchone()
                        if not _rt:
                            reply_to_id = None

                if msg_type not in SENDABLE_MSG_TYPES:
                    continue

                is_e2ee_msg = _is_e2ee_content(content)
                if is_e2ee_msg:
                    e2ee_room = get_room(db, room_id)
                    if not e2ee_room or e2ee_room["type"] != "dm":
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "message_rejected",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "reason": "Encrypted messages are only supported in direct messages",
                            },
                        )
                        continue

                max_content = msg_char_limit + 20 if msg_type == "text" else 2000
                if is_e2ee_msg:
                    # v2 envelopes add ~125 chars per device slot (device_id ->
                    # wrapped message key) on top of the base overhead, and this
                    # applies to EVERY message type now, not just text -- a
                    # 12-device image envelope is ~1,600 chars. +2000 headroom
                    # covers the device cap (12 total across both users) x
                    # ~125 chars/slot; raising the cap requires raising this too.
                    if msg_type == "text":
                        max_content = max(msg_char_limit * 2, 4000) + 2000
                    else:
                        max_content = 2000 + 2000
                if len(content) > max_content:
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "temp_id": temp_id,
                            "room_id": room_id,
                            "reason": "Message too long.",
                        },
                    )
                    continue

                if msg_type in ("image", "video") and not is_e2ee_msg:
                    try:
                        _media_url = json.loads(content).get("url", "")
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        _media_url = ""
                    if not _UPLOAD_URL_RE.match(_media_url):
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "message_rejected",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "reason": "Invalid media URL.",
                            },
                        )
                        continue

                send_room = get_room(db, room_id)
                if not send_room:
                    continue
                if send_room["is_read_only"]:
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "temp_id": temp_id,
                            "room_id": room_id,
                            "reason": "This room is read-only.",
                        },
                    )
                    continue
                if not send_room["allows_media"] and msg_type in ("image", "video"):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "temp_id": temp_id,
                            "room_id": room_id,
                            "reason": "Media is not allowed in this room.",
                        },
                    )
                    continue
                if send_room["type"] == "dm":
                    if not db.execute(
                        "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                        (room_id, user_id),
                    ).fetchone():
                        continue
                    other_participant = db.execute(
                        "SELECT user_id FROM dm_participants WHERE room_id = ? AND user_id != ?",
                        (room_id, user_id),
                    ).fetchone()
                    if other_participant and is_blocked(
                        db, other_participant["user_id"], user_id
                    ):
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "message_rejected",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "reason": "Cannot message this user.",
                            },
                        )
                        continue

                if send_room["type"] == "meetup":
                    if not db.execute(
                        "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                        (room_id, user_id),
                    ).fetchone():
                        continue

                join_room_membership(db, user_id, room_id)
                manager.user_badge_rooms.setdefault(user_id, set()).add(room_id)

                if not manager.check_rate_limit(user_id):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "temp_id": temp_id,
                            "room_id": room_id,
                            "reason": "Slow down — too many messages.",
                        },
                    )
                    continue

                if msg_type == "text":
                    try:
                        text_check = json.loads(content).get("text", "")
                    except Exception:
                        text_check = content
                    if manager.check_duplicate(user_id, text_check):
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "message_rejected",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "reason": "Duplicate message.",
                            },
                        )
                        continue

                _media_url = None
                if msg_type in ("image", "video"):
                    _explicit = data.get("media_url")
                    if isinstance(_explicit, str) and _UPLOAD_URL_RE.match(_explicit):
                        _media_url = _explicit
                    else:
                        try:
                            _candidate = json.loads(content).get("url") or ""
                        except Exception:
                            _candidate = ""
                        _media_url = (
                            _candidate if _UPLOAD_URL_RE.match(_candidate) else None
                        )

                room_ttl = send_room["ttl_minutes"]
                # Moderated messages are held 'pending' so room history never
                # serves them before moderation clears them (and a
                # moderation task killed mid-flight leaves them unservable
                # rather than silently un-moderated).
                _mod_status = (
                    "pending" if bool(send_room["is_moderated"]) else "approved"
                )
                msg = create_message(
                    db,
                    room_id,
                    user_id,
                    msg_type,
                    content,
                    ttl_minutes=room_ttl,
                    reply_to_id=reply_to_id,
                    media_url=_media_url,
                    moderation_status=_mod_status,
                )

                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "event": "message_acked",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "id": msg["id"],
                                "created_at": msg["created_at"],
                            }
                        )
                    )
                except Exception:
                    pass

                text_for_moderation = ""
                if msg_type == "text":
                    try:
                        text_for_moderation = json.loads(content).get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        text_for_moderation = content
                image_url = None
                if msg_type == "image":
                    try:
                        rel_url = json.loads(content).get("url")
                        if rel_url:
                            image_url = _image_to_data_uri(rel_url)
                    except (json.JSONDecodeError, AttributeError):
                        pass
                elif msg_type == "video":
                    try:
                        rel_url = json.loads(content).get("url")
                        if rel_url:
                            frames = _video_mod_frames(rel_url)
                            if frames:
                                image_url = frames
                    except (json.JSONDecodeError, AttributeError):
                        pass

                _spawn_bg_task(
                    _moderate_and_broadcast(
                        manager,
                        room_id,
                        user_id,
                        conn_id,
                        display_name,
                        username,
                        color_index,
                        avatar_url,
                        msg,
                        msg_type,
                        content,
                        text_for_moderation,
                        image_url,
                        reply_to_id,
                        ws,
                        is_moderated=bool(send_room["is_moderated"]),
                    )
                )

            elif event == "typing":
                if not manager.check_broadcast_rate(user_id):
                    continue
                room_id = data.get("room_id")
                active = data.get("active", False)
                if room_id:
                    await manager.broadcast_to_room(
                        room_id,
                        {
                            "event": "typing",
                            "room_id": room_id,
                            "user_id": user_id,
                            "active": active,
                        },
                        exclude_conn=conn_id,
                    )

            elif event == "create_meetup":
                if not manager.check_rate_limit(user_id):
                    continue
                mod_result = await check_ban_mute(db, user_id)
                if not mod_result["allowed"]:
                    if mod_result["action"] == "ban":
                        await manager.send_to_user(
                            user_id, {"event": "banned", "reason": mod_result["reason"]}
                        )
                    elif mod_result["action"] == "mute":
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "muted",
                                "reason": mod_result["reason"],
                                "message": mod_result.get("message", ""),
                            },
                        )
                    continue
                stage_id = data.get("stage_id")
                title = (data.get("title") or "")[:60]
                meetup_time = data.get("meetup_time")
                if not title or not meetup_time:
                    continue
                try:
                    _mt = datetime.fromisoformat(meetup_time)
                except (ValueError, TypeError):
                    continue
                _now_dt = datetime.now(timezone.utc)
                if (
                    _mt.tzinfo is None
                    or _mt <= _now_dt
                    or _mt > _now_dt + timedelta(days=30)
                ):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "create_meetup_error",
                            "reason": "Pick a valid meetup time in the future.",
                        },
                    )
                    continue
                from chat_moderation import get_word_filter
                _wf = get_word_filter()
                _mtext = " ".join(filter(None, [title, data.get("note") or "", data.get("label") or ""]))
                if _mtext.strip() and _wf.check(_mtext):
                    await manager.send_to_user(user_id, {"event": "create_meetup_error", "reason": "That meetup contains content that isn't allowed."})
                    continue
                meetup = create_meetup(
                    db,
                    user_id,
                    event_id,
                    stage_id,
                    title,
                    meetup_time,
                    location_lat=data.get("lat"),
                    location_lng=data.get("lng"),
                    location_label=(data.get("label") or "")[:100],
                    note=(data.get("note") or "")[:200],
                )
                invite_room = get_room(db, stage_id) if stage_id else None
                if (
                    stage_id
                    and invite_room
                    and invite_room["type"] in ("stage", "general")
                    and not invite_room["is_read_only"]
                ):
                    invite_content = json.dumps(
                        {
                            "meetup_id": meetup["id"],
                            "title": title,
                            "meetup_time": meetup_time,
                        }
                    )
                    invite_ttl = invite_room["ttl_minutes"]
                    invite_msg = create_message(
                        db,
                        stage_id,
                        user_id,
                        "meetup_invite",
                        invite_content,
                        ttl_minutes=invite_ttl,
                    )
                    # Re-fetch the sender's identity so a profile edit made
                    # after the WS handshake is reflected in the invite
                    # broadcast, not the frozen handshake values.
                    _mi_display_name, _mi_username = display_name, username
                    _mi_color_index, _mi_avatar_url = color_index, avatar_url
                    sender = get_user(db, user_id)
                    if sender:
                        _sk = sender.keys()
                        _mi_display_name = sender["display_name"]
                        if "username" in _sk:
                            _mi_username = sender["username"]
                        if "color_index" in _sk:
                            _mi_color_index = sender["color_index"]
                        if "avatar_url" in _sk:
                            _mi_avatar_url = sender["avatar_url"]
                    await manager.broadcast_to_room(
                        stage_id,
                        {
                            "event": "message",
                            "id": invite_msg["id"],
                            "room_id": stage_id,
                            "user_id": user_id,
                            "display_name": _mi_display_name,
                            "username": _mi_username,
                            "color_index": _mi_color_index,
                            "avatar_url": _mi_avatar_url,
                            "type": "meetup_invite",
                            "content": invite_content,
                            "created_at": invite_msg["created_at"],
                        },
                    )
                broadcast_room = stage_id
                if not broadcast_room:
                    main = get_main_room(db, event_id)
                    broadcast_room = main["id"] if main else "general"
                await manager.broadcast_to_room(
                    broadcast_room,
                    {
                        "event": "meetup_created",
                        "meetup": {
                            "id": meetup["id"],
                            "title": meetup["title"],
                            "meetup_time": meetup["meetup_time"],
                            "stage_id": stage_id,
                        },
                    },
                )

            elif event == "join_meetup":
                mod_result = await check_ban_mute(db, user_id)
                if not mod_result["allowed"]:
                    if mod_result["action"] == "ban":
                        await manager.send_to_user(
                            user_id, {"event": "banned", "reason": mod_result["reason"]}
                        )
                    elif mod_result["action"] == "mute":
                        await manager.send_to_user(
                            user_id,
                            {
                                "event": "muted",
                                "reason": mod_result["reason"],
                                "message": mod_result.get("message", ""),
                            },
                        )
                    continue
                meetup_id = data.get("meetup_id")
                if meetup_id:
                    _mj = db.execute(
                        "SELECT creator_id FROM meetups WHERE id = ?", (meetup_id,)
                    ).fetchone()
                    if _mj and (
                        is_blocked(db, _mj["creator_id"], user_id)
                        or is_blocked(db, user_id, _mj["creator_id"])
                    ):
                        continue
                    if not join_meetup(db, meetup_id, user_id):
                        continue
                    attendees = [
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in get_meetup_attendees(db, meetup_id)
                    ]
                    await manager.broadcast_to_room(
                        meetup_id,
                        {
                            "event": "meetup_updated",
                            "meetup_id": meetup_id,
                            "attendees": attendees,
                        },
                    )

            elif event == "leave_meetup":
                meetup_id = data.get("meetup_id")
                if meetup_id:
                    leave_meetup(db, meetup_id, user_id)
                    attendees = [
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in get_meetup_attendees(db, meetup_id)
                    ]
                    await manager.broadcast_to_room(
                        meetup_id,
                        {
                            "event": "meetup_updated",
                            "meetup_id": meetup_id,
                            "attendees": attendees,
                        },
                    )

            elif event == "open_dm":
                target_user_id = data.get("target_user_id")
                if not target_user_id:
                    continue
                if is_blocked(db, target_user_id, user_id):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "reason": "Cannot message this user.",
                        },
                    )
                    continue
                try:
                    existing = db.execute(
                        "SELECT dp1.room_id FROM dm_participants dp1 "
                        "JOIN dm_participants dp2 ON dp1.room_id = dp2.room_id "
                        "WHERE dp1.user_id = ? AND dp2.user_id = ?",
                        (user_id, target_user_id),
                    ).fetchone()
                    target = get_user(db, target_user_id)
                    target_name = target["display_name"] if target else ""
                    if existing:
                        room_id = existing["room_id"]
                    else:
                        room_id = None
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "dm_opened",
                            "room_id": room_id,
                            "target_user_id": target_user_id,
                            "name": target_name,
                        },
                    )
                except ValueError:
                    pass

            elif event == "block_user":
                target = data.get("target_user_id")
                if target:
                    block_user(db, user_id, target)

            elif event == "unblock_user":
                target = data.get("target_user_id")
                if target:
                    unblock_user(db, user_id, target)

            elif event == "add_reaction":
                if not manager.check_broadcast_rate(user_id):
                    continue
                message_id = data.get("message_id")
                emoji = data.get("emoji")
                if not message_id or not emoji or emoji not in ALLOWED_REACTIONS:
                    continue
                msg_row = db.execute(
                    "SELECT room_id FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                if msg_row:
                    r_room = get_room(db, msg_row["room_id"])
                    if r_room and r_room["type"] == "dm":
                        if not db.execute(
                            "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    if r_room and r_room["type"] == "meetup":
                        if not db.execute(
                            "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    add_reaction(db, message_id, user_id, emoji)
                    reactions = get_message_reactions(db, message_id)
                    await manager.broadcast_to_room(
                        msg_row["room_id"],
                        {
                            "event": "reaction_updated",
                            "message_id": message_id,
                            "reactions": reactions,
                        },
                    )

            elif event == "remove_reaction":
                if not manager.check_broadcast_rate(user_id):
                    continue
                message_id = data.get("message_id")
                emoji = data.get("emoji")
                if not message_id or not emoji:
                    continue
                msg_row = db.execute(
                    "SELECT room_id FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                if msg_row:
                    r_room = get_room(db, msg_row["room_id"])
                    if r_room and r_room["type"] == "dm":
                        if not db.execute(
                            "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    if r_room and r_room["type"] == "meetup":
                        if not db.execute(
                            "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    remove_reaction(db, message_id, user_id, emoji)
                    reactions = get_message_reactions(db, message_id)
                    await manager.broadcast_to_room(
                        msg_row["room_id"],
                        {
                            "event": "reaction_updated",
                            "message_id": message_id,
                            "reactions": reactions,
                        },
                    )

            elif event == "delete_message":
                message_id = data.get("message_id")
                if not message_id:
                    continue
                msg_row = db.execute(
                    "SELECT id, room_id, user_id, type, content, media_url, created_at FROM messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                if msg_row and msg_row["user_id"] == user_id:
                    age = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(msg_row["created_at"])
                    ).total_seconds()
                    if age > 120:
                        continue
                    room_id_del = msg_row["room_id"]
                    if msg_row["type"] in ("image", "video"):
                        try:
                            url = (
                                msg_row["media_url"]
                                if (
                                    "media_url" in msg_row.keys()
                                    and msg_row["media_url"]
                                )
                                else json.loads(msg_row["content"]).get("url", "")
                            )
                            # Only unlink the physical file if no OTHER message
                            # references it. Prevents a crafted media_url pointing
                            # at another user's file from deleting it on the
                            # attacker's own message delete.
                            others = db.execute(
                                "SELECT COUNT(*) FROM messages "
                                "WHERE media_url = ? AND id != ?",
                                (url, message_id),
                            ).fetchone()[0]
                            if url and _UPLOAD_URL_RE.match(url) and others == 0:
                                filename = url.rsplit("/", 1)[-1]
                                stem = filename.rsplit(".", 1)[0]
                                (_UPLOADS_DIR / filename).unlink(missing_ok=True)
                                (_UPLOADS_DIR / f"{stem}_mod.webp").unlink(
                                    missing_ok=True
                                )
                                for i in range(3):
                                    (_UPLOADS_DIR / f"{stem}_mod{i}.webp").unlink(
                                        missing_ok=True
                                    )
                        except Exception:
                            pass
                    db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                    db.commit()
                    await manager.broadcast_to_room(
                        room_id_del,
                        {
                            "event": "message_removed",
                            "id": message_id,
                            "room_id": room_id_del,
                        },
                    )

            elif event == "report_message":
                if not manager.check_rate_limit(user_id):
                    continue
                message_id = data.get("message_id")
                reason = data.get("reason")
                client_content = data.get("message_content")
                if not message_id or not reason:
                    continue
                if isinstance(reason, str):
                    reason = reason[:500]
                if isinstance(client_content, str):
                    client_content = client_content[:2000]
                msg_row = db.execute(
                    "SELECT * FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                if msg_row:
                    report_room = get_room(db, msg_row["room_id"])
                    if report_room and report_room["type"] == "dm":
                        if not db.execute(
                            "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    if report_room and report_room["type"] == "meetup":
                        if not db.execute(
                            "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                            (msg_row["room_id"], user_id),
                        ).fetchone():
                            continue
                    sender = get_user(db, msg_row["user_id"])
                    sender_name = sender["display_name"] if sender else "Unknown"
                    unverified = 0
                    if _is_e2ee_content(msg_row["content"]):
                        if client_content:
                            try:
                                text = json.loads(client_content).get(
                                    "text", client_content
                                )
                            except (json.JSONDecodeError, AttributeError):
                                text = client_content
                            unverified = 1
                        else:
                            text = "[encrypted message - no content provided]"
                    else:
                        try:
                            text = json.loads(msg_row["content"]).get(
                                "text", msg_row["content"]
                            )
                        except (json.JSONDecodeError, AttributeError):
                            text = msg_row["content"]
                    snapshot = f"[{msg_row['created_at']}] {sender_name}: {text}"
                    create_report(
                        db,
                        user_id,
                        msg_row["user_id"],
                        snapshot,
                        msg_row["room_id"],
                        reason,
                        unverified=unverified,
                    )
                    await manager.send_to_user(
                        user_id, {"event": "report_confirmed", "message_id": message_id}
                    )

            if event in (
                "send_message",
                "typing",
                "add_reaction",
                "remove_reaction",
                "delete_message",
                "create_meetup",
                "join_meetup",
                "leave_meetup",
                "report_message",
                "mark_read",
            ) and manager.should_update_last_active(user_id):
                update_last_active(db, user_id)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Chat WebSocket error for user %s", user_id)
    finally:
        disc_user, left_rooms = manager.disconnect(conn_id)
        for room_id in left_rooms:
            await manager.broadcast_to_room(
                room_id,
                {
                    "event": "presence",
                    "room_id": room_id,
                    "user_id": user_id,
                    "online": False,
                },
            )
        update_last_seen(db, user_id)
        db.close()


_purge_cycle = 0


def _checkpoint_wal() -> None:
    """Runs on a worker thread via asyncio.to_thread -- opens its own
    connection rather than sharing the purge loop's, since sqlite3
    connections cannot cross threads."""
    checkpoint_db = get_chat_db()
    try:
        checkpoint_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint_db.close()


async def purge_loop() -> None:
    global _purge_cycle
    while True:
        db = None
        try:
            db = get_chat_db()

            # A moderation task that dies mid-flight (server restart, unhandled
            # error) leaves its message stuck 'pending' forever, since only
            # that task ever flips it to 'approved'. 3 minutes is far longer
            # than moderation ever legitimately takes.
            stuck_cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=3)
            ).isoformat()
            stuck = sweep_stuck_pending(db, stuck_cutoff)
            for stuck_id, stuck_room_id, stuck_user_id in stuck:
                logger.info("[MOD] swept stuck-pending message %s", stuck_id)
                await manager.send_to_user(
                    stuck_user_id,
                    {
                        "event": "message_removed",
                        "id": stuck_id,
                        "room_id": stuck_room_id,
                        "reason": "Message could not be verified. Please try again.",
                    },
                )

            expired_msgs = purge_expired_messages(db)
            for batch in expired_msgs:
                await manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )

            expired_meetups = purge_expired_meetups(db)
            for meetup_id in expired_meetups:
                await manager.broadcast_to_room(
                    meetup_id,
                    {
                        "event": "meetup_expired",
                        "meetup_id": meetup_id,
                    },
                )
                manager.rooms.pop(meetup_id, None)
                manager._room_meta.pop(meetup_id, None)

            purge_expired_sessions(db)
            purge_old_reports(db)
            purge_expired_strikes(db)

            empty_dms = db.execute(
                "SELECT r.id FROM rooms r "
                "WHERE r.type = 'dm' AND r.last_message_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM messages m WHERE m.room_id = r.id"
                ")"
            ).fetchall()
            for row in empty_dms:
                db.execute(
                    "DELETE FROM dm_participants WHERE room_id = ?", (row["id"],)
                )
                db.execute(
                    "DELETE FROM room_memberships WHERE room_id = ?", (row["id"],)
                )
                db.execute("DELETE FROM rooms WHERE id = ?", (row["id"],))
                manager.rooms.pop(row["id"], None)
                manager._room_meta.pop(row["id"], None)
            if empty_dms:
                db.commit()

            _purge_cycle += 1

            if _purge_cycle % 120 == 0:
                await asyncio.to_thread(_checkpoint_wal)

            if _purge_cycle % 2880 == 0:
                purge_stale_push_subscriptions(db)

            if _purge_cycle % 240 == 0:
                cutoff = time.monotonic() - 7200
                stale_keys = [k for k, v in _push_debounce.items() if v < cutoff]
                for k in stale_keys:
                    _push_debounce.pop(k, None)
                    _push_sent.pop(k, None)
                    t = _push_flush_tasks.pop(k, None)
                    if t:
                        t.cancel()
                connected = set(manager.user_conns.keys())
                stale_activity = [
                    uid
                    for uid in list(manager._last_ws_activity)
                    if uid not in connected
                    and time.monotonic() - manager._last_ws_activity.get(uid, 0) > 7200
                ]
                for uid in stale_activity:
                    manager._last_ws_activity.pop(uid, None)

        except Exception:
            logger.exception("Purge loop error")
        finally:
            if db:
                db.close()
        await asyncio.sleep(30)
