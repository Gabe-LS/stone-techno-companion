# API contracts

This directory holds the OpenAPI contracts for Stage 2 (contract-first foundations) of
the platform migration described in `docs/roadmap.md`.

## `companion-openapi.yaml`

OpenAPI 3.1 document for the non-chat HTTP surface of `server/api.py`: session creation
and cross-device PIN sync, favorites (picks), personal schedule, push subscription
lifecycle, ICS calendar export, the public-transport departures/walk proxy, the DOP
aerial-tile proxy, and every static/catch-all route. It documents CURRENT behavior
as-built, including quirks and inconsistencies in the real code, each marked
`AS-BUILT:` inline. It is bug-compatible on purpose: divergences from ideal behavior
get fixed deliberately in their own change, not silently while writing this contract.

Notable AS-BUILT quirks captured in the file:

- `POST /api/session/{code}/push/subscribe` shares the "pick" rate-limit bucket
  (600 req / 3600s per IP) with the favorite add/remove endpoints, instead of having
  its own budget.
- `DELETE /api/session/{code}/push/subscribe` and `GET /api/session/{code}/push/status`
  have no rate limiting at all, unlike almost every other mutating/read endpoint in the
  file.
- `GET /ics/{slot_id}` has no rate limiting, and `slot_id` has no format validation
  (unlike the UUID-checked `artist_id`/`slot_id` params on the pick/schedule endpoints).
- `GET /api/transport/departures` and `GET /api/transport/walk` share ONE per-IP
  rate-limit hit list (`_check_transport_rate` keys only by client IP, not by endpoint
  or route/direction), even though each is called with a different limit (30 req/60s
  vs 10 req/60s). Heavy polling of departures from one IP can exhaust the walk
  endpoint's entire budget for that window before walk's own ceiling is ever reached.
- `GET /api/transport/departures`'s `time` query param regex accepts a single-digit
  hour (`^\d{1,2}:\d{2}$`) despite the documented `HH:MM` convention; its `date` regex
  only checks digit-group shape, not calendar validity.
- `GET /bios.json` sets no explicit `Cache-Control` header, unlike the otherwise
  equivalent `manifest.json`/`sw.js`/`shared.css`/`shared.js`/`timetable-transport.json`
  routes, which all set `no-cache`.
- `POST`/`DELETE` push subscription bodies are parsed via raw `request.json()` with no
  Pydantic model; a malformed body surfaces as an unhandled 500, not a 422.

## Out of scope

- **Chat REST API** (`server/chat_api.py`): stays as-is behind the future front and
  gets its own OpenAPI contract only when chat itself is ported to Next.js.
- **WebSocket endpoints**: not representable in OpenAPI, see the appendix below for
  the two WS surfaces that exist today.
- **The future lineup-data read API**: a new JSON API over artists/schedule/stages
  /events (replacing today's `lineup.html`/`bios.json`/`timetable.json` side-file
  convention) is being designed in parallel in `docs/api/lineup-data-api.md`. It is a
  separate contract, not part of this file.

## Contract-first rule

Per the Stage 2 roadmap: the Next.js front (Stage 3) is written against these contract
files, not against a reading of `server/api.py`. Any change to the real, running API
(new endpoint, changed parameter, changed response shape, changed status code, changed
rate limit or cache header) must update the matching `.yaml` file in the SAME pull
request as the code change. A PR that changes behavior without touching the contract
is incomplete, even if the change is a bug fix that intentionally removes one of the
AS-BUILT quirks documented above (removing a quirk still means editing this file to
drop its `AS-BUILT:` note).

## WebSocket appendix

Two independent WebSocket surfaces exist. Neither is described in the OpenAPI file
above (OpenAPI 3.1 has no first-class WebSocket support); this section is the prose
contract for both until a dedicated AsyncAPI (or similar) document is warranted.

**Lineup sync WebSocket** (`server/api.py`, `GET /ws/{code}`, upgraded to a WS
connection): one connection per browser tab, joined to a session's connection set
keyed by the resolved session id (never the share token directly; a share-token code
still resolves to the underlying session's broadcast group). Caps at 20 concurrent
connections per session (`MAX_WS_PER_SESSION`); the 21st connection attempt is closed
immediately with close code 1013. On connect, the server sends one initial message
with the full current state: `{"picks": [...], "schedule": [...], "readonly": bool}`.
After that, the connection is receive-only from the client's perspective: the client
never needs to send anything but the server enforces a 3600s read timeout on the
socket (an idle connection with no inbound frame for an hour is dropped). Two message
shapes are ever pushed to connected clients: a state update
`{"picks": [...], "schedule"?: [...]}` (broadcast whenever any device calls the
pick/schedule add/remove REST endpoints; `schedule` is included only when a
schedule-mutating endpoint triggered it) and `{"sync_complete": true}` (broadcast to
all of a session's connected clients once another device successfully exchanges a
sync PIN for that session, so an already-open tab can refresh its state). An invalid
`code` (fails the token-format regex, or matches no session) closes the socket
immediately with close code 1008.

**Chat WebSocket** (`server/chat_ws.py`, mounted via `chat_api.mount_chat`): fully out
of scope for this contract (chat REST is out of scope, and the WS surface is the
real-time half of that same system). Its message event names, for reference only (see
`server/chat_ws.py` and `server/chat/chat.html` for the actual contract, which belongs
to chat's own future contract document): `message`, `message_acked`,
`message_rejected`, `message_removed`, `messages_expired`, `room_history`,
`reaction_updated`, `typing`, `presence`, `badge_update`, `badge_counts`,
`profile_updated`, `key_rotated`, `link_preview`, `dm_opened`, `meetup_created`,
`meetup_created_ack`, `create_meetup_error`, `meetup_updated`, `meetup_expired`,
`muted`, `banned`, `strike`, `report_confirmed`, `rooms_changed`.
