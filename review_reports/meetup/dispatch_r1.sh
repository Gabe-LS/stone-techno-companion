#!/bin/bash
# Phase 1 broad review — 12 parallel read-only agents.
cd "/Users/gabrielelosurdo/Documents/Developer/Scripts/Personal/Stone Techno Companion" || exit 1
OUT=review_reports/meetup
M="claude-sonnet-5"
TOOLS="Read,Grep,Glob"

run() { # $1=slug  $2=perspective-block (stdin)
  local slug="$1"
  claude -p --model "$M" --allowedTools "$TOOLS" > "$OUT/r1-$slug.md" 2> "$OUT/r1-$slug.err"
}

PRE='You are a code/product reviewer performing ONE perspective of a multi-perspective review of the "meet-up" feature of a festival chat app. You are strictly read-only. Do not modify, create, or delete any files.

FIRST STEP: Read the file review_reports/meetup/MANIFEST.md — it is the complete Phase-0 file manifest for the meet-up feature (do not rediscover the codebase). Then Read the specific source files/line ranges it points to that are relevant to YOUR perspective (server/chat_db.py, server/chat_api.py, server/chat_ws.py, server/chat/chat.html, server/chat/admin.html, tests/).

Analyze YOUR single assigned perspective DEEPLY, and also its impact on the feature as a whole — this is not a general review. Only report findings relevant to your perspective.

If something looks suspicious but you cannot fully trace it, report it as speculative rather than omitting it.

OUTPUT: a list of findings, each EXACTLY in this format (markdown):

- ID: <perspective>-<n>
- Severity: critical / high / medium / low
- Confidence: certain / likely / speculative
- Location: file(s) and line(s) or component
- Finding: what is wrong and why it matters
- Recommendation: concrete change
- Effort: S / M / L
- Risk of change: low / medium / high

Output ONLY the findings. Your assigned perspective is:'

p_usability='USABILITY & UX FLOWS (perspective id prefix: usability). Trace every user flow of the meet-up feature: creating a meetup (modal: title/date/time/GPS/note), the invite card, RSVP/join/leave, opening the meetup room, notifications toggle (bell), expiry. Look for friction, dead ends, missing error states, missing empty states, confusing states, silent failures, no feedback, timezone confusion in the date/time picker, what happens when a meetup is in the past, when GPS is denied, when creation fails, when the room expires while you are in it.'

p_ui='UI & VISUAL CONSISTENCY (prefix: ui). Review the meetup CSS (.msg-card.card-meetup, .meetup-join, .meetup-join-wide, .meetup-going), the meetup modal, the meetup list items in the tab and hamburger menu, the invite card render, the bell toggle. Look for layout issues, inconsistency with the rest of the chat design system, responsive/mobile behavior, overflow/truncation of long titles or labels, hardcoded widths (e.g. 260px), emoji use in UI (the project forbids emojis), inconsistent button styles.'

p_a11y='ACCESSIBILITY (prefix: a11y). Review the meetup modal, cards, buttons, bell toggle, join buttons for screen-reader support (labels, roles, aria), keyboard navigation (focus management, focus trap in modal, Escape to close, tab order), color contrast of the meetup card/pill colors, touch target sizes on mobile, the GPS button, date/time selects. Compare against the HTML standards the project documents (role=dialog, aria-modal, focus return, tab trapping).'

p_completeness='FEATURE COMPLETENESS & GAPS (prefix: completeness). Evaluate the meet-up feature against reasonable user expectations for a festival meetup tool. Look for missing capabilities: editing a meetup after creation, cancelling/deleting your own meetup (only admin delete exists?), viewing who is attending, a map for the shared location, seeing meetups you created vs joined, notifications when someone joins, reminders before the meetup time, capacity limits, past/upcoming distinction. Also verify the create path actually persists ALL collected fields end-to-end (modal collects label & note — are they sent and stored and displayed?).'

p_codequality='CODE QUALITY & ARCHITECTURE (prefix: codequality). Review the meetup code across chat_db.py, chat_api.py, chat_ws.py, chat.html for duplication (two creation paths REST + WS?), coupling, error handling, the no-FK rooms<->meetups relationship and its manual teardown in multiple places (delete_user, delete_meetup, purge_expired_meetups), consistency of validation between REST and WS paths, test coverage gaps. Identify fragile invariants.'

p_notifications='NOTIFICATIONS (prefix: notifications). Review meetup-related notifications: the invite card broadcast, meetup_created/updated/expired events, the bell "get notified" toggle (_toggleMeetupGoing / join_room), push notification body for meetups (chat.html ~3879/3885), whether joining a meetup subscribes you to its room notifications, whether attendees are notified of changes/cancellation/expiry, spam risk (a user mass-creating meetups blasting invite cards into a room), opt-out. Cross-reference the chat push idle/debounce logic.'

p_safety='SAFETY & ABUSE (prefix: safety). This feature shares a creator GPS location and gathers people to a physical place. Analyze harassment/abuse vectors: can a blocked/banned user still create meetups or invite a victim; is meetup content (title/note/label) moderated at all (meetup rooms — is_moderated?); can a creator lure attendees with a fake location; is there reporting for a meetup; can a user see the location without joining; does leaving/blocking remove you from a meetup; can someone create a meetup impersonating a stage/host. Consider stalking via repeated meetups.'

p_security='SECURITY (prefix: security). Audit authorization on EVERY meetup endpoint (REST GET/POST/join/leave, admin list/delete) and every WS meetup event (create/join/leave). Check: is auth required; IDOR (can any authenticated user join/leave/read any meetup by id, read attendee identities, read GPS of any meetup without membership); injection (SQL — are queries parameterized; XSS — title/note/label rendered in cards/modal, is it escaped in chat.html); missing rate limits; admin-only endpoints properly gated; the /chat/m/{id} route. Check the invite card content JSON and how it is rendered.'

p_privacy='PRIVACY & COMPLIANCE / GDPR (prefix: privacy). The feature stores precise GPS coordinates (lat/lng) of a real person and an attendee list. Analyze: consent for sharing precise location; data minimization (is exact GPS necessary, is it truncated); retention (TTL — is location purged with the meetup, are attendee records purged); right to deletion (does user delete remove their meetups AND their attendance in others meetups AND their location); does a meetup invite leak location to a whole room; is location shown to non-attendees; are coordinates logged. Check delete_user and purge paths.'

p_performance='PERFORMANCE & CONCURRENCY (prefix: performance). Review meetup queries for N+1 (loadMeetups then per-meetup loadMeetupJoinState calls; get_active_meetups attendee subquery), missing indexes (meetup_attendees lookups by user_id?), race conditions (two users joining simultaneously; join while meetup being purged; create_meetup vs concurrent purge; the room-id==meetup-id collision risk), the 60s purge loop cost, broadcast fan-out. Consider simultaneous RSVP and the non-atomic check-then-act patterns.'

p_datamodel='DATA MODEL & INTEGRITY (prefix: datamodel). Review the meetups + meetup_attendees + rooms relationship. The critical smell: rooms has NO foreign key to meetups (joined only by matching UUID), requiring manual teardown in delete_user, delete_meetup, purge_expired_meetups — any missed path orphans a room or leaves a dangling meetup. Check: constraints, cascade correctness, meetup_time stored as text with no timezone normalization (fromisoformat naive vs aware, TTL math), expires_at derivation, what happens to attendees when creator is deleted, stage_id has no FK to stages, migrations/backfill. Look for states that violate integrity.'

p_resilience='OFFLINE & FAILURE RESILIENCE (prefix: resilience). Review behavior on flaky networks and failures: submitMeetup uses wsSend (fire-and-forget over WebSocket) then immediately shows "Meetup created!" toast and closes the modal — is there any confirmation/rollback if the WS send fails or moderation/validation rejects it? Optimistic UI without rollback. What happens if create_meetup partially fails. Join/leave via REST — retries, error handling (toggleMeetupJoin catch). WS reconnect and missed meetup_created/expired events. Duplicate meetup creation on double-tap/retry. GPS timeout handling.'

( run usability      <<EOF
$PRE
$p_usability
EOF
) &
( run ui             <<EOF
$PRE
$p_ui
EOF
) &
( run a11y           <<EOF
$PRE
$p_a11y
EOF
) &
( run completeness   <<EOF
$PRE
$p_completeness
EOF
) &
( run codequality    <<EOF
$PRE
$p_codequality
EOF
) &
( run notifications  <<EOF
$PRE
$p_notifications
EOF
) &
( run safety         <<EOF
$PRE
$p_safety
EOF
) &
( run security       <<EOF
$PRE
$p_security
EOF
) &
( run privacy        <<EOF
$PRE
$p_privacy
EOF
) &
( run performance    <<EOF
$PRE
$p_performance
EOF
) &
( run datamodel      <<EOF
$PRE
$p_datamodel
EOF
) &
( run resilience     <<EOF
$PRE
$p_resilience
EOF
) &
wait
echo "ALL_R1_DONE"
