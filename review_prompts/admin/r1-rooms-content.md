# Review: admin panel — room & content control completeness

You are a read-only reviewer. You CANNOT run anything (no Bash, no tests, no server). Do not claim any verification you did not do by reading code. The orchestrator runs all tests.

## Context

Festival companion app with ephemeral chat. Admin SPA at `/chat/admin`. Your job: judge whether the admin panel gives FULL control over rooms, messages, media, and app-level chat settings; find gaps, bugs, and missing options.

Authoritative files (read all):
- `server/chat/admin.html` — admin frontend (Rooms tab, room modal, drag reorder, settings)
- `server/chat_api.py` — admin room endpoints (`/admin/rooms*`, `/admin/settings`, roughly lines 2018-2400) and their db calls
- `server/chat_db.py` — create_room, update_room, delete_room, get_room_stats, get_setting/set_setting, chat_settings keys, purge logic
- `server/chat_ws.py` — rooms_changed handling, read-only enforcement, purge loop, meetup expiry
- `server/chat/chat.html` — only to check how room properties (is_read_only, allows_media, ttl, description) manifest for users
- `CLAUDE.md` sections "Room Properties", "Admin Page", "Chat Database", "Membership Model"

## Scope — what to evaluate

1. Room CRUD: create (name → room_id slugging — collisions? weird chars? uppercase? empty after slug?), edit (which fields are editable? can `type` change? can TTL of the main room change?), delete (what cascades: messages, memberships, media files on disk, meetups?).
2. `PATCH /admin/rooms/{room_id}` passes the raw JSON body as `update_room(db, room_id, **body)` — read `update_room` in chat_db.py and determine exactly what a malformed or extra key does (crash? SQL column injection? silently ignored?). FACT vs INFERENCE.
3. Reorder endpoint: validates room ids? Positions of rooms NOT in the payload? DM/meetup rooms included in the drag list in the UI?
4. Settings: `chat_settings` documents keys `room_sort, msg_char_limit, dm_ttl_minutes, room_ttl_minutes, meetup_ttl_minutes` — which are exposed in the admin UI? (Compare `/admin/settings` GET/PATCH with the documented keys.) Is there any UI to change message char limit or default TTLs, or does that require raw DB edits despite CLAUDE.md saying "Change in DB, no deploy needed"?
5. Content control: can an admin view a room's messages from the admin panel? Delete a single message? Purge a room's messages without deleting the room? Post an announcement into a room (read-only rooms say "only admins can post" — find how an admin actually posts: chat_ws read-only check, is it tied to admin emails or something else?).
6. Media: can an admin see or remove uploaded media? What happens to media files when a room is deleted or a message removed via admin paths (look for _unlink_media_if_orphaned usage in admin paths)?
7. Meetups: any admin visibility/control over meetups (list, delete a meetup, see attendees)? CLAUDE.md says meetup rooms are auto-managed — is a rogue meetup (offensive title) removable from the admin panel?
8. DMs: what does the admin see about DM rooms in the Rooms tab (they are E2EE — member counts? ability to delete a DM room?)? Should they be listed at all?
9. Stats bar: are the stats sufficient and correct (read get_admin_stats)? Anything misleading?
10. UI robustness of Rooms tab: TTL dropdown only offers fixed values — what renders if a room has a TTL not in the list (e.g. 120)? Does editing such a room silently rewrite its TTL?

## Hard rules
- Read-only. Cite evidence as `file:line`.
- Distinguish FACT (verified by reading code) from INFERENCE.
- Do not review user moderation actions, admin auth security, or multi-admin design — other reviewers own those. Note cross-lane findings in one line only.

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
