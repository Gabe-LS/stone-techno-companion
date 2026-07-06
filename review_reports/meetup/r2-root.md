- ID: ROOT-1
- Severity: critical
- Confidence: certain
- Location: server/chat_api.py:1159-1230 (`GET /meetups`, `GET /meetups/{id}`), server/chat_ws.py:1760-1832 (`meetup_created` broadcast, `create_meetup` invite content), server/chat/chat.html:2212-2224, 3183-3197 (invite card / `loadMeetupJoinState`)
- Finding: `location_lat`/`location_lng`/`location_label`/`note` and the full attendee roster were added to the `meetups` schema and threaded into every read path (`GET /meetups`, `GET /meetups/{id}`, the `meetup_created` WS broadcast) with no access-control layer ever built around them — any authenticated user, attendee or not, gets exact GPS + real names for every active meetup, and the `meetup_created` broadcast leaks it to the entire room before anyone RSVPs. At the same time, on the *legitimate* consumption side, nobody ever wired these same fields into the UI: the invite card, meetup list, and detail view all discard `location_label`/`note`/lat-lng and only ever show time + "N going". This is one underlying defect — the location/note fields were plumbed through the DB and API layer but no corresponding access-control-on-read or render-layer was ever completed — that manifests as both over-exposure to strangers (safety-3, security-2, privacy-1, privacy-2, privacy-6, safety-4, safety-8) and under-delivery to the people who actually need it (completeness-1, completeness-2, completeness-3).
- Recommendation: Add one `_meetup_view(meetup, requester_id)` shaping function used by every read path (list, detail, broadcast): non-attendees get title/time/attendee_count only; attendees (checked via `meetup_attendees`) get the full object including lat/lng/label/note. Use that same shaped object to finally render location/note in the invite card and detail view. Fold in block-list filtering (safety-4) and admin visibility of lat/lng for report investigation (safety-8) at the same call site so there's exactly one place that decides who sees meetup location data.
- Effort: M
- Risk of change: medium

- ID: ROOT-2
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3199-3213 (`toggleMeetupJoin`), 1890-1898 (`_toggleMeetupGoing`), 1494-1509 (`meetup_updated`/`meetup_expired` handlers) vs. 2223 (actual `.meetup-join`/`.meetup-join-wide` markup); server/chat_ws.py:1834-1866 (`join_meetup`/`leave_meetup` broadcasting handlers)
- Finding: Join/leave state sync is fully broken by two compounding defects that trace to the same event: the shipped client only ever calls the REST `POST/DELETE /meetups/{id}/join` endpoints, which never broadcast anything, while the WS `join_meetup`/`leave_meetup` handlers that *do* broadcast `meetup_updated` are dead code the client never invokes — and even if they were invoked, both client handlers query `.meetup-join-btn`, a class that doesn't exist anywhere in the rendered DOM (the real class is `.meetup-join`). No test exercises the WS event layer end-to-end, so this shipped undetected. The net effect is that attendee counts and join state never update live for any viewer, forcing the app to fall back to a workaround of re-fetching `GET /meetups/{id}` on every single card render (a poll-per-card pattern), and the meetup "bell"/notification affordance inherits the same dead broadcast path.
- Recommendation: Pick one transport (REST, since that's what ships) and have those endpoints broadcast `meetup_updated`/`meetup_created` to the room; fix both selectors to `.meetup-join`; delete or repurpose the now-redundant WS `join_meetup`/`leave_meetup` handlers. Once live updates work, remove the per-card polling fallback and add a `case 'meetup_created'` client handler plus a `loadMeetups()` reconciliation call on WS reconnect.
- Effort: M
- Risk of change: medium

- ID: ROOT-3
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3276-3296 (`submitMeetup`), 1086-1088 (`wsSend`); server/chat_ws.py:1748-1832 (`create_meetup` handler, silent `continue` on every failure); server/chat_api.py:1232-1264 (`POST /meetups`, has proper HTTP error semantics but is unused by the client)
- Finding: Meetup creation exists as two parallel, incomplete implementations. The WS path (the only one the client calls) is fire-and-forget: `wsSend` silently no-ops if the socket isn't open, and every server-side rejection (rate limit, bad title/time) just `continue`s with zero response — yet `submitMeetup` unconditionally closes the modal and toasts "Meetup created!" regardless of what actually happened. The REST path has the request/response semantics (`HTTPException` on failure, resolvable promise on success) needed to fix this, but it's dead from the frontend's perspective and — per ROOT-2 — doesn't even produce the invite-card/broadcast side effects the WS path does. Neither implementation alone is complete, and the client inherited the worse of the two.
- Recommendation: Converge on one creation path with both correct side effects (broadcast + invite card) and correct request/response semantics — either add ack/error events to the WS handler and gate the client's toast/modal-close on receiving one, or switch `submitMeetup` to `await` the REST endpoint and give that endpoint the same broadcast/invite-card behavior the WS handler has. Add a client-side submit-button disable to prevent duplicate submissions in the same pass.
- Effort: M
- Risk of change: low

- ID: ROOT-4
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3224-3263 (meetup modal, no `role="dialog"`/focus trap/`aria-labelledby`), 3233-3248 (unassociated labels, unlabeled hour/minute selects), 1858/2745 (`<li class="room-item" onclick=...>` with no `tabindex`/`role="button"`), 1644-1648 (tab bar with no `role="tablist"`), 3230 (`.modal-close` with no `aria-label`)
- Finding: The project's own CLAUDE.md documents a specific modal/interactive-element accessibility standard (`role="dialog"`, `aria-modal`, `aria-labelledby`, focus trap, focus-return; `tabindex="0" role="button"` + keyboard handlers on interactive rows) — but the meetup modal and meetup list rows implement none of it: no dialog semantics, no focus trap, no label/`for` associations (especially ambiguous on the hour/minute selects), click-only list items unreachable by keyboard, and an unlabeled `×` close button. This is a consistent, single-cause gap — the meetup feature was built without applying the documented pattern already established elsewhere in this file — rather than six unrelated oversights.
- Recommendation: Apply the documented modal pattern once to the meetup modal (dialog role, labelled title, focus trap/return) and the documented interactive-row pattern once to meetup `<li>` items (shared with the other room-list types if possible so it can't drift again); add `for`/`id` pairs or `aria-label` to all modal fields and the close button in the same pass.
- Effort: M
- Risk of change: low

- ID: ROOT-5
- Severity: high
- Confidence: certain
- Location: server/chat_db.py:1266-1272 (`join_meetup`, no existence check before `INSERT OR IGNORE`), server/chat_api.py:1267-1275 (no try/except), server/chat_ws.py:1333 (single outer `while True`), 2123-2126 (one outer `except Exception` wrapping the entire per-connection message loop)
- Finding: `join_meetup`/`leave_meetup` (and other event handlers) never check that a `meetup_id` still exists before writing, relying entirely on the `meetup_attendees → meetups` FK to reject bad/expired/deleted ids — which surfaces as an unhandled `sqlite3.IntegrityError`. Over REST this is an unhandled 500; over WS there is no per-event try/except at all, so the single outer handler catches it, logs, and falls through to `disconnect()` — tearing down the user's *entire* WebSocket connection (every room, not just the meetup action) for what is an expected, routine race against the 30s meetup-purge loop. This is one architectural gap (no defensive existence check + no per-event exception boundary) producing five distinct symptom reports.
- Recommendation: Add an explicit existence/active check before every `meetup_attendees` insert (return 404 / a scoped error event instead of relying on the FK failure), and wrap individual WS event branches — at minimum `create_meetup`/`join_meetup`/`leave_meetup` — in their own try/except that logs and replies to the sender, so one bad event can no longer take down the whole connection.
- Effort: M
- Risk of change: medium

- ID: ROOT-6
- Severity: critical
- Confidence: certain
- Location: server/chat_ws.py:1748-1832 (`create_meetup` handler, no `moderate_message`/`check_ban_mute` call), server/chat_api.py:1232-1264 (`POST /meetups`, same gap), server/chat_ws.py:1749 (`check_rate_limit`, generic 10/10s message bucket shared by create/join/leave)
- Finding: `create_meetup` (both WS and REST) was built without wiring in the standard message-safety plumbing every other user-generated-content path goes through: `title`/`note`/`location_label` are never passed through the word filter or OpenAI/GPT moderation pipeline, `check_ban_mute()` is never called (so a currently-muted or even banned-but-still-connected user can create/broadcast meetups), and creation shares the generic chat-message rate limit rather than a tighter, purpose-specific one — so up to 10 unmoderated, un-enforceable meetup broadcasts (each carrying GPS) can be fired in 10 seconds with zero strikes ever issued. This is a single "meetup creation skipped the enforcement layer" gap, not four independent oversights.
- Recommendation: Route `title`/`note`/`location_label` through the same moderation pipeline used for messages (reject/strike on fail), call `check_ban_mute` at the top of `create_meetup` and the join/leave endpoints, and give meetup creation its own stricter rate limit independent of the general chat bucket.
- Effort: M
- Risk of change: medium

- ID: ROOT-7
- Severity: high
- Confidence: certain
- Location: server/chat_db.py:532-551 (`delete_user`), 1314-1323 (`delete_meetup`), 1325-1361 (`purge_expired_meetups`); server/chat_ws.py:2195-2205 (purge-loop manager cleanup); server/chat_api.py:2903-2917 (`admin_delete_meetup`)
- Finding: "Delete a meetup and its room" is implemented three separate times with no shared function: `delete_user` re-implements `delete_meetup`'s body inline instead of calling it, and `purge_expired_meetups` reimplements it again with its own batching bug (the final `DELETE FROM meetups` re-filters by timestamp instead of using the already-captured id list, risking orphaned rooms on a slow purge tick) and its own non-batched per-row query pattern. Only the purge-loop caller remembers to evict the room from the in-memory WS manager state; `admin_delete_meetup` and `delete_user`'s teardown do not, leaving stale manager state for two of the three deletion paths. There is also no FK between `rooms` and `meetups` (joined only by matching UUID), which is why every caller has to remember to delete both rows manually in the first place.
- Recommendation: Make `delete_meetup(db, meetup_id)` (or a batched variant accepting a list of ids) the single teardown implementation, call it from `delete_user`'s loop and from `purge_expired_meetups`, and move the `manager.rooms`/`manager._room_meta` eviction into whatever wraps every caller so it can't be forgotten again. Consider adding `meetups.room_id REFERENCES rooms(id) ON DELETE CASCADE` to make the relationship enforceable rather than convention-only.
- Effort: M
- Risk of change: medium

- ID: ROOT-8
- Severity: critical
- Confidence: certain
- Location: server/chat_db.py:881-913 (`get_unread_counts`, no meetup branch), server/chat_db.py: `join_meetup`/`create_meetup` (never insert into `room_memberships`), server/api.py / server/chat_db.py `get_rooms_by_event` (filters `type IN ('stage','general')`), server/chat_ws.py:1772-1824 (invite card posted via bare `create_message` + broadcast, bypassing badge/push targeting)
- Finding: The meetup room type was added as a parallel structure that was never fully threaded through the shared room/membership/notification plumbing. Attendees are recorded only in `meetup_attendees`, never in `room_memberships` — so `get_unread_counts` (the gate `_do_send_push` checks before pushing) always evaluates to zero unread for meetup rooms, meaning **no chat message inside a meetup room can ever generate a push notification for anyone in production** (masked by a test fixture that manually inserts `room_memberships`, a step the real join flow never performs). Separately, `get_rooms_by_event` excludes `type='meetup'` entirely, so the client's `rooms` array never contains meetup rooms, and the header member-count always shows "0 members" for any open meetup. And when an invite card is posted, it goes through a bespoke `create_message` + `broadcast_to_room` call instead of the same targeting/push/badge path `send_message` uses, so even ordinary room members get no badge bump or push for a new meetup announcement. Three different call sites, one underlying cause: meetups were never fully registered as a first-class room type in the systems that track membership and drive notifications.
- Recommendation: Call `join_room_membership`/`leave_room_membership` symmetrically wherever `meetup_attendees` changes (create, join, leave), or make `get_unread_counts` meetup-aware via a `meetup_attendees` union; include `type='meetup'` (scoped appropriately) in whatever powers the client's room list/member-count lookup; and route the invite-card message through the same targeting/push/badge helper `send_message` uses instead of a bespoke broadcast.
- Effort: M
- Risk of change: medium

- ID: ROOT-9
- Severity: critical
- Confidence: certain
- Location: server/chat_ws.py:1748-1824 (`create_meetup` handler uses client-supplied `stage_id` directly as `create_message`'s target room), server/chat_db.py:146-159 (`meetups.stage_id TEXT`, no FK, misleadingly named — it actually stores a chat room id, not a lineup stage id)
- Finding: `create_meetup` takes `stage_id` verbatim from the client payload and uses it as the target room for an invite message and broadcast with zero validation: no check that the sender is a member of that room, no check the room isn't `is_read_only`, no check it's even a real room, and no moderation/mute enforcement on the write. This is every other room-write path's authorization boundary (membership + read-only + moderation), simply never implemented for this one code path. Consequences trace back to this single missing check: any user can inject an unmoderated message into any enumerable room including read-only announcement rooms (impersonating an official post), a muted user can keep effectively posting, and because the column is misnamed with no FK, a targeted room that's later deleted leaves the meetup permanently dangling with no detection.
- Recommendation: Before building/broadcasting the invite, validate `stage_id` exactly as `send_message` validates its room: fetch the room, reject if missing or `is_read_only`, require sender membership, and run `check_ban_mute`/moderation on the invite content. Rename the column to reflect what it holds (e.g. `origin_room_id`) and add a real FK.
- Effort: S
- Risk of change: low

- ID: ROOT-10
- Severity: high
- Confidence: certain
- Location: server/chat_api.py (no `PUT/PATCH /meetups/{id}`, no creator-scoped delete), server/chat_api.py:1278-1286 (`leave_meetup_endpoint`, no ownership branch), server/chat_api.py:2903-2913 (delete is admin-only)
- Finding: There is no self-service way for a creator to edit or cancel their own meetup. A typo, a wrong time, a changed location, or simply cancelled plans cannot be corrected or removed — the creator's only "leave" action just un-RSVPs them from their own still-fully-active meetup, and the only real deletion path requires an admin. Combined with the absence of any future-time validation on creation (a mis-clicked past date is one click away), a mistaken meetup can persist, fully visible and joinable, for up to the max TTL window with zero in-app recourse for the person who made it.
- Recommendation: Add a creator-only cancel/delete endpoint (mirroring `delete_meetup`, gated on `creator_id == user_id`) and a creator-only edit endpoint for at minimum title/time/location/note (recomputing `expires_at` on time changes), surfaced as explicit UI actions gated on `isCreator`.
- Effort: M
- Risk of change: medium

- ID: ROOT-11
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:1864 (bell, `aria-label` "Get notified"/"Mute meetup"), 1890-1898 (`_toggleMeetupGoing`), server/chat_api.py:1267-1286 (join/leave = RSVP, not notification mute)
- Finding: The meetup list reuses the exact same bell icon and "Get notified"/"Mute meetup" labels as the plain notification-mute toggle used for group rooms, but under the hood it calls the RSVP join/leave endpoint — adding or removing the user from `meetup_attendees`, the same table that drives the public attendee count/roster and (per ROOT-1) exposes the creator's GPS to fellow attendees. A user tapping what looks like a quiet "mute" bell is unknowingly publicly RSVPing or un-RSVPing, with real privacy/safety consequences given the location-sharing surface, and — independent of that mislabeling — the toggle doesn't even wire into the app's actual notification-delivery state (per ROOT-8, meetup pushes don't work at all yet, and no WS signal updates the current session's live notification-eligible-rooms either way).
- Recommendation: Give the meetup row a distinct, honestly-labeled affordance ("Join"/"Leave" or a checkmark, not a bell) instead of reusing the notification-mute metaphor for what is actually an RSVP action; if a separate "just watch, don't RSVP" notify-only option is wanted, build it as a genuinely distinct action decoupled from `meetup_attendees`.
- Effort: M
- Risk of change: medium

- ID: ROOT-12
- Severity: medium
- Confidence: certain
- Location: server/chat_ws.py:393-395, 537, 546, 1366-1369, 1399, 1598-1601, 1934-1936, 1969-1971, 2068-2070; server/chat_api.py:1072-1077, 1143-1148 (present), 1094-1117 (`get_message_context`, missing)
- Finding: The meetup-room access check (`SELECT 1 FROM meetup_attendees WHERE meetup_id = ? AND user_id = ?`, paired with the equivalent `dm_participants` check) is hand-copied at roughly eight separate call sites across `chat_ws.py`/`chat_api.py` instead of living behind one helper. This is precisely what let one instance get missed: `get_message_context` (backing the `/chat/msg/{id}` permalink) guards `dm` rooms but has no `meetup` branch, so any authenticated user can hit `GET /chat/api/messages/{id}` for a message inside a meetup room they never joined and learn its room_id/name/type — a case every sibling endpoint explicitly denies.
- Recommendation: Extract one `_check_room_access(db, room, user_id)` helper covering both `dm` and `meetup` types and use it at every gated endpoint including `get_message_context`, so a future access-semantics change (e.g. revoke-on-ban) only needs to happen once.
- Effort: S
- Risk of change: low

- ID: ROOT-13
- Severity: medium
- Confidence: likely
- Location: server/chat/chat.html:3242-3263 (date/hour/minute pickers, no future-time guard), server/chat_ws.py:1756-1759, server/chat_db.py:1219-1224 (`create_meetup`, bare `datetime.fromisoformat` with no tz-awareness check)
- Finding: No layer — client or server — validates that `meetup_time` is actually in the future, and the parsing accepts naive (non-timezone-aware) ISO strings without rejecting or normalizing them, even though `expires_at` comparisons elsewhere always use aware UTC timestamps. In practice the shipped client always sends an aware UTC string so this doesn't currently misfire, but a mis-picked past time silently produces a meetup that's already expired by the time `get_active_meetups` filters it out (vanishing right after the success toast), and any other caller (future client, script, malformed request) that sends a naive timestamp would silently corrupt the TTL math via lexicographic string comparison against an aware timestamp. Both are instances of the same gap: `meetup_time` is trusted as-is with no validation at the API boundary.
- Recommendation: At the `create_meetup` boundary, reject `meetup_time` values that are in the past or timezone-naive (or explicitly normalize naive input to UTC) before it ever reaches the DB insert; mirror the same guard client-side for immediate feedback.
- Effort: S
- Risk of change: low

- ID: ROOT-14
- Severity: medium
- Confidence: speculative
- Location: tests/test_chat_ws.py:945-968 (meetup coverage calls DB functions directly, never sends WS `create_meetup`/`join_meetup`/`leave_meetup` events through an actual socket), tests/test_chat_api.py `TestMeetups` (happy-path only)
- Finding: None of ROOT-2's broken selectors, ROOT-6's missing moderation, or the `meetup_card`/`meetup_invite` message-type mismatch (dead preview-text branch that always renders blank push/badge text for meetup invites) would have been caught by the existing suite, because the WS meetup event handlers are exercised only by calling their underlying DB helpers directly — never through an actual WS round-trip whose broadcast payload and resulting DOM/selector behavior get asserted. This is a single testing-strategy gap (unit tests of DB functions structurally cannot catch cross-layer wiring bugs) that plausibly explains why several independent symptom bugs above shipped and stayed unnoticed.
- Recommendation: Add a WS-level test that sends real `create_meetup`/`join_meetup`/`leave_meetup` socket events and asserts on the resulting broadcast payload and (for a browser-level check) the DOM update; add a REST test for joining a nonexistent meetup expecting 404; assert on `room["is_moderated"]` and on the meetup-invite push/badge preview text after creation.
- Effort: M
- Risk of change: low
