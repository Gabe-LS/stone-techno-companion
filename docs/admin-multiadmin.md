# Admin multi-admin / super-admin design (authoritative contract)

Stage B of the admin-panel hardening. This doc is the single source of truth all
implementation agents build against. No migrations on existing tables — two NEW tables
only (`CREATE TABLE IF NOT EXISTS` at startup).

## Roles

- **super_admin** — full power. Manages the admin list, deletes rooms/users, changes app
  settings, unbans / deletes bans, clears warnings. `CHAT_ADMIN_EMAILS` entries are PERMANENT
  super_admins (never stored in the DB, never removable via the panel — the lockout-proof root set).
- **admin** (moderator) — day-to-day moderation: ban, mute, unmute, strike, resolve reports,
  create/edit rooms, reorder, set-main, and all read/view endpoints. Cannot do super-admin-only actions.
- The shared `CHAT_ADMIN_TOKEN` header is a BOOTSTRAP/emergency credential → resolves to a
  `super_admin` actor with label `token` (unattributable, so demoted to bootstrap use).

## New schema (append to `init_db` executescript block in chat_db.py; IF NOT EXISTS)

```sql
CREATE TABLE IF NOT EXISTS admins (
    email_hash TEXT PRIMARY KEY,          -- sha256(email), matched against user_providers.provider_id
    role       TEXT NOT NULL DEFAULT 'admin',   -- 'admin' | 'super_admin'
    label      TEXT,                        -- super-admin-provided human label (name/role note), NOT required to be the email
    added_by   TEXT,                        -- actor label of who added this admin
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_actions (
    id             TEXT PRIMARY KEY,
    actor          TEXT NOT NULL,           -- actor label: email_hash[:12] | 'token' | 'system'
    action         TEXT NOT NULL,           -- see action vocabulary below
    target_user_id TEXT,
    target_room_id TEXT,
    detail         TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_created ON admin_actions(created_at);
```

Action vocabulary (string constants used in `action`): `ban, unban, delete_ban, mute, unmute,
strike, clear_warnings, delete_user, create_room, update_room, delete_room, set_main, reorder,
resolve_report, add_admin, remove_admin, update_settings`.

Emails stay hashed everywhere (privacy). The `label` is a super-admin-typed note so the Admins
list is human-readable without storing end-user PII.

## AdminActor

`_resolve_admin(request) -> dict | None` returns:
```
{"kind": "token"|"cookie", "role": "admin"|"super_admin",
 "user_id": str|None, "email_hash": str|None, "label": str}
```
Resolution order:
1. If `ADMIN_TOKEN` set and `X-Admin-Token` matches (constant-time): return token super_admin
   (`kind=token, role=super_admin, user_id=None, email_hash=None, label="token"`).
2. Else cookie: resolve user via `chat_session`; for each `provider_id` in that user's `user_providers`:
   - if `provider_id in _ADMIN_EMAIL_HASHES` (env): return `kind=cookie, role=super_admin,
     email_hash=provider_id, label=(admins.label if a row exists else provider_id[:12])`.
   - elif `get_admin(db, provider_id)` returns a row: return `kind=cookie, role=row["role"],
     email_hash=provider_id, label=(row["label"] or provider_id[:12])`.
3. Else return None.

`_require_admin(request) -> dict`: `actor = _resolve_admin(request)`; if None → record fail-rate +
raise 403; return actor. (Keep the Stage-A `_check_admin_fail_rate` call at the top.)
`_require_super_admin(request) -> dict`: `actor = _require_admin(request)`; if
`actor["role"] != "super_admin"` → raise 403 "Super-admin access required"; return actor.

## Endpoint role assignment

Change EVERY `/admin/*` endpoint from `_require_admin(request)` (discarded) to `actor = _require_admin(request)`.
Super-admin-only endpoints call `_require_super_admin` instead:

- **super_admin only**: `admin_delete_room`, `admin_delete_user`, `admin_update_settings`,
  `admin_unban`, `admin_delete_ban`, `admin_clear_warnings`, and ALL admin-management endpoints.
- **admin (or super)**: everything else (reports view/resolve, ban, mute, unmute, strike,
  create/update room, set_main, reorder, all GET views, audit view).

## Admin-account protection

Helper `_protected_role(db, target_user_id) -> "super_admin"|"admin"|None`:
- Gather target's provider_ids (users row + user_providers).
- If any is in `_ADMIN_EMAIL_HASHES` → return "super_admin" (env = permanent, highest).
- Else if any matches an `admins` row → return that row's role (super_admin wins over admin).
- Else None.

Apply in `admin_ban`, `admin_mute_user`, `admin_strike_user`, `admin_delete_user` (after the
target-exists check):
- if protected role == "super_admin" (env-permanent OR db super_admin) → **403 always**
  ("Cannot moderate a super-admin").
- elif protected role == "admin" → allow only if `actor["role"] == "super_admin"`, else 403
  ("Only a super-admin can moderate another admin").
Note: env super-admins are never moderatable by anyone via the panel (owner-safe).

## Attribution / audit

`log_admin_action(db, actor, action, target_user_id=None, target_room_id=None, detail=None)`
inserts one `admin_actions` row (actor = `actor_label` string). Call it at the end (post-success)
of every mutating admin endpoint, passing `actor["label"]`. This is what makes mutes/unmutes/unbans
and room ops attributable and log-visible.

`get_admin_actions(db, limit, offset) -> list[dict]`: newest first, joins `users` on
`target_user_id` for `target_name` when present. Shape per row:
`{id, actor, action, target_user_id, target_name, target_room_id, detail, created_at}`.

## New endpoints

- `GET /admin/me` → `{role, kind, label, email_hash}` for the resolved actor (via `_require_admin`).
- `GET /admin/audit?limit=50&offset=0` → `get_admin_actions` (admin-tier; clamp limit 1..200).
- `GET /admin/admins` (super) → list. Each: `{email_hash, role, label, permanent: bool,
  added_by, created_at}`. Include env super-admins as synthetic `permanent:true` rows
  (email_hash from `_ADMIN_EMAIL_HASHES`, role `super_admin`, label "env", added_by "env")
  merged with the DB rows (DB row wins for label if a hash appears in both, but permanence
  is driven by env membership).
- `POST /admin/admins` (super) `{email, role, label?}` → validate email non-empty, role in
  {admin, super_admin}; `h = hash_email(email)`; if `h in _ADMIN_EMAIL_HASHES` → 409
  ("Already a permanent super-admin"); upsert into `admins`; log `add_admin`. Return the row.
- `DELETE /admin/admins/{email_hash}` (super) → if `email_hash in _ADMIN_EMAIL_HASHES` → 400
  ("Cannot remove a permanent super-admin"); delete the row; log `remove_admin`. Guard against
  self-lockout is unnecessary (env super-admins always remain), but do NOT allow removing the
  last DB super_admin if there are also zero env super-admins — if `_ADMIN_EMAIL_HASHES` is
  empty AND this is the last `role='super_admin'` row, return 400 ("Would remove the last super-admin").

## UI (admin.html)

- On `init`, call `GET /admin/me`; store `window._me`. Render an identity chip in the tabs/stats
  area: `label` + a role pill (`super_admin` amber, `admin` blue) + a Logout control.
- Logout: token actor → `sessionStorage.removeItem('chat_admin_token')` + `renderLogin()`.
  Cookie actor → `POST /chat/api/logout` (existing chat logout; verify it exists — if not, just
  clear and reload) then `location.reload()`.
- New tabs appended after "Logs": **"Audit"** (all actors) and **"Admins"** (super_admin only —
  render the tab only when `window._me.role === 'super_admin'`).
  - Audit tab: table `Actor | Action | Target | Detail | Time`, Load-more like Logs.
  - Admins tab: list (label, role pill, permanent badge, added_by, created_at); an "Add admin"
    form (email, role select, label); Remove button per non-permanent row.
- Role-gate destructive controls: when `window._me.role !== 'super_admin'`, hide/omit
  Delete-room, Delete-user, Unban (both tabs), Clear-warnings, and the Rooms-tab sort/settings
  writes are fine (settings PATCH is super-only server-side, but room_sort toggle stays visible;
  if a non-super hits it they get a 403 — acceptable, or hide it). Server enforces regardless;
  the UI gate is cosmetic to avoid dead buttons.

## Tests / verification (orchestrator)

- All 198 existing chat tests still pass.
- New DB helpers unit-checked (add/get/list/remove admin, log/get admin_actions).
- Role resolution + protection spot-checked: token→super; env email→super permanent; db admin
  row→admin; moderating an env super-admin →403; admin moderating admin →403; super moderating
  admin →ok.
