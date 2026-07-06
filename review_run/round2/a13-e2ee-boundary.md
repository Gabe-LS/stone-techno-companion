## E2EE Trust-Boundary Audit — DM Content Leak Review

Read `docs/e2ee-multidevice.md`, `server/chat_ws.py`, `server/chat_api.py` (full, both halves), `server/chat_db.py`, `server/api.py`, and `server/chat/admin.html` (to verify how report data server-side actually renders).

### Code paths examined

- **[PASS]** `chat_ws.py:78-79,914-915` — `_dm_preview()` always returns the generic `"Sent you a message"` for any DM (text/image/video/location/meetup), applied at `_moderate_and_broadcast:914-915` regardless of `msg_type`. Push body (`_do_send_push:417-420`) and `badge_update` preview (`chat_ws.py:937`) both consume this generic string for DMs.
- **[PASS]** `chat_ws.py:1399-1404` — `text_for_moderation` is derived via `json.loads(content).get("text", "")`; an E2EE envelope has no `"text"` key, so this evaluates to `""` for E2EE messages — moderation/log never sees plaintext even before the `is_moderated` gate is checked.
- **[PASS]** `chat_ws.py:788,793` — `logger.info("[MOD] len=%d ...")` and `logger.info("[MOD] result: %s", mod_result)` log only length/result dict, never `text`/`content`.
- **[PASS]** `chat_ws.py:791,1440` + `chat_db.py:1067,312` — DMs are created with `is_moderated=0` (`find_or_create_dm`) and migration line 312 (`UPDATE rooms SET is_moderated = 0 WHERE type = 'dm'`) forces it retroactively; `_moderate_and_broadcast` only calls `moderate_message()` (which invokes OpenAI/GPT with `text`/`image_url`) when `is_moderated` is true. No conditional path re-enables moderation for DM rooms — confirmed no bypass.
- **[PASS]** `chat_ws.py:708-728, 745-758` — `_build_reply_snippet` / `_format_message_for_history` both check `_is_e2ee_content(...)` and force `reply_text = ""` for encrypted originals, so reply quotes broadcast to peers never carry plaintext.
- **[PASS]** `chat_ws.py:880-899` — link-preview fetch is explicitly skipped via `not _is_e2ee_content(content)`; `text` is already `""` for E2EE messages regardless (double-gated).
- **[PASS]** `chat_ws.py:862-874` — WS `event_data` broadcast to the room only relays `content` (the opaque envelope) and the blanked reply snippet; no content-derived field (preview, extracted text, media_url) is added.
- **[PASS]** `chat_ws.py:1361-1370` + `chat_db.py:120-131` — `media_url` (plaintext upload filename) is a top-level WS field separate from the encrypted `content`, stored in `messages.media_url` for server-side cleanup only; never included in `event_data` (`862-874`) or `_format_message_for_history` (`731-767`), so peers only learn the file location by decrypting the envelope client-side.
- **[PASS]** `chat_ws.py:1405-1421` — `_image_to_data_uri`/`_video_mod_frames` are computed unconditionally before the moderation gate, but for E2EE envelopes `json.loads(content).get("url")` is `None` (the URL lives inside `ct`), so no plaintext image/video bytes are ever loaded for encrypted media, and the data is unused anyway since `is_moderated=False` short-circuits before `moderate_message()`.
- **[PASS]** `chat_ws.py:362-408` — `_do_send_push`'s deferred/silent-push fallback path (`_flush_push_later`) reads `m.content` directly from SQL, but only uses it as `text_preview` when `room_type != "dm"` (`line 406`); since E2EE is only permitted in DM rooms (`chat_ws.py:1226-1237`), this branch can never surface E2EE plaintext.
- **[PASS]** `chat_api.py:1010-1049` (`/chat/api/dms`) and `chat_api.py:787-819` (`/rooms/{id}/messages`) — return the raw envelope in `content` to authenticated DM participants only (expected, client decrypts); no server-side parsing of `ct`.
- **[PASS]** `chat_ws.py:1714-1770` (`report_message`) + `chat_db.py:1111-1136` (`create_report`) — for E2EE originals, `text` comes only from client-supplied `client_content`, `unverified` is set to `1`, and the `reports` table stores it. `chat_api.py:1698-1721` (`GET /admin/reports`) selects and returns `unverified`, and `admin.html:296` renders `<div class="unverified-banner">Content provided by reporter (unverified...)</div>` when set. This path (the pending-reports tab) is correctly marked.
- **[PASS]** No message-search/index feature exists anywhere in `chat_db.py`/`chat_api.py` — nothing to leak DM content into.
- **[PASS]** No data-export/backup endpoint exists in `chat_api.py`; `deploy.sh`-style backups operate on the raw (still-encrypted) `chat.db` file, not a decrypted export.
- **[PASS]** `chat_api.py:1545-1552` (`/chat/api/swlog`) — verified via sub-agent: all 4 client call sites (`sw.js:57,86`, `chat.html:4548,4586`) send only push-navigation metadata (URLs like `/chat/msg/{id}`, window/visibility state) — never message content or envelopes.
- **[PASS]** `server/api.py` — lineup-favorites server, does not touch `chat.db`/message content at all; its own push scheduler payload (`api.py:346-352`) only carries artist/floor/time strings, unrelated to chat DMs.

### New leak found

**[HIGH]** `server/chat_db.py:1405-1411` (`get_user_admin_detail`) — the SQL query backing `GET /chat/api/admin/users/{user_id}` selects `r.id, u.display_name AS reporter_name, r.reason, r.message_snapshot, r.status, r.created_at` from `reports` but **omits `r.unverified`**. The returned `reports_against` list is rendered in `server/chat/admin.html:491-494` as:
```js
${u.reports_against.length ? '<h3>Reports (' + u.reports_against.length + ')</h3>' + u.reports_against.map(r =>
  '<div class="detail-sub-row">' + esc(r.reason) + ': ' + esc(r.message_snapshot) + ...
```
with no `unverified` check and no `.unverified-banner` — unlike the pending-Reports tab (`admin.html:296`), which correctly reads `r.unverified` (present in the `/admin/reports` payload) and shows `"Content provided by reporter (unverified - server cannot read encrypted messages)"`.

Net effect: any admin who expands a user's row in the **Users** tab and views their report history sees reporter-supplied E2EE-DM plaintext (`message_snapshot`) presented identically to a verified, server-observed group-room message — including reports already `actioned`/`dismissed` that have scrolled off the pending-reports tab. This defeats the documented mitigation ("flagged `unverified`... shown with a warning banner in the admin UI") for exactly the scenario it exists to cover, and risks an admin banning/striking a user on the basis of unverifiable, reporter-fabricatable text without realizing it was never server-verified.

**Fix**: add `r.unverified` to the SELECT in `get_user_admin_detail` (`chat_db.py:1405-1411`), include it in the returned dict (`chat_db.py:1450`, currently `"reports_against": [dict(r) for r in reports]` — this actually would carry it through automatically once selected, since `dict(r)` copies all row columns), and update `admin.html:491-494` to apply the same `unverified` conditional/banner used at `admin.html:296`.
