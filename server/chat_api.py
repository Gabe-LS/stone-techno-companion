"""Chat REST API + WebSocket mount for FastAPI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
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
    block_user as db_block_user,
    unblock_user as db_unblock_user,
    get_pending_reports,
    resolve_report,
    hash_email,
)
from chat_ws import handle_chat_ws, purge_loop

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/api")
DEFAULT_EVENT_ID = os.environ.get("CHAT_EVENT_ID", "stone-techno-2026")
ADMIN_TOKEN = os.environ.get("CHAT_ADMIN_TOKEN", "")

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
    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(401, "Session expired")
    return user, db


def _require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token") or request.query_params.get(
        "admin_token"
    )
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Admin access required")


def _set_session_cookie(response: Response, token: str) -> None:
    is_prod = not os.environ.get("CHAT_BASE_URL", "").startswith("http://")
    response.set_cookie(
        "chat_session",
        token,
        httponly=False,
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


@router.post("/auth/google")
async def auth_google(request: Request, response: Response):
    body = await request.json()
    id_token = body.get("id_token")
    fingerprint = body.get("device_fingerprint")
    if not id_token:
        raise HTTPException(400, "id_token required")

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        info = google_id_token.verify_oauth2_token(
            id_token, google_requests.Request(), client_id
        )
        provider_id = info["sub"]
        name = info.get("name") or info.get("email", "").split("@")[0]
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    db = _get_db()
    return _authenticate(db, "google", provider_id, name, fingerprint, response)


@router.post("/auth/apple")
async def auth_apple(request: Request, response: Response):
    body = await request.json()
    id_token = body.get("id_token")
    fingerprint = body.get("device_fingerprint")
    if not id_token:
        raise HTTPException(400, "id_token required")

    try:
        import jwt

        header = jwt.get_unverified_header(id_token)
        payload = jwt.decode(id_token, options={"verify_signature": False})
        provider_id = payload["sub"]
        name = body.get("display_name") or payload.get("email", "").split("@")[0]
    except Exception as e:
        raise HTTPException(401, f"Invalid Apple token: {e}")

    db = _get_db()
    return _authenticate(db, "apple", provider_id, name, fingerprint, response)


@router.post("/auth/email/start")
async def auth_email_start(request: Request):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    fingerprint = body.get("device_fingerprint")
    try:
        from email_validator import validate_email

        result = validate_email(email, check_deliverability=True)
        email = result.normalized
    except Exception as e:
        raise HTTPException(400, str(e))

    domain = email.split("@")[1]
    if domain in DISPOSABLE_DOMAINS:
        raise HTTPException(400, "Disposable email addresses are not allowed")

    token = secrets.token_urlsafe(32)
    provider_id = hash_email(email)

    db = _get_db()
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

    maileroo_key = os.environ.get("MAILEROO_API_KEY")
    if maileroo_key:
        try:
            from maileroo import MailerooClient, EmailAddress

            client = MailerooClient(api_key=maileroo_key)
            base_url = os.environ.get(
                "CHAT_BASE_URL", "https://stonetechno.deftlab.dev"
            )
            verify_url = f"{base_url}/chat/api/auth/email/verify?token={token}"
            from_addr = os.environ.get("CHAT_EMAIL_FROM", "no-reply@deftlab.dev")
            client.send_basic_email(
                {
                    "from": EmailAddress(from_addr),
                    "to": [EmailAddress(email)],
                    "subject": "Sign in to Festival Chat",
                    "html": f'<p>Click to sign in:</p><p><a href="{verify_url}">{verify_url}</a></p>'
                    f"<p>This link expires in 15 minutes.</p>",
                }
            )
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            raise HTTPException(500, "Failed to send email")
    else:
        logger.warning("MAILEROO_API_KEY not set — magic link token: %s", token)

    return {"sent": True}


@router.get("/auth/email/verify")
async def auth_email_verify(request: Request, token: str = ""):
    db = _get_db()
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
    _authenticate(db, "email", row["provider_id"], name, row["fingerprint"], redirect)
    return redirect


@router.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie("chat_session")
    return {"ok": True}


@router.delete("/auth/account")
async def auth_delete_account(request: Request, response: Response):
    user, db = _get_user_from_cookie(request)
    delete_user(db, user["id"])
    response.delete_cookie("chat_session")
    return {"ok": True}


import re
import unicodedata

try:
    import regex as _re_mod
except ImportError:
    _re_mod = re

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]{1,2}$")
_USERNAME_BAD_RE = re.compile(r"[._-]{2}")
_DISPLAYNAME_RE = _re_mod.compile(
    r"^[\p{Script=Latin}\d][\p{Script=Latin}\d ._-]*[\p{Script=Latin}\d]$"
    r"|^[\p{Script=Latin}\d]{1,2}$",
    _re_mod.UNICODE,
)


def _validate_username(
    username: str, db, exclude_user_id: str | None = None
) -> str | None:
    if not username or len(username) < 2 or len(username) > 20:
        return "2-20 characters"
    if not _USERNAME_RE.match(username):
        return "Allowed: a-z 0-9 . _ -"
    if _USERNAME_BAD_RE.search(username):
        return "No consecutive . _ -"
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
    return None


@router.get("/auth/check-username")
async def check_username(request: Request, name: str = ""):
    user, db = _get_user_from_cookie(request)
    err = _validate_username(name, db, user["id"])
    return {"available": err is None, "reason": err or ""}


@router.get("/auth/check-displayname")
async def check_displayname(request: Request, name: str = ""):
    err = _validate_display_name(name)
    return {"available": err is None, "reason": err or ""}


@router.put("/auth/profile")
async def auth_update_profile(request: Request):
    user, db = _get_user_from_cookie(request)
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

        ai_result = await check_openai_moderation(text_to_moderate)
        if ai_result:
            raise HTTPException(
                400, f"Name not allowed: {ai_result.get('category', 'content policy')}"
            )

    country = body.get("country")
    if country is not None:
        updates.append("country = ?")
        params.append(country.strip()[:2].upper())
    avatar_url = body.get("avatar_url")
    if avatar_url is not None:
        updates.append("avatar_url = ?")
        params.append(avatar_url)
    if updates:
        params.append(user["id"])
        db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    return {"ok": True}


@router.get("/auth/me")
async def auth_me(request: Request):
    user, db = _get_user_from_cookie(request)
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


# --- Rooms ---


@router.get("/rooms")
async def list_rooms(request: Request):
    db = _get_db()
    from chat_ws import manager

    rooms = get_rooms_by_event(db, DEFAULT_EVENT_ID)
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "online_count": len(manager.get_online_users(r["id"])),
        }
        for r in rooms
    ]


@router.get("/rooms/{room_id}/messages")
async def room_messages(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(404, "Room not found")
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


@router.get("/rooms/{room_id}/info")
async def room_info(room_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    room = get_room(db, room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    return {"id": room["id"], "name": room["name"], "type": room["type"]}


@router.get("/rooms/{room_id}/online")
async def room_online(room_id: str):
    from chat_ws import manager

    return manager.get_online_users(room_id)


# --- Meetups ---


@router.get("/meetups")
async def list_meetups(request: Request, stage_id: str | None = None):
    db = _get_db()
    meetups = get_active_meetups(db, DEFAULT_EVENT_ID)
    result = []
    for m in meetups:
        if stage_id and m["stage_id"] != stage_id:
            continue
        attendees = get_meetup_attendees(db, m["id"])
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
                "attendees": [
                    {"id": a["id"], "display_name": a["display_name"]}
                    for a in attendees
                ],
                "expires_at": m["expires_at"],
            }
        )
    return result


@router.get("/meetups/{meetup_id}")
async def get_meetup(meetup_id: str):
    db = _get_db()
    meetup = db.execute("SELECT * FROM meetups WHERE id = ?", (meetup_id,)).fetchone()
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


@router.post("/meetups", status_code=201)
async def create_meetup_endpoint(request: Request):
    user, db = _get_user_from_cookie(request)
    body = await request.json()
    title = body.get("title")
    meetup_time = body.get("meetup_time")
    if not title or not meetup_time:
        raise HTTPException(400, "title and meetup_time required")

    meetup = create_meetup(
        db,
        user["id"],
        DEFAULT_EVENT_ID,
        body.get("stage_id"),
        title,
        meetup_time,
        location_lat=body.get("lat"),
        location_lng=body.get("lng"),
        location_label=body.get("label"),
        note=body.get("note"),
    )
    return meetup


@router.post("/meetups/{meetup_id}/join")
async def join_meetup_endpoint(meetup_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    db_join_meetup(db, meetup_id, user["id"])
    attendees = get_meetup_attendees(db, meetup_id)
    return [{"id": a["id"], "display_name": a["display_name"]} for a in attendees]


@router.delete("/meetups/{meetup_id}/join")
async def leave_meetup_endpoint(meetup_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    db_leave_meetup(db, meetup_id, user["id"])
    attendees = get_meetup_attendees(db, meetup_id)
    return [{"id": a["id"], "display_name": a["display_name"]} for a in attendees]


# --- DMs ---


@router.get("/dms")
async def list_dms(request: Request):
    user, db = _get_user_from_cookie(request)
    dms = db.execute(
        "SELECT r.id, r.name, dp2.user_id AS other_user_id, u.display_name AS other_name "
        "FROM dm_participants dp1 "
        "JOIN dm_participants dp2 ON dp1.room_id = dp2.room_id AND dp1.user_id != dp2.user_id "
        "JOIN rooms r ON r.id = dp1.room_id "
        "JOIN users u ON u.id = dp2.user_id "
        "WHERE dp1.user_id = ?",
        (user["id"],),
    ).fetchall()
    return [
        {
            "room_id": dm["id"],
            "other_user_id": dm["other_user_id"],
            "other_name": dm["other_name"],
        }
        for dm in dms
    ]


@router.post("/dms", status_code=201)
async def create_dm(request: Request):
    user, db = _get_user_from_cookie(request)
    body = await request.json()
    target_id = body.get("target_user_id")
    if not target_id:
        raise HTTPException(400, "target_user_id required")
    try:
        room_id = find_or_create_dm(db, DEFAULT_EVENT_ID, user["id"], target_id)
    except ValueError:
        raise HTTPException(404, "User not found")
    return {"room_id": room_id}


# --- Users ---


@router.post("/users/{user_id}/block")
async def block_user_endpoint(user_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    db_block_user(db, user["id"], user_id)
    return {"ok": True}


@router.delete("/users/{user_id}/block")
async def unblock_user_endpoint(user_id: str, request: Request):
    user, db = _get_user_from_cookie(request)
    db_unblock_user(db, user["id"], user_id)
    return {"ok": True}


# --- Media ---


@router.post("/upload/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files allowed")

    data = await file.read()
    if len(data) > 500 * 1024:
        raise HTTPException(400, "Max file size is 500KB")

    db.execute(
        "UPDATE users SET avatar_url = ? WHERE id = ?",
        (f"/chat/api/avatar/{user['id']}", user["id"]),
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS avatars (user_id TEXT PRIMARY KEY, data BLOB NOT NULL)"
    )
    db.execute(
        "INSERT OR REPLACE INTO avatars (user_id, data) VALUES (?, ?)",
        (user["id"], data),
    )
    db.commit()

    return {"url": f"/chat/api/avatar/{user['id']}"}


@router.get("/avatar/{user_id}")
async def get_avatar(user_id: str):
    db = _get_db()
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


@router.post("/upload/image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)

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

    try:
        import pyvips

        img = pyvips.Image.new_from_buffer(data, "")
        img = img.autorot()
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        if img.get_typeof("exif-data"):
            img = img.remove("exif-data", "all")
        max_side = max(img.width, img.height)
        if max_side > 1500:
            scale = 1500 / max_side
            display = img.resize(scale, kernel=pyvips.enums.Kernel.LANCZOS3)
        else:
            display = img
        display.webpsave(str(out_path), Q=75)

        mod_path = upload_dir / f"{token}_mod.webp"
        if max_side > 880:
            mod_scale = 800 / max_side
            mod = img.resize(mod_scale, kernel=pyvips.enums.Kernel.LANCZOS3)
            mod.webpsave(str(mod_path), Q=60)
        else:
            img.webpsave(str(mod_path), Q=60)
    except Exception as e:
        raise HTTPException(500, f"Image processing failed: {e}")

    return {
        "url": f"/chat/uploads/{filename}",
        "width": display.width,
        "height": display.height,
    }


@router.post("/upload/video")
async def upload_video(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)

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
        probe = subprocess.run(
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
        width, height, duration = 0, 0, 0

    for i, frac in enumerate([0.25, 0.5, 0.75]):
        frame_path = upload_dir / f"{token}_mod{i}.webp"
        seek = duration * frac if duration > 0 else 0
        try:
            subprocess.run(
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
        except Exception:
            pass

    return {
        "url": f"/chat/uploads/{filename}",
        "width": width,
        "height": height,
        "duration": round(duration, 1),
    }


# --- Admin ---


@router.get("/admin/reports")
async def admin_reports(request: Request, status: str = "pending"):
    _require_admin(request)
    db = _get_db()
    reports = get_pending_reports(db) if status == "pending" else []
    return [
        {
            "id": r["id"],
            "reporter_name": r["reporter_name"],
            "reported_name": r["reported_name"],
            "message_snapshot": r["message_snapshot"],
            "room_id": r["room_id"],
            "reason": r["reason"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in reports
    ]


@router.patch("/admin/reports/{report_id}")
async def admin_resolve_report(report_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    status = body.get("status")
    if status not in ("actioned", "dismissed"):
        raise HTTPException(400, "status must be 'actioned' or 'dismissed'")
    db = _get_db()
    resolve_report(db, report_id, status)
    return {"ok": True}


@router.post("/admin/ban/{user_id}")
async def admin_ban(user_id: str, request: Request):
    _require_admin(request)
    body = await request.json()
    reason = body.get("reason", "Banned by admin")
    db = _get_db()
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


@router.post("/admin/unban/{user_id}")
async def admin_unban(user_id: str, request: Request):
    _require_admin(request)
    db = _get_db()
    db.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
    db.commit()
    return {"ok": True}


# --- Admin page ---


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    _require_admin(request)
    return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Chat Admin</title>
<style>
body { font-family: system-ui; max-width: 800px; margin: 0 auto; padding: 20px; }
.report { border: 1px solid #ddd; padding: 16px; margin: 12px 0; border-radius: 8px; }
.report .meta { color: #666; font-size: 0.85em; margin-bottom: 8px; }
.report .snapshot { background: #f5f5f5; padding: 10px; border-radius: 4px; margin: 8px 0; }
.report .actions { display: flex; gap: 8px; margin-top: 8px; }
button { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.ban { background: #e53e3e; color: #fff; }
.dismiss { background: #eee; }
.empty { color: #999; text-align: center; padding: 40px; }
</style></head><body>
<h1>Chat Admin</h1>
<div id="reports"></div>
<script>
const token = new URLSearchParams(location.search).get('admin_token');
async function load() {
  const res = await fetch('/chat/api/admin/reports?status=pending&admin_token=' + token);
  const reports = await res.json();
  const el = document.getElementById('reports');
  if (!reports.length) { el.innerHTML = '<div class="empty">No pending reports</div>'; return; }
  el.innerHTML = reports.map(r => `
    <div class="report">
      <div class="meta">${r.reporter_name} reported ${r.reported_name} · ${r.created_at}</div>
      <div>Reason: ${r.reason}</div>
      <div class="snapshot">${r.message_snapshot}</div>
      <div class="actions">
        <button class="ban" onclick="action('${r.id}','${r.reported_name}','actioned')">Ban User</button>
        <button class="dismiss" onclick="action('${r.id}','','dismissed')">Dismiss</button>
      </div>
    </div>`).join('');
}
async function action(id, name, status) {
  if (status === 'actioned' && !confirm('Ban ' + name + '?')) return;
  await fetch('/chat/api/admin/reports/' + id + '?admin_token=' + token,
    { method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status}) });
  load();
}
load();
</script></body></html>""")


# --- Mount ---


CHAT_DIR = Path(__file__).resolve().parent / "chat"


def mount_chat(app):
    from fastapi.staticfiles import StaticFiles

    app.include_router(router)

    @app.websocket("/ws/chat/{token}")
    async def chat_websocket(websocket: WebSocket, token: str):
        await handle_chat_ws(websocket, token, DEFAULT_EVENT_ID)

    @app.get("/chat", response_class=HTMLResponse)
    @app.get("/chat/", response_class=HTMLResponse)
    async def serve_chat():
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

    db = _get_db()
    seed_event_room(db, DEFAULT_EVENT_ID, "Stone Techno 2026")

    return purge_loop
