I have enough evidence now to write the findings.

- ID: completeness-1
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:3242-3290 (modal collects `label`/`note`), server/chat_ws.py:1767-1779 (invite payload), server/chat/chat.html:2212-2224 (`meetup_invite` card render), server/chat/chat.html:1851-1867 (`loadMeetups` list render)
- Finding: `location_label` and `note` are collected in the create-meetup modal and correctly persisted end-to-end into `meetups.location_label`/`meetups.note` (`create_meetup`, GET `/meetups`, GET `/meetups/{id}` all return them). But nothing in the frontend ever displays them: the WS `create_meetup` handler builds the invite card's `invite_content` with only `meetup_id`/`title`/`meetup_time` (chat_ws.py:1773-1779), so location/note aren't even present in the chat message that announces the meetup. The meetup list item (`loadMeetups`) renders only time + "N going". The meetup detail fetch (`loadMeetupJoinState`) reads `/meetups/{id}` but only uses `attendees`/count, discarding `location_label`/`note` from the response. A user who types a meeting spot ("Main bar area") or a note ("bring cash, no cards") into the modal has that information silently swallowed — nobody, including the creator's own invite card, ever sees it again.
- Recommendation: Include `location_label` and `note` in the `meetup_invite` message payload and render them in the card; also show them in the room-list entry and/or a meetup detail view.
- Effort: M
- Risk of change: low

- ID: completeness-2
- Severity: medium
- Confidence: certain
- Location: server/chat_api.py:1159-1230 (attendees already returned), server/chat/chat.html:1844-1897, 3183-3213
- Finding: Both `GET /meetups` and `GET /meetups/{id}` already return a full `attendees` array with `id`/`display_name`, but the frontend only ever renders a numeric "N going" count — there is no UI to see the actual list of who is attending a meetup (contrast with group rooms, which have a member list). Users can't tell if a friend already joined before deciding to go themselves.
- Recommendation: Add a lightweight attendee list (e.g. tap "N going" to expand names), reusing data already fetched.
- Effort: S
- Risk of change: low

- ID: completeness-3
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:2206-2211 (standalone `location` message renders a Google Maps link) vs. meetup flow which never surfaces `location_lat`/`location_lng`
- Finding: The generic "share my location" message type renders a clickable Google Maps link (`https://maps.google.com/?q=lat,lng`). Meetups store GPS coords the exact same way (`location_lat`/`location_lng` via `useMeetupGPS`), but the meetup invite card / list / detail view never render a map link for it — the coordinates are captured and persisted but functionally dead once submitted (same root cause as completeness-1: nothing downstream of `submitMeetup` reads `lat`/`lng`/`label` back out). Attendees have no way to actually navigate to a GPS-based meetup point from the app.
- Recommendation: When a meetup has `location_lat`/`location_lng`, render the same maps-link affordance used for shared-location messages in the invite card and/or meetup detail.
- Effort: S
- Risk of change: low

- ID: completeness-4
- Severity: high
- Confidence: certain
- Location: server/chat_api.py (no PUT/PATCH `/meetups/{id}`), server/chat_ws.py (no `update_meetup`/`edit_meetup` handler), server/chat_db.py (no `update_meetup` function)
- Finding: There is no way to edit a meetup after creation — not the title, not the time, not the location, not the note. A typo in the title, a time correction, or a changed meeting spot cannot be fixed; the only recourse is creating a brand-new meetup (fragmenting attendees/chat across two rooms) or asking an admin to delete it. For a real-time festival tool where plans shift constantly, this is a significant everyday-use gap.
- Recommendation: Add creator-only `PATCH /meetups/{id}` (+ WS `update_meetup` event) restricted to fields that don't require re-deriving `expires_at`/room TTL carefully (time changes need `expires_at` recomputation), broadcasting `meetup_updated` with the new fields to attendees.
- Effort: M
- Risk of change: medium

- ID: completeness-5
- Severity: high
- Confidence: certain
- Location: server/chat_api.py:1278-1286 (`leave_meetup_endpoint`, no ownership branch), server/chat_api.py:2903-2913 (admin-only `DELETE /admin/meetups/{id}`), server/chat_db.py:1314-1323 (`delete_meetup`)
- Finding: There is no creator-initiated cancel/delete for a meetup. The only "leave" path (`DELETE /chat/api/meetups/{id}/join`) removes the caller from `meetup_attendees` like any other attendee — for the creator this just makes them stop "going" to their own meetup while it stays fully active, still listed to everyone else, still joinable, until it naturally expires (`meetup_time` + up to 30 days per `meetup_ttl_minutes` bounds in chat_api.py:2193). The only way to actually remove a mistaken or cancelled meetup is `DELETE /chat/api/admin/meetups/{id}`, which requires an admin. A normal user who created a meetup by accident, picked the wrong date, or whose plans fell through has no way to take it down.
- Recommendation: Add a creator-only delete/cancel endpoint (mirroring `delete_meetup` but gated on `creator_id == user_id` rather than admin role), and surface a "Cancel meetup" action in the UI for the creator.
- Effort: S
- Risk of change: low

- ID: completeness-6
- Severity: low
- Confidence: certain
- Location: server/chat/chat.html:1844-1867 (`loadMeetups`)
- Finding: The meetup list is a flat, undifferentiated feed — no way to distinguish "meetups I created" from "meetups I joined" from "meetups I haven't joined yet," and no filter/section for either. `is_going`/`creator_id` are available (creator_id via `get_active_meetups`, though it's not even forwarded in the `/meetups` REST response — see completeness-1's pattern of dropped fields) but unused for grouping.
- Recommendation: Add a lightweight grouping or badge ("Hosting" vs "Going") using data already available server-side.
- Effort: S
- Risk of change: low

- ID: completeness-7
- Severity: medium
- Confidence: certain
- Location: server/chat_ws.py:1834-1867 (`join_meetup`/`leave_meetup` handlers)
- Finding: Joining or leaving a meetup only triggers `manager.broadcast_to_room(meetup_id, {"event": "meetup_updated", ...})` — a live WS broadcast that only reaches clients currently connected/subscribed to that specific meetup room. Unlike regular messages, no row is inserted into `messages` and `_send_push`/offline-push is never invoked (compare to the generic message path, which computes `_get_room_notification_targets` and pushes to offline/idle members). This means a meetup creator who isn't actively viewing the meetup room gets no notification — push or otherwise — when someone RSVPs "going." For a feature whose entire value is coordinating a real-world meetup, the organizer has no way to know attendance is building unless they keep the room open.
- Recommendation: Send a push (or at least an in-app toast/badge) to the creator (and optionally other attendees) when someone joins, respecting existing idle/offline push infrastructure.
- Effort: M
- Risk of change: low

- ID: completeness-8
- Severity: medium
- Confidence: certain
- Location: server/api.py (lineup push scheduler — no meetup awareness), server/chat_ws.py purge loop (2195-2205, only expiry, no pre-event reminder)
- Finding: No reminder notification exists before a meetup starts. The lineup's push scheduler (per CLAUDE.md, matches `timetable.json` slots) has nothing to do with meetups, and chat's only meetup-related background job is `purge_expired_meetups` (fires only after `expires_at`, i.e. after the meetup is already over). An attendee who joined a meetup happening in 3 hours gets no "starting soon" nudge — a core expectation for a scheduling tool.
- Recommendation: Add a reminder push (e.g. 15 min before `meetup_time`) to all attendees, analogous to the existing lineup set-reminder scheduler.
- Effort: M
- Risk of change: low

- ID: completeness-9
- Severity: low
- Confidence: certain
- Location: server/chat_db.py `meetups` schema (chat_db.py:146-159), `join_meetup` (1266-1273)
- Finding: No capacity/attendee cap exists anywhere in the schema, `create_meetup`, or `join_meetup` — anyone can join an unlimited number of times regardless of venue constraints (e.g. a small meetup point, a car with limited seats, a guided walk). This may be an intentional simplicity choice for a casual feature, but it's worth flagging as a plausible expectation gap for use cases like ride-shares or small-group activities.
- Recommendation: If desired, add an optional `max_attendees` column and reject `join_meetup` past capacity; otherwise document as an intentional non-goal.
- Effort: S
- Risk of change: low

- ID: completeness-10
- Severity: low
- Confidence: certain
- Location: server/chat_db.py:1291-1301 (`get_active_meetups`, ordered by `meetup_time`), server/chat_api.py:1159-1200 (`list_meetups` re-sorts by `last_message_at` descending)
- Finding: `get_active_meetups` orders chronologically by `meetup_time`, but `list_meetups` immediately re-sorts the result by `last_message_at` (most recent chat activity) descending, discarding the chronological order. This means a meetup happening in 10 minutes with no chat yet can be buried below an older/later meetup that happens to have recent banter. There's also no visual distinction between "upcoming" and "already started" meetups (a meetup remains in the "active" list from creation until `meetup_time + ttl`, so a meetup that already started an hour ago looks identical in the list to one 3 days out) — no "starting soon"/"in progress" indicator.
- Recommendation: Sort by `meetup_time` ascending (soonest first) by default, and/or add a status indicator (upcoming / happening now) computed client-side from `meetup_time` vs. now.
- Effort: S
- Risk of change: low
