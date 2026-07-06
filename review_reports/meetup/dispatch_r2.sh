#!/bin/bash
# Phase 3 deep-dive — 5 read-only agents.
cd "/Users/gabrielelosurdo/Documents/Developer/Scripts/Personal/Stone Techno Companion" || exit 1
OUT=review_reports/meetup
M="claude-sonnet-5"
TOOLS="Read,Grep,Glob"
run() { claude -p --model "$M" --allowedTools "$TOOLS" > "$OUT/r2-$1.md" 2> "$OUT/r2-$1.err"; }

RO='You are strictly read-only. Do not modify, create, or delete any files.'
FMT='OUTPUT each finding EXACTLY in this format:

- ID: <prefix>-<n>
- Severity: critical / high / medium / low
- Confidence: certain / likely / speculative
- Location: file(s) and line(s) or component
- Finding: what is wrong and why it matters
- Recommendation: concrete change
- Effort: S / M / L
- Risk of change: low / medium / high

If something looks suspicious but you cannot fully trace it, report it as speculative rather than omitting it. Output ONLY findings.'

# --- DEEP-A: create write path (anti-anchored: NO prior findings) ---
DEEP_A="$RO
FIRST: Read review_reports/meetup/MANIFEST.md for the file map of the meet-up feature, then Read the relevant source (server/chat_ws.py create_meetup handler ~1748-1866, server/chat_api.py meetup endpoints ~1159-1287 and 2893-2913, server/chat_db.py create_meetup/join/leave ~1205-1360, server/chat/chat.html meetup modal/submit ~3180-3330 and card render ~2212-2224).

MANDATE: Audit the meetup CREATION write path EXHAUSTIVELY — both the WebSocket create_meetup event and the REST POST /meetups endpoint, and everything they trigger (invite card message, broadcasts, room creation, attendee insert). For every actor (normal user, muted user, banned user, blocked user, non-member of the target room, admin), every input field (title, note, label, lat, lng, stage_id, meetup_time), and every side effect: is it authorized, validated, moderated, rate-limited, and consistent between the two creation paths? Consider content moderation, ban/mute enforcement, the E2EE/DM boundary, injection/XSS, room-membership and read-only enforcement on the injected invite card, and what a malicious stage_id or meetup_time can do. Do not assume anything is safe because it is elsewhere — trace each guard yourself. Prefix finding IDs DEEP-A-.
$FMT"

# --- DEEP-B: read/exposure & access control (anti-anchored) ---
DEEP_B="$RO
FIRST: Read review_reports/meetup/MANIFEST.md, then Read the meetup READ paths: server/chat_api.py GET /meetups + GET /meetups/{id} (~1159-1230), the meetup room-access checks (~1034,1072-1077,1094-1117,1143-1148), admin GET/DELETE /admin/meetups (~2893-2913); server/chat_ws.py meetup_created/updated/expired broadcasts (~1825-1866,2195-2205) and the presence/report/history meetup gates; server/chat_db.py get_active_meetups/get_meetup_attendees/get_all_meetups (~1283-1312); server/chat/chat.html rendering of meetup data.

MANDATE: Audit every path by which meetup DATA leaves the server EXHAUSTIVELY — REST responses, WS broadcasts, admin views, permalink/message-context. For the creator GPS coordinates (location_lat/lng), the location_label, the note, and the attendee identities: exactly who can read each field, and is that gated on membership/attendance/creator/admin? Trace whether a non-attendee, a non-member of the origin room, a blocked user, or a stranger who guesses a meetup UUID can obtain location or attendee identity. Consider data minimization (GPS precision), enumeration, and consistency of access gates across sibling endpoints. Prefix finding IDs DEEP-B-.
$FMT"

# --- DEEP-C: lifecycle + concurrency + reconnect (anti-anchored, targets blind spot) ---
DEEP_C="$RO
FIRST: Read review_reports/meetup/MANIFEST.md, then Read the meetup LIFECYCLE code: server/chat_db.py create_meetup/join_meetup/leave_meetup/delete_meetup/purge_expired_meetups/delete_user (~532-547,1205-1361), server/chat_ws.py join/leave handlers + purge_loop (~1834-1866,2157-2262), server/chat_api.py join/leave/admin-delete, server/chat/chat.html meetup_updated/meetup_expired handlers + reconnect/onopen (~1494-1509,1059-1080) and toggleMeetupJoin/loadMeetupJoinState.

MANDATE: Audit the FULL meetup lifecycle EXHAUSTIVELY: every state transition (created, RSVP join/leave, time-passed, expired/purged, admin-deleted, creator-deleted), every failure mode, and every CONCURRENT scenario. Specifically: what happens when the 30s purge deletes a meetup while a user is joining, viewing its room, or sending into it; whether purge/delete can orphan a room, messages, attendees, or in-memory manager state; whether captured-id vs re-query-by-timestamp in purge is safe; what a client sees across a disconnect/reconnect gap for join counts and expiry; whether sending to a just-deleted meetup room inserts orphaned rows or errors the socket; TTL/expires_at derivation and timezone handling. Enumerate integrity violations the schema permits. Prefix finding IDs DEEP-C-.
$FMT"

# --- ROOT: cross-cutting (RECEIVES all Round 1 findings) ---
ROOT="$RO
FIRST: Read ALL twelve Round-1 finding files: review_reports/meetup/r1-usability.md, r1-ui.md, r1-a11y.md, r1-completeness.md, r1-codequality.md, r1-notifications.md, r1-safety.md, r1-security.md, r1-privacy.md, r1-performance.md, r1-datamodel.md, r1-resilience.md. Also Read review_reports/meetup/MANIFEST.md for the code map.

MANDATE: Identify PATTERNS where multiple independent findings trace back to a single architectural/root cause. For each root cause, name the symptom finding IDs it subsumes, and propose ONE root-cause fix instead of many symptom patches. Look especially for: duplicated logic that drifted (creation paths, teardown paths, access-gate SQL, preview-text), a missing shared abstraction, a wrong default, or a data-flow field that is collected-but-dropped. Rank root causes by how many findings each collapses. Prefix finding IDs ROOT-.
$FMT"

# --- RED: adversarial on location (feature handles location sharing) ---
RED="$RO
FIRST: Read review_reports/meetup/MANIFEST.md, then Read the meetup location + attendee code paths (create/read/broadcast) across chat_ws.py, chat_api.py, chat_db.py, chat.html as mapped there.

MANDATE: Assume this feature has an EXPLOITABLE flaw that lets an attacker/harasser/stalker track or lure a specific festival-goer via meetups. Find it. Think like an attacker: how do I learn where a specific user (by display name) physically is or will be, without them knowing; how do I keep contacting/luring a victim who blocked or reported me; how do I bypass a mute/ban to keep creating meetups; how do I post an official-looking meetup into a room I cannot post in; how do I enumerate meetups/locations; how do I abuse the RSVP/attendee list to deanonymize who is meeting whom. Produce concrete attack chains (step by step) and the exact code that enables each. Prefix finding IDs RED-.
$FMT"

( run deep-a <<EOF
$DEEP_A
EOF
) &
( run deep-b <<EOF
$DEEP_B
EOF
) &
( run deep-c <<EOF
$DEEP_C
EOF
) &
( run root <<EOF
$ROOT
EOF
) &
( run red <<EOF
$RED
EOF
) &
wait
echo "ALL_R2_DONE"
