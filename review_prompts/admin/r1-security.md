# Review: admin panel — security & robustness of the admin surface

You are a read-only reviewer. You CANNOT run anything (no Bash, no tests, no server). Do not claim any verification you did not do by reading code. The orchestrator runs all tests.

## Context

Festival companion app, FastAPI + SQLite, admin SPA at `/chat/admin`. Auth: `X-Admin-Token` header (shared secret, compared with `secrets.compare_digest`) OR chat session cookie matched against hashed `CHAT_ADMIN_EMAILS`. Production is Docker behind Caddy with TLS. Your job: security and robustness review of the ADMIN surface only (endpoints under `/chat/api/admin/*`, the admin HTML, and the auth helpers). This is a defensive review of the maintainer's own code.

Authoritative files (read all):
- `server/chat_api.py` — `_require_admin`, `_load_admin_emails`, `ADMIN_TOKEN`, `_set_session_cookie`, every `/admin/*` endpoint (~lines 178-268 and 1900-2410)
- `server/chat/admin.html` — entire file (token handling, DOM injection points, esc usage)
- `server/chat_db.py` — functions called by admin endpoints, especially `update_room` (called as `update_room(db, room_id, **body)` with raw client JSON), `search_users` (LIKE query building), `get_moderation_log`
- `server/static/shared.js` — esc/escapeHtml implementation the admin page relies on
- `CLAUDE.md` "Admin Page", "Auth", "Environment Variables"

## Scope — what to evaluate

1. `update_room(db, room_id, **body)` with attacker-controlled keys: read `update_room` — is there a column allowlist, or can arbitrary SQL columns / kwargs crash or write unintended columns (is_main? event_id? type?)? FACT with line numbers.
2. CSRF: admin actions are state-changing POST/PATCH/DELETE authenticated by a cookie (SameSite=strict in prod, lax in dev). Evaluate realistic CSRF exposure in prod AND dev, plus whether any admin GET has side effects.
3. XSS in admin.html: enumerate every interpolation into innerHTML and check esc() coverage — user-controlled values include display names, usernames, room names/descriptions, report reasons, message snapshots, ban reasons, modlog detail, country. Any unescaped sink? Also check esc() used inside inline event handler attribute strings (onclick="...'${esc(x)}'...") — is escapeHtml sufficient there or can a quote break out? Give a concrete verdict per sink class.
4. Token handling: admin token accepted via URL query param (`?admin_token=`) then stored in sessionStorage and stripped via history.replaceState — residual risks (server access logs, Caddy logs, Referer, browser history before replaceState)? Is the token ever sent to non-admin endpoints?
5. Missing `_require_admin`? Diff the endpoint list — is EVERY /admin/* route guarded (including GET /admin page itself — should the HTML be gated?), and does anything else in the file mutate admin-owned state without the guard?
6. Input validation on admin endpoints: mute minutes (negative? huge?), ban reason length, room name length/characters (slug collisions, path-safety of room_id since it lands in URLs), reorder order list (non-existent ids, other event's rooms), settings values, report status transitions (re-resolving an already-resolved report), limit/offset bounds on users/modlog.
7. Rate limiting / brute force: `_require_admin` token comparison — any rate limit on failed attempts? (Compare with `_check_auth_rate` used on auth endpoints.) Realistic risk given 403 on failure.
8. Error handling / DoS-ish robustness: `await request.json()` on bodies — what happens with invalid JSON (500?)? db connections closed on all paths? asyncio.create_task usages in admin endpoints — fire-and-forget with the db already closed?
9. Information exposure: what does the admin API leak that even an admin maybe shouldn't see trivially (hashed provider ids OK; raw emails anywhere? push endpoints? session tokens in user detail?). Check get_user_admin_detail and search_users outputs.
10. The admin HTML is served ungated (`GET /chat/admin` has no _require_admin) — assess whether that matters (it's a static shell) and note if any data is embedded.

## Hard rules
- Read-only. Cite evidence as `file:line`.
- Distinguish FACT from INFERENCE. No speculative CVE theater — realistic severities for a single-maintainer festival app behind TLS.
- Do not review multi-admin design or moderation completeness — other reviewers own those. Cross-lane findings: one line.

## Required final report format

```
## Findings
### F1. <title> [severity: high|medium|low] [kind: vuln|hardening|bug|robustness]
Evidence: file:line ...
Attack/failure scenario: ...
Proposed change: ...
(repeat)

## Non-issues checked (one line each, so the orchestrator knows what was cleared)
- ...

## Uncertain / needs orchestrator verification
- ...
```
