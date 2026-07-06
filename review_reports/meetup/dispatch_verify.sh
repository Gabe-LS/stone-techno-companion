#!/bin/bash
# Phase 6 verification — 3 read-only agents (may run read-only git/pytest via Bash).
cd "/Users/gabrielelosurdo/Documents/Developer/Scripts/Personal/Stone Techno Companion" || exit 1
OUT=review_reports/meetup
M="claude-sonnet-5"
TOOLS="Read,Grep,Glob,Bash"
run() { claude -p --model "$M" --allowedTools "$TOOLS" > "$OUT/v-$1.md" 2> "$OUT/v-$1.err"; }

RO='You are strictly READ-ONLY. Do not modify, create, or delete any files. You MAY use Bash ONLY for read-only inspection: `git log`, `git diff ad2f5b1..HEAD`, `git show`, `grep`, and `python -m pytest`. Never run a command that writes to the repo.

BEFORE reporting, read review_reports/meetup/DEFERRED.md — those items are KNOWN and intentionally deferred; do NOT re-report them. The 20 commits under review are the range ad2f5b1..HEAD (each tagged [item: finding IDs]).'

FMT='OUTPUT each issue you find EXACTLY in this format:
- ID: V-<n>
- Severity: critical / high / medium / low
- Confidence: certain / likely / speculative
- Location: file:line
- Finding: what is wrong (regression, incomplete fix, or scope creep)
- Recommendation: concrete change
If you find NOTHING actionable, say "NO NEW CRITICAL/HIGH ISSUES" explicitly and list what you verified. Be skeptical and concrete; verify against the actual code, do not speculate to fill a quota.'

REG="$RO
ROLE: REGRESSION CHECKER. Review the full diff of ad2f5b1..HEAD (\`git diff ad2f5b1..HEAD\`, and \`git log --oneline ad2f5b1..HEAD\`). Look for: (1) breakage of existing behavior (a changed function/endpoint/handler whose other callers now misbehave), (2) INCOMPLETE fixes (a fix that only covers one of several code paths — e.g. a guard added to the WS path but not REST, or vice versa), (3) SCOPE CREEP (edits unrelated to the meet-up feature). Pay special attention to: the changed return type of join_meetup (bool), the new _shape_meetup response shape (any client reader that expects the old fields for non-attendees), the new DELETE /meetups/{id} route vs DELETE /meetups/{id}/join (route shadowing), and the chat.html WS event handlers. Run \`python -m pytest tests/test_chat_db.py tests/test_chat_moderation.py tests/test_chat_ws.py tests/test_chat_api.py tests/test_chat_admin_roles.py -q\` and report any failure.
$FMT"

SEC="$RO
ROLE: SECURITY/SAFETY/PRIVACY RE-CHECKER. For EACH of these fixes, confirm it is ACTUALLY resolved and not merely patched around — trace the real code path, look for a bypass the fix missed:
 - A1 ban/mute on create + join (WS and REST): is there any meetup mutation path that still skips check_ban_mute?
 - A2 stage_id validation before invite card: can a crafted stage_id still inject a message into a read-only room, a DM room, or a room the sender is not in?
 - A3 word-filter on title/note/label: both create paths? any field unfiltered?
 - A4 location/attendee gating: is there ANY remaining path (list, detail, meetup_created broadcast, meetup_invite card, join/leave responses, admin) that leaks location_lat/lng or attendee names to a non-attendee?
 - A5 get_message_context meetup gate.
 - A6 block enforcement on join+list: any bypass (WS vs REST)?
 - B1 join nonexistent -> 404 not 500/socket-teardown.
Verify with grep/reads. Report only REAL gaps.
$FMT"

FE="$RO
ROLE: FRONTEND RE-CHECKER (server/chat/chat.html). Confirm the meet-up frontend changes are coherent and did not break existing flows. Check: (1) B2 rooms_changed guard doesn't break stage-room rename/redirect; (2) B6 meetup_updated/meetup_expired/meetup_created handlers use selectors that MATCH the actual rendered DOM (.meetup-join / .meetup-going with data-meetup-id), and meetup_created_ack/create_meetup_error handlers reference variables that exist; (3) C1 label/note render is XSS-safe (escaped) and the attendee-gated map link only appears with coordinates; (4) C2 cancelMeetup + Cancel button wired correctly; (5) C4 modal focus trap/restore is correct and does not trap the user; (6) C6 submitMeetup no longer toasts success unconditionally and the pending/timeout/ack logic is sound. Extract and syntax-check the inline JS if useful: python3 -c \"import re,os;s=open('server/chat/chat.html').read();b=max(re.findall(r'<script>(.*?)</script>',s,re.S),key=len);open(os.path.join(os.environ.get('TMPDIR','.'),'c.js'),'w').write(b)\" then \`node --check \$TMPDIR/c.js\`. Report real bugs only.
$FMT"

( run regression <<EOF
$REG
EOF
) &
( run security <<EOF
$SEC
EOF
) &
( run frontend <<EOF
$FE
EOF
) &
wait
echo "ALL_VERIFY_DONE"
