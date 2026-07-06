# Pre-Deploy Review Round 2 — Triage

Seven Sonnet-5 static review agents (r01-r07) + one multi-browser UX agent (r08).
Findings below were cross-corroborated and spot-verified by the orchestrator against
the live code before triage. IDs prefixed `R2`.

## FIX NOW — verified, corroborated, low-risk

| ID | Sev | Finding | Location | Verified |
|----|-----|---------|----------|----------|
| R2-1 | CRIT | Ban/mute enforcement + connect-time ban not applied to DMs / open conns. `moderate_message` (which holds is_banned/is_muted) only runs when `is_moderated`; DMs are `is_moderated=False`. Connect only checks session token. | chat_ws.py:896-900, 1150-1159; chat_moderation.py:420-438 | YES — confirmed |
| R2-2 | HIGH | Delete/TTL-expire during in-flight moderation: after moderation passes, blind `UPDATE ... moderation_status='approved'` then unconditional broadcast/push — no re-check row still exists → retracted message resurrected for room. | chat_ws.py:967-1006 | YES — confirmed |
| R2-3 | HIGH | Automatic (moderation) ban closes only the single flagging `ws`, not all `user_conns`. Admin path closes all. | chat_ws.py:939-946 vs chat_api.py:1906-1910 | YES |
| R2-4 | HIGH | `auth_logout` deletes session row but never closes the user's live WS connection(s) → open tab keeps full access on shared device. | chat_api.py:555-566 | YES |
| R2-5 | HIGH | Lineup sync WS `/ws/{code}` carries raw session_id in path; redaction filter only matches `/ws/chat/` and `/chat/v/` → session token logged in plaintext (90-day hijack). | api.py:55-64, 797 | YES |
| R2-6 | HIGH | Unread counts + push-preview fallback queries never exclude `moderation_status='pending'` (fix propagated only to room_history). Pending/soon-rejected content appears in push body + inflates app badge. | chat_db.py:765-795; chat_ws.py:451-471 | YES |
| R2-7 | HIGH | `/chat/api/swlog` — no auth, no rate limit; anonymous log-injection/flood, can rotate away security log lines. | chat_api.py:1671-1678 | YES |
| R2-8 | HIGH | `.gitignore` misses `server/chat-uploads/` — the real prod bind-mount (`./chat-uploads:/app/chat/uploads`) inside the git worktree deploy.sh pulls. Live user media (incl. DM) exposed to stray `git add`/`clean`. | .gitignore; server/docker-compose.yml:14 | YES |
| R2-9 | HIGH | IP-keyed auth rate limit (20/5min) locks out festival shared-NAT venue on a normal sign-in rush; Caddy forwards real client IP so NAT collision is real. | chat_api.py:_check_auth_rate | YES (context) |
| R2-10 | MED | admin.html renders literal "null" for deleted reporter/reported names after ceeec3b's LEFT JOIN. | admin.html:293-294; chat_db.py LEFT JOINs | YES |
| R2-11 | MED | Debounced/deferred group push shows raw JSON envelope as body (`text_preview = content`), not parsed text. | chat_ws.py:424, 458-471 | YES |
| R2-12 | MED | Profile-edit propagation: `join_room` (+ meetup_invite) seed rooms with stale handshake identity; only send_message was fixed to re-fetch. | chat_ws.py:1153-1157, 1235-1263, 1636-1654 | YES |
| R2-13 | MED | `dm_participants` has no index for its hot `WHERE user_id=?` lookups (full scan on every DM open + unread/push check). email_tokens/strikes.expires_at also unindexed (scanned every 30s). | chat_db.py:167-171, 65-71, 193-201 | YES |
| R2-14 | MED | deploy.sh: PROD_VARS `grep|cut` lacks `|| true` (silent set-e abort before its own error msg); final `xargs rm` runs empty on <6 backups → aborts after successful deploy. | deploy.sh:49-54, 183 | YES |
| R2-15 | MED | SSRF-safe link preview pins to `safe[0]` with no fallback across other resolved addresses → dual-stack targets with unreachable first address now fail. | chat_ws.py:170-229 | YES |
| R2-16 | MED | `/rooms/{id}/online` gates DM but not meetup membership; `/rooms/{id}/join` ignores room type (pollutes membership for DM/meetup ids). | chat_api.py:959-974, 856-868 | YES |
| R2-17 | MED | `/chat/api/login` returns ban reason before ownership proof → email/ban-reason enumeration. | chat_api.py:476-478 | YES |
| R2-18 | MED | render.py `_link()`/`_popupLink()` build hrefs with only `esc()` — no scheme allowlist; `overrides.toml` (repo-editable) can inject `javascript:` onto the public page. | render.py:920-922, 2425-2427 | YES |
| R2-19 | MED | Room switch drops a pending mark-read (globals not per-room; openRoom never flushes). Stale unread badge. | chat.html:2299-2351 | YES |
| R2-20 | MED | E2EE DM REST-fallback path renders raw envelope (blank bubble) — never calls `_decryptMessageContent`. | chat.html:1978-1989 | YES |
| R2-21 | LOW | Fire-and-forget `create_task` (moderation, push, flush) kept only by weak ref → GC can strand a message at `pending` forever. Add a task-ref set + stuck-pending sweep in purge loop. | chat_ws.py:389-391, 1566-1585 | YES |
| R2-22 | LOW | shared.css/shared.js served with no Cache-Control (now deployed on every content push) → stale bundle after deploy. | api.py static routes | YES |
| R2-23 | LOW | First-run notif prompt can stack over a manually-opened Notifications modal. | chat.html:_maybePromptNotifications | YES |
| R2-24 | LOW | Lineup push payload omits `push_id`; iOS tag-uniqueness holds only by Math.random accident. Add `push_id` for the documented invariant. | api.py:371-377 | YES |
| R2-25 | LOW | `/check-name` (no auth/limit vs `/check-username`), `_upload_rate` never prunes, `/push/ack` no rate limit. | chat_api.py:676-679, 89-98, 1681-1704 | YES |

## FLAG FOR HUMAN — real but risky/invasive/out-of-scope for an auto-fix pass

| ID | Sev | Finding | Why deferred |
|----|-----|---------|--------------|
| R2-F1 | MED | Word filter defeated by letter-spacing (`m d m a`) and no substring match (H9). | Tightening risks false positives; AI layers 2/3 cover. Needs tuning, not a blind fix. |
| R2-F2 | MED | Bio HTML sanitizer is regex blacklist, bypassable via entity-encoded `javascript:` / `xlink:href`. | Proper fix = allowlist lib (nh3/bleach) = new dependency the day before deploy. Minimal hardening applied instead (entity-decode check); full fix flagged. |
| R2-F3 | MED | Upload endpoints still buffer up to 110MB before per-endpoint size check. | True streaming enforcement is invasive; 110MB middleware ceiling caps worst case. |
| R2-F4 | MED | Zombie connection in ConnectionManager state on half-dead socket; DM `_room_meta`/`rooms` dicts grow unboundedly over multi-day event. | Restart-bounded; eviction logic carries its own race risk. Post-deploy. |
| R2-F5 | MED | Reconnect replacing `messagesByRoom` can wipe an unacked optimistic message. | Merge logic is subtle; needs its own test. Post-deploy. |
| R2-F6 | LOW | Container runs as root (no `USER`). | Adding USER can break the bind-mount volume perms; needs deploy testing. |
| R2-F7 | MED | Uploaded-but-never-sent media never garbage collected (no pending_uploads table). | Needs schema addition + sweep; bounded by 10/min rate limit. Post-deploy. |
| R2-F8 | MED | VPS backup does not cover `server/chat-uploads/` (24h TTL now, was 60min). | Add rsync of chat-uploads to deploy.sh backup — included in FIX NOW as part of R2-8 mitigation. |
