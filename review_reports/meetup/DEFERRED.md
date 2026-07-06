# Deferred findings (KNOWN and intentionally NOT fixed this pass)

Do NOT re-report these — they were consciously deferred for a human/product decision or as low-risk-of-change-vs-value:

- Schema FK rooms<->meetups + rename stage_id -> origin_room_id (datamodel-1, datamodel-3): DB migration, deferred. (Teardown consolidation was done in B4 without the schema change.)
- Meetup EDIT (change title/time/location after creation) (completeness-4, ROOT-10 edit half): new feature.
- Bell "Get notified"/"Mute" RSVP relabel / decouple notify-from-attendance (usability-4, usability-7 relabel, ui-7, ROOT-11): product decision. (B6 fixed the live sync; the label semantics were left as-is.)
- Full meetup PUSH wiring (room_memberships for meetup rooms / get_unread_counts meetup branch) (notifications-1, notifications-2, notifications-3, ROOT-8): medium risk, needs design decision. (B3 fixed only the blank preview-text typo.)
- Pre-meetup reminder push (completeness-8, notifications-4 partial): new scheduler feature.
- Attendee-list UI ("who's going"), hosting-vs-going grouping, capacity cap, upcoming/soonest sort (completeness-2, completeness-6, completeness-9, completeness-10, performance-1, performance-2): enhancements.
- Dedicated stricter meetup-creation rate limit (safety-7, RED-7, notifications-5): tuning decision. (Ban/mute + word-filter + past-time now gate creation.)
- admin_delete_meetup manager-state eviction + admin location detail view (datamodel-5, DEEP-C-4, safety-8): admin polish. (Creator-cancel C2 does evict + broadcast correctly.)
- Keyboard-activatable meetup LIST rows (a11y-4): belongs to the shared room-item render (rooms/dms/meetups), out of meetup scope. (Modal a11y was done in C4.)
- Meetup-in-DM plaintext / lock-icon inconsistency full fix (usability-11): A2 now blocks stage_id of type dm/meetup, which prevents the invite card being injected into a DM — the residual UX nuance is deferred.
- Assorted low/speculative perf + a11y items (performance-6/7/8/9, a11y-5/6/7/8/9, ui-4/8/9, resilience-4/6, notifications-6, privacy-7).

## What WAS implemented (20 commits, A1-A7, B1-B6, C1-C6 + tests)
See review_reports/meetup/PHASE4_PLAN.md groups A/B/C. Commit range: ad2f5b1..HEAD (each commit tagged with [item: finding IDs]).
