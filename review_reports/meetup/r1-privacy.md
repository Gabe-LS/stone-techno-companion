- ID: privacy-1
- Severity: critical
- Confidence: certain
- Location: server/chat_ws.py:1760-1832 (`create_meetup` handler, `meetup_created` broadcast at 1829-1832); server/chat_db.py:1253-1263 (`create_meetup` return dict includes `location_lat`/`location_lng`/`location_label`/`note`)
- Finding: When a meetup is created, the full meetup object — including the creator's exact GPS coordinates and free-text note — is broadcast via the `meetup_created` WS event to every currently-connected client in the target room. If no `stage_id` is given, `broadcast_room` falls back to the main/general room, which is `auto_join` for all users. This means precise real-world location of a natural person is pushed to the entire online user base of the event, unsolicited, before a single person has RSVP'd or is even shown as an "attendee." The separate `meetup_invite` message card (which IS rendered to the room) correctly omits location — but the parallel `meetup_created` event does not, so the leak happens silently in the WS payload regardless of what the UI displays.
- Recommendation: Strip `location_lat`/`location_lng`/`location_label`/`note` from the `meetup_created` broadcast payload (and from any other room-wide broadcast). Only ever return that data via `GET /meetups/{id}` after verifying the requester is an attendee (see privacy-2), or once a client has explicitly joined.
- Effort: S
- Risk of change: low

- ID: privacy-2
- Severity: critical
- Confidence: certain
- Location: server/chat_api.py:1159-1230 (`GET /chat/api/meetups`, `GET /chat/api/meetups/{meetup_id}`)
- Finding: Both endpoints return `location_lat`/`location_lng`/`location_label`/`note` (plus the full attendee list with display names) to **any authenticated user**, with no check that the caller is the creator or an attendee. `GET /meetups` lists every active meetup for the event with coordinates included by default (not gated behind `is_going`), and `GET /meetups/{meetup_id}` has no membership check at all — knowing/guessing a meetup UUID (e.g. from the `/chat/m/{id}` URL shared out-of-band, or brute-forcing since IDs are also referenced in messages) is sufficient to pull a stranger's exact location. This directly contradicts the feature's apparent intent (join-to-see-location) and is a data minimization / access control failure under GDPR Art. 5(1)(c)/25.
- Recommendation: Omit `location_lat`/`location_lng`/`location_label`/`note` from the `/meetups` list response entirely (the list view doesn't render them — see privacy-3). For `/meetups/{id}`, only include location fields in the response when `user_id` is in `meetup_attendees` for that meetup (return the rest of the fields regardless, so non-attendees can still see title/time/attendee_count to decide whether to join).
- Effort: S
- Risk of change: low

- ID: privacy-3
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:2224 (`setTimeout(() => loadMeetupJoinState(mid), 0)` inside the `meetup_invite` card renderer), 3183-3197 (`loadMeetupJoinState`)
- Finding: Every time a `meetup_invite` card renders in any room (which happens for every member who scrolls past it, whether they intend to join or not), the client automatically fetches `GET /meetups/{id}` just to compute a boolean "joined?" state and an attendee count. Because of privacy-2, this silently pulls the creator's exact GPS into the browser's memory/network log of every room viewer, with zero user interaction and no indication to the user that this happened. Even after privacy-2 is fixed server-side, this call pattern is worth flagging because it establishes a habit of over-fetching sensitive fields for a trivial UI need.
- Recommendation: Once GET /meetups/{id} is gated per privacy-2, this becomes low-risk automatically. Additionally, consider a lighter-weight endpoint/response (e.g. `{joined: bool, attendee_count: int}`) for this specific polling path so it never has the shape to carry location even by future accident.
- Effort: S
- Risk of change: low

- ID: privacy-4
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:3266-3274 (`useMeetupGPS`); server/chat_db.py:146-159 (`location_lat`/`location_lng REAL`, no rounding anywhere in the write path)
- Finding: `useMeetupGPS` captures `pos.coords.latitude`/`longitude` at full device precision (the `.toFixed(4)` is only used for the display label text, not for what's actually stored in `_meetupCoords` and sent to the server). Full-precision GPS (sub-meter, sometimes sub-centimeter with modern phones) is stored and later exposed to every attendee (and currently to every user, per privacy-2). A festival "meet here" feature doesn't need this precision — coarser coordinates (e.g. 4-5 decimal places, ~10m) are sufficient for humans to find each other and reduce the blast radius of the over-exposure bugs above (data minimization, GDPR Art. 5(1)(c)).
- Recommendation: Round lat/lng to a fixed precision (e.g. 4 decimal places) either client-side before sending or server-side in `create_meetup` before the INSERT.
- Effort: S
- Risk of change: low

- ID: privacy-5
- Severity: medium
- Confidence: likely
- Location: server/chat/chat.html:3242-3273 (meetup modal location UI, `useMeetupGPS`)
- Finding: The only consent gate before sharing precise location is the browser's generic "Allow this site to know your location?" permission prompt, which says nothing about the fact that the coordinates will be attached to a meetup and shown to other attendees (and, per privacy-1/2, currently to the whole room/all users). This falls short of GDPR's informed-consent/transparency bar (Art. 5(1)(a), Art. 13) for what is effectively real-time physical location disclosure of a person to other individuals, not just to the service provider.
- Recommendation: Add explicit in-product copy at the "📍 GPS" action (or a one-time first-use dialog) stating who will see this location (e.g. "This will be visible to everyone who joins your meetup") before invoking `navigator.geolocation`.
- Effort: S
- Risk of change: low

- ID: privacy-6
- Severity: low
- Confidence: certain
- Location: server/chat_api.py:1177-1194, 1214-1224 (`get_active_meetups`/`get_meetup`+attendee list built into both endpoints)
- Finding: Independent of the location leak, the full attendee list (real display names + "who's going") for every active meetup is returned to any authenticated user via `GET /meetups`/`GET /meetups/{id}`, not just to attendees. This discloses participation/social data (who is planning to meet whom, when) beyond what's needed for a non-member to decide whether to join (a count would suffice).
- Recommendation: Return only `attendee_count` to non-attendees; include the named attendee list only for the creator/attendees (or in the room's member list once joined).
- Effort: S
- Risk of change: low

- ID: privacy-7
- Severity: low
- Confidence: speculative
- Location: server/chat_api.py:2189-2194 (`meetup_ttl_minutes` bounds 1-43200); server/chat_db.py:1219-1224 (`expires_at = meetup_time + meetup_ttl`)
- Finding: There's no upper bound on how far in the future `meetup_time` itself can be set, and the admin-configurable TTL can extend retention up to 30 days past the meetup time. Combined, a meetup (and the creator's exact location) can be created and persist in the DB for a long time before any purge occurs, well beyond what's needed for a festival-duration event.
- Recommendation: Consider clamping `meetup_time` to a sane window relative to the event's start/end dates (already available on the `events` table), so location data can't be scheduled to live far outside the event.
- Effort: S
- Risk of change: low

- ID: privacy-8
- Severity: low
- Confidence: certain (positive note, no action required)
- Location: server/chat_db.py:532-551 (`delete_user`), 1314-1361 (`delete_meetup`, `purge_expired_meetups`)
- Finding: Right-to-deletion and retention paths for the meetup's own data are handled correctly: `delete_user` explicitly tears down the room+attendees+meetup row for meetups the user created (compensating for the missing `rooms → meetups` FK), and `meetup_attendees`/`messages` rows for meetups the user merely attended cascade automatically via `ON DELETE CASCADE`. `purge_expired_meetups` deletes the room, messages, attendee rows, and the meetup row (including the location columns) together, and admin's `get_all_meetups` deliberately omits lat/lng (only exposes `location_label`) — good minimization precedent that the two endpoints in privacy-2 should follow. No action needed here, but noting it so the fixes above aren't mistaken for indicating a broader deletion problem.
- Effort: S
- Risk of change: low
