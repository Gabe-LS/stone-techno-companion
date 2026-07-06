# Phase 4 — Triage & Implementation Plan

147 findings (100 Round-1 + 47 deep-dive). Deduplicated around root causes. Each item below
was **verified against the code by the orchestrator** before selection. Ordered so
root-cause/architectural fixes land before dependent smaller fixes.

## Verified anchors (orchestrator-confirmed)
- Auth enforced (`_get_user_from_cookie` → 401). GET /meetups + /meetups/{id} return lat/lng +
  attendee names with NO membership gate (chat_api.py:1159-1230). ✓
- `create_message` default `moderation_status="approved"` → invite card bypasses moderation. ✓
- WS `create_meetup` posts invite into client-supplied `stage_id` with no room/membership/read-only/ban check (chat_ws.py:1748-1832). ✓
- `check_ban_mute(db, user_id)` async helper exists, returns {allowed,reason,action}. ✓
- join/leave endpoints have no existence check (chat_api.py:1267-1287). ✓
- `rooms_changed` handler force-navigates any open meetup/DM room to main (chat.html:1424-1443; meetup/DM never in `rooms`). ✓
- `get_message_context` gates dm but not meetup (chat_api.py:1094-1117). ✓
- preview text checks `meetup_card`; messages created as `meetup_invite` → blank preview (chat_ws.py:115,1140 vs 1790,1820). ✓
- `purge_expired_meetups` final DELETE re-filters by timestamp not captured ids (chat_db.py:1354). ✓
- `delete_meetup` non-atomic (delete_room commits, then more deletes+commit) (chat_db.py:1314-1322). ✓
- label/note stored but never rendered; invite payload omits them (chat_ws.py:1773-1779). ✓

---

## IMPLEMENT — Group A: Security / Safety / Privacy (critical/high, verified)

**A1 — Ban/mute enforcement on meetup create + join/leave**
IDs: ROOT-6, DEEP-A-2, RED-2, safety-2. Files: chat_ws.py (create_meetup, join/leave handlers), chat_api.py (POST /meetups, join/leave endpoints).
Accept: a muted or banned user calling create_meetup / join / leave (WS or REST) is rejected with the standard mute/ban response; not-muted users unaffected. New test asserts muted user cannot create a meetup.

**A2 — Validate `stage_id` before posting invite card**
IDs: ROOT-9, DEEP-A-1/6/7, security-1, safety-6, RED-3. Files: chat_ws.py create_meetup.
Accept: invite card is posted only when `stage_id` names an existing room the sender is a member of, `is_read_only` is false, and type ∉ (dm, meetup); otherwise the meetup is still created but no invite is injected (or a scoped error is returned). Bogus stage_id no longer raises IntegrityError / drops the socket.

**A3 — Moderate meetup title/note/label on create**
IDs: ROOT-6, DEEP-A-3, safety-1, RED-5. Files: chat_ws.py + chat_api.py create paths (shared helper).
Accept: title/note/label pass the word filter before create commits; a blocklisted term is rejected with feedback (mirrors message rejection); clean input unaffected. (Word-filter layer at minimum; AI layers optional/deferred.)

**A4 — Gate location + attendee identity reads on attendance (single shaping fn)**
IDs: ROOT-1, DEEP-B-1/2/3, security-2, safety-3, privacy-1/2/6, RED-1. Files: chat_api.py (GET /meetups, GET /meetups/{id}), chat_ws.py (meetup_created broadcast), chat_db.py.
Accept: non-attendees receive only id/title/meetup_time/stage_id/attendee_count (no lat/lng/label/note/attendee names); attendees (in meetup_attendees) receive full object. `meetup_created` broadcast carries no lat/lng/note/creator_id. New test asserts non-attendee response omits coordinates.

**A5 — Meetup access gate in `get_message_context`**
IDs: ROOT-12, DEEP-B-4, codequality-3. Files: chat_api.py:1094-1117.
Accept: a non-attendee requesting a meetup-room message id gets 404, same as the dm branch.

**A6 — Block enforcement on meetup join + list**
IDs: RED-4, safety-4. Files: chat_api.py + chat_ws.py join, GET /meetups.
Accept: a user blocked by (or blocking) the creator cannot join that meetup and does not see it in their list. New test.

**A7 — GPS minimization: round to ~4 dp + validate range**
IDs: privacy-4, DEEP-B-5, DEEP-A-8. Files: chat_db.py create_meetup (or API boundary).
Accept: stored lat/lng rounded to 4 decimals; out-of-range/non-finite rejected.

## IMPLEMENT — Group B: High-impact bugs (verified)

**B1 — join/leave existence check → clean 404, no socket teardown**
IDs: ROOT-5, DEEP-C-1/2, security-4, performance-4, codequality-4, datamodel-6. Files: chat_db.py join/leave, chat_api.py endpoints, chat_ws.py handlers (per-event try/except).
Accept: joining a nonexistent/expired meetup returns 404 (REST) / scoped error (WS) and never raises IntegrityError or disconnects the socket. New test: POST /meetups/{bogus}/join → 404.

**B2 — Fix `rooms_changed` kicking meetup/DM rooms to main**
IDs: DEEP-C-3. Files: chat.html:1424-1443.
Accept: an open meetup or DM room is not force-navigated to main when `rooms_changed` fires for an unrelated room.

**B3 — Fix meetup preview text typo (`meetup_card`→`meetup_invite`)**
IDs: codequality-8, ROOT-14. Files: chat_ws.py:115,1140.
Accept: push/badge preview for a meetup invite renders "Shared a meetup", not blank. Test asserts preview text.

**B4 — Purge/delete integrity: delete by captured ids + atomic delete_meetup**
IDs: datamodel-4, DEEP-C-7, ROOT-7 (consolidation). Files: chat_db.py purge_expired_meetups, delete_meetup; delete_user calls delete_meetup.
Accept: purge deletes exactly the captured expired ids; delete_meetup is a single transaction; delete_user reuses delete_meetup. Existing purge/delete tests still pass.

**B5 — Reject past / timezone-naive meetup_time (+ far-future cap)**
IDs: ROOT-13, usability-2, DEEP-C-9, DEEP-A-5, datamodel-2. Files: chat_ws.py + chat_api.py create, chat.html submitMeetup.
Accept: server rejects meetup_time in the past, tz-naive, or beyond a sane future window; client shows "pick a future time". New test.

**B6 — Real-time attendee sync: fix selectors + REST broadcast + meetup_created handler**
IDs: ROOT-2, ui-3, usability-5/6, codequality-2, resilience-5. Files: chat.html (selectors `.meetup-join-btn`→`.meetup-join`, add meetup_created handler), chat_api.py join/leave (broadcast meetup_updated).
Accept: when a user joins/leaves, other connected viewers see the "N going" count update live; expired card's Join button is removed.

## IMPLEMENT — Group C: Low-risk, high-value UX/completeness

**C1 — Render location_label/note + map link in invite card; include in payload**
IDs: completeness-1/3, DEEP-A-10, H7. Files: chat_ws.py invite_content, chat.html card render.
Accept: a meetup created with a label/note shows them + a maps link on the card.

**C2 — Creator cancel meetup**
IDs: completeness-5, usability-7, ROOT-10(cancel). Files: chat_api.py (creator-gated delete), chat.html (Cancel action for creator).
Accept: creator can cancel their own meetup; non-creator/non-admin cannot.

**C3 — GPS button: replace emoji with SVG + timeout + loading state**
IDs: ui-2, usability-10, resilience-8. Files: chat.html useMeetupGPS + button.
Accept: no emoji; getCurrentPosition has a timeout; button shows locating state.

**C4 — Meetup modal accessibility**
IDs: a11y-1/2/3/4, ROOT-4. Files: chat.html modal.
Accept: role=dialog + aria-modal + aria-labelledby, field labels/aria, focus trap+return, list items keyboard-activatable.

**C5 — Card width overflow + room-name truncation**
IDs: ui-1, ui-6. Files: chat.html CSS.
Accept: meetup card/button shrink on narrow viewports; long titles truncate with ellipsis.

**C6 — Meetup create ack/error feedback (truthful success)**
IDs: ROOT-3, resilience-1/2/3, usability-1. Files: chat.html submitMeetup, chat_ws.py create_meetup (ack/error events).
Accept: "Meetup created!" shown only after server confirms; failure shows an error and keeps the modal.

---

## DEFER — for human decision (not implemented)
- **Schema: FK rooms↔meetups + rename `stage_id`→`origin_room_id`** (datamodel-1/3): migration, medium risk. (B4 does the low-risk consolidation without the schema change.)
- **Meetup edit** (completeness-4, ROOT-10 edit): new feature/product scope.
- **Bell RSVP semantics** (usability-4, ui-7, ROOT-11): decoupling notify-from-attendance is a product decision. (B6 fixes the sync; relabel/decouple deferred.)
- **Full meetup push wiring** (notifications-1/2/3, ROOT-8): choice between room_memberships insert vs get_unread_counts union affects badge behavior app-wide; medium risk, needs design sign-off. (B3 fixes the trivial preview typo only.)
- **Pre-meetup reminders, attendee list UI, capacity cap, hosting/going grouping, upcoming sort** (completeness-2/6/8/9/10, notifications-4, performance-*): enhancements, product scope.
- **Dedicated stricter meetup rate limit** (safety-7, RED-7, notifications-5): tuning decision.
- **admin_delete_meetup manager-state eviction / admin location detail view** (datamodel-5, DEEP-C-4, safety-8): admin polish.
- Assorted low/speculative perf + a11y items (performance-1/2/7/9, a11y-5..9, ui-4/8/9).
