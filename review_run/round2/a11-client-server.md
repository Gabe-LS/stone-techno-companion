## Trust Boundary Review — Stone Techno Companion Chat (chat.html ↔ chat_ws.py / chat_api.py)

Reviewed every WS event handler in `handle_chat_ws` (chat_ws.py:1086-1804) and every REST route in chat_api.py against what chat.html actually sends. Cross-checked against chat_db.py for DB-layer validation (allowlists, FK enforcement, length limits). Findings below are new — not duplicating the round-1 list (E2EE media_url validation gap, word-filter substring matching, missing device_fingerprint, upload buffering) except where noted as a broader variant of a round-1 finding.

### Input path table

| Path | Fields (client→server) | Validation present? | Finding |
|---|---|---|---|
| WS `send_message` | `room_id`, `type`, `content`, `temp_id`, `reply_to_id`, `media_url` | type/length/room-state checks yes; content-schema no | #4, #5 |
| WS `typing` | `room_id`, `active` | none (no membership check) | #6 |
| WS `mark_read` | `room_id`, `timestamp` | DB-layer UPDATE-only (verified no phantom membership created — safe) | none |
| WS `add_reaction`/`remove_reaction` | `message_id`, `emoji` | add: emoji allowlisted + dm/meetup membership check; remove: emoji not allowlisted but harmless (FK-scoped delete) | none new |
| WS `create_meetup` | `stage_id`, `title`, `meetup_time`, `lat`, `lng`, `label`, `note` | rate-limited, length-truncated, ISO date validated | none new (see REST asymmetry, #3) |
| WS `open_dm`/`block_user`/`unblock_user` | `target_user_id` | open_dm: blocked-check yes; block/unblock: no existence check | #9 |
| WS `report_message` | `message_id`, `reason`, `message_content` | no rate limit, no length cap | #2 |
| WS `delete_message` | `message_id` | ownership + 120s window enforced | none new (round-1 media_url issue applies here) |
| REST `POST /push/subscribe` | `endpoint`, `keys.p256dh`, `keys.auth` | presence-only, no URL/host validation | **#1 CRITICAL** |
| REST `POST /meetups` | `title`, `meetup_time`, `stage_id`, `lat/lng/label/note` | no rate limit (WS path has one) | #3 |
| REST `POST /swlog` | arbitrary JSON | none — no auth, no rate limit | #7 |
| REST `POST /push/ack` | `endpoint`, `action` | no auth, no rate limit | #8 |
| REST `PUT /profile`, `/keys`, `/upload/*` | various | generally solid (JWK validated, avatar moderated, username/displayname regex+moderation) | none new |
| REST `PATCH /admin/rooms/{id}` | arbitrary JSON body → `update_room(**body)` | allowlisted columns in chat_db.py — safe despite `**kwargs` | none |

### Issues

**[CRITICAL] server/chat_api.py:1494-1508, server/chat_ws.py:463-479 — SSRF via push subscription endpoint.** `chat_push_subscribe` stores whatever string the client sends as `endpoint` with zero validation (no scheme check, no push-service host allowlist). `_do_send_push` later passes `sub["endpoint"]` straight into `pywebpush.webpush(subscription_info={"endpoint": ...})`, which performs a signed outbound HTTP POST to that URL. Compare this to `_is_safe_preview_url` (chat_ws.py:141-167), which explicitly resolves DNS and rejects loopback/private/link-local/reserved addresses before fetching link previews — the exact same class of client-supplied-URL-triggers-server-fetch problem, but with no protection applied here. An authenticated user can register `endpoint` = an internal service or cloud-metadata URL, then make themselves push-eligible (e.g. `POST /push/idle` + any pending unread message, or DM themselves from a second account) to make the server issue an outbound request to that URL.

**[HIGH] server/chat_ws.py:1714-1770 — `report_message` has no rate limit and no size cap, and reports never expire while pending.** Unlike `send_message`/`create_meetup`, this branch never calls `manager.check_rate_limit`. `reason` and `message_content` (used verbatim in the stored `snapshot`) have no length truncation. In chat_db.py, the `reports` table has no length CHECK constraints, and `purge_old_reports` only deletes rows with `status IN ('actioned','dismissed')` — a `status='pending'` report is retained indefinitely. Net effect: any logged-in user can flood the `reports` table with unbounded-size rows forever, with no cleanup unless an admin manually resolves each one.

**[HIGH] server/chat_api.py:954-982 vs server/chat_ws.py:1459-1461 — meetup creation rate limit is bypassable via REST.** The WS `create_meetup` handler checks `manager.check_rate_limit(user_id)` before calling `create_meetup`. The REST `POST /chat/api/meetups` endpoint calls the identical `create_meetup` DB function with no rate-limit check at all. A client using the REST path (trivial — it's a documented public endpoint) bypasses the anti-spam protection entirely.

**[HIGH] server/chat_ws.py:1262-1276 vs 1361-1370 — media_url validation bypass is not E2EE-specific; it applies to every message.** The regex check (`_UPLOAD_URL_RE.match`) only validates `content`'s embedded `url` and is skipped when `is_e2ee_msg`. But the value actually persisted via `create_message(..., media_url=_media_url)` comes from a second, later block (1361-1370) that unconditionally prefers the client's separate top-level `media_url` field (`data.get("media_url")`) over the validated `content.url` — with no `is_e2ee_msg` guard at all. A plain (non-E2EE, moderated group room) image/video message can therefore carry a `content.url` that passes the regex (so the send isn't rejected) while the stored/`delete_message`-trusted `media_url` (chat_ws.py:1683-1702) is a completely different, unchecked filename — letting one user delete another user's uploaded media file (same `chat/uploads/` directory) by referencing a filename observed elsewhere, not limited to E2EE DMs as the round-1 finding implied.

**[MEDIUM] server/chat_ws.py:792, 872, 1399-1404 — moderation only inspects the `text` key; the rest of `content` passes through unchecked.** `moderate_message(db, user_id, text, image_url)` is called with `text` extracted solely from `json.loads(content).get("text", "")`. The full `content` string — including any extra attacker-added JSON keys — is stored and broadcast verbatim (`event_data["content"] = content`, line 872) without ever being scanned by the word filter or OpenAI moderation. `{"text": "", "note": "<banned content>"}` sails through both moderation layers untouched. The stock client only renders `c.text`, but the raw content is still delivered to every room member's WS connection and stored in the DB.

**[MEDIUM] server/chat_ws.py:1444-1457 — `typing` has no room-membership check.** Every other room-scoped event (send_message, add_reaction, mark_read, report_message) verifies `dm_participants`/`meetup_attendees` membership before acting on DM/meetup rooms. `typing` skips this entirely — any authenticated user who knows/obtains a DM or meetup `room_id` can broadcast a spoofed typing indicator into it without ever being a participant, as long as a real participant currently has it open.

**[MEDIUM] server/chat_api.py:1545-1552 — `/chat/api/swlog` is unauthenticated and unrate-limited.** No `_get_user_from_cookie` call, no IP/user throttle (contrast with `/login`'s per-IP limiter and uploads' per-user `_check_upload_rate`). Accepts arbitrary JSON from anyone and writes it into server logs — usable for log injection/flooding.

**[LOW] server/chat_api.py:1555-1578 — `/chat/api/push/ack` is unauthenticated and unrate-limited.** It mutates another user's `last_seen`/`last_active` based purely on a client-supplied `endpoint` string match, with no throttling — an attacker who obtains an endpoint value can keep manipulating that user's presence state indefinitely.

**[LOW] server/chat_ws.py:1591-1599 — `block_user`/`unblock_user` WS events don't check the target exists.** A bogus `target_user_id` triggers an uncaught `sqlite3.IntegrityError` (FK constraint on `blocks.blocked_id`) that propagates to the outer `except Exception` handler (chat_ws.py:1788-1789), killing the sender's own WebSocket connection. Self-inflicted only — the REST equivalent (chat_api.py:1103-1112) correctly checks `get_user()` first.
