## Findings

### F1. Direct admin Ban does not delete the banned user's existing messages [severity: high] [kind: bug]
Evidence: `chat_api.py:1943-1991` (`admin_ban` — no call to `delete_user_messages`) vs `chat_api.py:2146` (`admin_mute_user` calls it), `chat_api.py:2185-2194` (`admin_strike_user` calls it whenever a strike escalates to mute/ban), `chat_api.py:2254` (`admin_delete_user` calls it). CLAUDE.md's "Moderation Pipeline" section states: "Mute/ban also deletes all user's active messages and broadcasts removal" — the code contradicts this for direct bans.
What happens: Both the Users-tab "Ban" button and the Reports-tab "Ban" action call the same `POST /admin/ban/{user_id}` endpoint (so they're at least consistent with each other), which bans the account and closes sockets but leaves every message that user ever sent — spam, harassment, drugs, whatever prompted the ban — fully visible in the room. To also clear the spam, the admin has to separately mute the user or push 3 more strikes.
Proposed change: call `delete_user_messages` (and broadcast `messages_expired`) inside `admin_ban`, matching mute/strike-escalation/delete-user.

### F2. Direct admin Ban never sends a "banned" WS event — the banned client silently retries forever [severity: high] [kind: bug]
Evidence: `chat_api.py:1982-1988` closes sockets with `code=4003` but never calls `manager.send_to_user(..., {"event":"banned",...})` first, unlike `chat_ws.py:994-1002` (auto-ban path) and `chat_api.py:2196-2204` (admin strike-to-ban path) which both send the event before closing. `chat/chat.html:1071-1080` — the client's `onclose` handler only special-cases close code `4001`; any other code (including `4003`) is treated as a transient drop and triggers exponential-backoff reconnection because `currentUser` is still set. `chat_ws.py:1253-1262` — every reconnect attempt from a banned user is rejected again with `code=4003`.
What happens: A user banned via the Users tab or a Report never sees "You have been banned" — the client just keeps silently disconnecting/reconnecting (up to 30s backoff, indefinitely) instead of being kicked to the login screen the way the strike-escalation and auto-moderation ban paths correctly do (`chat.html:1457`).
Proposed change: send the same `{"event": "banned", "reason": ...}` payload before closing sockets in `admin_ban`; consider having the client also special-case `4003` as defense in depth.

### F3. No dedicated "Unmute" action — the only way to lift a mute wipes strike history and resets the lifetime mute counter [severity: high] [kind: bug/missing-feature]
Evidence: `admin.html:440-449` (Warnings dropdown only has Strike / Mute / Clear warnings), `chat_api.py:2220-2243` (`/admin/users/{user_id}/clear-warnings` deletes every row in `strikes` for the user AND sets `muted_until = NULL, mute_count = 0`). CLAUDE.md: "Lifetime mute counter: 3 total mutes across the event = permanent ban (prevents cycling)."
What happens: If an admin wants to end a mute early (wrong person muted, situation resolved, etc.), the only available tool also erases the user's entire strike history and resets `mute_count` to 0 — undoing the anti-cycling protection. A user who had already accumulated 2 lifetime mutes looks completely fresh after any early unmute.
Proposed change: add `POST /admin/unmute/{user_id}` that clears only `muted_until`, leaving `strikes`/`mute_count` intact; keep "Clear warnings" as the separate, intentionally destructive full reset.

### F4. Direct admin Mute doesn't count toward the lifetime mute→ban escalation and leaves no audit trail [severity: high] [kind: bug/inconsistency]
Evidence: `chat_api.py:2135-2167` (`admin_mute_user` calls `mute_user(db, user_id, minutes=minutes)` only — never `increment_mute_count`, and `mute_user` at `chat_db.py:543-546` takes no reason parameter and writes nothing else) vs `chat_moderation.py:373-376` (`process_strike`'s 3rd-strike mute path calls `increment_mute_count`, which is what actually enforces "3 mutes = ban"). `chat_db.py:1710-1731` (`get_moderation_log` unions only `strikes`, `bans`, and resolved `reports` — mutes are absent).
What happens: An admin who repeatedly hits "Mute" on the same repeat offender never trips the lifetime-mute-to-ban rule (`mute_count` stays 0 through this path forever), and none of those mutes ever appear in the Logs tab — there's no record a user was muted multiple times, unlike bans/strikes.
Proposed change: have `admin_mute_user` call `increment_mute_count` and apply the same `MAX_MUTES_BEFORE_BAN` escalation `process_strike` uses; add a log-visible record for direct mutes.

### F5. No way to view or delete an individual message from the admin panel [severity: medium] [kind: missing-feature]
Evidence: full admin route list (`chat_api.py:1902-2394`) has no `/admin/messages` or `/admin/rooms/{id}/messages` endpoint; `get_user_admin_detail` (`chat_db.py:1636-1697`) returns only `message_count`, never content. Message clearing is only possible via the all-or-nothing `delete_user_messages` triggered by mute/ban/strike-escalation/delete-user.
What happens: For "a user is spamming right now," an admin sees a message *count* for a flagged user but can't read what was actually sent, can't delete one offending message while leaving the rest, and has no way to browse a room's live feed from the admin panel to build context.
Proposed change: add a paginated message-browsing endpoint (per user or per room) with a per-message delete action, mirroring the delete the regular chat UI already gives ordinary users.

### F6. Per-ban-id unban ("Banned" tab) can leave a multi-provider user still banned; the two unban paths are not equivalent [severity: medium] [kind: inconsistency]
Evidence: `chat_db.py:597-629` (`ban_user_all_providers` inserts one `bans` row per linked provider, all sharing the same `user_id`); `chat_api.py:1994-2003` (`POST /admin/unban/{user_id}` deletes every row for that `user_id`) vs `chat_api.py:2006-2015` (`DELETE /admin/bans/{ban_id}` deletes exactly one row); `admin.html:582-591` — the "Banned" tab lists and unbans per `ban_id`; `chat_db.py:1700-1707` (`get_all_bans` returns one row per ban, so a 2-provider ban shows as two rows with the same display name).
What happens: A user banned across two linked providers (Google + email) appears twice in the "Banned" tab. Clicking "Unban" on one row deletes only that provider's ban — the sibling row (and thus the ban) remains in effect, even though the row visibly disappears and the admin believes the unban succeeded. The Users-tab Unban button doesn't have this problem. This also makes the stats-bar "Bans" count (`chat_db.py:1571`, `COUNT(*) FROM bans`) overstate distinct banned users.
Proposed change: make per-ban-id unban also remove sibling rows with the same `user_id` (or collapse the Banned tab to one row per user listing all bound providers); switch the stat to `COUNT(DISTINCT user_id)`.

### F7. Resolved/dismissed reports are not viewable anywhere in the admin UI [severity: medium] [kind: missing-feature]
Evidence: `chat_api.py:1902-1925` — `GET /admin/reports` returns `[]` for any `status` other than `"pending"` (no `get_reports_by_status`-style function exists in `chat_db.py`); `admin.html:280` only ever requests `status=pending` and has no control to request anything else. The Logs tab only shows a one-line `report_actioned`/`report_dismissed` summary (`chat_db.py:1710-1731`) — no message snapshot, no reporter, no room.
What happens: Once a report is actioned or dismissed it's effectively gone — no way to re-open it to double-check what was reported or whether the right call was made.
Proposed change: let `GET /admin/reports` accept `status=actioned|dismissed|all`; add a read-only resolved-reports view/filter.

### F8. Reports lack room context and a jump-to-user affordance [severity: low] [kind: ux]
Evidence: `admin_reports` (`chat_api.py:1902-1925`) returns `room_id`, but the reports table (`admin.html:287-304`) never renders or links it; the inline reporter/reported history line (`admin.html:290-294`, `historyLine`) is plain text, not a link into the Users tab.
What happens: An admin triaging a report can't see which room the incident occurred in, and has to separately search the Users tab by name to view the full profile/strike/ban history.
Proposed change: show the room name in the reports table and add a click-through to that user's row/detail panel in Users.

### F9. No custom reason/detail text for admin ban / mute / strike actions [severity: low] [kind: ux]
Evidence: `admin.html:390-404` (`reportAction`), `admin.html:535-568` (`adminStrike`/`adminMute`/`adminBan`) all send hardcoded strings ("Banned by admin", "Banned via report", "admin"/"Manual admin action") — no input field collects free text, even though the API accepts an arbitrary `reason`/`detail` (`chat_api.py:1946-1947`, `2174-2175`), and `mute_user` (`chat_db.py:543-546`) has no reason field to populate at all.
What happens: Every admin ban/strike in the moderation log and ban list carries the same generic label, so a second admin reviewing later can't tell why a given action was taken without cross-referencing the triggering report.
Proposed change: add a reason/detail text field to the ban and strike confirmation dialogs (ties into F4 for mutes).

## Feature ideas (not bugs)
- Slowmode / temporary room freeze for an actively-spamming room.
- Broadcast/announcement message from admin visible in all rooms.
- Bulk/multi-select moderation actions (e.g., ban 3 users flagged by the same raid at once).
- Per-strike removal (currently only full "clear all strikes").
- Show strike expiry countdown ("expires in 47m") rather than only creation time + active/expired tag.
- Exportable moderation history for post-event review.
- Attribution of which admin performed a given action (relevant if multiple admins share access) — **crosses into multi-admin/auth design, which is another reviewer's lane; flagging only because it's the natural companion to F9's reason-tracking gap.**

## Uncertain / needs orchestrator verification
- F2's "client stuck reconnecting forever after ban" is based on static code reading (chat.html `onclose` logic + server close codes), not a live browser test — worth confirming with an actual banned-session repro.
- Whether there's genuinely *no* way to correlate a banned/reported chat user to a real identity (emails are SHA-256 hashed with no reverse lookup stored, per `chat_db.py:1825-1826`, and `email_tokens` plaintext rows are purged with expiry per `chat_db.py:1770`) — this may be intentional privacy-first design rather than a gap, but is worth an explicit product decision given it forecloses any escalation path (e.g. to festival security/law enforcement) for a serious repeat offender.
- Whether `get_room_stats`'s `member_count`/`online_count` fields (used tangentially by the admin Rooms tab, out of my assigned scope) are accurate — not reviewed here since room management UI is another reviewer's lane.
