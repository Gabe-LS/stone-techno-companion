# Implementation spec: pre-deployment fixes

You are a Sonnet 5 implementation agent. Apply the changes below EXACTLY as specified. This spec is authoritative — it is the arbitrated output of a six-agent review; every item here is already confirmed. Do NOT redesign, do NOT add extra changes, do NOT refactor surrounding code. If a change as written is impossible (the anchor doesn't exist / already fixed), note it in your final report and move on — do not improvise an alternative.

You CANNOT run commands (Bash is unavailable and will fail). Do not claim to have tested anything. The orchestrator runs all tests after you finish. Your job is precise edits only.

Tools: Read, Glob, Grep, Edit, Write. Read each file/region before editing it.

Follow existing code style. No emojis anywhere (code, comments, logs). Match the surrounding logging style (`logger.info/warning`, `dbg()` in JS).

---

## FILE: `.gitignore` (repo root)

**Change 1.** The current `.gitignore` omits real runtime/user data that CLAUDE.md claims is ignored. Add these lines under the existing "Server runtime data" section:

```
# Chat runtime data (ephemeral DB + user uploads — must never be committed)
server/chat.db
server/chat.db-shm
server/chat.db-wal
server/chat/uploads/
server/chat/tmp/
server/static/bios.json
server/static/index.html
server/static/photos/
server/static/timetable.json
lineup.db.bak
lineup.db.bak-shm
```

---

## FILE: `server/.dockerignore` (NEW FILE)

**Change 2.** Create `server/.dockerignore` so local runtime files are never baked into the image build context (the Dockerfile does `COPY chat/ ./chat/`):

```
chat/uploads/
chat/tmp/
data/
*.env
.env
*.pem
__pycache__/
*.pyc
.DS_Store
```

---

## FILE: `server/chat_api.py`

**Change 3 (magic-link fail-loud).** In the email magic-link handler (around the `else: logger.warning("MAILEROO_API_KEY not set — email not sent")` branch, ~line 438), the function currently falls through to `return {"sent": True}` even when no email was sent. Change the `else` branch so it raises instead of silently succeeding:

```python
    else:
        logger.error("MAILEROO_API_KEY not set — cannot send magic link")
        raise HTTPException(500, "Email delivery is not configured")
```

Leave the final `return {"sent": True}` in place (it is now only reached on the success path).

**Change 4 (site_short visibility).** In `_load_site_short` (~line 118) the `except Exception: pass` hides a real production failure. Change it to log a warning:

```python
    except Exception as e:
        logger.warning("site_short lookup failed (lineup.db not reachable): %s", e)
```

**Change 5 (avatar moderation + rate limit).** In `upload_avatar` (~line 1121):
- (a) Add a rate-limit call. Immediately after `user, db = _get_user_from_cookie(request)` and inside the `try:`, add `_check_upload_rate(user["id"])` as the first statement (mirroring `upload_image`). Read how `upload_image` calls it to match exactly (argument and placement).
- (b) After the avatar bytes are re-encoded to webp (`data = img.webpsave_buffer(Q=80)`) and BEFORE the `db.execute("UPDATE users SET avatar_url ...")`, run moderation on the processed image and reject on a flag. Use the existing moderation helper. First Read `server/chat_moderation.py` to confirm the exact signature of `check_openai_moderation` and how `server/chat_ws.py` builds an image data-URI for it (`_image_to_data_uri` or similar). Then add, adapting to the real signature:

```python
        from chat_moderation import check_openai_moderation
        import base64

        data_uri = "data:image/webp;base64," + base64.b64encode(data).decode()
        mod = await check_openai_moderation("", data_uri)
        if mod is not None and not mod.get("allowed", True):
            raise HTTPException(400, "Image rejected by moderation")
```

IMPORTANT: match the real return shape of `check_openai_moderation`. If it returns `None` on "no violation / not configured" and a dict with a flag on violation, treat `None` as pass. If the flag key differs (e.g. `"flagged"`), use the real key. Read the function first and adapt. Keep the change fail-open (missing API key / None = allowed) so a moderation outage does not block all avatar uploads.

**Change 6 (image format allowlist — SVG/other loader rejection).** In BOTH `upload_avatar` (~line 1132) and `_process_image` (~line 1211), after the image is decoded via `pyvips.Image.new_from_buffer(...)` (including the `unlimited=True` fallback) and before it is used, reject non-raster loaders. Add right after the decode block, before the pixel-count check:

```python
            loader = ""
            try:
                loader = img.get("vips-loader")
            except Exception:
                loader = ""
            if loader not in ("jpegload", "pngload", "webpload", "heifload", "gifload", "jpegload_buffer", "pngload_buffer", "webpload_buffer", "heifload_buffer", "gifload_buffer"):
                raise HTTPException(400, "Unsupported image format")
```

Do NOT remove the `unlimited=True` fallback (it is required for iPhone HEIC Live Photos per CLAUDE.md). Only add the loader allowlist.

**Change 7 (admin_ban: close live WS + ban across all providers).** In `admin_ban` (~line 1688):
- (a) After `db_ban_user(...)` succeeds and before `return {"ok": True}`, close all of the banned user's live WebSocket connections, mirroring the pattern already in `admin_delete_user` (Read it, ~line 1952). Add:

```python
        from chat_ws import manager
        for conn_id, ws in list(manager.user_conns.get(user_id, {}).items()):
            try:
                asyncio.create_task(ws.close(code=4003, reason="Account banned"))
            except Exception:
                pass
```

Confirm `asyncio` is already imported in this file (it is used elsewhere); if the manager import name differs, match what `admin_delete_user` uses.
- (b) Ban across every linked provider, not just the frozen `users.provider/provider_id`. Replace the single `db_ban_user(...)` call with a loop over the user's `user_providers` rows, plus the base row. First Read `db_ban_user`/`ban_user` in `chat_db.py` and check for a helper that lists a user's providers. Then:

```python
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
            db_ban_user(db, user_id, p["provider"], p["provider_id"], reason, user["device_fingerprint"])
        # ensure the base users-row identity is covered too
        if (user["provider"], user["provider_id"]) not in seen:
            db_ban_user(db, user_id, user["provider"], user["provider_id"], reason, user["device_fingerprint"])
```

Note: `ban_user` deletes sessions each call; that is harmless when repeated. Keep the WS-close step (a) after this loop.

**Change 8 (Google requires email_verified before linking).** In BOTH `/auth/google` (~line 278) and `/auth/google/code` (~line 339), the code links a Google login to an existing email-provider account via `find_user_by_provider(db, "email", email_hash)`. Guard the email-based lookup/link so it only happens when Google asserts the email is verified. Change the condition `if not user and email:` to also require verification:

```python
        email_verified = info.get("email_verified") is True or info.get("email_verified") == "true"
        if not user and email and email_verified:
```

Apply the identical change in both endpoints. Do not change the primary `find_user_by_provider(db, "google", provider_id)` lookup — only the email-fallback link is gated.

---

## FILE: `server/chat_moderation.py` (or wherever startup init lives) — OPENAI key visibility

**Change 9.** Add a loud startup log if `OPENAI_API_KEY` is unset, mirroring the VAPID consistency check. The cleanest home is the server startup in `server/api.py` (where `_check_vapid_key_consistency` is called at startup). Read `server/api.py` around the startup event / where `_check_vapid_key_consistency()` is invoked, and add right after it:

```python
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning(
            "OPENAI_API_KEY not set — AI moderation layers 2 and 3 are DISABLED; "
            "only the local word filter is active for moderated rooms"
        )
    else:
        logger.info("OPENAI_API_KEY present — AI moderation enabled")
```

Confirm `os` and `logger` are in scope in that file (they are). If `_check_vapid_key_consistency` is called inside a function, place this adjacent to that call in the same scope.

---

## FILE: `server/chat_ws.py`

**Change 10 (redact message text in log).** Line ~786: `logger.info("[MOD] text=%r is_moderated=%s", text[:50], is_moderated)` logs plaintext user content verbatim, defeating the ephemeral-privacy design. Replace with a non-content log:

```python
    logger.info("[MOD] len=%d is_moderated=%s", len(text or ""), is_moderated)
```

**Change 11 (reply_to_id room-scoping + payload validation).** In the `send_message` handler (~line 1183 where `room_id`/`content`/`reply_to_id` are read from `data`), add validation BEFORE `create_message` is called:
- Reject non-string `content` and non-string `room_id`.
- If `reply_to_id` is provided, verify it references an existing message IN THE SAME `room_id`; if not, drop it (set to None) rather than passing an arbitrary/cross-room id.

Read the handler first (~lines 1183-1345). After the existing `if not room_id or not content: continue` guard, add:

```python
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
```

Also harden `_build_reply_snippet` (~line 708) defensively: it is now only reached with same-room ids, but add a `room_id` parameter and WHERE clause so the snippet query itself is room-scoped. If threading the param through all call sites is more than a couple of edits, SKIP the `_build_reply_snippet` signature change and rely solely on the send_message validation above (which is the primary fix) — note which you did in your report.

**Change 12 (message_acked includes room_id).** In the ack payload (~line 1346), add `room_id`:

```python
                            {
                                "event": "message_acked",
                                "temp_id": temp_id,
                                "room_id": room_id,
                                "id": msg["id"],
                                "created_at": msg["created_at"],
                            }
```

(The client half of this fix is Change 20 in chat.html.)

**Change 13 (unlink rejected media file on moderation reject).** In the moderation-reject branch (~line 795, `if not mod_result["allowed"]:`), the DB row is deleted but the served file is not. Before/after the `db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))`, unlink the served file when the message is image/video. Read how `delete_message` (~line 1629) unlinks (`_UPLOADS_DIR`, `{stem}_mod*.webp`). Add, adapting to use the same `_UPLOADS_DIR` and helper logic:

```python
        if not mod_result["allowed"]:
            if msg_type in ("image", "video"):
                try:
                    _u = json.loads(content).get("url", "")
                    if _u:
                        _fn = _u.rsplit("/", 1)[-1]
                        _stem = _fn.rsplit(".", 1)[0]
                        (_UPLOADS_DIR / _fn).unlink(missing_ok=True)
                        (_UPLOADS_DIR / f"{_stem}_mod.webp").unlink(missing_ok=True)
                        for _i in range(3):
                            (_UPLOADS_DIR / f"{_stem}_mod{_i}.webp").unlink(missing_ok=True)
                except Exception:
                    pass
            db.execute("DELETE FROM messages WHERE id = ?", (msg["id"],))
```

Confirm `_UPLOADS_DIR` and `json` are in scope in this function (they are used elsewhere in the file; if `_UPLOADS_DIR` is not importable here, match however `delete_message` references the uploads dir).

**Change 14 (meetup authz on reaction/report).** In `add_reaction`, `remove_reaction`, and `report_message` handlers (~lines 1568, 1595, 1663), the membership check only covers `dm` rooms. Add the same `meetup` membership check that `send_message` uses (~line 1295). For each of the three handlers, where it currently does `if r_room and r_room["type"] == "dm": <check dm_participants>`, extend to also gate meetup:

```python
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
```

Match the exact variable names each handler already uses for the room row and message row.

**Change 15 (purge preserves DMs that ever had messages).** The purge loop deletes DM rooms with no current messages (~line 1775), which after TTL expiry wrongly destroys DM threads that DID have messages (contradicts CLAUDE.md "DM rooms persist after messages expire"). Fix using a new `rooms.last_message_at` column (added in Change 17):
- Change the `empty_dms` query so it only deletes DM rooms that NEVER had a message:

```python
            empty_dms = db.execute(
                "SELECT r.id FROM rooms r "
                "WHERE r.type = 'dm' AND r.last_message_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM messages m WHERE m.room_id = r.id"
                ")"
            ).fetchall()
```

**Change 16 (media_url cleanup for E2EE + all media).** E2EE image/video DMs store the file URL inside the encrypted envelope, so neither purge nor delete ever cleans the file. Fix by having the server persist a plaintext `media_url` alongside the message (Change 17 adds the column; Change 18 populates it via create_message; Change 19 passes it from the send handler). Then update the two cleanup paths to prefer `media_url`:
- In `delete_message` (~line 1629), before the `json.loads(...).get("url","")` parse, prefer the column: if `msg_row["media_url"]` is set, use it as the url; else fall back to the existing content-parse. Adapt:

```python
                    _url = msg_row["media_url"] if ("media_url" in msg_row.keys() and msg_row["media_url"]) else json.loads(msg_row["content"]).get("url", "")
```

and use `_url` where the old `url` variable was.

**Change 17 (schema: rooms.last_message_at + messages.media_url).** File `server/chat_db.py`, function `_migrate_chat_db` (~line 251). Add idempotent migrations following the existing `PRAGMA table_info` pattern:

```python
    if "last_message_at" not in room_cols:
        db.execute("ALTER TABLE rooms ADD COLUMN last_message_at TEXT")
        # backfill so existing DMs with history are not wrongly purged
        db.execute(
            "UPDATE rooms SET last_message_at = ("
            "  SELECT MAX(created_at) FROM messages WHERE messages.room_id = rooms.id"
            ") WHERE EXISTS (SELECT 1 FROM messages WHERE messages.room_id = rooms.id)"
        )
        db.commit()
    if "media_url" not in cols:  # `cols` is the messages table_info set at top of function
        db.execute("ALTER TABLE messages ADD COLUMN media_url TEXT")
        db.commit()
```

Place the `room_cols` addition after the existing `room_cols` loop (reuse the `room_cols` set already computed there), and the `messages` addition after the `link_preview` check (reuse the `cols` set already computed at the top for the messages table). Also add both columns to the `CREATE TABLE IF NOT EXISTS rooms` (~line 94) and `messages` (~line 119) statements so fresh DBs get them: add `last_message_at TEXT` to rooms and `media_url TEXT` to messages (as nullable columns, no NOT NULL).

**Change 18 (create_message stores media_url + stamps room).** File `server/chat_db.py`, `create_message` (~line 748):
- Add a `media_url: str | None = None` parameter (last param).
- Add `media_url` to the INSERT column list and values.
- After the message INSERT, stamp the room: `db.execute("UPDATE rooms SET last_message_at = ? WHERE id = ?", (now, room_id))`.
- Add `"media_url": media_url` to the returned dict.

Resulting INSERT:

```python
    db.execute(
        "INSERT INTO messages (id, room_id, user_id, type, content, reply_to_id, media_url, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, room_id, user_id, msg_type, content, reply_to_id, media_url, expires, now),
    )
    db.execute("UPDATE rooms SET last_message_at = ? WHERE id = ?", (now, room_id))
    db.commit()
```

**Change 19 (send handler passes media_url).** File `server/chat_ws.py`, in the `send_message` handler where `create_message(...)` is called (~line 1340). For `image`/`video` messages the client sends the URL; extract a plaintext media URL to pass through. Because DM content may be an E2EE envelope (no url), also accept an explicit `media_url` field from the client payload. Compute before the create_message call:

```python
                _media_url = None
                if msg_type in ("image", "video"):
                    _explicit = data.get("media_url")
                    if isinstance(_explicit, str) and _explicit:
                        _media_url = _explicit
                    else:
                        try:
                            _media_url = json.loads(content).get("url") or None
                        except Exception:
                            _media_url = None
```

and pass `media_url=_media_url` to `create_message(...)`.

---

## FILE: `server/chat/chat.html`

**Change 20 (message_acked uses room_id).** Find the `case 'message_acked':` handler (~line 1313). It currently looks up `messagesByRoom[currentRoom]`. Change it to use the acked room:

```js
  case 'message_acked': {
    const _ackRoom = data.room_id || currentRoom;
    const msgs = messagesByRoom[_ackRoom] || [];
```

and use `_ackRoom` consistently within that case where `currentRoom` was used for the lookup. Do not change unrelated `currentRoom` usages.

**Change 21 (send media_url for E2EE cleanup).** Find where image/video messages are sent over the WS (the `sendChatMessage` / media-send path that builds the `{event:'send_message', ...}` payload). For image/video sends, include a top-level plaintext `media_url` field carrying the uploaded file URL (the same URL that gets encrypted into the E2EE envelope), so the server can garbage-collect the file. Read the send path first; add `media_url: <uploadedUrl>` to the payload object for image/video types only. The uploaded URL is available at send time (it is what the client encrypts). If the URL is not readily in scope at the payload-construction point without significant plumbing, note this in your report and skip ONLY this change (the server-side fallback in Change 16/19 still cleans non-E2EE media; E2EE media cleanup depends on this field).

**Change 22 (chat push resync on load).** Find `_repairPushSubscription` (~line 3877). It only re-subscribes when a subscription is entirely absent, so it never repairs a subscription whose endpoint was rotated by the lineup enable-flow. Add an always-resync step: when `push_enabled === '1'` and a subscription EXISTS, re-POST the current endpoint to the chat backend (`/chat/api/push/subscribe`) so the chat server record is refreshed on every load (mirroring what the lineup page already does). Read the function and the existing `_subscribePush` POST shape (~line 3859) to match the request body exactly. Implement as: if permission granted and `storageGet('push_enabled')==='1'`, get the current subscription; if present, POST its `endpoint`+`keys` to `/chat/api/push/subscribe` (credentials included); keep the existing "subscribe if missing" path. Guard so it runs at most once per load.

**Change 23 (do not force-unsubscribe when enabling chat push).** In `_subscribePush` (~line 3854), the sequence `getSubscription()` → `if (sub) await sub.unsubscribe()` → `subscribe()` rotates the shared origin-level push endpoint, silently breaking the lineup subscription (both surfaces share one root-scope SW subscription). Change it to reuse an existing subscription instead of destroying it:

```js
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
    }
```

Remove the `if (sub) await sub.unsubscribe();` line. Re-subscribing is unnecessary when a subscription with the same VAPID key already exists; reusing it preserves the endpoint for both surfaces. Keep the subsequent POST-to-backend logic unchanged.

---

## FILE: `scraper/render.py` (lineup frontend, generated into lineup.html)

**Change 24 (do not force-unsubscribe when enabling lineup push).** In `enableNotifications` (~line 2191), same issue as Change 23 on the lineup side. Change:

```js
        var oldSub = await reg.pushManager.getSubscription();
        if (oldSub) await oldSub.unsubscribe();
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
```

to:

```js
        var sub = await reg.pushManager.getSubscription();
        if (!sub) {
          sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes });
        }
```

Keep everything after (`await ensureSession(); ... fetch(... /push/subscribe ...)`) unchanged.

---

## FILE: `server/api.py`

**Change 25 (remove dead `tag` field from lineup push payload).** ~line 348: the payload sets `"tag": f"stc-{slot_id}",` but `sw.js` never reads `data.tag` (it derives the tag from `push_id`/random). Remove that single `"tag": ...,` line from the payload dict to avoid misleading dead code. Do not change anything else in the payload.

**Change 26 (lineup scheduler per-session exception isolation).** In the scheduler loop `for session_id, slot_id in to_send:` (~line 334-392), wrap the per-session body in a try/except so one malformed session cannot abort the whole batch. Read the loop, then wrap its body:

```python
        for session_id, slot_id in to_send:
            try:
                <existing body>
            except Exception:
                logger.exception("push: failed for session %s slot %s", session_id, slot_id)
                continue
```

Preserve the existing inner `try/except WebPushException` for per-endpoint handling; this outer wrapper is additional.

**Change 27 (sync PIN strict rate bucket).** ~line 38-43 `RATE_LIMITS` and the `exchange_sync_pin` handler (~line 509) which calls `_check_rate(ip, "load")`. Add a dedicated strict bucket and use it for the PIN exchange:
- Add to `RATE_LIMITS`: `"sync_pin": (12, 3600),` (12 attempts/hour/IP).
- In `exchange_sync_pin`, change `_check_rate(ip, "load")` to `_check_rate(ip, "sync_pin")`. Read the function to confirm the exact call and variable names before editing.

---

## FILE: `server/docker-compose.yml`

**Change 28 (log rotation).** Add a logging config to the service so container logs don't fill the VPS disk. Read the file, and under the service definition (same indent level as `restart:`/`volumes:`), add:

```yaml
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

---

## Final report format

When done, produce a report listing each change number with one of: `APPLIED` (with file:line), `SKIPPED` (with reason — anchor missing / already present / could not thread through as noted), or `PARTIAL` (what you did vs skipped). For any change where you deviated from the literal spec (e.g. adapted a variable name or a function signature you read), state the deviation and why. Do not claim any change was tested.
