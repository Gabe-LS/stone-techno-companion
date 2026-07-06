# Meet-up Feature — File Manifest (Phase 0 Recon)

The "meet-up" feature lets a chat user create a real-world meetup (title, date/time,
optional GPS location + label, optional note) tied optionally to a stage/room. It creates
a dedicated meetup chat room, an attendee list (RSVP / "going"), sends an invite card into
the originating room, notifies attendees, and auto-expires the meetup + room + messages a
configurable number of minutes after the meetup time.

## Data model (server/chat_db.py)
- `meetups` table (chat_db.py:146-159): id (uuid PK), creator_id (FK users ON DELETE CASCADE),
  stage_id (TEXT, nullable, NO FK), title, location_lat REAL, location_lng REAL, location_label,
  meetup_time TEXT (ISO), note, created_at, expires_at. Index idx_meetups_expires on expires_at.
- `meetup_attendees` table (chat_db.py:161-166): meetup_id (FK meetups CASCADE), user_id (FK users
  CASCADE), joined_at, PK(meetup_id, user_id).
- The meetup's chat room lives in `rooms` with type='meetup' and **room id == meetup id** (joined
  only by matching UUID in app code — rooms has NO FK to meetups).
- Setting default: chat_settings 'meetup_ttl_minutes' = '60' (chat_db.py:257).

## DB functions (server/chat_db.py)
- create_meetup (1205-1264): inserts meetup, creates room (ttl=meetup_ttl), adds creator as attendee.
  expires_at = meetup_time + meetup_ttl_minutes.
- join_meetup (1266-1273), leave_meetup (1275-1281)
- get_meetup_attendees (1283-1289), get_active_meetups (1291-1301), get_all_meetups (1303-1312, admin)
- delete_meetup (1314-1323): deletes room + attendees + meetup row
- purge_expired_meetups (1325-1355): finds expired, deletes room/messages/attendees/meetup
- delete_user (533-547): manually tears down user's meetup rooms before user row deleted (no FK rooms->meetups)
- wipe / table list (2008-2009)

## REST API (server/chat_api.py, prefix /chat/api)
- GET  /meetups (1159-1203): list active meetups (optional stage_id filter)
- GET  /meetups/{meetup_id} (1205-1230): meetup detail + attendees
- POST /meetups (1232-1264): create (also creatable via WS); validates title + meetup_time ISO
- POST /meetups/{meetup_id}/join (1267-1276)
- DELETE /meetups/{meetup_id}/join (1278-1287): leave
- Room access gating for meetup rooms (1034, 1072-1075, 1143-1146): membership via meetup_attendees
- Admin: GET /admin/meetups (2893-2900), DELETE /admin/meetups/{id} (2903-2913)
- Route /chat/m/{meetup_id} serves chat.html (2957-2961)
- meetup_ttl_minutes settings bounds (2193): ("60", 1, 43200)
- Room edit/delete guards reject dm/meetup (2606-2607, 2734-2735)

## WebSocket (server/chat_ws.py)
- Room-access / membership checks for meetup rooms (393-395, 537, 546, 1366-1369, 1399, 1598-1601,
  1934-1936, 1969-1971, 2068-2070)
- create_meetup event handler (1748-1832): rate-limited; builds meetup via create_meetup; if stage_id,
  posts a 'meetup_invite' card message into that room and broadcasts; broadcasts meetup_created to
  stage room or main room. Reads lat/lng/label/note from the WS payload (keys: lat, lng, label, note).
- join_meetup (1834-1849) / leave_meetup (1851-1865) handlers -> broadcast meetup_updated
- allowed events list includes create/join/leave_meetup (2115-2117)
- preview text for meetup_card / meetup_invite = "Shared a meetup" (115, 1140-1141)
- purge loop: purge_expired_meetups -> broadcast meetup_expired, drop room from manager (2195-2205)

## Frontend (server/chat/chat.html)
- CSS cards: .msg-card.card-meetup, .meetup-join, .meetup-join-wide, .meetup-going (105-115)
- WS handlers: meetup_updated (1494-1505), meetup_expired (1506-1509)
- Tabs / menu sections "meetups" (1646, 1672, 1819-1831, 2690-2691, 2721-2750, 2803-2806)
- loadMeetups (1844-1867): renders meetup list, bell/going toggle
- _toggleMeetupGoing (1890-1895)
- openRoom routing for meetup (1952-1974)
- action-menu "Create Meetup" button (2039-2050)
- meetup_invite card render (2212-2224): Join button, loadMeetupJoinState
- Meetup modal: openMeetupModal (3215-3264), useMeetupGPS (3266-3273), submitMeetup (3276-3296),
  closeMeetupModal (3324-3328), loadMeetupJoinState (3183-3197), toggleMeetupJoin (3199-3212)
- notification body for meetup (3879, 3885)
- routing /chat/m/ (4600-4603)
- NOTE: submitMeetup sends stage_id=currentRoom, title, meetup_time, and optionally lat/lng/label/note.

## Admin UI (server/chat/admin.html)
- Meetup rows shown in Rooms tab; delete meetup room (7 references)

## Tests
- tests/test_chat_db.py (meetup CRUD, cascade, purge — 30 refs)
- tests/test_chat_api.py (endpoints — 25 refs)
- tests/test_chat_ws.py (WS create/join/leave — 7 refs)
- tests/test_chat_admin_roles.py (admin meetup delete — 12 refs)
- tests/test_notifications.py (meetup push — 17 refs)

## Cross-cutting context
- meetup rooms are NOT moderated the same as group rooms? (check is_moderated for type='meetup' in create_room)
- location_lat/lng are real GPS coords of the CREATOR, shared to all attendees (privacy/safety surface)
- meetup_time parsed with datetime.fromisoformat; TTL math uses naive/aware datetimes (timezone surface)
- No FK from rooms->meetups: integrity relies on app-code UUID matching + manual teardown
