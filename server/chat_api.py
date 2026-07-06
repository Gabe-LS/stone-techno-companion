"""Chat REST API + WebSocket mount for FastAPI."""

from __future__ import annotations

import asyncio
import base64
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
    ban_user_all_providers,
    is_banned,
    is_user_banned,
    create_room,
    get_room,
    get_rooms_by_event,
    get_room_messages,
    seed_event_rooms,
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
    get_room_messages_admin,
    delete_message_by_id,
    get_reports_by_status,
    get_all_meetups,
    delete_meetup,
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
    increment_mute_count,
    MAX_MUTES_BEFORE_BAN,
    delete_room,
    update_room,
    update_last_seen,
    update_last_active,
    delete_user_messages,
    find_user_by_push_endpoint,
    get_reachable_member_counts,
    get_setting,
    set_setting,
    upsert_e2ee_device_key,
    get_e2ee_device_keys,
    get_admin,
    list_admins,
    add_admin,
    remove_admin,
    count_super_admins,
    log_admin_action,
    get_admin_actions,
    VALID_ADMIN_ROLES,
)
from chat_ws import handle_chat_ws, purge_loop

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/api")
DEFAULT_EVENT_ID = os.environ.get("CHAT_EVENT_ID", "stone-techno-2026")
ADMIN_TOKEN = os.environ.get("CHAT_ADMIN_TOKEN", "")

_upload_rate: dict[str, list[float]] = {}


def _check_upload_rate(user_id: str, max_uploads: int = 10, window: int = 60):
    now = time.monotonic()
    bucket = _upload_rate.setdefault(user_id, [])
    bucket[:] = [t for t in bucket if now - t < window]
    if len(bucket) >= max_uploads:
        raise HTTPException(429, "Upload rate limit exceeded")
    bucket.append(now)
    if len(_upload_rate) > 1000:
        stale = [
            k for k, v in _upload_rate.items() if all(now - t >= window for t in v)
        ]
        for k in stale:
            del _upload_rate[k]


_SITE_SHORT = ""
# Fallback for production, where lineup.db is not inside the container
_SITE_NAME = "Stone Techno"


def _load_site_short() -> None:
    global _SITE_SHORT, _SITE_NAME
    lineup_db = Path(__file__).resolve().parent.parent / "lineup.db"
    if not lineup_db.exists():
        return
    try:
        db = sqlite3.connect(str(lineup_db))
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT name, edition, short_name FROM events WHERE id = ?",
            (DEFAULT_EVENT_ID,),
        ).fetchone()
        if row and row["short_name"]:
            _SITE_SHORT = row["short_name"]
        if row and row["name"]:
            _SITE_NAME = row["name"] + (f" {row['edition']}" if row["edition"] else "")
        db.close()
    except Exception as e:
        logger.warning("site_short lookup failed (lineup.db not reachable): %s", e)


def _magic_link_email(site: str, verify_url: str, host: str) -> tuple[str, str, str]:
    """Build (subject, html, plain) for the sign-in email.

    Deliverability notes: multipart with a real text/plain body, visible link
    text identical to the href, explanation of why the mail was received, and
    a plain no-image layout — all of these matter to Outlook's spam filter.
    """
    # Colors/radius/font mirror the site design tokens in shared.css
    # (gray scale, --color-bg white surface, --radius-md 8px, --radius-lg 16px)
    subject = f"Your sign-in link for {site} Chat"
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    html = f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background-color:#f3f4f6;">
<div style="max-width:480px;margin:0 auto;padding:32px 16px;font-family:{font};color:#111827;">
  <div style="background-color:#ffffff;border:1px solid #e5e7eb;border-radius:16px;padding:32px 24px;">
    <p style="font-size:15px;font-weight:600;margin:0 0 24px;">{site} Companion</p>
    <p style="font-size:15px;line-height:1.6;margin:0 0 8px;">Here is the sign-in link you requested for the {site} festival chat:</p>
    <p style="margin:28px 0;"><a href="{verify_url}" style="background-color:#111827;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:15px;font-weight:600;display:inline-block;">Sign in to the chat</a></p>
    <p style="font-size:13px;line-height:1.6;margin:0 0 8px;color:#374151;">Or copy and paste this link into your browser:</p>
    <p style="font-size:13px;line-height:1.6;margin:0 0 24px;"><a href="{verify_url}" style="color:#374151;word-break:break-all;">{verify_url}</a></p>
    <p style="font-size:13px;line-height:1.6;margin:0 0 8px;color:#374151;">The link expires in 15 minutes and works once. After that, just request a new one.</p>
    <p style="font-size:13px;line-height:1.6;margin:0 0 24px;color:#6b7280;">Didn't request this email? You can safely ignore it &mdash; no account will be created and you won't hear from us again.</p>
    <p style="font-size:15px;line-height:1.6;margin:0;">See you on the dancefloor.</p>
  </div>
  <p style="font-size:12px;line-height:1.6;color:#9ca3af;margin:16px 8px 0;">{site} Companion &middot; you are receiving this one-time email because this address was entered on {host}.</p>
</div>
</body>
</html>"""
    plain = (
        f"Here is the sign-in link you requested for the {site} festival chat:\n\n"
        f"{verify_url}\n\n"
        f"The link expires in 15 minutes and works once. "
        f"After that, just request a new one.\n\n"
        f"Didn't request this email? You can safely ignore it - "
        f"no account will be created and you won't hear from us again.\n\n"
        f"See you on the dancefloor.\n\n"
        f"--\n"
        f"{site} Companion - you are receiving this one-time email "
        f"because this address was entered on {host}.\n"
    )
    return subject, html, plain


_ADMIN_EMAIL_HASHES: set[str] = set()


def _load_admin_emails() -> None:
    global _ADMIN_EMAIL_HASHES
    raw = os.environ.get("CHAT_ADMIN_EMAILS", "")
    if raw:
        _ADMIN_EMAIL_HASHES = {
            hash_email(e.strip()) for e in raw.split(",") if e.strip()
        }


_email_rate: dict[str, list[float]] = {}
_email_dest_rate: dict[str, list[float]] = {}
_auth_rate: dict[str, list[float]] = {}
_admin_fail_rate: dict[str, list[float]] = {}


def _check_auth_rate(request: Request, max_n: int = 120, window: int = 300) -> None:
    # Magic-link tokens are 128-bit and OAuth is validated Google-side, so
    # brute-force is not the threat this limiter defends against; a shared
    # public IP at a festival venue hitting this ceiling is the real risk.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    _auth_rate[ip] = [t for t in _auth_rate.get(ip, []) if now - t < window]
    if len(_auth_rate[ip]) >= max_n:
        raise HTTPException(429, "Too many requests. Try again later.")
    _auth_rate[ip].append(now)
    if len(_auth_rate) > 1000:
        stale = [k for k, v in _auth_rate.items() if all(now - t >= window for t in v)]
        for k in stale:
            del _auth_rate[k]


def _check_admin_fail_rate(
    request: Request, max_n: int = 20, window: int = 300
) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    _admin_fail_rate[ip] = [t for t in _admin_fail_rate.get(ip, []) if now - t < window]
    if len(_admin_fail_rate[ip]) >= max_n:
        raise HTTPException(429, "Too many attempts")
    if len(_admin_fail_rate) > 1000:
        stale = [
            k for k, v in _admin_fail_rate.items() if all(now - t >= window for t in v)
        ]
        for k in stale:
            del _admin_fail_rate[k]


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


def _resolve_admin(request: Request) -> dict | None:
    header_token = request.headers.get("X-Admin-Token") or ""
    if (
        ADMIN_TOKEN
        and header_token
        and secrets.compare_digest(header_token, ADMIN_TOKEN)
    ):
        return {
            "kind": "token",
            "role": "super_admin",
            "user_id": None,
            "email_hash": None,
            "label": "token",
        }
    session_token = request.cookies.get("chat_session")
    if session_token:
        db = _get_db()
        try:
            user = get_user_by_token(db, session_token)
            if user:
                providers = db.execute(
                    "SELECT provider_id FROM user_providers WHERE user_id = ?",
                    (user["id"],),
                ).fetchall()
                pid_list = [p["provider_id"] for p in providers]
                if user["provider_id"] not in pid_list:
                    pid_list.append(user["provider_id"])
                # env emails = permanent super-admins
                for pid in pid_list:
                    if pid in _ADMIN_EMAIL_HASHES:
                        row = get_admin(db, pid)
                        label = row["label"] if row and row["label"] else pid[:12]
                        return {
                            "kind": "cookie",
                            "role": "super_admin",
                            "user_id": user["id"],
                            "email_hash": pid,
                            "label": label,
                        }
                # DB-backed admins
                for pid in pid_list:
                    row = get_admin(db, pid)
                    if row:
                        return {
                            "kind": "cookie",
                            "role": row["role"],
                            "user_id": user["id"],
                            "email_hash": pid,
                            "label": (row["label"] or pid[:12]),
                        }
        finally:
            db.close()
    return None


def _require_admin(request: Request) -> dict:
    _check_admin_fail_rate(request)
    actor = _resolve_admin(request)
    if actor is None:
        ip = request.client.host if request.client else "unknown"
        _admin_fail_rate.setdefault(ip, []).append(time.monotonic())
        raise HTTPException(403, "Admin access required")
    return actor


def _require_super_admin(request: Request) -> dict:
    actor = _require_admin(request)
    if actor["role"] != "super_admin":
        raise HTTPException(403, "Super-admin access required")
    return actor


def _protected_role(db, target_user_id: str) -> str | None:
    user = get_user(db, target_user_id)
    if not user:
        return None
    pids = [
        p["provider_id"]
        for p in db.execute(
            "SELECT provider_id FROM user_providers WHERE user_id = ?",
            (target_user_id,),
        ).fetchall()
    ]
    if user["provider_id"] not in pids:
        pids.append(user["provider_id"])
    if any(p in _ADMIN_EMAIL_HASHES for p in pids):
        return "super_admin"
    best = None
    for p in pids:
        row = get_admin(db, p)
        if row:
            if row["role"] == "super_admin":
                return "super_admin"
            best = "admin"
    return best


def _guard_target(db, actor: dict, target_user_id: str) -> None:
    role = _protected_role(db, target_user_id)
    if role == "super_admin":
        raise HTTPException(403, "Cannot moderate a super-admin")
    if role == "admin" and actor["role"] != "super_admin":
        raise HTTPException(403, "Only a super-admin can moderate another admin")


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
        # Existing account: check bans across every identity linked to it,
        # not just the (provider, provider_id) pair used for this login —
        # otherwise a ban on one linked provider is evaded by signing in
        # through another already-linked provider.
        ban = is_user_banned(db, user["id"])
        if ban:
            raise HTTPException(403, "This account has been banned.")
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
    db = _get_db()
    try:
        msg_limit = int(get_setting(db, "msg_char_limit", "1000"))
    except (ValueError, TypeError):
        msg_limit = 1000
    finally:
        db.close()
    return {
        "google_client_id": google_id if google_id else None,
        "site_short": _SITE_SHORT or None,
        "msg_char_limit": msg_limit,
    }


@router.post("/auth/google")
async def auth_google(request: Request, response: Response):
    _check_auth_rate(request)
    body = await request.json()
    id_token = body.get("id_token")
    if not id_token:
        raise HTTPException(400, "id_token required")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(501, "Google Sign-In not configured")

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        info = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token,
            google_requests.Request(),
            client_id,
        )
        provider_id = info["sub"]
        email = info.get("email", "")
        name = _safe_provider_display_name(info.get("name") or "", email.split("@")[0])
    except Exception as e:
        logger.warning("Google token verification failed: %s", e)
        raise HTTPException(401, "Invalid Google token")

    db = _get_db()
    try:
        user = find_user_by_provider(db, "google", provider_id)
        email_verified = (
            info.get("email_verified") is True or info.get("email_verified") == "true"
        )
        if not user and email and email_verified:
            email_hash = hash_email(email)
            user = find_user_by_provider(db, "email", email_hash)
            if user and not is_user_banned(db, user["id"]):
                add_user_provider(db, user["id"], "google", provider_id)
                logger.info("Linked google provider to existing user %s", user["id"])
        result = _authenticate(db, "google", provider_id, name, None, response)
        if email:
            add_user_provider(db, result["id"], "email", hash_email(email))
        return result
    finally:
        db.close()


@router.post("/auth/google/code")
async def auth_google_code(request: Request, response: Response):
    _check_auth_rate(request)
    body = await request.json()
    code = body.get("code")
    if not code:
        raise HTTPException(400, "code required")

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(501, "Google Sign-In not configured")

    try:
        import httpx

        token_resp = await asyncio.to_thread(
            httpx.post,
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "postmessage",
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_data = token_resp.json()
        id_token_str = token_data.get("id_token")
        if not id_token_str:
            raise ValueError(token_data.get("error_description", "No id_token"))

        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        info = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token_str,
            google_requests.Request(),
            client_id,
        )
        provider_id = info["sub"]
        email = info.get("email", "")
        name = _safe_provider_display_name(info.get("name") or "", email.split("@")[0])
    except Exception as e:
        logger.warning("Google code exchange failed: %s", e)
        raise HTTPException(401, "Google authentication failed")

    db = _get_db()
    try:
        user = find_user_by_provider(db, "google", provider_id)
        email_verified = (
            info.get("email_verified") is True or info.get("email_verified") == "true"
        )
        if not user and email and email_verified:
            email_hash = hash_email(email)
            user = find_user_by_provider(db, "email", email_hash)
            if user and not is_user_banned(db, user["id"]):
                add_user_provider(db, user["id"], "google", provider_id)
                logger.info("Linked google provider to existing user %s", user["id"])
        result = _authenticate(db, "google", provider_id, name, None, response)
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

    # Per-destination limit (in addition to per-IP above): keyed by the
    # target email so rotating source IPs can't mail-bomb one inbox or
    # burn the Maileroo quota against a single victim.
    _email_dest_rate[provider_id] = [
        t for t in _email_dest_rate.get(provider_id, []) if now - t < 3600
    ]
    if len(_email_dest_rate[provider_id]) >= 3:
        raise HTTPException(429, "Too many requests. Try again later.")
    _email_dest_rate[provider_id].append(now)
    if len(_email_dest_rate) > 5000:
        stale = [
            k for k, v in _email_dest_rate.items() if all(now - t >= 3600 for t in v)
        ]
        for k in stale:
            del _email_dest_rate[k]

    db = _get_db()
    try:
        ban = is_banned(db, "email", provider_id)
        if ban:
            raise HTTPException(403, "This account cannot sign in.")

        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO email_tokens (token, email, provider_id, fingerprint, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, email, provider_id, None, expires),
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
            host = base_url.split("://", 1)[-1].split("/")[0].split(":")[0]
            subject, html, plain = _magic_link_email(_SITE_NAME, verify_url, host)
            await asyncio.to_thread(
                client.send_basic_email,
                {
                    "from": EmailAddress(from_addr, f"{_SITE_NAME} Chat"),
                    "to": [EmailAddress(email)],
                    "subject": subject,
                    "html": html,
                    "plain": plain,
                },
            )
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            raise HTTPException(500, "Failed to send email")
    else:
        logger.error("MAILEROO_API_KEY not set — cannot send magic link")
        raise HTTPException(500, "Email delivery is not configured")

    return {"sent": True}


@router.get("/verify")
async def auth_email_verify(request: Request, token: str = ""):
    _check_auth_rate(request)
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
            user = get_user_by_token(db, token)
            db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            db.commit()
        finally:
            db.close()
        if user:
            from chat_ws import manager

            for conn_id, ws in list(manager.user_conns.get(user["id"], {}).items()):
                try:
                    await ws.close(code=4001, reason="Logged out")
                except Exception:
                    pass
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

        for conn_id, ws in list(manager.user_conns.get(user["id"], {}).items()):
            try:
                await ws.close(code=4003, reason="Account deleted")
            except Exception:
                pass

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


def _safe_provider_display_name(raw: str, fallback: str) -> str:
    # Provider names (Google "name") are untrusted. Keep only display-safe chars,
    # collapse whitespace, cap length, and fall back to a safe base if nothing valid remains.
    name = unicodedata.normalize("NFKC", raw or "").strip()
    name = re.sub(r"\s+", " ", name)
    # allow Latin letters/digits/space and . _ - ; drop everything else (quotes, <>, control)
    name = re.sub(r"[^\w .\-]", "", name, flags=re.UNICODE).strip()
    name = name[:30]
    if len(name) < 2:
        base = re.sub(r"[^\w.\-]", "", (fallback or "user"))[:20] or "user"
        return base
    return name


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
    user, db = _get_user_from_cookie(request)
    db.close()
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
            from chat_ws import manager

            fresh = get_user(db, user["id"])
            if fresh:
                fk = fresh.keys()
                await manager.broadcast_profile_update(
                    user["id"],
                    {
                        "display_name": fresh["display_name"],
                        "username": fresh["username"] if "username" in fk else "",
                        "color_index": (
                            fresh["color_index"] if "color_index" in fk else 0
                        ),
                        "avatar_url": (
                            fresh["avatar_url"] if "avatar_url" in fk else ""
                        ),
                        "country": fresh["country"] if "country" in fk else "",
                    },
                )
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
                "auto_join": bool(r["auto_join"]) if "auto_join" in r.keys() else False,
                "allows_media": bool(r["allows_media"]),
                "ttl_minutes": r["ttl_minutes"],
                "online_count": len(manager.get_online_users(r["id"])),
                "member_count": reachable.get(r["id"], 0),
                "is_member": r["id"] in member_rooms,
                "position": r["position"] if "position" in r.keys() else 0,
                "last_message_at": last_msgs.get(r["id"], ""),
            }
            for r in rooms
        ]
        sort_mode = get_setting(db, "room_sort", "auto")
        if sort_mode == "manual":
            result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
            result.sort(key=lambda r: r.get("position", 0))
        else:

            def _auto_sort_key(r):
                if r["is_main"]:
                    return (0, "")
                if r["is_member"]:
                    return (1, r["last_message_at"] or "")
                return (2, r["last_message_at"] or "")

            result.sort(key=lambda r: r["last_message_at"] or "", reverse=True)
            result.sort(key=lambda r: _auto_sort_key(r)[:1])
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
        if room["type"] in ("dm", "meetup"):
            raise HTTPException(403, "This room cannot be joined directly")
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
        if room and room["type"] == "meetup":
            if not db.execute(
                "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
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
        elif room and room["type"] == "meetup":
            if not db.execute(
                "SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?",
                (room_id, user["id"]),
            ).fetchone():
                raise HTTPException(403, "Access denied")
        from chat_ws import manager

        return manager.get_online_users(room_id)
    finally:
        db.close()


# --- Meetups ---


def _shape_meetup(*, id, title, meetup_time, stage_id, attendee_count, expires_at,
                  is_attendee, location_lat, location_lng, location_label, note,
                  attendees, last_message_at=None):
    out = {
        "id": id, "title": title, "meetup_time": meetup_time, "stage_id": stage_id,
        "attendee_count": attendee_count, "is_going": is_attendee, "expires_at": expires_at,
    }
    if last_message_at is not None:
        out["last_message_at"] = last_message_at
    if is_attendee:
        out.update({
            "location_lat": location_lat, "location_lng": location_lng,
            "location_label": location_label, "note": note, "attendees": attendees,
        })
    return out


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
            if db_is_blocked(db, m["creator_id"], user_id) or db_is_blocked(
                db, user_id, m["creator_id"]
            ):
                continue
            if stage_id and m["stage_id"] != stage_id:
                continue
            attendees = get_meetup_attendees(db, m["id"])
            att_ids = {a["id"] for a in attendees}
            result.append(
                _shape_meetup(
                    id=m["id"],
                    title=m["title"],
                    meetup_time=m["meetup_time"],
                    stage_id=m["stage_id"],
                    attendee_count=m["attendee_count"],
                    expires_at=m["expires_at"],
                    is_attendee=(user_id in att_ids),
                    location_lat=m["location_lat"],
                    location_lng=m["location_lng"],
                    location_label=m["location_label"],
                    note=m["note"],
                    attendees=[
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in attendees
                    ],
                    last_message_at=last_msgs.get(m["id"], ""),
                )
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
        att_list = [{"id": a["id"], "display_name": a["display_name"]} for a in attendees]
        is_attendee = any(a["id"] == user["id"] for a in attendees)
        return _shape_meetup(
            id=meetup["id"],
            title=meetup["title"],
            meetup_time=meetup["meetup_time"],
            stage_id=meetup["stage_id"] if "stage_id" in meetup.keys() else None,
            attendee_count=len(attendees),
            expires_at=meetup["expires_at"],
            is_attendee=is_attendee,
            location_lat=meetup["location_lat"],
            location_lng=meetup["location_lng"],
            location_label=meetup["location_label"],
            note=meetup["note"],
            attendees=att_list,
        )
    finally:
        db.close()


@router.post("/meetups", status_code=201)
async def create_meetup_endpoint(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        from chat_ws import manager
        from chat_moderation import check_ban_mute

        if not manager.check_rate_limit(user["id"]):
            raise HTTPException(429, "Too many requests. Slow down.")
        _bm = await check_ban_mute(db, user["id"])
        if not _bm["allowed"]:
            raise HTTPException(403, _bm["reason"])
        body = await request.json()
        title = (body.get("title") or "")[:60]
        meetup_time = body.get("meetup_time")
        if not title or not meetup_time:
            raise HTTPException(400, "title and meetup_time required")
        try:
            _mt = datetime.fromisoformat(meetup_time)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid meetup_time format")
        _now_dt = datetime.now(timezone.utc)
        if (
            _mt.tzinfo is None
            or _mt <= _now_dt
            or _mt > _now_dt + timedelta(days=30)
        ):
            raise HTTPException(400, "Pick a valid meetup time in the future.")

        from chat_moderation import get_word_filter
        _wf = get_word_filter()
        _mtext = " ".join(filter(None, [title, body.get("note") or "", body.get("label") or ""]))
        if _mtext.strip() and _wf.check(_mtext):
            raise HTTPException(400, "That meetup contains content that isn't allowed.")

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
        from chat_moderation import check_ban_mute

        _bm = await check_ban_mute(db, user["id"])
        if not _bm["allowed"]:
            raise HTTPException(403, _bm["reason"])
        _m = db.execute(
            "SELECT creator_id FROM meetups WHERE id = ?", (meetup_id,)
        ).fetchone()
        if _m and (
            db_is_blocked(db, _m["creator_id"], user["id"])
            or db_is_blocked(db, user["id"], _m["creator_id"])
        ):
            raise HTTPException(403, "You cannot join this meetup.")
        if not db_join_meetup(db, meetup_id, user["id"]):
            raise HTTPException(404, "This meetup has ended.")
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
            "SELECT r.id, r.name, dp2.user_id AS other_user_id, "
            "u.display_name AS other_name, u.username AS other_username, "
            "u.avatar_url AS other_avatar_url, u.color_index AS other_color_index, "
            "u.country AS other_country, "
            "CASE WHEN EXISTS ("
            "  SELECT 1 FROM e2ee_device_keys dk WHERE dk.user_id = dp2.user_id"
            ") THEN 1 ELSE 0 END AS other_has_key, "
            "(SELECT MAX(m.created_at) FROM messages m WHERE m.room_id = r.id AND m.expires_at > ?) AS last_message_at "
            "FROM dm_participants dp1 "
            "JOIN dm_participants dp2 ON dp1.room_id = dp2.room_id AND dp1.user_id != dp2.user_id "
            "JOIN rooms r ON r.id = dp1.room_id "
            "JOIN users u ON u.id = dp2.user_id "
            "WHERE dp1.user_id = ? "
            "ORDER BY last_message_at DESC",
            (now, user["id"]),
        ).fetchall()
        avatar_base = "/chat/api/avatar/"
        return [
            {
                "room_id": dm["id"],
                "other_user_id": dm["other_user_id"],
                "other_name": dm["other_name"] or dm["other_username"] or "Anonymous",
                "other_avatar_url": f"{avatar_base}{dm['other_user_id']}?v=1"
                if dm["other_avatar_url"]
                else "",
                "other_color_index": dm["other_color_index"] or 0,
                "other_country": dm["other_country"] or "",
                "other_has_key": bool(dm["other_has_key"]),
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
        from chat_ws import manager

        manager.user_badge_rooms.setdefault(user["id"], set()).add(room_id)
        manager.user_badge_rooms.setdefault(target_id, set()).add(room_id)
        return {"room_id": room_id}
    finally:
        db.close()


# --- Users ---


@router.get("/blocks")
async def get_blocks(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        rows = db.execute(
            "SELECT b.blocked_id, u.display_name, u.username, u.avatar_url "
            "FROM blocks b JOIN users u ON u.id = b.blocked_id "
            "WHERE b.blocker_id = ?",
            (user["id"],),
        ).fetchall()
        return [
            {
                "user_id": r["blocked_id"],
                "display_name": r["display_name"] or "",
                "username": r["username"] or "",
                "avatar_url": r["avatar_url"] or "",
            }
            for r in rows
        ]
    finally:
        db.close()


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
        _check_upload_rate(user["id"])
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(400, "Only image files allowed")

        data = await file.read()
        if len(data) > 500 * 1024:
            raise HTTPException(400, "Max file size is 500KB")

        try:
            import pyvips

            try:
                img = pyvips.Image.new_from_buffer(data, "")
            except pyvips.Error:
                img = pyvips.Image.new_from_buffer(data, "", unlimited=True)
            loader = ""
            try:
                loader = img.get("vips-loader")
            except Exception:
                loader = ""
            if loader not in (
                "jpegload",
                "pngload",
                "webpload",
                "heifload",
                "gifload",
                "jpegload_buffer",
                "pngload_buffer",
                "webpload_buffer",
                "heifload_buffer",
                "gifload_buffer",
            ):
                raise HTTPException(400, "Unsupported image format")
            if img.width * img.height > 10_000_000:
                raise HTTPException(400, "Image too large")
            data = img.webpsave_buffer(Q=80)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "Invalid image file")

        from chat_moderation import check_openai_moderation

        data_uri = "data:image/webp;base64," + base64.b64encode(data).decode()
        mod = await check_openai_moderation("", data_uri)
        if mod is not None:
            raise HTTPException(400, "Image rejected by moderation")

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
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )
    finally:
        db.close()


@router.post("/upload/image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    user, db = _get_user_from_cookie(request)
    db.close()
    _check_upload_rate(user["id"])

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

        t_start = time.monotonic()
        try:
            img = pyvips.Image.new_from_buffer(data, "")
        except pyvips.Error:
            img = pyvips.Image.new_from_buffer(data, "", unlimited=True)
        t_decode = time.monotonic()
        loader = ""
        try:
            loader = img.get("vips-loader")
        except Exception:
            loader = ""
        if loader not in (
            "jpegload",
            "pngload",
            "webpload",
            "heifload",
            "gifload",
            "jpegload_buffer",
            "pngload_buffer",
            "webpload_buffer",
            "heifload_buffer",
            "gifload_buffer",
        ):
            raise ValueError("Unsupported image format")
        w, h = img.width, img.height
        if w * h > 40_000_000:
            raise ValueError("Image too large")
        max_side = max(w, h)
        resized = False
        if max_side > 1500:
            scale = 1500 / max_side
            img = img.resize(scale, kernel=pyvips.enums.Kernel.LANCZOS3)
            w, h = img.width, img.height
            resized = True
        img.webpsave(str(out_path), Q=80)
        t_save = time.monotonic()
        mod_path = upload_dir / f"{token}_mod.webp"
        if max(w, h) > 880:
            mod_scale = 800 / max(w, h)
            mod = img.resize(mod_scale, kernel=pyvips.enums.Kernel.LANCZOS3)
            mod.webpsave(str(mod_path), Q=60)
        else:
            img.webpsave(str(mod_path), Q=60)
        t_mod = time.monotonic()
        out_size = out_path.stat().st_size
        logger.info(
            "[UPLOAD] image %dx%d %dKB->%dKB resized=%s "
            "decode=%.0fms save=%.0fms mod=%.0fms total=%.0fms",
            w,
            h,
            len(data) // 1024,
            out_size // 1024,
            resized,
            (t_decode - t_start) * 1000,
            (t_save - t_decode) * 1000,
            (t_mod - t_save) * 1000,
            (t_mod - t_start) * 1000,
        )
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
    _check_upload_rate(user["id"])

    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "Only video files allowed")

    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "Max file size is 100MB")

    upload_dir = Path(__file__).resolve().parent / "chat" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = upload_dir.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    import subprocess
    import tempfile

    token = secrets.token_hex(16)
    filename = f"{token}.mp4"
    out_path = upload_dir / filename

    t_start = time.monotonic()
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".mp4", dir=str(tmp_dir))
    try:
        os.write(tmp_fd, data)
        os.close(tmp_fd)
        t_write = time.monotonic()

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
                tmp_name,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        t_probe = time.monotonic()
        info = json.loads(probe.stdout)
        duration = float(info["format"].get("duration", 0))
        if duration > 65:
            raise HTTPException(400, "Video must be 60 seconds or less")

        video_stream = next(
            (s for s in info["streams"] if s["codec_type"] == "video"), None
        )
        if not video_stream:
            raise HTTPException(400, "No video stream found")
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        codec = video_stream.get("codec_name", "?")
        bitrate_kbps = int(video_stream.get("bit_rate", 0)) // 1000

        import shutil

        shutil.move(tmp_name, str(out_path))
        tmp_name = None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Could not process video file")
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    t_frames_start = time.monotonic()
    mod_frames = 0
    for i, frac in enumerate([0.25, 0.5, 0.75]):
        frame_path = upload_dir / f"{token}_mod{i}.webp"
        seek = duration * frac if duration > 0 else 0
        try:
            raw_frame = tmp_dir / f"{token}_raw{i}.png"
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-y",
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
                    str(raw_frame),
                ],
                timeout=10,
            )
            if raw_frame.exists() and raw_frame.stat().st_size > 0:

                def _convert(src, dst):
                    import pyvips

                    img = pyvips.Image.new_from_file(str(src))
                    img.webpsave(str(dst), Q=60)
                    src.unlink()

                await asyncio.to_thread(_convert, raw_frame, frame_path)
                mod_frames += 1
            else:
                raw_frame.unlink(missing_ok=True)
                logger.warning(
                    "[UPLOAD] ffmpeg frame extraction failed for frame %d", i
                )
        except Exception as e:
            logger.warning("[UPLOAD] frame %d failed: %s", i, e)
            raw_frame.unlink(missing_ok=True)
            frame_path.unlink(missing_ok=True)
    t_frames_end = time.monotonic()

    if mod_frames == 0:
        out_path.unlink(missing_ok=True)
        for j in range(3):
            (tmp_dir / f"{token}_raw{j}.png").unlink(missing_ok=True)
            (upload_dir / f"{token}_mod{j}.webp").unlink(missing_ok=True)
        raise HTTPException(400, "Could not process video for moderation")

    logger.info(
        "[UPLOAD] video %dx%d %s %dkbps %.1fs %dKB "
        "write=%.0fms probe=%.0fms frames=%.0fms(%d) total=%.0fms",
        width,
        height,
        codec,
        bitrate_kbps,
        duration,
        len(data) // 1024,
        (t_write - t_start) * 1000,
        (t_probe - t_write) * 1000,
        (t_frames_end - t_frames_start) * 1000,
        mod_frames,
        (t_frames_end - t_start) * 1000,
    )

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
        if not _is_valid_push_endpoint(endpoint):
            raise HTTPException(422, "Invalid push endpoint")
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


@router.post("/push/idle", status_code=204)
async def chat_push_idle(request: Request):
    user, db = _get_user_from_cookie(request)
    db.close()
    from chat_ws import manager

    manager._last_ws_activity[user["id"]] = 0
    return Response(status_code=204)


_swlog_rate: dict[str, list[float]] = {}


@router.post("/swlog", status_code=204)
async def chat_swlog(request: Request):
    # Temporary diagnostic: SW/page push-navigation timeline, see [PUSH] debugging
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _swlog_rate.get(ip, []) if now - t < 60]
    if len(hits) >= 30:
        return Response(status_code=204)
    hits.append(now)
    _swlog_rate[ip] = hits
    if len(_swlog_rate) > 1000:
        _swlog_rate.clear()
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=204)
    logger.info("[SWLOG] %s", json.dumps(body)[:500])
    return Response(status_code=204)


_push_ack_rate: dict[str, list[float]] = {}


@router.post("/push/ack", status_code=204)
async def chat_push_ack(request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _push_ack_rate.get(ip, []) if now - t < 60]
    if len(hits) >= 60:
        return Response(status_code=204)
    hits.append(now)
    _push_ack_rate[ip] = hits
    if len(_push_ack_rate) > 1000:
        _push_ack_rate.clear()
    body = await request.json()
    endpoint = body.get("endpoint")
    action = body.get("action")
    if not endpoint or action not in ("delivered", "clicked", "dismissed"):
        raise HTTPException(400, "endpoint and action required")
    logger.info(
        "[PUSH-ACK] action=%s sw=%s url=%s endpoint=...%s",
        action,
        body.get("v", "pre-v3"),
        body.get("url"),
        endpoint[-16:],
    )
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


# --- E2EE device keys ---

_DEVICE_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _validate_e2ee_jwk(public_key: str) -> None:
    """Validate a P-256 public key JWK string. Raises HTTPException 422 on invalid input."""
    if not isinstance(public_key, str):
        raise HTTPException(422, "public_key must be a string")
    try:
        jwk = json.loads(public_key)
    except Exception:
        raise HTTPException(422, "Invalid JWK: not valid JSON")
    if not isinstance(jwk, dict):
        raise HTTPException(422, "Invalid JWK: must be an object")
    if jwk.get("kty") != "EC":
        raise HTTPException(422, "Invalid JWK: kty must be EC")
    if jwk.get("crv") != "P-256":
        raise HTTPException(422, "Invalid JWK: crv must be P-256")
    if "d" in jwk:
        raise HTTPException(422, "Invalid JWK: private key field d not allowed")
    for coord in ("x", "y"):
        val = jwk.get(coord)
        if not val or not isinstance(val, str):
            raise HTTPException(422, f"Invalid JWK: {coord} missing")
        try:
            rem = len(val) % 4
            padded = val + "=" * (4 - rem) if rem else val
            decoded = base64.urlsafe_b64decode(padded)
        except Exception:
            raise HTTPException(422, f"Invalid JWK: {coord} is not valid base64url")
        if len(decoded) != 32:
            raise HTTPException(
                422, f"Invalid JWK: {coord} must decode to exactly 32 bytes"
            )


@router.put("/keys", status_code=204)
async def put_e2ee_key(request: Request):
    user, db = _get_user_from_cookie(request)
    try:
        body = await request.json()
        device_id = body.get("device_id", "")
        if not isinstance(device_id, str) or not _DEVICE_ID_RE.match(device_id):
            raise HTTPException(422, "Invalid device_id: must be 32 hex chars")
        public_key = body.get("public_key", "")
        _validate_e2ee_jwk(public_key)
        changed = upsert_e2ee_device_key(db, user["id"], device_id, public_key)
        # Broadcast on ANY changed mapping, including the first upload: a peer
        # may have opened the DM (and latched into unencrypted fallback) while
        # this user was still in profile setup, before their first key existed.
        # Same-key re-uploads (every page load) stay silent.
        if changed:
            dm_rows = db.execute(
                "SELECT dp1.room_id, dp2.user_id AS other_user_id "
                "FROM dm_participants dp1 "
                "JOIN dm_participants dp2 "
                "  ON dp1.room_id = dp2.room_id AND dp2.user_id != dp1.user_id "
                "WHERE dp1.user_id = ?",
                (user["id"],),
            ).fetchall()
            from chat_ws import manager

            for row in dm_rows:
                asyncio.create_task(
                    manager.send_to_user(
                        row["other_user_id"],
                        {
                            "event": "key_rotated",
                            "user_id": user["id"],
                            "room_id": row["room_id"],
                        },
                    )
                )
            # Sibling devices of the SAME user must also invalidate their
            # cached device list for this user, so their next send fans out
            # to the new/re-keyed device. No room to key this on.
            asyncio.create_task(
                manager.send_to_user(
                    user["id"],
                    {
                        "event": "key_rotated",
                        "user_id": user["id"],
                        "room_id": None,
                    },
                )
            )
        return Response(status_code=204)
    finally:
        db.close()


@router.get("/keys/{user_id}")
async def get_e2ee_key_endpoint(user_id: str, request: Request):
    _user, db = _get_user_from_cookie(request)
    try:
        devices = get_e2ee_device_keys(db, user_id)
        if not devices:
            raise HTTPException(404, "Key not found")
        return {
            "user_id": user_id,
            "devices": [
                {
                    "device_id": d["device_id"],
                    "public_key": d["public_key"],
                    "created_at": d["created_at"],
                }
                for d in devices
            ],
        }
    finally:
        db.close()


# --- Admin ---


async def _admin_json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")


@router.get("/admin/reports")
async def admin_reports(request: Request, status: str = "pending"):
    actor = _require_admin(request)
    db = _get_db()
    try:
        if status == "pending":
            reports = get_pending_reports(db)
        elif status in ("actioned", "dismissed", "all"):
            reports = get_reports_by_status(db, status)
        else:
            raise HTTPException(400, "invalid status")
        return [
            {
                "id": r["id"],
                "reporter_id": r["reporter_id"],
                "reporter_name": r["reporter_name"],
                "reported_name": r["reported_name"],
                "message_snapshot": r["message_snapshot"],
                "room_id": r["room_id"],
                "room_name": (r["room_name"] if "room_name" in r.keys() else None),
                "reported_user_id": r["reported_user_id"],
                "reason": r["reason"],
                "status": r["status"],
                "unverified": bool(r["unverified"]),
                "created_at": r["created_at"],
            }
            for r in reports
        ]
    finally:
        db.close()


@router.patch("/admin/reports/{report_id}")
async def admin_resolve_report(report_id: str, request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    status = body.get("status")
    if status not in ("actioned", "dismissed"):
        raise HTTPException(400, "status must be 'actioned' or 'dismissed'")
    db = _get_db()
    try:
        if resolve_report(db, report_id, status) == 0:
            raise HTTPException(409, "Report already resolved")
        log_admin_action(db, actor["label"], "resolve_report", detail=status)
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/ban/{user_id}")
async def admin_ban(user_id: str, request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    reason = body.get("reason", "Banned by admin")
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        _guard_target(db, actor, user_id)
        providers = db.execute(
            "SELECT provider, provider_id FROM user_providers WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        seen = set()
        for p in providers:
            key = (p["provider"], p["provider_id"])
            if key in seen:
                continue
            seen.add(key)
            db_ban_user(
                db,
                user_id,
                p["provider"],
                p["provider_id"],
                reason,
                user["device_fingerprint"],
            )
        # ensure the base users-row identity is covered too
        if (user["provider"], user["provider_id"]) not in seen:
            db_ban_user(
                db,
                user_id,
                user["provider"],
                user["provider_id"],
                reason,
                user["device_fingerprint"],
            )

        from chat_ws import manager

        removed = delete_user_messages(db, user_id)
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

        await manager.send_to_user(user_id, {"event": "banned", "reason": reason})

        for conn_id, ws in list(manager.user_conns.get(user_id, {}).items()):
            try:
                await ws.close(code=4003, reason="Account banned")
            except Exception:
                pass
        log_admin_action(
            db, actor["label"], "ban", target_user_id=user_id, detail=reason
        )
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/unban/{user_id}")
async def admin_unban(user_id: str, request: Request):
    actor = _require_super_admin(request)
    db = _get_db()
    try:
        db.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        db.commit()
        log_admin_action(db, actor["label"], "unban", target_user_id=user_id)
        return {"ok": True}
    finally:
        db.close()


@router.delete("/admin/bans/{ban_id}")
async def admin_delete_ban(ban_id: str, request: Request):
    actor = _require_super_admin(request)
    db = _get_db()
    try:
        row = db.execute("SELECT user_id FROM bans WHERE id = ?", (ban_id,)).fetchone()
        if row and row["user_id"]:
            db.execute("DELETE FROM bans WHERE user_id = ?", (row["user_id"],))
        else:
            db.execute("DELETE FROM bans WHERE id = ?", (ban_id,))
        db.commit()
        log_admin_action(
            db,
            actor["label"],
            "delete_ban",
            target_user_id=(row["user_id"] if row else None),
        )
        return {"ok": True}
    finally:
        db.close()


_SETTINGS_INT_DEFAULTS = {
    "msg_char_limit": ("1000", 1, 5000),
    "dm_ttl_minutes": ("1440", 1, 43200),
    "room_ttl_minutes": ("1440", 1, 43200),
    "meetup_ttl_minutes": ("60", 1, 43200),
}


@router.get("/admin/settings")
async def admin_get_settings(request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        out = {"room_sort": get_setting(db, "room_sort", "auto")}
        for key, (default, _lo, _hi) in _SETTINGS_INT_DEFAULTS.items():
            try:
                out[key] = int(get_setting(db, key, default))
            except (ValueError, TypeError):
                out[key] = int(default)
        return out
    finally:
        db.close()


@router.patch("/admin/settings")
async def admin_update_settings(request: Request):
    actor = _require_super_admin(request)
    body = await _admin_json(request)
    db = _get_db()
    try:
        changed = []
        if "room_sort" in body:
            if body["room_sort"] not in ("auto", "manual"):
                raise HTTPException(400, "room_sort must be 'auto' or 'manual'")
            old = get_setting(db, "room_sort", "auto")
            if old != body["room_sort"]:
                changed.append(f"room_sort: {old} -> {body['room_sort']}")
            set_setting(db, "room_sort", body["room_sort"])
        for key, (default, lo, hi) in _SETTINGS_INT_DEFAULTS.items():
            if key not in body:
                continue
            v = body[key]
            if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
                raise HTTPException(
                    400, f"{key} must be an integer between {lo} and {hi}"
                )
            old = get_setting(db, key, default)
            if str(old) != str(v):
                changed.append(f"{key}: {old} -> {v}")
            set_setting(db, key, str(v))
        log_admin_action(
            db,
            actor["label"],
            "update_settings",
            detail="; ".join(changed) if changed else "no changes",
        )
        return {"ok": True}
    finally:
        db.close()


@router.get("/admin/stats")
async def admin_stats(request: Request):
    actor = _require_admin(request)
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
    actor = _require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
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
    actor = _require_admin(request)
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
    actor = _require_admin(request)
    db = _get_db()
    try:
        return get_all_bans(db)
    finally:
        db.close()


@router.get("/admin/modlog")
async def admin_modlog(request: Request, limit: int = 50, offset: int = 0):
    actor = _require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db = _get_db()
    try:
        return get_moderation_log(db, limit, offset)
    finally:
        db.close()


@router.get("/admin/rooms")
async def admin_rooms(request: Request):
    actor = _require_admin(request)
    from chat_ws import manager

    online_counts = {}
    if hasattr(manager, "rooms"):
        for room_id, room in manager.rooms.items():
            online_counts[room_id] = (
                len(room.connections) if hasattr(room, "connections") else 0
            )
    db = _get_db()
    try:
        # DM rooms are end-to-end encrypted and unmanageable from here (no read,
        # edit, delete, reorder, or set-main), so they are excluded from the tab.
        rooms = [r for r in get_room_stats(db, online_counts) if r["type"] != "dm"]
        counts = get_reachable_member_counts(db, [r["id"] for r in rooms])
        for r in rooms:
            r["member_count"] = counts.get(r["id"], 0)
        return rooms
    finally:
        db.close()


@router.post("/admin/mute/{user_id}")
async def admin_mute_user(user_id: str, request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    minutes = body.get("minutes", 30)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        _guard_target(db, actor, user_id)
        mute_user(db, user_id, minutes=minutes)
        mute_count = increment_mute_count(db, user_id)
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

        if mute_count >= MAX_MUTES_BEFORE_BAN:
            ban_reason = f"Auto-ban: muted {MAX_MUTES_BEFORE_BAN} times (admin mute)"
            ban_user_all_providers(db, user_id, ban_reason)
            await manager.send_to_user(
                user_id, {"event": "banned", "reason": ban_reason}
            )
            for conn_id, ws in list(manager.user_conns.get(user_id, {}).items()):
                try:
                    await ws.close(code=4003, reason="Account banned")
                except Exception:
                    pass
            log_admin_action(
                db, actor["label"], "ban", target_user_id=user_id, detail=ban_reason
            )
            return {"ok": True, "action": "ban"}

        asyncio.create_task(
            manager.send_to_user(
                user_id, {"event": "muted", "reason": "Muted by admin"}
            )
        )
        log_admin_action(
            db, actor["label"], "mute", target_user_id=user_id, detail=str(minutes)
        )
        return {"ok": True, "action": "mute"}
    finally:
        db.close()


@router.post("/admin/unmute/{user_id}")
async def admin_unmute_user(user_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        db.execute("UPDATE users SET muted_until = NULL WHERE id = ?", (user_id,))
        db.commit()
        log_admin_action(db, actor["label"], "unmute", target_user_id=user_id)
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/strike/{user_id}")
async def admin_strike_user(user_id: str, request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    reason = body.get("reason", "admin")
    detail = body.get("detail", "Manual admin action")
    db = _get_db()
    try:
        _guard_target(db, actor, user_id)

        from chat_moderation import process_strike

        result = process_strike(db, user_id, reason, detail)

        if result["action"] in ("mute", "ban"):
            from chat_ws import manager

            removed = delete_user_messages(db, user_id)
            for batch in removed:
                await manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )

            if result["action"] == "ban":
                await manager.send_to_user(
                    user_id, {"event": "banned", "reason": result["reason"]}
                )
                for conn_id, ws in list(manager.user_conns.get(user_id, {}).items()):
                    try:
                        await ws.close(code=4003, reason="Banned")
                    except Exception:
                        pass
            else:
                await manager.send_to_user(
                    user_id,
                    {
                        "event": "muted",
                        "reason": result["reason"],
                        "message": result.get("message", ""),
                    },
                )

        if result["action"] != "none":
            log_admin_action(
                db,
                actor["label"],
                result["action"],
                target_user_id=user_id,
                detail=detail,
            )
        return result
    finally:
        db.close()


@router.post("/admin/users/{user_id}/clear-warnings")
async def admin_clear_warnings(user_id: str, request: Request):
    actor = _require_super_admin(request)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        db.execute("DELETE FROM strikes WHERE user_id = ?", (user_id,))
        db.execute(
            "UPDATE users SET muted_until = NULL, mute_count = 0 WHERE id = ?",
            (user_id,),
        )
        from chat_db import _uuid, _now

        db.execute(
            "INSERT INTO strikes (id, user_id, reason, detail, created_at, expires_at) "
            "VALUES (?, ?, 'warnings_cleared', 'Cleared by admin', ?, '2000-01-01T00:00:00+00:00')",
            (_uuid(), user_id, _now()),
        )
        db.commit()
        log_admin_action(db, actor["label"], "clear_warnings", target_user_id=user_id)
        return {"ok": True}
    finally:
        db.close()


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    actor = _require_super_admin(request)
    db = _get_db()
    try:
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(404, "User not found")
        _guard_target(db, actor, user_id)
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
                await ws.close(code=4003, reason="Account deleted")
            except Exception:
                pass
        log_admin_action(db, actor["label"], "delete_user", target_user_id=user_id)
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/rooms")
async def admin_create_room(request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    name = body.get("name", "").strip()
    room_type = body.get("type", "general")
    if not name:
        raise HTTPException(400, "Room name required")
    if len(name) > 80:
        raise HTTPException(400, "Room name too long (max 80)")
    if len(body.get("description", "")) > 500:
        raise HTTPException(400, "Description too long (max 500)")
    if room_type not in ("general", "stage"):
        raise HTTPException(400, "type must be 'general' or 'stage'")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise HTTPException(400, "Room name must contain letters or digits")
    room_id = slug
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
            auto_join=body.get("auto_join", False),
            allows_media=body.get("allows_media", True),
            ttl_minutes=body.get(
                "ttl_minutes", int(get_setting(db, "room_ttl_minutes", "360"))
            ),
            position=body.get("position", 0),
        )
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        log_admin_action(
            db, actor["label"], "create_room", target_room_id=room_id, detail=name
        )
        return room
    finally:
        db.close()


@router.patch("/admin/rooms/{room_id}")
async def admin_update_room(room_id: str, request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    db = _get_db()
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["type"] in ("dm", "meetup"):
            raise HTTPException(400, "DM and meetup rooms cannot be edited")
        if "ttl_minutes" in body:
            v = body["ttl_minutes"]
            if v is not None and (
                isinstance(v, bool) or not isinstance(v, int) or not (0 < v <= 43200)
            ):
                raise HTTPException(
                    400, "ttl_minutes must be a positive integer or null"
                )
        if "position" in body:
            v = body["position"]
            if isinstance(v, bool) or not isinstance(v, int):
                raise HTTPException(400, "position must be an integer")
        if "name" in body:
            v = body["name"]
            if not isinstance(v, str) or not v.strip() or len(v) > 80:
                raise HTTPException(400, "name must be a non-empty string (max 80)")
        if "description" in body:
            v = body["description"]
            if not isinstance(v, str) or len(v) > 500:
                raise HTTPException(400, "description must be a string (max 500)")
        # The client sends the whole form on every save, so diff against the
        # current values (room still holds them) to log only what actually changed,
        # recording old -> new so the audit says exactly what was edited.
        bool_fields = ("is_moderated", "is_read_only", "auto_join", "allows_media")
        editable = bool_fields + ("name", "description", "ttl_minutes", "position")

        def _fmt(k, v):
            if k in bool_fields:
                return "on" if v else "off"
            if v is None:
                return "none"
            if v == "":
                return "(empty)"
            s = str(v)
            return s if len(s) <= 40 else s[:37] + "..."

        changed = []
        for k in body:
            if k not in editable or k not in room.keys():
                continue
            differs = (
                bool(room[k]) != bool(body[k])
                if k in bool_fields
                else room[k] != body[k]
            )
            if differs:
                changed.append(f"{k}: {_fmt(k, room[k])} -> {_fmt(k, body[k])}")
        update_room(db, room_id, **body)
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        log_admin_action(
            db,
            actor["label"],
            "update_room",
            target_room_id=room_id,
            detail="; ".join(changed) if changed else "no changes",
        )
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/rooms/{room_id}/main")
async def admin_set_main_room(room_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["type"] not in ("general", "stage"):
            raise HTTPException(400, "Only group rooms can be the main room")
        db.execute(
            "UPDATE rooms SET is_main = 0 WHERE event_id = ?", (room["event_id"],)
        )
        db.execute("UPDATE rooms SET is_main = 1 WHERE id = ?", (room_id,))
        db.commit()
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        log_admin_action(db, actor["label"], "set_main", target_room_id=room_id)
        return {"ok": True}
    finally:
        db.close()


@router.post("/admin/rooms/reorder")
async def admin_reorder_rooms(request: Request):
    actor = _require_admin(request)
    body = await _admin_json(request)
    order = body.get("order", [])
    if not order:
        raise HTTPException(400, "order required")
    db = _get_db()
    try:
        valid = {
            r["id"]
            for r in db.execute(
                "SELECT id FROM rooms WHERE type IN ('general', 'stage')"
            ).fetchall()
        }
        for i, room_id in enumerate(order):
            if room_id not in valid:
                continue
            db.execute("UPDATE rooms SET position = ? WHERE id = ?", (i, room_id))
        db.commit()
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        log_admin_action(db, actor["label"], "reorder", detail=f"{len(order)} rooms")
        return {"ok": True}
    finally:
        db.close()


@router.delete("/admin/rooms/{room_id}")
async def admin_delete_room(room_id: str, request: Request):
    actor = _require_super_admin(request)
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
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
        log_admin_action(db, actor["label"], "delete_room", target_room_id=room_id)
        return {"ok": True}
    finally:
        db.close()


@router.get("/admin/me")
async def admin_me(request: Request):
    actor = _require_admin(request)
    return {
        "role": actor["role"],
        "kind": actor["kind"],
        "label": actor["label"],
        "email_hash": actor["email_hash"],
    }


@router.get("/admin/audit")
async def admin_audit(request: Request, limit: int = 50, offset: int = 0):
    _require_admin(request)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db = _get_db()
    try:
        return get_admin_actions(db, limit, offset)
    finally:
        db.close()


@router.get("/admin/admins")
async def admin_list_admins(request: Request):
    _require_super_admin(request)
    db = _get_db()
    try:
        rows = list_admins(db)
        by_hash = {r["email_hash"]: r for r in rows}
        out = []
        # env permanent super-admins first
        for h in sorted(_ADMIN_EMAIL_HASHES):
            db_row = by_hash.pop(h, None)
            out.append(
                {
                    "email_hash": h,
                    "role": "super_admin",
                    "label": (db_row["label"] if db_row and db_row["label"] else "env"),
                    "permanent": True,
                    "added_by": "env",
                    "created_at": (db_row["created_at"] if db_row else ""),
                }
            )
        for r in by_hash.values():
            out.append({**r, "permanent": False})
        return out
    finally:
        db.close()


@router.post("/admin/admins")
async def admin_add_admin(request: Request):
    actor = _require_super_admin(request)
    body = await _admin_json(request)
    email = (body.get("email") or "").strip()
    role = body.get("role", "admin")
    label = (body.get("label") or "").strip() or None
    if not email:
        raise HTTPException(400, "email required")
    if role not in VALID_ADMIN_ROLES:
        raise HTTPException(400, "role must be 'admin' or 'super_admin'")
    h = hash_email(email)
    if h in _ADMIN_EMAIL_HASHES:
        raise HTTPException(409, "Already a permanent super-admin")
    db = _get_db()
    try:
        row = add_admin(db, h, role, label, actor["label"])
        log_admin_action(db, actor["label"], "add_admin", detail=f"{role}:{h[:12]}")
        return {**row, "permanent": False}
    finally:
        db.close()


@router.delete("/admin/admins/{email_hash}")
async def admin_remove_admin(email_hash: str, request: Request):
    actor = _require_super_admin(request)
    if email_hash in _ADMIN_EMAIL_HASHES:
        raise HTTPException(400, "Cannot remove a permanent super-admin")
    db = _get_db()
    try:
        row = get_admin(db, email_hash)
        if not row:
            raise HTTPException(404, "Admin not found")
        # never leave zero super-admins when there are no env super-admins
        if (
            row["role"] == "super_admin"
            and not _ADMIN_EMAIL_HASHES
            and count_super_admins(db) <= 1
        ):
            raise HTTPException(400, "Would remove the last super-admin")
        remove_admin(db, email_hash)
        log_admin_action(db, actor["label"], "remove_admin", detail=email_hash[:12])
        return {"ok": True}
    finally:
        db.close()


@router.get("/admin/rooms/{room_id}/messages")
async def admin_room_messages(room_id: str, request: Request, limit: int = 100):
    _require_admin(request)
    limit = max(1, min(limit, 200))
    db = _get_db()
    try:
        room = get_room(db, room_id)
        if not room:
            raise HTTPException(404, "Room not found")
        if room["type"] == "dm":
            raise HTTPException(400, "DM messages are end-to-end encrypted")
        return get_room_messages_admin(db, room_id, limit)
    finally:
        db.close()


@router.delete("/admin/messages/{message_id}")
async def admin_delete_message(message_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        result = delete_message_by_id(db, message_id)
        if not result:
            raise HTTPException(404, "Message not found")
        log_admin_action(
            db,
            actor["label"],
            "delete_message",
            target_user_id=result["user_id"],
            target_room_id=result["room_id"],
            detail=message_id[:8],
        )
        from chat_ws import manager

        asyncio.create_task(
            manager.broadcast_to_room(
                result["room_id"],
                {
                    "event": "messages_expired",
                    "room_id": result["room_id"],
                    "message_ids": [message_id],
                },
            )
        )
        return {"ok": True}
    finally:
        db.close()


@router.get("/admin/meetups")
async def admin_list_meetups(request: Request):
    _require_admin(request)
    db = _get_db()
    try:
        return get_all_meetups(db)
    finally:
        db.close()


@router.delete("/admin/meetups/{meetup_id}")
async def admin_delete_meetup(meetup_id: str, request: Request):
    actor = _require_admin(request)
    db = _get_db()
    try:
        if not delete_meetup(db, meetup_id):
            raise HTTPException(404, "Meetup not found")
        log_admin_action(
            db, actor["label"], "delete_room", target_room_id=meetup_id, detail="meetup"
        )
        from chat_ws import manager

        asyncio.create_task(manager.broadcast_to_all({"event": "rooms_changed"}))
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
    app.include_router(router)

    @app.websocket("/ws/chat/{token}")
    async def chat_websocket(websocket: WebSocket, token: str):
        await handle_chat_ws(websocket, token, DEFAULT_EVENT_ID)

    @app.get("/chat/v/{token}")
    async def verify_via_path(request: Request, token: str):
        return await auth_email_verify(request, token)

    @app.get("/chat/admin", response_class=HTMLResponse)
    async def serve_admin_shortcut(request: Request):
        return HTMLResponse(_admin_html, headers={"Cache-Control": "no-store"})

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
            return HTMLResponse(
                chat_html.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-store"},
            )
        raise HTTPException(404, "Chat not available")

    uploads_dir = CHAT_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    _upload_filename_re = __import__("re").compile(r"^[a-f0-9]{32}\.(webp|mp4)$")

    @app.get("/chat/uploads/{filename}")
    async def serve_upload(filename: str):
        if not _upload_filename_re.match(filename):
            raise HTTPException(404)
        path = uploads_dir / filename
        if not path.is_file():
            raise HTTPException(404)
        from starlette.responses import FileResponse

        media = "image/webp" if filename.endswith(".webp") else "video/mp4"
        return FileResponse(
            path,
            media_type=media,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    _load_disposable_domains()
    _load_admin_emails()
    _load_site_short()

    tmp_dir = CHAT_DIR / "tmp"
    if tmp_dir.is_dir():
        stale = 0
        for f in tmp_dir.iterdir():
            if f.is_file():
                f.unlink()
                stale += 1
        if stale:
            logger.info("Cleaned %d stale temp files from %s", stale, tmp_dir)

    db = _get_db()
    seed_event_rooms(db, DEFAULT_EVENT_ID, "Stone Techno 2026")
    db.close()

    return purge_loop
