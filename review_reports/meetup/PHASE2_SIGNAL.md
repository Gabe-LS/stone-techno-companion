# Phase 2 — Signal Analysis

100 findings across 12 perspectives. Convergence analysis below.

## HOTSPOTS (flagged by 2+ unrelated perspectives)

### H1 — `create_meetup` write path: no authz / no moderation / no ban-mute / stage_id trust
Perspectives: **security-1, safety-1/2/6/7, privacy-1, notifications-3, codequality-1/5, usability-11, resilience-2**
- WS `create_meetup` takes `stage_id` from client, injects an **unmoderated** `meetup_invite` message into ANY room with no membership/read-only check (security-1, safety-6).
- title/note/label never moderated; no `check_ban_mute` → muted/banned users create meetups + broadcast (safety-1/2).
- Meetup content bypasses strike system (safety-7).
- REST vs WS creation diverge (REST posts no invite/broadcast) (codequality-1).
- "Create Meetup" offered in DMs → plaintext card bypasses E2EE, lock icon lies (usability-11).

### H2 — Meetup location + attendee identity exposed to any authenticated user
Perspectives: **security-2, safety-3/4, privacy-1/2/3/4/6**
- `GET /meetups` and `GET /meetups/{id}` return exact GPS + full attendee names to any logged-in user, no membership gate.
- `meetup_created` WS broadcast ships GPS to whole room/main room (privacy-1).
- Full-precision GPS stored (no rounding) (privacy-4).
- Block feature never checked in meetup paths (safety-4).
- `get_message_context` missing meetup access gate (codequality-3) — related read-path leak.

### H3 — Real-time attendee sync fully broken
Perspectives: **usability-5/6, performance-5, codequality-2, ui-3, resilience-5/6**
- UI join/leave calls REST only; REST never broadcasts `meetup_updated`.
- Client handler queries `.meetup-join-btn` — a class that does not exist (real class `.meetup-join`).
- No `meetup_created` client handler; no reconnect resync.

### H4 — join/leave has no existence check → IntegrityError → 500 / whole-socket teardown
Perspectives: **security-4, datamodel-6, performance-4, codequality-4, resilience-7**
- `join_meetup` INSERT relies on FK; bogus/expired id raises `sqlite3.IntegrityError`.
- WS path: unhandled → outer except → disconnects entire WS connection.
- Race: 30s purge deletes meetup between list and join.

### H5 — Meetup push/badges fundamentally non-functional in production
Perspectives: **notifications-1/2/3, codequality-8**
- No `room_memberships` row ever created for meetup rooms; `get_unread_counts` has no meetup branch → `_do_send_push` always bails (count 0).
- `meetup_card` vs `meetup_invite` type typo → blank preview text.
- Green tests mask it (fixture hand-inserts room_memberships).

### H6 — Meetup teardown duplicated 3x; no FK rooms→meetups; stale manager state
Perspectives: **datamodel-1/4/5, codequality-7**
- delete_meetup / delete_user / purge_expired_meetups each hand-roll teardown; diverged.
- admin_delete_meetup + delete_user leave stale `manager.rooms` state.
- purge re-filters by timestamp not captured ids (orphan race, datamodel-4 speculative).

### H7 — label/note/lat/lng collected but never displayed; no map link
Perspectives: **completeness-1/3, usability (implied)**

### H8 — Bell "Get notified"/"Mute" actually RSVPs (attendance conflation)
Perspectives: **usability-4/7, ui-7**

### H9 — No creator cancel/edit of meetup
Perspectives: **completeness-4/5, usability-7**

### H10 — No future-time validation; multi-day date not shown; timezone naive accepted
Perspectives: **usability-2/3, datamodel-2, privacy-7**

## LEADS (speculative → deeper problems)
- usability-8: sending to a just-deleted meetup room_id — orphaned rows? untraced.
- datamodel-4: purge re-filter-by-timestamp orphan race.
- privacy-7: unbounded meetup_time future.

## BLIND SPOTS (complex, few findings)
- **Meetup lifecycle under concurrency + reconnect** — the full state machine (create → RSVP → expire/purge/delete → open-room-during-expiry) is only lightly touched. Interaction of purge with live sockets, manager state, and an actively-open room is under-examined.
- **E2EE interaction** — only usability-11 touched meetup-in-DM; the encryption boundary around meetup invites is thin.

## Deep-dive targets selected (Phase 3)
- **DEEP-A (HOTSPOT)**: the meetup CREATE write path (WS + REST) — exhaustive authz/moderation/abuse/E2EE audit.
- **DEEP-B (HOTSPOT)**: meetup READ / data-exposure & access-control across ALL read paths + broadcasts + admin.
- **DEEP-C (HOTSPOT/BLIND SPOT)**: meetup LIFECYCLE — RSVP + expiry/purge/delete + concurrency + reconnect + real-time sync.
- **ROOT (cross-cutting)**: systemic root causes across all 100 findings.
- **RED (adversarial)**: location involved → attacker/harasser targeting location exposure + meetup abuse.
