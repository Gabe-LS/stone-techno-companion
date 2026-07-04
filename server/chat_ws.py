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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import WebSocket, WebSocketDisconnect

from chat_db import (
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
from chat_moderation import moderate_message

logger = logging.getLogger(__name__)

_UPLOADS_DIR = Path(__file__).resolve().parent / "chat" / "uploads"
_UPLOAD_URL_RE = re.compile(r"^/chat/uploads/[a-f0-9]{32}\.(webp|mp4)$")


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
    resp = await asyncio.wait_for(
        client.get(url, headers=_og_headers, follow_redirects=False),
        timeout=3.0,
    )
    if resp.is_redirect:
        location = resp.headers.get("location", "")
        if not location or not await _is_safe_preview_url(location):
            return None
        resp = await asyncio.wait_for(
            client.get(location, headers=_og_headers, follow_redirects=False),
            timeout=3.0,
        )
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


async def _send_chat_push(
    user_id: str,
    room_id: str,
    room_type: str,
    room_name: str,
    sender_name: str,
    text_preview: str,
    msg_id: str | None = None,
) -> None:
    key = f"{user_id}:{room_id}"
    now = time.monotonic()
    if now - _push_debounce.get(key, 0) < 10:
        return
    _push_debounce[key] = now
    db = get_chat_db()
    subs = get_push_subscriptions(db, user_id)
    db.close()
    if not subs:
        return
    vapid_private_key = os.environ.get("VAPID_PRIVATE_KEY")
    if not vapid_private_key:
        return
    if "BEGIN" not in vapid_private_key and not os.path.isfile(vapid_private_key):
        logger.warning("VAPID_PRIVATE_KEY file not found: %s", vapid_private_key)
        return
    if room_type == "dm":
        title = sender_name
        body = text_preview[:100]
    elif room_type == "meetup":
        title = room_name
        body = f"{sender_name}: {text_preview[:80]}"
    else:
        title = f"#{room_name}"
        body = f"{sender_name}: {text_preview[:80]}"
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "tag": f"chat-{room_id}",
            "url": f"/chat/msg/{msg_id}" if msg_id else f"/chat/r/{room_id}",
        }
    )
    vapid_claims = {
        "sub": os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:noreply@example.com")
    }
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed, skipping chat push")
        return
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
                vapid_claims=vapid_claims,
            )
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
        self.user_rooms: dict[str, set[str]] = {}
        self._rate_buckets: dict[str, list[float]] = {}
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

    async def connect(self, ws: WebSocket, user_id: str, conn_id: str) -> None:
        self.user_conns.setdefault(user_id, {})[conn_id] = ws
        self.conn_user[conn_id] = user_id
        self.user_rooms.setdefault(user_id, set())
        self._last_ws_activity[user_id] = time.monotonic()

    def disconnect(self, conn_id: str) -> tuple[str | None, set[str]]:
        user_id = self.conn_user.pop(conn_id, None)
        if not user_id:
            return None, set()
        conns = self.user_conns.get(user_id, {})
        conns.pop(conn_id, None)
        for room_id in list(self.user_rooms.get(user_id, set())):
            room = self.rooms.get(room_id)
            if room:
                room.connections.pop(conn_id, None)
                room.conn_users.pop(conn_id, None)
        if not conns:
            self.user_conns.pop(user_id, None)
            rooms = self.user_rooms.pop(user_id, set())
            self.user_badge_rooms.pop(user_id, None)
            self.user_unread.pop(user_id, None)
            self._rate_buckets.pop(user_id, None)
            self._recent_msgs.pop(user_id, None)
            self._last_active_ts.pop(user_id, None)
            for room_id in rooms:
                room = self.rooms.get(room_id)
                if room and not any(u == user_id for u in room.conn_users.values()):
                    room.user_names.pop(user_id, None)
                    room.user_info.pop(user_id, None)
            return user_id, rooms
        return user_id, set()

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
        self.user_rooms.setdefault(user_id, set()).add(room_id)
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
        rooms = self.user_rooms.get(user_id or "")
        if rooms:
            rooms.discard(room_id)

    async def broadcast_to_room(
        self,
        room_id: str,
        event: dict,
        exclude_conn: str | None = None,
    ) -> None:
        room = self.rooms.get(room_id)
        if room:
            await room.broadcast(event, exclude_conn=exclude_conn)

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


def _build_reply_snippet(db, reply_to_id: str | None) -> dict | None:
    if not reply_to_id:
        return None
    orig = db.execute(
        "SELECT m.content, m.type, u.display_name FROM messages m "
        "JOIN users u ON u.id = m.user_id WHERE m.id = ?",
        (reply_to_id,),
    ).fetchone()
    if not orig:
        return None
    reply_text = ""
    if orig["type"] == "text":
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
        if m["reply_type"] == "text":
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
    logger.info("[MOD] text=%r is_moderated=%s", text[:50], is_moderated)
    db = get_chat_db()
    try:
        if is_moderated:
            mod_result = await moderate_message(db, user_id, text, image_url)
            logger.info("[MOD] result: %s", mod_result)
        else:
            mod_result = {"allowed": True}

        if not mod_result["allowed"]:
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
                try:
                    await ws.close(code=4003, reason="Banned")
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
        reply_snippet = _build_reply_snippet(db, reply_to_id)
        if reply_snippet:
            event_data["reply_to"] = reply_snippet
        await mgr.broadcast_to_room(room_id, event_data, exclude_conn=conn_id)

        if msg_type == "text" and text:
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

        text_preview = ""
        if msg_type == "text":
            text_preview = (text or "")[:100]
        elif msg_type == "image":
            text_preview = "Sent a photo"
        elif msg_type == "video":
            text_preview = "Sent a video"
        elif msg_type == "location":
            text_preview = "Shared a location"
        elif msg_type == "meetup_card":
            text_preview = "Shared a meetup"

        room_obj = mgr.rooms.get(room_id)
        active_viewers = set(room_obj.conn_users.values()) if room_obj else set()
        meta = mgr._room_meta.get(room_id, {"type": "general", "name": ""})
        for uid, badge_rooms in list(mgr.user_badge_rooms.items()):
            if room_id not in badge_rooms or uid in active_viewers or uid == user_id:
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
            asyncio.create_task(
                _send_chat_push(
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
                rel_url = json.loads(content).get("url", "")
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
                    await manager.join_room(
                        room_id,
                        user_id,
                        conn_id,
                        display_name,
                        username,
                        color_index,
                        avatar_url,
                        country,
                    )
                    is_member = db.execute(
                        "SELECT 1 FROM room_memberships WHERE user_id = ? AND room_id = ?",
                        (user_id, room_id),
                    ).fetchone()
                    if is_member:
                        manager.user_badge_rooms.setdefault(user_id, set()).add(room_id)
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

            elif event == "send_message":
                room_id = data.get("room_id")
                msg_type = data.get("type", "text")
                content = data.get("content", "")
                temp_id = data.get("temp_id")
                reply_to_id = data.get("reply_to_id")

                if not room_id or not content:
                    continue

                if msg_type not in SENDABLE_MSG_TYPES:
                    continue

                max_content = msg_char_limit + 20 if msg_type == "text" else 2000
                if len(content) > max_content:
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "temp_id": temp_id,
                            "reason": "Message too long.",
                        },
                    )
                    continue

                if msg_type in ("image", "video"):
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
                                "reason": "Duplicate message.",
                            },
                        )
                        continue

                room_ttl = send_room["ttl_minutes"]
                msg = create_message(
                    db,
                    room_id,
                    user_id,
                    msg_type,
                    content,
                    ttl_minutes=room_ttl,
                    reply_to_id=reply_to_id,
                )

                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "event": "message_acked",
                                "temp_id": temp_id,
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

                asyncio.create_task(
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
                stage_id = data.get("stage_id")
                title = (data.get("title") or "")[:60]
                meetup_time = data.get("meetup_time")
                if not title or not meetup_time:
                    continue
                try:
                    datetime.fromisoformat(meetup_time)
                except (ValueError, TypeError):
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
                if stage_id:
                    invite_content = json.dumps(
                        {
                            "meetup_id": meetup["id"],
                            "title": title,
                            "meetup_time": meetup_time,
                        }
                    )
                    invite_msg = create_message(
                        db, stage_id, user_id, "meetup_invite", invite_content
                    )
                    await manager.broadcast_to_room(
                        stage_id,
                        {
                            "event": "message",
                            "id": invite_msg["id"],
                            "room_id": stage_id,
                            "user_id": user_id,
                            "display_name": display_name,
                            "username": username,
                            "color_index": color_index,
                            "avatar_url": avatar_url,
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
                    {"event": "meetup_created", "meetup": meetup},
                )

            elif event == "join_meetup":
                meetup_id = data.get("meetup_id")
                if meetup_id:
                    join_meetup(db, meetup_id, user_id)
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
                    from chat_db import get_user

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
                    "SELECT id, room_id, user_id, type, content, created_at FROM messages WHERE id = ?",
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
                            url = json.loads(msg_row["content"]).get("url", "")
                            filename = url.rsplit("/", 1)[-1]
                            stem = filename.rsplit(".", 1)[0]
                            (_UPLOADS_DIR / filename).unlink(missing_ok=True)
                            (_UPLOADS_DIR / f"{stem}_mod.webp").unlink(missing_ok=True)
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
                message_id = data.get("message_id")
                reason = data.get("reason")
                if not message_id or not reason:
                    continue
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
                    sender = get_user(db, msg_row["user_id"])
                    sender_name = sender["display_name"] if sender else "Unknown"
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


async def purge_loop() -> None:
    global _purge_cycle
    while True:
        db = None
        try:
            db = get_chat_db()
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
                "WHERE r.type = 'dm' AND NOT EXISTS ("
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
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            if _purge_cycle % 2880 == 0:
                purge_stale_push_subscriptions(db)

        except Exception:
            logger.exception("Purge loop error")
        finally:
            if db:
                db.close()
        await asyncio.sleep(30)
