# Pre-Deploy Fix Spec (Round 2)

You are the fix-implementation agent for the Stone Techno Companion project. Read CLAUDE.md first.
Apply EVERY fix below exactly as specified. Do not add scope. Do not refactor unrelated code.
Preserve existing style: no emojis anywhere; every user/automated action must keep its `dbg()`/`logger`
logging; SQL stays parameterized. After each change, re-read the surrounding function to confirm it
still parses and the change is internally consistent. You have no Bash — you cannot run tests; the
orchestrator runs the full pytest suite + browser checks afterward. Do not claim verification you
did not perform.

When a fix instruction and the actual code disagree (line numbers may have drifted), TRUST THE CODE:
locate the described construct by its logic, not the line number, and adapt. If a described construct
genuinely does not exist, skip it and note that in your final report rather than inventing a change.

---

## R2-1 [CRITICAL] Enforce ban/mute on all rooms + reject banned users at WS connect

Problem: `moderate_message` (server/chat_moderation.py) holds the `is_banned`/`is_muted` checks, but
`_moderate_and_broadcast` only calls it `if is_moderated:`. DM rooms are `is_moderated=False`, so a
banned/muted user with an open socket sends DMs freely. Connect-time only validates the session token.

Fix A — connect-time ban check. In `handle_chat_ws` (server/chat_ws.py), right after `user` is resolved
and before/around `await manager.connect(...)` (near line 1159), add a ban check and close if banned:
```python
from chat_db import is_banned  # ensure imported at top if not already
if is_banned(db, user["provider"], user["provider_id"],
             user["device_fingerprint"] if "device_fingerprint" in user.keys() else None):
    dbg = None  # (do not add dbg in python; use logger)
    logger.info("[WS] rejecting banned user %s at connect", user_id)
    try:
        await ws.close(code=4003, reason="Banned")
    except Exception:
        pass
    return
```
(Use `logger.info`, not `dbg` — that is a JS helper. Place the check before `manager.connect`.)

Fix B — enforce ban/mute for every send regardless of moderation. In `_moderate_and_broadcast`
(server/chat_ws.py ~896), change the branch so the ban/mute check ALWAYS runs even when `is_moderated`
is False. Simplest correct approach: when `is_moderated` is False, still run the ban/mute portion.
Replace:
```python
        if is_moderated:
            mod_result = await moderate_message(db, user_id, text, image_url)
            logger.info("[MOD] result: %s", mod_result)
        else:
            mod_result = {"allowed": True}
```
with a version that consults ban/mute in the unmoderated branch too. Add a small helper in
server/chat_moderation.py:
```python
async def check_ban_mute(db, user_id: str) -> dict:
    """Ban/mute enforcement only (no content scan). Used for unmoderated rooms
    (DMs) so a banned/muted user cannot keep sending over an open socket."""
    from chat_db import is_muted, is_banned, get_user
    user = get_user(db, user_id)
    if user and is_banned(db, user["provider"], user["provider_id"],
                          user["device_fingerprint"] if "device_fingerprint" in user.keys() else None):
        return {"allowed": False, "reason": "You have been banned.", "action": "ban"}
    if is_muted(db, user_id):
        return {"allowed": False, "reason": "You are temporarily muted.", "action": "mute"}
    return {"allowed": True}
```
Note the action string is `"mute"` (matching the `_moderate_and_broadcast` handler's
`elif mod_result["action"] == "mute"` check), NOT `"muted"`. Then in `_moderate_and_broadcast`:
```python
        if is_moderated:
            mod_result = await moderate_message(db, user_id, text, image_url)
            logger.info("[MOD] result: %s", mod_result)
        else:
            mod_result = await check_ban_mute(db, user_id)
```
Import `check_ban_mute` alongside the existing `moderate_message` import in chat_ws.py.
This routes DM sends by a banned/muted user through the existing rejection path (message deleted,
`message_removed` sent, ban closes the socket). Confirm the rejection block does not assume
`is_moderated` anywhere.

## R2-2 [HIGH] Don't broadcast a message deleted/expired during moderation

In `_moderate_and_broadcast`, the "moderation passed" path does
`UPDATE messages SET moderation_status='approved' WHERE id=?` then unconditionally broadcasts.
Guard on whether the row still exists. Replace the UPDATE with an existence-aware update:
```python
        cur = db.execute(
            "UPDATE messages SET moderation_status = 'approved' WHERE id = ?",
            (msg["id"],),
        )
        db.commit()
        if cur.rowcount == 0:
            # Message was deleted (user delete) or purged (TTL) while moderation
            # was in flight. Do not resurrect it: skip broadcast, link preview,
            # badge fan-out and push.
            logger.info("[MOD] message %s gone before approve; skipping broadcast", msg["id"])
            return
```
(If `moderation_status` starts 'approved' for unmoderated rooms, this UPDATE still matches the row when
it exists, so `rowcount` is 1 for a live message and 0 only when the row is truly gone. Verify the
column default so a live unmoderated message is not falsely skipped — if the UPDATE is a no-op because
status is already 'approved', SQLite still reports rowcount=1 for a matched row under the default
behavior; if you find rowcount would be 0 for an unchanged row, switch the guard to an explicit
`SELECT 1 FROM messages WHERE id=?` existence check instead.)

Prefer the explicit existence check to avoid any rowcount ambiguity:
```python
        db.execute("UPDATE messages SET moderation_status='approved' WHERE id=?", (msg["id"],))
        db.commit()
        still_exists = db.execute("SELECT 1 FROM messages WHERE id=?", (msg["id"],)).fetchone()
        if not still_exists:
            logger.info("[MOD] message %s gone before approve; skipping broadcast", msg["id"])
            return
```
Use this explicit-SELECT form.

## R2-3 [HIGH] Auto-ban must close ALL of the user's connections

In `_moderate_and_broadcast`, the `mod_result["action"] == "ban"` branch does `await ws.close(...)` for
the single socket. Replace with closing every connection for the user, mirroring admin_ban
(chat_api.py:1906-1910):
```python
            if mod_result["action"] == "ban":
                await mgr.send_to_user(user_id, {"event": "banned", "reason": mod_result["reason"]})
                for _cid, _ws in list(mgr.user_conns.get(user_id, {}).items()):
                    try:
                        await _ws.close(code=4003, reason="Banned")
                    except Exception:
                        pass
```
Confirm the manager reference in this function is `mgr` (it is used as `mgr.send_to_user`).

## R2-4 [HIGH] Logout must close the user's live WS connections

In `auth_logout` (server/chat_api.py ~555), after deleting the session and before returning, look up
the user by the token FIRST (before deletion) and close their sockets. Restructure:
```python
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
            for conn_id, ws in list(manager.user_conns.get(user["id"], {}).items()):
                try:
                    await ws.close(code=4001, reason="Logged out")
                except Exception:
                    pass
    response.delete_cookie("chat_session")
    return {"ok": True}
```
Use whatever `manager`/`get_user_by_token` symbols the module already imports (match auth_delete_account
at chat_api.py:569-581 for the exact pattern and symbol names).

## R2-5 [HIGH] Redact the lineup sync WS session token from access logs

In `_RedactTokensFilter._PATTERNS` (server/api.py ~59), add a pattern for `/ws/{code}` that does NOT
clobber the `/ws/chat/<token>` route (which is already handled). Add as the FIRST pattern:
```python
        re.compile(r"(/ws/)(?!chat/)[^/\s?]+"),
```
Keep the existing `/ws/chat/` and `/chat/v/` and query-param patterns. Verify order does not double
-redact.

## R2-6 [HIGH] Exclude pending messages from unread counts and push previews

(a) `get_unread_counts` (server/chat_db.py ~765): the `LEFT JOIN messages m` has conditions on
`created_at`, `expires_at`, `user_id`. Add `AND m.moderation_status != 'pending'` to that join's ON
clause so pending messages are not counted.

(b) `_do_send_push` (server/chat_ws.py ~451-471): both ad-hoc queries (the `SELECT id ...` for
first_msg_id and the `SELECT m.content, u.display_name ...` for the preview) must add
`AND moderation_status != 'pending'` (alias `m.` where the query uses `m`). This stops a pending or
soon-to-be-rejected message from appearing as the push body.

## R2-7 [HIGH] Rate-limit /chat/api/swlog

In `chat_swlog` (server/chat_api.py ~1671), add a light per-IP rate limit and keep the 500-char cap.
Reuse the existing in-memory rate-limit pattern (see `_email_rate`/`_auth_rate`). Add a module-level
`_swlog_rate: dict = {}` and gate:
```python
@router.post("/swlog", status_code=204)
async def chat_swlog(request: Request):
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
```
Match `time`/`Response` imports already present in the file.

## R2-8 [HIGH] .gitignore the real prod upload path + back it up

(a) In `.gitignore`, add `server/chat-uploads/` (the docker-compose bind mount). Place it near the
existing `server/chat/uploads/` line.

(b) In deploy.sh Step 1 (backup), after the `server/data/` rsync, add a best-effort rsync of the uploads
directory so a VPS disk failure does not lose a day of user media (TTL is now 24h). Add:
```bash
    # Best-effort: back up live user uploads too (24h TTL of media)
    rsync -az --progress "$VPS:$VPS_DIR/server/chat-uploads/" \
        "$LOCAL_BACKUPS/$TIMESTAMP/chat-uploads/" 2>/dev/null || \
        echo "  (no chat-uploads dir on VPS yet, skipping)"
```
Place inside the `if [ "$DRY_RUN" = false ]` block, after the integrity-check loop. Do NOT run the
integrity check on media files.

## R2-9 [HIGH] Raise the auth IP rate limit for shared-NAT venues

Find `_check_auth_rate` in server/chat_api.py (the function applied to /auth/google, /auth/google/code,
/verify — 20 requests / 5 min per IP). Raise the ceiling to tolerate a festival's shared public IP.
Change the max from 20 to 120 (keep the 5-min / 300s window). Magic-link tokens are 128-bit and OAuth
is validated Google-side, so brute-force is not the threat this limiter defends; venue lockout is the
real risk. Add a code comment stating why the limit is high.

## R2-10 [MED] admin.html: show "[deleted user]" instead of literal "null"

In server/chat/admin.html, wherever `reporter_name`/`reported_name` (and any name that can now be NULL
from the LEFT JOINs) are rendered via `esc(...)`, coalesce null/empty to a placeholder. Add a helper or
inline: `esc(r.reporter_name || '[deleted user]')` at each such call site in the Reports and Users
tabs. Find every `esc(` call that renders a report's reporter/reported name.

## R2-11 [MED] Parse push preview content instead of showing raw JSON

In `_do_send_push` (server/chat_ws.py), the preview fallback sets
`text_preview = msg_row["content"] or ""` for non-DM count==1. The stored `content` is a JSON envelope.
Replace with type-aware parsing mirroring the parsing used on the live path (search for where `content`
is parsed into "Sent a photo"/text elsewhere, ~chat_ws.py:1030-1041). Extract a small helper
`_preview_from_content(content: str) -> str` that returns the text for a text message, or "Sent a
photo"/"Sent a video"/"Shared a location"/"Sent a meetup" for the respective types, and "" for E2EE
envelopes. Use it here. Reuse existing constants if present.

## R2-12 [MED] Re-fetch identity on join_room and meetup_invite

The send path was fixed to re-fetch `display_name`/`username`/`color_index`/`avatar_url` from the DB
(chat_ws.py:976-988). Apply the same to `join_room` (~1235-1263) and the `meetup_invite` broadcast
(~1636-1654): before seeding `room.user_info`/broadcasting, `sender = get_user(db, user_id)` and use its
fields (guarded by `sender.keys()` as the send path does). Do not change the handshake locals; just use
fresh values for the room-seed/broadcast.

## R2-13 [MED] Add missing hot-path indexes

In server/chat_db.py schema creation (the CREATE INDEX block), add:
```sql
CREATE INDEX IF NOT EXISTS idx_dm_participants_user ON dm_participants(user_id);
CREATE INDEX IF NOT EXISTS idx_email_tokens_expires ON email_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_strikes_expires ON strikes(expires_at);
```
Place alongside the other `CREATE INDEX IF NOT EXISTS` statements so they run on every startup
(idempotent). Confirm the table/column names match the schema.

## R2-14 [MED] deploy.sh robustness

(a) The required-var loop (`for var in $PROD_VARS`) uses `val=$(grep ... | cut ...)`. Add `|| true` so a
missing var does not trip `set -e` before the intended "Missing values" error prints — match the
OPTIONAL_VARS loop which already has `|| true`.
(b) The final backup-prune `ls -dt ... | tail -n +6 | xargs rm -rf`: guard the empty case. Replace with:
```bash
    to_prune=$(ssh "$VPS" "ls -dt $VPS_DIR/server/data.bak.* 2>/dev/null | tail -n +6" || true)
    if [ -n "$to_prune" ]; then
        echo "$to_prune" | ssh "$VPS" "xargs rm -rf"
    fi
```
Adapt to the actual prune target (local vs VPS path) as written in the current script — the key fix is:
never invoke `rm` with no operand. Match the existing remote/local structure.

## R2-15 [MED] Link-preview: fall back across all safe resolved addresses

In the SSRF-safe fetch (`_resolve_safe_ip`/`_pinned_preview_get`, chat_ws.py ~170-229), instead of
connecting only to `safe[0]`, iterate over all validated safe addresses and use the first that connects;
only fail if all fail. Keep every address SSRF-validated (private/loopback/link-local rejected) before
any connection attempt. Do not weaken the validation — only add the fallback loop.

## R2-16 [MED] Meetup online-list + typed join

(a) `GET /chat/api/rooms/{room_id}/online` (chat_api.py ~959): add a meetup membership check mirroring
the DM check — for `room["type"] == "meetup"`, verify the caller is in `meetup_attendees` (match how
`room_messages` at ~886-904 gates meetup).
(b) `POST /chat/api/rooms/{room_id}/join` (~856): reject (or no-op with 403) when `room["type"]` is
`dm` or `meetup` — only `group`/standard rooms are joinable this way.

## R2-17 [MED] Don't leak ban reason on /login

In `auth_email_start`/`/login` (chat_api.py ~476), when `is_banned` is true return a generic message
(e.g. `HTTPException(403, "This account cannot sign in.")`) WITHOUT the admin-authored ban reason and
without confirming the address is registered. Keep the ban enforcement; just drop the reason text and
any registration-status signal.

## R2-18 [MED] URL scheme allowlist in render.py link builders

Add a helper in scraper/render.py:
```python
def _safe_href(href: str) -> str:
    h = (href or "").strip()
    return h if h.lower().startswith(("http://", "https://", "mailto:")) else "#"
```
Apply it in `_link()` (~920) and in the client-side `_popupLink` generator (~2425): wrap the href with
`_safe_href(...)` before `esc(...)`. For the client-JS string, add an equivalent guard in the emitted JS
(a small `function _safeHref(u){return /^https?:\/\//i.test(u||'')?u:'#';}` used where popup links are
built). Reject non-http(s) at the render layer regardless of upstream hygiene.

## R2-19 [MED] Flush pending mark-read on room switch

In server/chat/chat.html `openRoom` (near where a room is opened, ~1978), call `_flushMarkRead()` at the
very start (before switching `currentRoom`), so a scheduled-but-unsent mark-read for the previous room is
sent instead of dropped by the room-switch reset. `_flushMarkRead` already exists (used by
visibilitychange). Add a `dbg('[READ] flushing pending mark-read before room switch')` line.

## R2-20 [MED] Decrypt E2EE content in the REST fallback path

In `openRoom`'s REST fallback (chat.html ~1978-1989), after fetching messages via
`api('/rooms/'+roomId+'/messages')` and before `renderMessages()`, run the same decryption the WS
`room_history` path uses — call `await _decryptMessageContent(...)` over the fetched messages (match the
signature/loop used in the `room_history` handler). Add a `dbg()` line. This prevents blank E2EE bubbles
on a slow/failed WS handshake.

## R2-21 [LOW] Hold task references + sweep stuck-pending messages

(a) Add a module-level `_bg_tasks: set = set()` in server/chat_ws.py. For each fire-and-forget
`asyncio.create_task(...)` (moderation task ~1566, push tasks ~1086, flush ~389), do:
```python
_t = asyncio.create_task(...)
_bg_tasks.add(_t)
_t.add_done_callback(_bg_tasks.discard)
```
(b) In the purge loop (chat_ws.py ~1976-2007), add a sweep: any message with
`moderation_status='pending'` older than 3 minutes is stuck (its task died); delete it and send
`message_removed` to the sender. Add a `chat_db` helper `sweep_stuck_pending(db, older_than_iso)` that
returns `[(id, room_id, user_id), ...]` deleted, and the purge loop notifies each sender. Keep it cheap
(index-free scan is fine; pending rows are few).

## R2-22 [LOW] Cache-Control on shared.css / shared.js

In server/api.py, the routes serving `/shared.css` and `/shared.js` (and while there, `/manifest.json`)
should set `Cache-Control: no-cache` (match `/sw.js`). This ensures a content deploy that ships new
shared bundles is picked up. Add the header to those FileResponse/Response returns.

## R2-23 [LOW] First-run notif prompt: don't stack over the settings modal

In `_maybePromptNotifications` (chat.html), before showing the prompt, bail if a Notifications modal or
another modal-overlay is already open: `if (document.querySelector('.modal-overlay.open')) { return; }`
(re-arm so it can show later — do NOT set `_notifPromptChecked=true` in that early return; just return so
the next route tick can retry). Confirm this does not permanently suppress the prompt.

## R2-24 [LOW] Add push_id to the lineup push payload

In the lineup scheduler payload (server/api.py ~371-377), add `"push_id": secrets.token_hex(8)` to the
JSON payload dict, matching chat_ws.py. `secrets` is already imported in api.py (confirm; import if not).

## R2-25 [LOW] Minor endpoint hardening

(a) `/chat/api/check-name` (chat_api.py ~676): require a session cookie like `/check-username` does
(add the same `_get_user_from_cookie(request)` gate).
(b) `_upload_rate` dict (~89): add the same self-prune the other rate dicts have (`if len > 1000: clear`).
(c) `/chat/api/push/ack` (~1681): add a light per-IP rate limit (reuse the swlog pattern, 60/min).

## R2-U1 [HIGH — from UX agent] First-run notif prompt interrupts the user on entry

The multi-browser UX pass found `_maybePromptNotifications` firing on a 1.5s timer after `route()`
completes, popping a full-screen `.modal-overlay` that intercepts the user's first clicks in the room
across all Chromium-family browsers. Profile-incomplete users are safe (they `return` before the timer),
but a fully-onboarded user is interrupted immediately on entering.

Fix: stop auto-firing on a timer. Instead, arm the prompt and show it after the user's FIRST sent
message (a natural engagement moment, and better product design than an unprompted popup):
1. Remove the `setTimeout(_maybePromptNotifications, 1500);` call in `route()`.
2. In `sendChatMessage` (the function that sends a user's message over WS), after a successful send,
   call `_maybePromptNotifications()` ONCE — guard with the existing `_notifPromptChecked` flag plus a
   new `storageGet('sent_first_msg')` gate so it only triggers from the first message ever sent, e.g.:
   ```js
   if (!_notifPromptChecked && storageGet('notif_prompt_done') !== '1') {
     storageSet('sent_first_msg', '1');
     setTimeout(_maybePromptNotifications, 800);
   }
   ```
   (The 800ms lets the sent bubble settle first.) Keep all existing skip conditions inside
   `_maybePromptNotifications` intact.
3. Combine with R2-23: `_maybePromptNotifications` must also bail (without setting `_notifPromptChecked`)
   if `document.querySelector('.modal-overlay.open')` exists, so it never stacks or fires mid-modal.
Add `dbg()` lines. Confirm `sendChatMessage` is the correct send entry point (grep for the send button
handler / `wsSend('send_message'`).

## R2-U2 [LOW — from UX agent] `/api/me` 401 logged via console.error on lineup

On the lineup page, a chat-session-only user with no lineup cookie hits `/api/me`, gets a correctly
-handled 401, but it is routed through `console.error`, polluting error monitoring. In scraper/render.py
find the `/api/me` fetch; when the response is a 401/expected-unauthenticated case, log via
`console.debug`/`dbg` (or simply do not `console.error`) rather than surfacing it as an error. Do not
change the functional handling (the `if (res.ok)` guard stays).

---

## Out of scope (do NOT touch) — flagged for human, see TRIAGE2.md FLAG table
Word-filter spacing/substring bypass; bio sanitizer allowlist migration (new dependency);
streaming upload size enforcement; zombie-connection / DM room_meta eviction; reconnect optimistic-merge;
container USER directive; abandoned-upload GC table. Leave these entirely alone.

## Final report format
List each R2-ID with: DONE / SKIPPED (+reason) / DEVIATED (+what you did and why). Note any place the
code disagreed with the spec. Do not summarize the whole project — just the per-fix status.
