- ID: usability-1
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3276-3296 (`submitMeetup`), server/chat/chat.html:1086-1088 (`wsSend`), server/chat_ws.py:1748-1832 (`create_meetup` handler)
- Finding: Meetup creation is entirely fire-and-forget. `wsSend()` silently no-ops if the socket isn't open (`if (ws && ws.readyState === 1) ws.send(...)`), and `submitMeetup()` unconditionally closes the modal and shows `showToast('Meetup created!')` regardless of whether the send happened or the server accepted it. Server-side, invalid input (empty title after trim can't happen client-side, but a malformed `meetup_time`, or `check_rate_limit` failure) causes the handler to silently `continue` with zero feedback — unlike `send_message`, which sends a `message_rejected` event back to the sender on the equivalent failures (chat_ws.py:1608-1613). A user on a flaky connection, mid-reconnect, or who hits the meetup rate limit gets a confident "Meetup created!" toast for a meetup that was never created, discovers this only when nobody shows up (or the room never appears), with no path to retry or diagnose.
- Recommendation: Have the server emit an ack/error event for `create_meetup` (mirroring `message_rejected`) and have the client await it before toasting success; if `wsSend` can't send (socket not open), show an explicit "not connected, try again" error instead of a generic success toast.
- Effort: M
- Risk of change: low

- ID: usability-2
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3215-3296 (meetup modal + `submitMeetup`), server/chat_ws.py:1756-1759, server/chat_db.py:1219-1224 (`create_meetup`), server/chat_db.py:1291-1301 (`get_active_meetups`)
- Finding: Nothing — client or server — validates that `meetup_time` is in the future. The date/time picker defaults to "now + 15 min rounded" but lets the user freely pick any past date, and the hour/minute `<select>` lists all 24 hours / all four quarter-hours with no filtering even when today's date is chosen (so picking today + an hour that already passed is a one-click mistake). If the resulting `meetup_time` (+ `expires_at = meetup_time + ttl`, default 60 min) ends up in the past, `get_active_meetups` (`WHERE expires_at > now`) excludes it immediately or within moments of creation — the meetup silently vanishes from the list the user was just shown a success toast for, with no explanation.
- Recommendation: Client-side, reject/clamp submission when `meetupDate <= new Date()` with a clear toast ("Pick a time in the future"); optionally hide past hours when today's date is selected. Server-side, reject `create_meetup` (and send feedback per usability-1) when `meetup_time` is not sufficiently in the future.
- Effort: S
- Risk of change: low

- ID: usability-3
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:1855 (`countParts`), server/chat/chat.html:2220 (`card-meta`), server/static/shared.js:28-30 (`fmtTime`)
- Finding: Every place a meetup's time is displayed (sidebar list row, invite card) uses `fmtTime()`, which renders only `hour:minute` — never the date. This is a multi-day festival app; a meetup created for tomorrow at 14:00 and one created for today at 14:00 render identically as "14:00". A user glancing at the meetup list has no way to tell which day a meetup is on without opening it (and even then, only `meetup_time`/`expires_at` ISO strings are available, not surfaced anywhere else in the UI either).
- Recommendation: Show a short date alongside the time wherever meetup time is displayed (e.g. "Fri 14:00", or "Today"/"Tomorrow" + time), especially since the festival CLAUDE.md context confirms multi-day events.
- Effort: S
- Risk of change: low

- ID: usability-4
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:1864 (bell button, aria-label "Get notified"/"Mute meetup"), server/chat/chat.html:1890-1897 (`_toggleMeetupGoing`), server/chat_api.py:1267-1286 (join/leave endpoints), server/chat_db.py:1266-1281
- Finding: The meetup list reuses the exact same "bell" affordance/labels as room notification toggles ("Get notified" / "Mute meetup"), but under the hood it calls `POST/DELETE /meetups/{id}/join`, which adds/removes the user from `meetup_attendees` — the same table that drives the public "N going" attendee count and attendee list shown to everyone (including in the invite card and to admins). A user who just wants to avoid missing chat updates for a meetup they're on the fence about is, without any indication, publicly RSVPing as "going" and joining the attendee list. Given the manifest explicitly flags GPS coordinates are shared to all attendees, this conflation means clicking what looks like a quiet "notify me" bell can expose the user's presumed attendance (and by extension exposes them to the creator's shared location) without them realizing they've "joined" anything.
- Recommendation: Either rename/relabel the meetup bell to reflect that it's an RSVP ("Join"/"Leave", not "Get notified"/"Mute"), or decouple attendance from notification: let users watch a meetup's chat without being added to `meetup_attendees`, with a separate explicit "I'm going" action for RSVP.
- Effort: M
- Risk of change: medium

- ID: usability-5
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:3199-3213 (`toggleMeetupJoin`), server/chat/chat.html:1890-1897 (`_toggleMeetupGoing`), server/chat_api.py:1267-1286, server/chat_ws.py:1834-1866 (`join_meetup`/`leave_meetup` WS handlers)
- Finding: Both actual UI paths for joining/leaving a meetup (`toggleMeetupJoin` used by the invite-card "Join" button, and `_toggleMeetupGoing` used by the sidebar bell) call the REST endpoints `POST/DELETE /chat/api/meetups/{id}/join` exclusively. Those REST handlers update the DB and return but never broadcast anything over WebSocket. The `meetup_updated` broadcast only exists in the WS `join_meetup`/`leave_meetup` event handlers (chat_ws.py:1834-1866), which the current client never sends. Net effect: when someone joins or leaves a meetup, every *other* connected client (viewing the same invite card, the meetup room, or the sidebar list) never gets a live update — the "N going" count and Join/Joined button state are frozen at whatever they were on last page load/reopen, silently understating or overstating real attendance until the user manually navigates away and back.
- Recommendation: Either have the REST join/leave endpoints also broadcast `meetup_updated` to the meetup room, or have the client send the WS `join_meetup`/`leave_meetup` events (which already broadcast) instead of/alongside the REST calls.
- Effort: S
- Risk of change: low

- ID: usability-6
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:1494-1505 (`meetup_updated` handler) vs. server/chat/chat.html:2223 (`class="meetup-join meetup-join-wide"`), server/chat/chat.html:105-115 (CSS class list)
- Finding: Independent of usability-5, even if a `meetup_updated` event were ever received, the handler looks for `` `[data-meetup-id="${data.meetup_id}"] .meetup-join-btn` `` — a class name that doesn't exist anywhere in the codebase. The actual button class is `meetup-join`/`meetup-join-wide` (chat.html:2223), and the "going" count element is a bare `.meetup-going` span with its own `data-meetup-id` attribute, not nested under the button's container the way the handler's selector for `.meetup-going` (line 1501) also assumes. This is dead/broken code that would silently no-op even after fixing usability-5.
- Recommendation: Fix the selectors to match the real DOM (`.meetup-join[data-meetup-id="..."]`, `.meetup-going[data-meetup-id="..."]`), matching the pattern already used correctly in `loadMeetupJoinState`.
- Effort: S
- Risk of change: low

- ID: usability-7
- Severity: medium
- Confidence: likely
- Location: server/chat/chat.html:2212-2224 (`isCreator` branch), server/chat_api.py (no creator-facing delete endpoint), server/chat_api.py:2903-2913 (admin-only delete)
- Finding: There is no self-service way for a meetup's creator to cancel or edit it. The only delete path is `DELETE /chat/api/admin/meetups/{id}`, gated to admins. If a user creates a meetup with a wrong time, wrong location, or simply changes plans, their only recourse is to wait out the full `meetup_time + ttl` window (which admins can configure up to 43200 minutes / 30 days, chat_api.py:2193) or ask an admin to intervene — there's no in-app affordance even hinting this is possible. Combined with usability-2 (no past-time validation), a mis-clicked date could leave a stale, uncancellable meetup visible for a long time.
- Recommendation: Add a creator-only "Cancel meetup" action (client-gated on `isCreator`, server-enforced) that calls something equivalent to `delete_meetup`.
- Effort: M
- Risk of change: low

- ID: usability-8
- Severity: medium
- Confidence: speculative
- Location: server/chat_ws.py:2195-2205 (`purge_expired_meetups` broadcast) vs. server/chat/chat.html:1506-1510 (`meetup_expired` handler), server/chat_db.py:1325-1361
- Finding: When a meetup expires, the server deletes its room and all its messages directly (chat_db.py:1350-1351) without going through the `messages_expired` flow used for normal message TTL purges. The client's `meetup_expired` handler only fades a `[data-meetup-id]` element in the sidebar/card — there is nothing that detects "I currently have this exact room open" and reacts (no banner, no forced navigation away, no clearing of the message pane). A user actively viewing an expiring meetup's chat keeps looking at now-orphaned messages with no indication the room is gone server-side. I did not trace what happens if they then try to send a message into the deleted room_id (whether it's silently dropped, errors, or — if FK enforcement is lenient — inserts orphaned rows), so flagging this final part as speculative, but the missing "your meetup just ended" in-room notice is confirmed by reading the handler.
- Recommendation: When `meetup_expired` fires for the currently-open room, show a clear banner/toast ("This meetup has ended") and disable/hide the composer (or route the user back to the room list), rather than leaving a silently-stale view.
- Effort: S
- Risk of change: low

- ID: usability-9
- Severity: low
- Confidence: certain
- Location: server/chat/chat.html:3183-3197 (`loadMeetupJoinState`), server/chat_api.py:1205-1213 (`GET /meetups/{id}` → 404 if gone)
- Finding: For an invite card whose meetup has already expired/been purged (message TTL for the invite itself is the *room's* TTL and can outlive the meetup room), `loadMeetupJoinState` calls `GET /chat/api/meetups/{id}`, which 404s, and the catch block only does `dbg(...)` — no UI change. The Join button is left showing its default "Join" text/state forever, with no visual cue the meetup is gone. Clicking it then calls `toggleMeetupJoin` → `POST /meetups/{id}/join`, which will fail against a non-existent meetup, surfacing whatever raw `e.message` the fetch wrapper produces via `showToast` — not a clear "This meetup has ended."
- Recommendation: On 404 in `loadMeetupJoinState`, replace the Join button with a disabled "Meetup ended" state instead of leaving default markup.
- Effort: S
- Risk of change: low

- ID: usability-10
- Severity: low
- Confidence: certain
- Location: server/chat/chat.html:3266-3274 (`useMeetupGPS`)
- Finding: Clicking the "📍 GPS" button gives no loading/pending feedback while `getCurrentPosition` resolves, and no timeout is passed (defaults to no timeout, can hang indefinitely on a slow/denied-but-not-yet-resolved fix). On a mediocre connection or borderline permission state, the button appears completely unresponsive, inviting repeated clicks. The denial/failure path does show a generic toast ("Could not get location"), which is reasonable, but doesn't distinguish permission-denied (actionable: check browser settings) from a timeout (actionable: retry).
- Recommendation: Add a brief in-button loading state (e.g. "Locating…") and pass an explicit `timeout` (e.g. 8-10s) to `getCurrentPosition`, with a distinct message for `PERMISSION_DENIED` vs. timeout/unavailable.
- Effort: S
- Risk of change: low

- ID: usability-11
- Severity: medium
- Confidence: likely
- Location: server/chat/chat.html:2039-2040 (`showMeetupBtn = !isMeetup`), server/chat_ws.py:1772-1824 (`create_meetup` posts `meetup_invite` via `create_message` when `stage_id` is set)
- Finding: "Create Meetup" is offered in the action menu for any non-meetup room, including DMs (`currentRoomType === 'dm'` also satisfies `!isMeetup`). Submitting from a DM sends `stage_id: currentRoom` (the DM room id), and the server's `create_meetup` handler unconditionally calls `create_message(db, stage_id, ..., "meetup_invite", invite_content, ...)` with a plain JSON payload — bypassing the E2EE envelope path entirely (`_is_e2ee_content` / client-side encryption used for normal `send_message`). The recipient's DM view still shows the "locked" header icon (set purely from room-level `_unencryptedRooms` state, chat.html:1966-1971) even though this specific card was stored and broadcast in plaintext. A user relying on the lock icon as a privacy signal for that conversation gets an inconsistent, unannounced exception.
- Recommendation: Either hide "Create Meetup" for DM rooms (simplest), or route the meetup invite content through the same E2EE envelope path used for other DM message types so the "locked" indicator stays accurate.
- Effort: S (hide button) / M (encrypt invite)
- Risk of change: low
