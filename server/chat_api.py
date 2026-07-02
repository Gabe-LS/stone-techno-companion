"""Chat REST API + WebSocket mount for FastAPI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    UploadFile,
    File,
)
from fastapi.responses import HTMLResponse

from chat_db import (
    get_chat_db,
    create_user,
    find_user_by_provider,
    add_user_provider,
    get_user,
    get_user_by_token,
    update_display_name,
    delete_user,
    create_session,
    ban_user as db_ban_user,
    is_banned,
    create_room,
    get_room,
    get_rooms_by_event,
    get_room_messages,
    seed_event_room,
    create_meetup,
    join_meetup as db_join_meetup,
    leave_meetup as db_leave_meetup,
    get_meetup_attendees,
    get_active_meetups,
    find_or_create_dm,
    is_blocked as db_is_blocked,
    block_user as db_block_user,
    unblock_user as db_unblock_user,
    get_pending_reports,
    resolve_report,
    hash_email,
    save_push_subscription,
    delete_push_subscription,
    get_push_subscription_count,
    leave_room_membership,
    get_admin_stats,
    search_users,
    get_user_admin_detail,
    get_all_bans,
    get_moderation_log,
    get_room_stats,
    mute_user,
    delete_room,
    update_last_seen,
    update_last_active,
    delete_user_messages,
    find_user_by_push_endpoint,
    get_reachable_member_counts,
)
from chat_ws import handle_chat_ws, purge_loop

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/api")
DEFAULT_EVENT_ID = os.environ.get("CHAT_EVENT_ID", "stone-techno-2026")
ADMIN_TOKEN = os.environ.get("CHAT_ADMIN_TOKEN", "")
_ADMIN_EMAIL_HASHES: set[str] = set()


def _load_admin_emails() -> None:
    global _ADMIN_EMAIL_HASHES
    raw = os.environ.get("CHAT_ADMIN_EMAILS", "")
    if raw:
        _ADMIN_EMAIL_HASHES = {
            hash_email(e.strip()) for e in raw.split(",") if e.strip()
        }


_email_rate: dict[str, list[float]] = {}

DISPOSABLE_DOMAINS: set[str] = set()
_DISPOSABLE_PATH = Path(__file__).resolve().parent / "chat" / "disposable_domains.txt"


def _load_disposable_domains() -> None:
    global DISPOSABLE_DOMAINS
    if _DISPOSABLE_PATH.exists():
        DISPOSABLE_DOMAINS = {
            line.strip().lower()
            for line in _DISPOSABLE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        logger.info("Loaded %d disposable email domains", len(DISPOSABLE_DOMAINS))


def _get_db():
    return get_chat_db()


def _get_user_from_cookie(request: Request):
    token = request.cookies.get("chat_session")
    if not token:
        raise HTTPException(401, "Not authenticated")
    db = _get_db()
    try:
        user = get_user_by_token(db, token)
        if not user:
            raise HTTPException(401, "Session expired")
    except Exception:
        db.close()
        raise
    return user, db


def _require_admin(request: Request) -> None:
    header_token = request.headers.get("X-Admin-Token") or ""
    if (
        ADMIN_TOKEN
        and header_token
        and secrets.compare_digest(header_token, ADMIN_TOKEN)
    ):
        return
    session_token = request.cookies.get("chat_session")
    if session_token and _ADMIN_EMAIL_HASHES:
        db = _get_db()
        try:
            user = get_user_by_token(db, session_token)
            if user:
                providers = db.execute(
                    "SELECT provider, provider_id FROM user_providers WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
                for p in providers:
                    if p["provider_id"] in _ADMIN_EMAIL_HASHES:
                        return
        finally:
            db.close()
    raise HTTPException(403, "Admin access required")


def _set_session_cookie(response: Response, token: str) -> None:
    is_prod = not os.environ.get("CHAT_BASE_URL", "").startswith("http://")
    response.set_cookie(
        "chat_session",
        token,
        httponly=False,  # JS reads cookie for WebSocket auth URL
        secure=is_prod,
        samesite="lax" if not is_prod else "strict",
        max_age=7 * 24 * 3600,
        path="/",
    )


def _authenticate(
    db,
    provider: str,
    provider_id: str,
    display_name: str,
    device_fingerprint: str | None,
    response: Response,
) -> dict:
    ban = is_banned(db, provider, provider_id, device_fingerprint)
    if ban:
        raise HTTPException(403, f"You have been banned: {ban['reason']}")

    user = find_user_by_provider(db, provider, provider_id)
    if user:
        user_dict = dict(user)
    else:
        user_dict = create_user(
            db, provider, provider_id, display_name, device_fingerprint
        )

    session = create_session(db, user_dict["id"])
    _set_session_cookie(response, session["token"])

    return {
        "id": user_dict["id"],
        "display_name": user_dict["display_name"],
        "provider": provider,
    }


# --- Auth ---


@router.get("/config")
async def get_config():
    google_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    return {"google_client_id": google_id if google_id else None}


@router.post("/auth/google")
async def auth_google(request: Request, response: Response):
    body = await request.json()
    id_token = body.get("id_token")
    fingerprint = body.get("device_fingerprint")
    if not id_token:
        raise HTTPException(400, "id_token required")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(501, "Google Sign-In not configured")

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        info = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), client_id
        )
        provider_id = info["sub"]
        email = info.get("email", "")
        name = info.get("name") or email.split("@")[0]
    except Exception as e:
        logger.warning("Google token verification failed: %s", e)
        raise HTTPException(401, "Invalid Google token")

    db = _get_db()
    try:
        user = find_user_by_provider(db, "google", provider_id)
        if not user and email:
            email_hash = hash_email(email)
            user = find_user_by_provider(db, "email", email_hash)
            if user:
                add_user_provider(db, user["id"], "google", provider_id)
                logger.info("Linked google provider to existing user %s", user["id"])
        result = _authenticate(db, "google", provider_id, name, fingerprint, response)
        if email:
            add_user_provider(db, result["id"], "email", hash_email(email))
        return result
    finally:
        db.close()


@router.post("/auth/google/code")
async def auth_google_code(request: Request, response: Response):
    body = await request.json()
    code = body.get("code")
    fingerprint = body.get("device_fingerprint")
    if not code:
        raise HTTPException(400, "code required")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(501, "Google Sign-In not configured")

    try:
        import httpx

        token_resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "postmessage",
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        id_token_str = token_data.get("id_token")
        if not id_token_str:
            raise ValueError(token_data.get("error_description", "No id_token"))

        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        info = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), client_id
        )
        provider_id = info["sub"]
        email = info.get("email", "")
        name = info.get("name") or email.split("@")[0]
    except Exception as e:
        logger.warning("Google code exchange failed: %s", e)
        raise HTTPException(401, "Google authentication failed")

    db = _get_db()
    try:
        user = find_user_by_provider(db, "google", provider_id)
        if not user and email:
            email_hash = hash_email(email)
            user = find_user_by_provider(db, "email", email_hash)
            if user:
                add_user_provider(db, user["id"], "google", provider_id)
                logger.info("Linked google provider to existing user %s", user["id"])
        result = _authenticate(db, "google", provider_id, name, fingerprint, response)
        if email:
            add_user_provider(db, result["id"], "email", hash_email(email))
        return result
    finally:
        db.close()


@router.post("/login")
async def auth_email_start(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    _email_rate[ip] = [t for t in _email_rate.get(ip, []) if now - t < 900]
    if len(_email_rate[ip]) >= 5:
        raise HTTPException(429, "Too many requests. Try again later.")
    _email_rate[ip].append(now)
    if len(_email_rate) > 1000:
        stale = [k for k, v in _email_rate.items() if all(now - t >= 900 for t in v)]
        for k in stale:
            del _email_rate[k]
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    fingerprint = body.get("device_fingerprint")
    try:
        from email_validator import validate_email

        result = await asyncio.to_thread(
            validate_email, email, check_deliverability=True
        )
        email = result.normalized
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))

    domain = email.split("@")[1]
    if domain in DISPOSABLE_DOMAINS:
        raise HTTPException(400, "Disposable email addresses are not allowed")

    token = secrets.token_urlsafe(32)
    provider_id = hash_email(email)

    db = _get_db()
    try:
        ban = is_banned(db, "email", provider_id, fingerprint)
        if ban:
            raise HTTPException(403, f"You have been banned: {ban['reason']}")

        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO email_tokens (token, email, provider_id, fingerprint, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, email, provider_id, fingerprint, expires),
        )
        db.commit()
    finally:
        db.close()

    maileroo_key = os.environ.get("MAILEROO_API_KEY")
    if maileroo_key:
        try:
            from maileroo import MailerooClient, EmailAddress

            client = MailerooClient(api_key=maileroo_key)
            base_url = os.environ.get(
                "CHAT_BASE_URL", "https://stonetechno.deftlab.dev"
            )
            verify_url = f"{base_url}/chat/v/{token}"
            from_addr = os.environ.get("CHAT_EMAIL_FROM", "no-reply@deftlab.dev")
            await asyncio.to_thread(
                client.send_basic_email,
                {
                    "from": EmailAddress(from_addr),
                    "to": [EmailAddress(email)],
                    "subject": "Sign in to Festival Chat",
                    "html": f'<p>Click to sign in:</p><p><a href="{verify_url}">{verify_url}</a></p>'
                    f"<p>This link expires in 15 minutes.</p>",
                },
            )
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            raise HTTPException(500, "Failed to send email")
    else:
        logger.warning("MAILEROO_API_KEY not set — email not sent")

    return {"sent": True}


@router.get("/verify")
async def auth_email_verify(request: Request, token: str = ""):
    db = _get_db()
    try:
        row = db.execute(
            "SELECT email, provider_id, fingerprint, expires_at FROM email_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(400, "Invalid or expired link")
        db.execute("DELETE FROM email_tokens WHERE token = ?", (token,))
        db.commit()
        if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
            raise HTTPException(400, "Link expired")

        name = ""

        from starlette.responses import RedirectResponse

        base_url = os.environ.get("CHAT_BASE_URL", "")
        redirect_url = f"{base_url}/chat" if base_url else "/chat"
        redirect = RedirectResponse(url=redirect_url, status_code=302)
        _authenticate(
            db, "email", row["provider_id"], name, row["fingerprint"], redirect
        )
        return redirect
    finally:
        db.close()


@router.post("/logout")
async def auth_logout(request: Request, response: Response):
    token = request.cookies.get("chat_session")
    if token:
        db = _get_db()
        try:
            db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            db.commit()
        finally:
            db.close()
    response.delete_cookie("chat_session")
    return {"ok": True}


@router.delete("/account")
async def auth_delete_account(request: Request, response: Response):
    user, db = _get_user_from_cookie(request)
    try:
        removed = delete_user_messages(db, user["id"])
        delete_user(db, user["id"])
        response.delete_cookie("chat_session")
        from chat_ws import manager

        for batch in removed:
            asyncio.create_task(
                manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )
            )
        return {"ok": True}
    finally:
        db.close()


import re
import unicodedata

try:
    import regex as _re_mod

    _DISPLAYNAME_RE = _re_mod.compile(
        r"^[\p{Script=Latin}\d][\p{Script=Latin}\d ._-]*[\p{Script=Latin}\d]$"
        r"|^[\p{Script=Latin}\d]{1,2}$",
        _re_mod.UNICODE,
    )
except ImportError:
    _re_mod = re
    _DISPLAYNAME_RE = re.compile(
        r"^[a-zA-ZÀ-ɏ\d][a-zA-ZÀ-ɏ\d ._-]*[a-zA-ZÀ-ɏ\d]$"
        r"|^[a-zA-ZÀ-ɏ\d]{1,2}$",
        re.UNICODE,
    )

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]{1,2}$")
_USERNAME_BAD_RE = re.compile(r"[._-]{2}")


def _validate_username(
    username: str, db, exclude_user_id: str | None = None
) -> str | None:
    if not username or len(username) < 2 or len(username) > 20:
        return "2-20 characters"
    if not _USERNAME_RE.match(username):
        return "Allowed: a-z 0-9 . _ -"
    if _USERNAME_BAD_RE.search(username):
        return "No consecutive . _ -"
    from chat_moderation import get_word_filter

    wf = get_word_filter()
    if wf.check_username(username):
        return "Username not allowed"
    lower = username.lower()
    query = "SELECT id FROM users WHERE username_lower = ?"
    params = [lower]
    if exclude_user_id:
        query += " AND id != ?"
        params.append(exclude_user_id)
    if db.execute(query, params).fetchone():
        return "Username taken"
    return None


def _validate_display_name(name: str) -> str | None:
    if not name or len(name) < 2 or len(name) > 30:
        return "2-30 characters"
    normalized = unicodedata.normalize("NFKC", name)
    try:
        if not _DISPLAYNAME_RE.match(normalized):
            return "Allowed: a-z 0-9 spaces . _ -"
    except Exception:
        return "Invalid characters"
    if "  " in normalized:
        return "No double spaces"
    from chat_moderation import get_word_filter

    wf = get_word_filter()
    if wf.check(name):
        return "Display name not allowed"
    return None


@router.get("/check-username")
async def check_username(request: Request, name: str = ""):
    user, db = _get_user_from_cookie(request)
    try:
        err = _validate_username(name, db, user["id"])
        return {"available": err is None, "reason": err or ""}
    finally:
        db.close()


@router.get("/check-name")
async def check_displayname(request: Request, name: str = ""):
    err = _validate_display_name(name)
    return {"available": err is None, "reason": err or ""}


@router.put("/profile")
async def auth_update_profile(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        updates = []
        params = []
        name = body.get("display_name")
        if name:
            name = unicodedata.normalize("NFKC", name.strip())[:30]
            err = _validate_display_name(name)
            if err:
                raise HTTPException(400, err)
            updates.append("display_name = ?")
            params.append(name)
        username = body.get("username")
        if username:
            username = username.strip()[:20]
            err = _validate_username(username, db, user["id"])
            if err:
                raise HTTPException(400, err)
            updates.append("username = ?")
            params.append(username)
            updates.append("username_lower = ?")
            params.append(username.lower())

        text_to_moderate = " ".join(filter(None, [username, name]))
        if text_to_moderate:
            from chat_moderation import check_openai_moderation

            try:
                ai_result = await check_openai_moderation(text_to_moderate)
            except Exception:
                raise HTTPException(
                    500, "Name could not be verified. Please try again."
                )
            if ai_result:
                raise HTTPException(
                    400,
                    f"Name not allowed: {ai_result.get('category', 'content policy')}",
                )

        country = body.get("country")
        if country is not None:
            updates.append("country = ?")
            params.append(country.strip()[:2].upper())
        avatar_url = body.get("avatar_url")
        if avatar_url is not None:
            if avatar_url and not avatar_url.startswith("/chat/api/avatar/"):
                raise HTTPException(400, "Invalid avatar URL")
            updates.append("avatar_url = ?")
            params.append(avatar_url)
        if updates:
            params.append(user["id"])
            try:
                db.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params
                )
                db.commit()
            except sqlite3.IntegrityError:
                raise HTTPException(400, "Username taken")
        return {"ok": True}
    finally:
        db.close()


@router.get("/me")
async def auth_me(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        keys = user.keys()
        return {
            "id": user["id"],
            "display_name": user["display_name"],
            "username": user["username"] if "username" in keys else "",
            "country": user["country"] if "country" in keys else "",
            "avatar_url": user["avatar_url"] if "avatar_url" in keys else "",
            "color_index": user["color_index"] if "color_index" in keys else 0,
            "provider": user["provider"],
        }
    finally:
        db.close()


# --- Rooms ---


@router.get("/rooms")
async def list_rooms(request: Request):
    db = _get_db()
    try:
        from chat_ws import manager

        user_id = None
        token = request.cookies.get("chat_session")
        if token:
            user = get_user_by_token(db, token)
            if user:
                user_id = user["id"]
        member_rooms = set()
        if user_id:
            rows = db.execute(
                "SELECT room_id FROM room_memberships WHERE user_id = ?", (user_id,)
            ).fetchall()
            member_rooms = {r["room_id"] for r in rows}
        rooms = get_rooms_by_event(db, DEFAULT_EVENT_ID)
        room_ids = [r["id"] for r in rooms]
        reachable = get_reachable_member_counts(db, room_ids)
        last_msgs = {}
        for row in db.execute(
            "SELECT room_id, MAX(created_at) as last_at FROM messages "
            "WHERE expires_at > ? GROUP BY room_id",
            (datetime.now(timezone.utc).isoformat(),),
        ).fetchall():
            last_msgs[row["room_id"]] = row["last_at"]
        result = [
            {
                "id": r["id"],
                "type": r["type"],
                "name": r["name"],
                "description": r["description"] or "",
                "is_main": bool(r["is_main"]),
                "is_moderated": bool(r["is_moderated"]),
                "is_read_only": bool(r["is_read_only"]),
                "allows_media": bool(r["allows_media"]),
                "ttl_minutes": r["ttl_minutes"],
                "online_count": len(manager.get_online_users(r["id"])),
                "member_count": reachable.get(r["id"], 0),
                "is_member": r["id"] in member_rooms,
                "last_message_at": last_msgs.get(r["id"], ""),
            }
            for r in rooms
        ]
        result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
        result.sort(key=lambda r: r.get("position", 0))
        return result
    finally:
        db.close()


@router.post("/rooms/{room_id}/join", status_code=204)
async def join_room_endpoint(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        from chat_db import join_room_membership

        join_room_membership(db, user["id"], room_id)
        return Response(status_code=204)
    finally:
        db.close()


@router.delete("/rooms/{room_id}/join", status_code=204)
async def leave_room_endpoint(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["is_main"]:
            raise HTTPException(400, "Cannot leave main room")
        leave_room_membership(db, user["id"], room_id)
        return Response(status_code=204)
    finally:
        db.close()


@router.get("/rooms/{room_id}/messages")
async def room_messages(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["type"] == "dm":
            if not db.execute(
                "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                (room_id, user["id"]),
            ).fetchone():
                raise HTTPException(403, "Access denied")
        elif room["type"] == "meetup":
            if not db.execute(
                "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                (room_id, user["id"]),
            ).fetchone():
                raise HTTPException(403, "Access denied")
        messages = get_room_messages(db, room_id, limit=100)
        return [
            {
                "id": m["id"],
                "user_id": m["user_id"],
                "display_name": m["display_name"],
                "type": m["type"],
                "content": m["content"],
                "created_at": m["created_at"],
            }
            for m in reversed(messages)
        ]
    finally:
        db.close()


@router.get("/messages/{message_id}")
async def get_message_context(message_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        msg = db.execute(
            "SELECT room_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found")
        room = get_room(db, msg["room_id"])
        if room and room["type"] == "dm":
            if not db.execute(
                "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                (msg["room_id"], user["id"]),
            ).fetchone():
                raise HTTPException(404, "Message not found")
        return {
            "message_id": message_id,
            "room_id": msg["room_id"],
            "room_name": room["name"] if room else "Chat",
            "room_type": room["type"] if room else "general",
        }
    finally:
        db.close()


@router.get("/rooms/{room_id}/info")
async def room_info(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        return {"id": room["id"], "name": room["name"], "type": room["type"]}
    finally:
        db.close()


@router.get("/rooms/{room_id}/online")
async def room_online(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        room = get_room(db, room_id)
        if room and room["type"] == "dm":
            if not db.execute(
                "SELECT 1 FROM dm_participants WHERE room_id = ? AND user_id = ?",
                (room_id, user["id"]),
            ).fetchone():
                raise HTTPException(403, "Access denied")
        from chat_ws import manager

        return manager.get_online_users(room_id)
    finally:
        db.close()


# --- Meetups ---


@router.get("/meetups")
async def list_meetups(request: Request, stage_id: str | None = None):
    user, db = _get_user_from_cookie(request)
    try:
        user_id = user["id"]
        meetups = get_active_meetups(db, DEFAULT_EVENT_ID)
        now = datetime.now(timezone.utc).isoformat()
        last_msgs = {}
        for row in db.execute(
            "SELECT room_id, MAX(created_at) as last_at FROM messages "
            "WHERE expires_at > ? GROUP BY room_id",
            (now,),
        ).fetchall():
            last_msgs[row["room_id"]] = row["last_at"]
        result = []
        for m in meetups:
            if stage_id and m["stage_id"] != stage_id:
                continue
            attendees = get_meetup_attendees(db, m["id"])
            att_ids = {a["id"] for a in attendees}
            result.append(
                {
                    "id": m["id"],
                    "title": m["title"],
                    "meetup_time": m["meetup_time"],
                    "location_lat": m["location_lat"],
                    "location_lng": m["location_lng"],
                    "location_label": m["location_label"],
                    "note": m["note"],
                    "stage_id": m["stage_id"],
                    "attendee_count": m["attendee_count"],
                    "is_going": user_id in att_ids,
                    "attendees": [
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in attendees
                    ],
                    "expires_at": m["expires_at"],
                    "last_message_at": last_msgs.get(m["id"], ""),
                }
            )
        result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
        return result
    finally:
        db.close()


@router.get("/meetups/{meetup_id}")
async def get_meetup(meetup_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        meetup = db.execute(
            "SELECT * FROM meetups WHERE id = ?", (meetup_id,)
        ).fetchone()
        if not meetup:
            raise HTTPException(404, "Meetup not found")
        attendees = get_meetup_attendees(db, meetup_id)
        return {
            "id": meetup["id"],
            "title": meetup["title"],
            "meetup_time": meetup["meetup_time"],
            "location_lat": meetup["location_lat"],
            "location_lng": meetup["location_lng"],
            "location_label": meetup["location_label"],
            "note": meetup["note"],
            "attendees": [
                {"id": a["id"], "display_name": a["display_name"]} for a in attendees
            ],
            "expires_at": meetup["expires_at"],
        }
    finally:
        db.close()


@router.post("/meetups", status_code=201)
async def create_meetup_endpoint(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        title = (body.get("title") or "")[:60]
        meetup_time = body.get("meetup_time")
        if not title or not meetup_time:
            raise HTTPException(400, "title and meetup_time required")
        try:
            datetime.fromisoformat(meetup_time)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid meetup_time format")

        meetup = create_meetup(
            db,
            user["id"],
            DEFAULT_EVENT_ID,
            body.get("stage_id"),
            title,
            meetup_time,
            location_lat=body.get("lat"),
            location_lng=body.get("lng"),
            location_label=(body.get("label") or "")[:100],
            note=(body.get("note") or "")[:200],
        )
        return meetup
    finally:
        db.close()


@router.post("/meetups/{meetup_id}/join")
async def join_meetup_endpoint(meetup_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        db_join_meetup(db, meetup_id, user["id"])
        attendees = get_meetup_attendees(db, meetup_id)
        return [{"id": a["id"], "display_name": a["display_name"]} for a in attendees]
    finally:
        db.close()


@router.delete("/meetups/{meetup_id}/join")
async def leave_meetup_endpoint(meetup_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        db_leave_meetup(db, meetup_id, user["id"])
        attendees = get_meetup_attendees(db, meetup_id)
        return [{"id": a["id"], "display_name": a["display_name"]} for a in attendees]
    finally:
        db.close()


# --- DMs ---


@router.get("/dms")
async def list_dms(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        now = datetime.now(timezone.utc).isoformat()
        dms = db.execute(
            "SELECT r.id, r.name, dp2.user_id AS other_user_id, u.display_name AS other_name, "
            "(SELECT MAX(m.created_at) FROM messages m WHERE m.room_id = r.id AND m.expires_at > ?) AS last_message_at "
            "FROM dm_participants dp1 "
            "JOIN dm_participants dp2 ON dp1.room_id = dp2.room_id AND dp1.user_id != dp2.user_id "
            "JOIN rooms r ON r.id = dp1.room_id "
            "JOIN users u ON u.id = dp2.user_id "
            "WHERE dp1.user_id = ? "
            "ORDER BY last_message_at DESC",
            (now, user["id"]),
        ).fetchall()
        return [
            {
                "room_id": dm["id"],
                "other_user_id": dm["other_user_id"],
                "other_name": dm["other_name"],
                "last_message_at": dm["last_message_at"] or "",
            }
            for dm in dms
        ]
    finally:
        db.close()


@router.post("/dms", status_code=201)
async def create_dm(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        target_id = body.get("target_user_id")
        if not target_id:
            raise HTTPException(400, "target_user_id required")
        if target_id == user["id"]:
            raise HTTPException(400, "Cannot message yourself")
        if db_is_blocked(db, target_id, user["id"]):
            raise HTTPException(403, "Cannot message this user")
        try:
            room_id = find_or_create_dm(db, DEFAULT_EVENT_ID, user["id"], target_id)
        except ValueError:
            raise HTTPException(404, "User not found")
        return {"room_id": room_id}
    finally:
        db.close()


# --- Users ---


@router.post("/users/{user_id}/block")
async def block_user_endpoint(user_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        if not get_user(db, user_id):
            raise HTTPException(404, "User not found")
        db_block_user(db, user["id"], user_id)
        return {"ok": True}
    finally:
        db.close()


@router.delete("/users/{user_id}/block")
async def unblock_user_endpoint(user_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        db_unblock_user(db, user["id"], user_id)
        return {"ok": True}
    finally:
        db.close()


# --- Media ---


@router.post("/upload/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)
    try:
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(400, "Only image files allowed")

        data = await file.read()
        if len(data) > 500 * 1024:
            raise HTTPException(400, "Max file size is 500KB")

        try:
            import pyvips

            img = pyvips.Image.new_from_buffer(data, "")
            if img.width * img.height > 10_000_000:
                raise HTTPException(400, "Image too large")
            data = img.webpsave_buffer(Q=80)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Invalid image file")

        import time

        version = str(int(time.time()))
        avatar_url = f"/chat/api/avatar/{user['id']}?v={version}"
        db.execute(
            "UPDATE users SET avatar_url = ? WHERE id = ?",
            (avatar_url, user["id"]),
        )
        db.execute(
            "INSERT OR REPLACE INTO avatars (user_id, data) VALUES (?, ?)",
            (user["id"], data),
        )
        db.commit()

        return {"url": avatar_url}
    finally:
        db.close()


@router.get("/avatar/{user_id}")
async def get_avatar(user_id: str):
    db = _get_db()
    try:
        row = db.execute(
            "SELECT data FROM avatars WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Avatar not found")
        from starlette.responses import Response as RawResponse

        return RawResponse(
            content=row["data"],
            media_type="image/webp",
            headers={
                "Cache-Control": "no-cache",
                "ETag": hashlib.md5(row["data"]).hexdigest(),
            },
        )
    finally:
        db.close()


@router.post("/upload/image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)
    db.close()

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files allowed")

    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Max file size is 5MB")

    upload_dir = Path(__file__).resolve().parent / "chat" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(16)
    filename = f"{token}.webp"
    out_path = upload_dir / filename

    def _process_image():
        import pyvips

        img = pyvips.Image.new_from_buffer(data, "")
        w, h = img.width, img.height
        if w * h > 40_000_000:
            raise ValueError("Image too large")
        max_side = max(w, h)
        if max_side > 1500:
            scale = 1500 / max_side
            img = img.resize(scale, kernel=pyvips.enums.Kernel.LANCZOS3)
            w, h = img.width, img.height
        img.webpsave(str(out_path), Q=80)
        mod_path = upload_dir / f"{token}_mod.webp"
        if max(w, h) > 880:
            mod_scale = 800 / max(w, h)
            mod = img.resize(mod_scale, kernel=pyvips.enums.Kernel.LANCZOS3)
            mod.webpsave(str(mod_path), Q=60)
        else:
            img.webpsave(str(mod_path), Q=60)
        return w, h

    try:
        width, height = await asyncio.to_thread(_process_image)
    except Exception as e:
        out_path.unlink(missing_ok=True)
        logger.error("Image processing failed: %s", e)
        raise HTTPException(500, "Image processing failed")

    return {
        "url": f"/chat/uploads/{filename}",
        "width": width,
        "height": height,
    }


@router.post("/upload/video")
async def upload_video(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)
    db.close()

    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "Only video files allowed")

    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "Max file size is 100MB")

    upload_dir = Path(__file__).resolve().parent / "chat" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(16)
    filename = f"{token}.mp4"
    out_path = upload_dir / filename
    out_path.write_bytes(data)

    import subprocess

    try:
        probe = await asyncio.to_thread(
            subprocess.run,
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        info = json.loads(probe.stdout)
        duration = float(info["format"].get("duration", 0))
        if duration > 65:
            out_path.unlink(missing_ok=True)
            raise HTTPException(400, "Video must be 60 seconds or less")

        video_stream = next(
            (s for s in info["streams"] if s["codec_type"] == "video"), None
        )
        width = int(video_stream["width"]) if video_stream else 0
        height = int(video_stream["height"]) if video_stream else 0
    except HTTPException:
        raise
    except Exception:
        out_path.unlink(missing_ok=True)
        raise HTTPException(400, "Could not process video file")

    mod_frames = 0
    for i, frac in enumerate([0.25, 0.5, 0.75]):
        frame_path = upload_dir / f"{token}_mod{i}.webp"
        seek = duration * frac if duration > 0 else 0
        try:
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-v",
                    "quiet",
                    "-ss",
                    str(seek),
                    "-i",
                    str(out_path),
                    "-vf",
                    "scale='min(800,iw)':'min(800,ih)':force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "60",
                    str(frame_path),
                ],
                timeout=10,
            )
            if frame_path.exists():
                mod_frames += 1
        except Exception:
            pass

    if mod_frames == 0:
        out_path.unlink(missing_ok=True)
        raise HTTPException(400, "Could not process video for moderation")

    return {
        "url": f"/chat/uploads/{filename}",
        "width": width,
        "height": height,
        "duration": round(duration, 1),
    }


# --- Push notifications ---


@router.get("/push/vapid-key")
async def chat_vapid_key():
    key = os.environ.get("VAPID_PUBLIC_KEY")
    if not key:
        raise HTTPException(501, "Push notifications not configured")
    return Response(
        content=json.dumps({"public_key": key}),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/push/subscribe", status_code=204)
async def chat_push_subscribe(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        endpoint = body.get("endpoint", "")
        keys = body.get("keys", {})
        p256dh = keys.get("p256dh", "")
        auth = keys.get("auth", "")
        if not endpoint or not p256dh or not auth:
            raise HTTPException(422, "Missing subscription fields")
        save_push_subscription(db, user["id"], endpoint, p256dh, auth)
        return Response(status_code=204)
    finally:
        db.close()


@router.delete("/push/subscribe", status_code=204)
async def chat_push_unsubscribe(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        endpoint = body.get("endpoint", "")
        if not endpoint:
            raise HTTPException(422, "Missing endpoint")
        delete_push_subscription(db, user["id"], endpoint)
        return Response(status_code=204)
    finally:
        db.close()


@router.get("/push/status")
async def chat_push_status(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        count = get_push_subscription_count(db, user["id"])
        return {"subscribed": count > 0}
    finally:
        db.close()


@router.post("/push/ack", status_code=204)
async def chat_push_ack(request: Request):
    body = await request.json()
    endpoint = body.get("endpoint")
    action = body.get("action")
    if not endpoint or action not in ("delivered", "clicked", "dismissed"):
        raise HTTPException(400, "endpoint and action required")
    db = _get_db()
    try:
        user = find_user_by_push_endpoint(db, endpoint)
        if not user:
            return Response(status_code=204)
        update_last_seen(db, user["id"])
        if action == "clicked":
            update_last_active(db, user["id"])
    finally:
        db.close()


# --- Admin ---


@router.get("/admin/reports")
async def admin_reports(request: Request, status: str = "pending"):
    _require_admin(request)
    db = _get_db()
    try:
        reports = get_pending_reports(db) if status == "pending" else []
        return [
            {
                "id": r["id"],
                "reporter_name": r["reporter_name"],
                "reported_name": r["reported_name"],
                "message_snapshot": r["message_snapshot"],
                "room_id": r["room_id"],
                "reported_user_id": r["reported_user_id"],
                "reason": r["reason"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in reports
        ]
    finally:
        db.close()


@router.patch("/admin/reports/{report_id}")
async def admin_resolve_report(report_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    status = body.get("status")
    if status not in ("actioned", "dismissed"):
        raise HTTPException(400, "status must be 'actioned' or 'dismissed'")
    db = _get_db()
    try:
        resolve_report(db, report_id, status)
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/ban/{user_id}")
async def admin_ban(user_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    reason = body.get("reason", "Banned by admin")
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        db_ban_user(
            db,
            user_id,
            user["provider"],
            user["provider_id"],
            reason,
            user["device_fingerprint"],
        )
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/unban/{user_id}")
async def admin_unban(user_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        db.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/admin/bans/{ban_id}")
async def admin_delete_ban(ban_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        db.execute("DELETE FROM bans WHERE id = ?", (ban_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/admin/stats")
async def admin_stats(request: Request):
    _require_admin(request)
    from chat_ws import manager

    online_ids = (
        set(manager.user_conns.keys()) if hasattr(manager, "user_conns") else set()
    )
    db = _get_db()
    try:
        return get_admin_stats(db, online_ids)
    finally:
        db.close()


@router.get("/admin/users")
async def admin_users(
    request: Request,
    q: str = "",
    online_only: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    _require_admin(request)
    from chat_ws import manager

    online_ids = (
        set(manager.user_conns.keys()) if hasattr(manager, "user_conns") else set()
    )
    db = _get_db()
    try:
        return search_users(db, online_ids, q, online_only, limit, offset)
    finally:
        db.close()


@router.get("/admin/users/{user_id}")
async def admin_user_detail(user_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        detail = get_user_admin_detail(db, user_id)
        if not detail:
            raise HTTPException(404, "User not found")
        from chat_ws import manager

        detail["is_online"] = user_id in (
            manager.user_conns if hasattr(manager, "user_conns") else {}
        )
        return detail
    finally:
        db.close()


@router.get("/admin/bans")
async def admin_bans(request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        return get_all_bans(db)
    finally:
        db.close()


@router.get("/admin/modlog")
async def admin_modlog(request: Request, limit: int = 50, offset: int = 0):
    _require_admin(request)
    db = _get_db()
    try:
        return get_moderation_log(db, limit, offset)
    finally:
        db.close()


@router.get("/admin/rooms")
async def admin_rooms(request: Request):
    _require_admin(request)
    from chat_ws import manager

    online_counts = {}
    if hasattr(manager, "rooms"):
        for room_id, room in manager.rooms.items():
            online_counts[room_id] = (
                len(room.connections) if hasattr(room, "connections") else 0
            )
    db = _get_db()
    try:
        return get_room_stats(db, online_counts)
    finally:
        db.close()


@router.post("/admin/mute/{user_id}")
async def admin_mute_user(user_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    minutes = body.get("minutes", 30)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        mute_user(db, user_id, minutes=minutes)
        removed = delete_user_messages(db, user_id)
        from chat_ws import manager

        for batch in removed:
            asyncio.create_task(
                manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )
            )
        asyncio.create_task(
            manager.send_to_user(
                user_id, {"event": "muted", "reason": "Muted by admin"}
            )
        )
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/strike/{user_id}")
async def admin_strike_user(user_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    reason = body.get("reason", "admin")
    detail = body.get("detail", "Manual admin action")
    db = _get_db()
    try:
        from chat_moderation import process_strike

        result = process_strike(db, user_id, reason, detail)
        return result
    finally:
        db.close()


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        removed = delete_user_messages(db, user_id)
        delete_user(db, user_id)
        from chat_ws import manager

        for batch in removed:
            asyncio.create_task(
                manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )
            )
        for conn_id, ws in list(manager.user_conns.get(user_id, {}).items()):
            try:
                asyncio.create_task(ws.close(code=4003, reason="Account deleted"))
            except Exception:
                pass
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/rooms")
async def admin_create_room(request: Request):
    _require_admin(request)
    body = await request.json()
    name = body.get("name", "").strip()
    room_type = body.get("type", "general")
    if not name:
        raise HTTPException(400, "Room name required")
    room_id = name.lower().replace(" ", "-")
    db = _get_db()
    try:
        existing = get_room(db, room_id)
        if existing:
            raise HTTPException(409, "Room already exists")
        room = create_room(
            db,
            room_id,
            DEFAULT_EVENT_ID,
            room_type,
            name,
            description=body.get("description", ""),
            is_moderated=body.get("is_moderated", True),
            is_read_only=body.get("is_read_only", False),
            allows_media=body.get("allows_media", True),
            ttl_minutes=body.get("ttl_minutes", 60),
            position=body.get("position", 0),
        )
        return room
    finally:
        db.close()


@router.delete("/admin/rooms/{room_id}")
async def admin_delete_room(room_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["is_main"]:
            raise HTTPException(400, "Cannot delete the main room")
        if room["type"] in ("dm", "meetup"):
            raise HTTPException(400, "DM and meetup rooms are managed automatically")
        delete_room(db, room_id)
        return {"ok": True}
    finally:
        db.close()


# --- Admin page ---


_admin_html = (Path(__file__).resolve().parent / "chat" / "admin.html").read_text()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return HTMLResponse(_admin_html, headers={"Cache-Control": "no-store"})


# --- Mount ---


CHAT_DIR = Path(__file__).resolve().parent / "chat"


def mount_chat(app):
    from fastapi.staticfiles import StaticFiles

    app.include_router(router)

    @app.websocket("/ws/chat/{token}")
    async def chat_websocket(websocket: WebSocket, token: str):
        await handle_chat_ws(websocket, token, DEFAULT_EVENT_ID)

    @app.get("/chat/v/{token}")
    async def verify_via_path(request: Request, token: str):
        return await auth_email_verify(request, token)

    @app.get("/chat", response_class=HTMLResponse)
    @app.get("/chat/", response_class=HTMLResponse)
    @app.get("/chat/r/{room_id}", response_class=HTMLResponse)
    @app.get("/chat/d/{username}", response_class=HTMLResponse)
    @app.get("/chat/m/{meetup_id}", response_class=HTMLResponse)
    @app.get("/chat/msg/{message_id}", response_class=HTMLResponse)
    async def serve_chat(
        room_id: str = "", username: str = "", meetup_id: str = "", message_id: str = ""
    ):
        chat_html = CHAT_DIR / "chat.html"
        if chat_html.exists():
            return HTMLResponse(chat_html.read_text(encoding="utf-8"))
        raise HTTPException(404, "Chat not available")

    uploads_dir = CHAT_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/chat/uploads", StaticFiles(directory=str(uploads_dir)), name="chat-uploads"
    )

    _load_disposable_domains()
    _load_admin_emails()

    db = _get_db()
    seed_event_room(db, DEFAULT_EVENT_ID, "Stone Techno 2026")
    db.close()

    return purge_loop
