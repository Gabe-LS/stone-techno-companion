# Review: admin panel — user-moderation control completeness

You are a read-only reviewer. You CANNOT run anything (no Bash, no tests, no server). Do not claim any verification you did not do by reading code. The orchestrator runs all tests.

## Context

This is a festival companion app with an ephemeral chat. The admin panel is a dark-themed SPA at `/chat/admin`. During the festival, one or more admins will use it (often from a phone) to keep the chat safe in real time. Your job: judge whether the admin panel gives an admin FULL control over user moderation, and find gaps, bugs, and missing options.

Authoritative files (read all of these):
- `server/chat/admin.html` — the whole admin frontend
- `server/chat_api.py` — admin endpoints are between the `/admin/reports` route and the `mount_chat` function (roughly lines 1900-2410); auth helpers `_require_admin`, `_load_admin_emails` around lines 178-268
- `server/chat_db.py` — db helpers used by admin endpoints (search_users, get_user_admin_detail, get_admin_stats, get_moderation_log, ban_user, mute_user, delete_user_messages, delete_user, get_all_bans, get_pending_reports, resolve_report)
- `server/chat_moderation.py` — process_strike, check_ban_mute, strike escalation
- `server/chat_ws.py` — how mute/ban events reach live connections; what events exist
- `CLAUDE.md` sections "Moderation Pipeline", "Admin Page", "Chat Database (chat.db)" for intended behavior

## Scope — what to evaluate

Walk through realistic admin scenarios and check each is possible, correct, and reachable in the UI:
1. A user is spamming right now: can the admin see the user's recent messages? Delete a specific message? Delete all their messages without muting them?
2. Mute: is there an UNMUTE action distinct from "clear warnings"? Can the admin choose mute duration? Does the UI show remaining mute time?
3. Ban: can the admin give a custom ban reason? Does report-ban vs users-tab-ban behave consistently? Does ban delete the user's messages (compare with mute/strike paths — is there an inconsistency)?
4. Strike: does an admin strike escalate identically to an automatic strike (message deletion, mute/ban events, socket close)? Compare `/admin/strike` with `/admin/mute` and `/admin/ban` side effects.
5. Unban: two unban paths exist (`POST /admin/unban/{user_id}`, `DELETE /admin/bans/{ban_id}`). Are they equivalent? Does unban by ban_id leave sibling provider bans in place (a user banned across 2 providers)?
6. Delete user: what does it cascade to (messages, avatar, sessions, memberships, reports, push subscriptions, e2ee keys)? Is anything orphaned?
7. Reports: can the admin view resolved/dismissed reports afterwards? Can they jump from a report to the user's detail or the room? Is the report actionable enough (room context, timestamps)?
8. Visibility: does the users list expose enough (strike expiry times, mute remaining, provider emails are hashed — is there ANY way to correlate a user to a real identity when needed)?
9. Live enforcement: after each admin action, what does the affected user and the room see, immediately? Any action that only takes effect on next reconnect?
10. Anything else an admin would need mid-festival that is missing (e.g. slowmode, room-level mute, freeze chat, broadcast announcement — note these but mark as feature ideas, not bugs).

## Hard rules
- Read-only. Cite evidence as `file:line`.
- Distinguish FACT (verified by reading code) from INFERENCE.
- Do not review security/auth of the admin surface itself, room management UI, or multi-admin design — other reviewers own those. Stay in your lane except where a finding crosses lanes; then note it in one line.

## Required final report format

```
## Findings
### F1. <title> [severity: high|medium|low] [kind: bug|missing-feature|inconsistency|ux]
Evidence: file:line ...
What happens / what's missing: ...
Proposed change: ...
(repeat)

## Feature ideas (not bugs)
- ...

## Uncertain / needs orchestrator verification
- ...
```
