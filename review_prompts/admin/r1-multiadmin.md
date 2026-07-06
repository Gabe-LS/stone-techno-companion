# Review: admin panel — multi-admin & super-admin readiness

You are a read-only reviewer. You CANNOT run anything (no Bash, no tests, no server). Do not claim any verification you did not do by reading code. The orchestrator runs all tests.

## Context

Festival companion app with ephemeral chat. Admin SPA at `/chat/admin`. Auth today: EITHER a single shared `CHAT_ADMIN_TOKEN` header token OR a chat session cookie whose user has a provider_id matching a hash in `CHAT_ADMIN_EMAILS`. The owner wants the panel to be **multi-admin ready** (several trusted people moderating during the festival) with a **super-admin** concept (the owner) above regular admins. Your job: assess how far the current design is from that, and specify precisely what is missing.

Authoritative files (read all):
- `server/chat_api.py` — `_require_admin`, `_load_admin_emails`, `ADMIN_TOKEN` definition, every `/admin/*` endpoint
- `server/chat/admin.html` — login flow, token storage, all actions
- `server/chat_db.py` — moderation log source (`get_moderation_log`), strikes/bans/reports tables (what actor info they store), chat_settings
- `server/chat_ws.py` — anything gating admin capabilities in-chat (read-only room posting, admin flags on WS identity)
- `docs/chat-spec.md` if it covers admin design
- `CLAUDE.md` sections "Admin Page", "Environment Variables", "Auth"

## Scope — what to evaluate

1. Actor attribution: when an admin bans/strikes/mutes/deletes/edits a room, is WHICH admin did it recorded anywhere (bans.reason? strikes.detail? modlog)? Trace exactly what `get_moderation_log` shows and what is lost. Multi-admin without attribution = unaccountable.
2. Identity: does the backend even know which admin is acting? `_require_admin` returns None — it never resolves the acting admin identity. What refactor is needed so every admin endpoint knows actor id/email (cookie path) or "token" (header path)?
3. Roles: is there any role model (admin vs super-admin)? What SHOULD super-admin-only cover — propose a concrete minimal split. Candidates: managing the admin list itself, deleting rooms, deleting users, changing app settings, unbanning, viewing the mod log vs acting. Justify each placement.
4. Admin list management: today admins are env-var-only (`CHAT_ADMIN_EMAILS`, requires redeploy/restart to change). For festival ops, should there be a DB-backed admins table manageable from the panel by the super-admin? What are the risks (lockout, privilege escalation) and mitigations (env-var emails are permanent super-admins, DB rows only add)?
5. Admin-vs-admin safety: can one admin ban/mute/delete another admin (or the super-admin)? Trace the ban path with an admin's user_id. Should admin accounts be protected?
6. Concurrency: two admins acting at once — resolve the same report twice, both editing rooms, drag-reorder races. Read `resolve_report` (does it guard on current status?), room updates. Any lost-update or double-action hazards? Severity realistically.
7. Shared-token weakness for multi-admin: X-Admin-Token is one shared secret — no revocation per person, no attribution. Cookie path is per-person. Should the token path be demoted (super-admin bootstrap only)? Note UI implications (admin.html login screen only asks for the token; cookie-authed admins hit /chat/admin how?).
8. Session/UX for multiple admins: does the panel behave correctly when opened by a cookie-authed admin without a token (read init() flow)? Is there any indication of who you are logged in as?
9. In-chat admin powers: in the chat itself, do admins have any visible badge/powers (delete others' messages in-room, post in read-only rooms)? How is "admin" determined in chat_ws, if at all?

## Hard rules
- Read-only. Cite evidence as `file:line`.
- Distinguish FACT (verified by reading code) from INFERENCE, and DESIGN PROPOSAL from both.
- Do not review generic endpoint security (CSRF, rate limits, XSS) or moderation-action completeness — other reviewers own those. Note cross-lane findings in one line only.

## Required final report format

```
## Current state (facts, with file:line)
...

## Findings
### F1. <title> [severity: high|medium|low] [kind: gap|bug|design]
Evidence: ...
Proposed change: ...
(repeat)

## Proposed multi-admin/super-admin design (concise, implementable)
- data model changes
- endpoint changes
- UI changes
- migration/bootstrap story

## Uncertain / needs orchestrator verification
- ...
```
