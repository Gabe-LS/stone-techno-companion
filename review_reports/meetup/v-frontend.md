All meetup-related backend tests pass. I've completed the frontend re-check across all six requested areas.

## Findings

- ID: V-1
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:4774-4781 (interacts with C4's `openMeetupModal`/`closeMeetupModal`, lines 3273-3428)
- Finding: The global `keydown` Escape handler closes any open modal via `document.querySelector('.modal-overlay').remove()` directly, bypassing each modal's own close function. For the meetup modal this skips `closeMeetupModal()` entirely, so pressing Escape does **not** restore focus to `modal._trigger` (the element that had focus before the modal opened) and doesn't reset `_meetupCoords`. C4's commit message claims "focus trap+return," but that return-of-focus guarantee only holds for the close button / backdrop-click paths, not the Escape path, which is the standard/expected way to dismiss a WAI-ARIA dialog. This is a pre-existing pattern shared by all modals (predates this PR, blame 2026-07-03), not a regression introduced by C4 — but C4 didn't close the gap for the modal it was specifically fixing for accessibility.
- Recommendation: In the global Escape handler, special-case `#meetup-modal` (and ideally all modals with a `._trigger`/custom close function) to call `closeMeetupModal()` instead of `modal.remove()`, so focus restoration is consistent regardless of dismissal method.

Everything else checked out:
- **B2** (`rooms_changed` guard, line 1432): `currentRoomType !== 'dm' && currentRoomType !== 'meetup'` correctly scopes the skip to DM/meetup rooms only; stage-room rename (line 1439-1442) and not-found→redirect-to-main logic are untouched and still execute for stage rooms.
- **B6**: `meetup_updated`/`meetup_expired`/`meetup_created` selectors (`.meetup-join[data-meetup-id]`, `.meetup-going[data-meetup-id]`) match the actual rendered DOM at lines 2246/2253 exactly. `meetup_created_ack`/`create_meetup_error` reference `_meetupPending`, `_meetupAckTimer`, `closeMeetupModal`, `_resetMeetupSubmitBtn` — all defined, and event names match the server (`server/chat_ws.py:1785,1794,1871,1882`).
- **C1**: label/note rendered via `escapeHtml()` (lines 2248, 2250) — XSS-safe. Map link (line 3230-3242) only appended when `data.location_lat/lng` are present, which the server only returns to attendees per the inline comment — confirmed gated correctly.
- **C2**: `cancelMeetup` (line 3262) calls `DELETE /meetups/{id}`, and the server broadcasts `meetup_expired` on manual delete (`chat_api.py:1368`), which the existing B6 handler already dims/removes correctly. Wired correctly.
- **C4**: Tab-trap keydown handler (lines 3316-3323) cycles correctly between first/last focusable elements. Trigger capture/restore (lines 3275, 3314, 3424) is correct for the close-button/backdrop-click paths — only the Escape-key path is uncovered (V-1).
- **C6**: `submitMeetup` (lines 3352-3383) no longer toasts success synchronously; success only fires from `meetup_created_ack`, error/timeout correctly reset `_meetupPending`, clear the timer, and re-enable the submit button. Sound.
- `node --check` on the extracted inline `<script>` block passed with no syntax errors.
- Meetup-related backend tests (`test_chat_api.py`, `test_chat_ws.py -k meetup`) all pass (13 passed).
- Nothing from `DEFERRED.md` was re-reported.
