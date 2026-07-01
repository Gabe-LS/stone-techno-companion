"""Chat REST API + WebSocket mount for FastAPI."""

from __future__ import annotations

import asyncio
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
    seed_stage_rooms,
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
    response.set_cookie(
        "chat_session",
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 3600,
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
    if not email or "@" not in email:
        raise HTTPException(400, "Valid email required")

    domain = email.split("@")[1]
    if domain in DISPOSABLE_DOMAINS:
        raise HTTPException(400, "Disposable email addresses are not allowed")

    token = secrets.token_urlsafe(32)
    provider_id = hash_email(email)

    db = _get_db()
    ban = is_banned(db, "email", provider_id, fingerprint)
    if ban:
        raise HTTPException(403, f"You have been banned: {ban['reason']}")

    _email_tokens[token] = {
        "email": email,
        "provider_id": provider_id,
        "fingerprint": fingerprint,
        "expires": datetime.now(timezone.utc) + timedelta(minutes=15),
    }

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        try:
            import resend

            resend.api_key = resend_key
            base_url = os.environ.get(
                "CHAT_BASE_URL", "https://stonetechno.deftlab.dev"
            )
            verify_url = f"{base_url}/chat/api/auth/email/verify?token={token}"
            resend.Emails.send(
                {
                    "from": os.environ.get(
                        "CHAT_EMAIL_FROM", "chat@stonetechno.deftlab.dev"
                    ),
                    "to": email,
                    "subject": "Sign in to Festival Chat",
                    "html": f'<p>Click to sign in:</p><p><a href="{verify_url}">{verify_url}</a></p>'
                    f"<p>This link expires in 15 minutes.</p>",
                }
            )
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            raise HTTPException(500, "Failed to send email")
    else:
        logger.warning("RESEND_API_KEY not set — magic link token: %s", token)

    return {"sent": True}


_email_tokens: dict[str, dict] = {}


@router.get("/auth/email/verify")
async def auth_email_verify(request: Request, response: Response, token: str = ""):
    data = _email_tokens.pop(token, None)
    if not data:
        raise HTTPException(400, "Invalid or expired link")
    if datetime.now(timezone.utc) > data["expires"]:
        raise HTTPException(400, "Link expired")

    db = _get_db()
    name = data["email"].split("@")[0]
    user = _authenticate(
        db, "email", data["provider_id"], name, data["fingerprint"], response
    )

    base_url = os.environ.get("CHAT_BASE_URL", "")
    redirect_url = f"{base_url}/#chat" if base_url else "/#chat"
    return HTMLResponse(
        f'<html><head><meta http-equiv="refresh" content="0;url={redirect_url}"></head>'
        f"<body>Signed in. Redirecting...</body></html>"
    )


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


@router.put("/auth/profile")
async def auth_update_profile(request: Request):
    user, db = _get_user_from_cookie(request)
    body = await request.json()
    name = body.get("display_name")
    if name:
        name = name.strip()[:30]
        if len(name) < 3:
            raise HTTPException(400, "Display name must be at least 3 characters")
        update_display_name(db, user["id"], name)
    return {"id": user["id"], "display_name": name or user["display_name"]}


@router.get("/auth/me")
async def auth_me(request: Request):
    user, db = _get_user_from_cookie(request)
    return {
        "id": user["id"],
        "display_name": user["display_name"],
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

    return purge_loop
