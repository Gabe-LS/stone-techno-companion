# Admin panel review — Round 1 arbitration & implementation plan

Four parallel Sonnet 5 reviewers (moderation, rooms/content, multi-admin, security). Orchestrator (Fable) arbitrated every finding against the source. Rulings below; `[A]`=accept, `[A-]`=accept lower severity, `[R]`=reject, `[C]`=Stage-C/later.

## CRITICAL — must fix first (Stage A)

### SEC-1 [A, CRITICAL] Stored XSS in admin panel via untrusted display_name in inline onclick
- MECHANISM VERIFIED: `escapeHtml` (shared.js:23) encodes `'`→`&#39;`. admin.html builds handlers like
  `onclick="adminBan('${esc(u.display_name)}')"` (admin.html:448; also 299-300, 447, 484, 590, 381-382).
  The HTML parser decodes `&#39;`→`'` BEFORE the onclick string is compiled as JS, so an entity-encoded
  quote re-materialises and closes the JS string literal. HTML-encoding is NOT sufficient in a JS-string
  context. This is a real, well-known bypass.
- REACHABILITY VERIFIED: OAuth display_name is stored UNVALIDATED — `name = info.get("name") ...`
  (chat_api.py:365, 434) → `create_user` stores verbatim (chat_db.py:429-470). `_validate_display_name`
  (chat_api.py:690) is wired ONLY to `PUT /profile`, which is optional. `reported_name` reaches an onclick
  too, so merely being reported + an admin clicking Ban/Strike triggers the payload in the admin's
  authenticated context (token at admin.html:157, non-httpOnly cookie). Full admin-surface takeover.
- FIX (two layers):
  1. Input: sanitise OAuth-derived names before `create_user` (reuse/relax `_validate_display_name`;
     on reject, fall back to the email localpart or a safe slug). Never store raw provider text in display_name.
  2. Output: remove ALL untrusted strings from inline `onclick` string args. IDs are UUID/hash (safe charset);
     names are only used for `confirm()` text. Pass only ids through onclick; look the name up from the
     already-rendered in-memory arrays (window._reportsData/_bansData/_roomsData/_usersData) inside the handler.
     JS values held in memory are never re-parsed as code — safe. escapeHtml stays for HTML-content sinks.
- Cross-lane: same root cause likely affects chat.html bubble onclicks — separate follow-up, out of this pass.

## Stage A — high-severity moderation + security bugs

- MOD-1 [A, high] `admin_ban` (chat_api.py:1943) never deletes the banned user's messages (mute/strike/delete-user all do). Add `delete_user_messages` + `messages_expired` broadcast.
- MOD-2 [A, high] `admin_ban` never sends `{"event":"banned"}` before closing sockets, so the client treats 4003 as a transient drop and reconnect-loops. Send the banned event first (mirror strike-to-ban path chat_api.py:2196). Also harden client: treat 4003 like 4001.
- MOD-3 [A, high] No standalone Unmute — only "Clear warnings" (chat_api.py:2220) which nukes all strikes + resets `mute_count` (defeats anti-cycling). Add `POST /admin/unmute/{user_id}` clearing only `muted_until`; add UI action.
- MOD-4 [A, high] `admin_mute_user` (chat_api.py:2135) never calls `increment_mute_count` and mutes never appear in the mod log. Increment the lifetime counter, escalate to ban at `MAX_MUTES_BEFORE_BAN` like process_strike, and make direct mutes log-visible.
- MOD-6 [A, med] `DELETE /admin/bans/{ban_id}` (chat_api.py:2006) removes one provider row, leaving sibling bans of the same user_id → user stays banned though the row vanishes. Delete all rows for that user_id; change `active_bans` stat to `COUNT(DISTINCT user_id)`.
- SEC-2 [A, med] `admin_update_room` forwards raw JSON; `ttl_minutes:"soon"` stores a non-int → every later `create_message` raises TypeError (per-room outage). Validate `ttl_minutes` (None|positive int, sane cap), `position` (int) in the endpoint.
- ROOM-5 [A, med] `admin_create_room` accepts any `type`; `admin_update_room` can target dm/meetup rooms server-side. Allowlist type to {general,stage}; reject PATCH/edit on dm/meetup (mirror the DELETE guard 2386).
- ROOM-2 [A, high] Admin Rooms "Members" column hardcoded to 0 (chat_db.py:1757). Compute via `get_reachable_member_counts` in `admin_rooms`.
- SEC-3 [A-, low] No rate-limit on `_require_admin` token guesses. Apply `_check_auth_rate`-style per-IP limit to failed admin auth.
- SEC-4 [A, low] Clamp `limit` (1..200) and `offset` (>=0) on `/admin/users` and `/admin/modlog` (negative LIMIT = full dump).
- SEC-5 [A, low] Wrap `await request.json()` on admin writes → 400 on malformed JSON (helper).
- ROOM-10/SEC-6 [A, low] Slugify room name with `[a-z0-9-]` allowlist, reject empty; cap name/description length.
- MM-8 [A, low] `resolve_report` (chat_db.py:1389) has no status guard → double-resolution clobbers audit trail. Add `AND status='pending'`, report rowcount.
- ROOM-3 [A, med] Editing a room whose TTL isn't a preset silently rewrites to 30m. Inject a synthetic selected option for non-preset TTLs; add a custom-minutes input.
- ROOM-11 [A-, low] Stats "Messages" counts pending; `total_rooms` computed but unused. Exclude pending; surface Rooms stat.

## Stage B — multi-admin + super-admin (design ACCEPTED as reviewers converged)

- MM-2 [A] `_require_admin` → `_resolve_admin(request) -> AdminActor {kind: token|user, user_id?, email_hash?, role}`. Every mutating endpoint receives the actor.
- MM-1 [A] Actor attribution: add `acted_by` to bans + strikes, `reviewed_by` to reports; persist actor; surface "acted by" in mod log.
- MM-3/MM-4 [A] Role model + DB-backed admins:
  - `admins` table: (email_hash PK, role ['admin'|'super_admin'], added_by, created_at).
  - `CHAT_ADMIN_EMAILS` entries = PERMANENT super-admins, layered on top, never removable via panel (lockout-proof bootstrap).
  - Super-admin-only: manage admin list, delete room, delete user, change global settings, unban/delete-ban, clear-warnings.
  - Admin (moderator): mute/unmute/strike/ban, resolve reports, view users/bans/log, create/edit room, reorder, set-main.
- MM-5 [A] Reject ban/mute/strike/delete when target is an admin/super-admin unless actor is super-admin (and never allow demoting env super-admins). Protects the owner's account.
- MM-6 [A] Demote shared `X-Admin-Token` to bootstrap/emergency; cookie+role is the normal per-person path.
- MM-7 [A] `GET /admin/me` (whoami) + header identity display + logout in admin.html.
- MM-9 [A] Update docs/chat-spec.md + CLAUDE.md to match the new auth/role model.

## Stage C — completeness features

- MOD-5/ROOM-7 [C] Admin message browse + single-message delete (paginated, per room, includes pending; reuse `_unlink_media_if_orphaned`).
- ROOM-1 [C] Admin post into read-only/announcement rooms (WS has NO admin bypass today — chat_ws.py:1552 rejects everyone). Add an admin-post endpoint or WS admin check.
- MOD-7/MOD-8/MOD-9 [C] Resolved-reports view (`status=actioned|dismissed|all`); room context + jump-to-user in reports; custom reason/detail fields on ban/mute/strike dialogs.
- ROOM-4 [C] App-settings panel: msg_char_limit, dm/room/meetup TTLs (extend `/admin/settings`).
- ROOM-6 [C] Moderate meetup title/note at creation; admin meetup list + delete-meetup.
- ROOM-8/ROOM-9 [C] DM rows show participant names (or excluded); reorder validates ids & excludes dm/meetup.

## Rejected / noted
- None outright rejected. Privacy note (MOD uncertain-3): emails are one-way hashed by design — no reverse lookup for escalation. Intentional; leave as-is.
