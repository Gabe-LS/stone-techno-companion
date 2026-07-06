Everything is in order. Now let's compile the final report.

## Final Report

**R2-1** DONE — Fix A: ban check in `handle_chat_ws` after user/handshake fields resolved, before `manager.connect` (server/chat_ws.py). Fix B: added `check_ban_mute` to chat_moderation.py, wired into `_moderate_and_broadcast`'s unmoderated branch.
**R2-2** DONE — explicit `SELECT 1 FROM messages WHERE id=?` existence check after the approve UPDATE; skips broadcast/preview/badge/push if gone.
**R2-3** DONE — ban branch now closes every connection in `mgr.user_conns.get(user_id, {})`, not just the single `ws`.
**R2-4** DONE — `auth_logout` now looks up the user by token before deleting the session, then closes all live WS connections (code 4001).
**R2-5** DONE — added `(/ws/)(?!chat/)[^/\s?]+` as the first `_RedactTokensFilter` pattern in server/api.py; verified it can't double-redact or clobber `/ws/chat/`.
**R2-6** DONE — (a) `get_unread_counts` JOIN gains `AND m.moderation_status != 'pending'`; (b) both `_do_send_push` queries gain the same guard, and the preview path now uses the new `_preview_from_content` helper (see R2-11).
**R2-7** DONE — `/swlog` gets a `_swlog_rate` 30/min-per-IP limiter matching the spec's snippet.
**R2-8** DONE — (a) `.gitignore` gains `server/chat-uploads/`; (b) deploy.sh Step 1 backs up `chat-uploads/` best-effort after the integrity-check loop.
**R2-9** DONE — `_check_auth_rate` default `max_n` raised 20→120, comment added.
**R2-10** DONE — admin.html Reports tab: all four `reporter_name`/`reported_name` call sites now fall back to `'[deleted user]'`. (Users tab has no reporter/reported rendering; Banned tab already had its own `'Deleted user'` fallback, left untouched.)
**R2-11** DONE — added `_preview_from_content(msg_type, content)` helper (deviated from the spec's `_preview_from_content(content)` signature — needed `msg_type`, which I added to the SELECT, since the type-vs-preview-string mapping can't be derived from content alone); used in `_do_send_push`.
**R2-12** DONE — `join_room` and `meetup_invite` both now re-fetch `get_user(db, user_id)` into scratch locals (`_jr_*`, `_mi_*`) before seeding/broadcasting, leaving the handshake locals (`display_name` etc.) untouched.
**R2-13** DONE — added the three indexes to chat_db.py's schema block.
**R2-14** DONE — (a) `|| true` added to the required-var grep; (b) prune step rewritten to guard the empty case before `xargs rm -rf`.
**R2-15** DONE — `_resolve_safe_ip` renamed to `_resolve_safe_ips` (returns `list[str]`, all-or-nothing validation preserved); `_pinned_preview_get` now loops over all validated addresses, using the first that connects.
**R2-16** DONE — (a) `/rooms/{room_id}/online` gains a meetup-attendee check mirroring the dm check; (b) `POST /rooms/{room_id}/join` now 403s for `dm`/`meetup` room types.
**R2-17** DONE — `/login` ban response is now generic `"This account cannot sign in."`, no reason text.
**R2-18** DONE — added `_safe_href` (Python, in `_link()`) and `_safeHref` (client JS, in `_popupLink`); both are the only two anchor-building call sites in render.py.
**R2-19** DONE — `openRoom` calls `_flushMarkRead()` first thing, with the requested `dbg()` line.
**R2-20** DONE — REST fallback in `openRoom` now runs fetched messages through `_decryptMessageContent` before rendering.
**R2-21** DONE — (a) added `_bg_tasks`/`_spawn_bg_task` and converted all three fire-and-forget `asyncio.create_task` call sites (flush, push, moderation); (b) added `sweep_stuck_pending` to chat_db.py and wired a 3-minute sweep into `purge_loop`.
**R2-22** DONE — `/manifest.json`, `/shared.css`, `/shared.js` all now set `Cache-Control: no-cache`.
**R2-23** DONE — `_maybePromptNotifications` bails (without setting `_notifPromptChecked`) when `.modal-overlay.open` exists.
**R2-24** DONE — lineup push payload gains `"push_id": secrets.token_hex(8)`.
**R2-25** DONE — (a) `/check-name` now gated by `_get_user_from_cookie`; (b) `_upload_rate` self-prunes past 1000 keys; (c) `/push/ack` gets a 60/min-per-IP limiter.
**R2-U1** DONE — removed the 1.5s `setTimeout` in `route()`; `sendChatMessage` now arms the prompt (800ms) once, guarded by `_notifPromptChecked` and a `sent_first_msg` localStorage flag. **Deviated**: the spec's own example snippet checks `storageGet('notif_prompt_done')` but only ever sets `'sent_first_msg'` — that's internally inconsistent (checks one key, sets another, and `'notif_prompt_done'` is a *different*, pre-existing flag already checked inside `_maybePromptNotifications`). I implemented the logically consistent version: check and set `'sent_first_msg'`, matching the stated intent ("triggers from the first message ever sent"). Combined with R2-23's modal-overlay bail per point 3.
**R2-U2** SKIPPED — the described construct doesn't exist. The only `/api/me` fetch in render.py already guards with `if (res.ok)` and only logs via `dbg()` in its `catch`; there is no `console.error` call anywhere in render.py to fix.

**Spec/code discrepancies worth flagging:**
- `chat_moderation.moderate_message`'s pre-existing `is_muted` early-return uses `"action": "muted"` (chat_moderation.py ~line 437), which doesn't match the `"mute"` string that `_moderate_and_broadcast` actually checks for — meaning an already-muted user re-sending in a *moderated* room skips the mute-broadcast/mass-delete branch today. This is a pre-existing bug, out of R2-1's explicit scope (which only specifies the string for the *new* `check_ban_mute` helper), so I left it untouched per "do not add scope."
- R2-U1's own code snippet is internally inconsistent (see above) — implemented the corrected version instead of the literal snippet.