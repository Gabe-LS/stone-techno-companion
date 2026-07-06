Consistent — `delete_meetup`'s `bool` return type is used correctly everywhere (admin path, new cancel endpoint, `delete_user`). No signature mismatch.

Based on this review, I found no actionable regressions, incomplete fixes, or scope creep.

## NO NEW CRITICAL/HIGH ISSUES

What I verified across `git diff ad2f5b1..HEAD` (server/chat_api.py, chat_ws.py, chat_db.py, chat/chat.html, tests/test_chat_api.py, CLAUDE.md):

- **`join_meetup` bool return**: both callers (`chat_ws.py:1912` WS path, `chat_api.py:1331` REST path) correctly check the new `bool` return and short-circuit (`continue` / `404`). No caller still treats it as `None`.
- **`_shape_meetup` attendee gating**: applied consistently in both `list_meetups` and `get_meetup` — `location_lat/lng/label/note/attendees` only included when `is_attendee`; `attendee_count`/`is_going` always present. Confirmed the client (`loadMeetups`, `loadMeetupJoinState`) only reads fields that are always present, or handles the gated fields as optional.
- **`DELETE /meetups/{id}` vs `DELETE /meetups/{id}/join`**: not a route-shadowing risk — FastAPI path params don't match `/`, so these are distinct paths regardless of registration order. Verified both routes exist and resolve independently.
- **WS `create_meetup`/`join_meetup` vs REST equivalents**: ban/mute check, block check, word-filter, and time-range validation were added to both the WS handler and the REST endpoint in lockstep — this is a case of a fix correctly applied to both paths, not an incomplete one.
- **`delete_meetup` reordering** (delete meetup rows, then `delete_room` last): correctly keeps everything in one atomic commit via `delete_room`'s single `db.commit()`; `delete_user` and `admin_delete_meetup` both funnel through the same updated `delete_meetup`, so no divergent behavior.
- **`purge_expired_meetups`switch to `id IN (...)`** using captured ids instead of re-querying by `expires_at <= now`: correctly avoids a TOCTOU mismatch between the room-deletion loop and the final meetups delete.
- **`meetup_created` WS broadcast payload reduction** (full `meetup` dict → `{id, title, meetup_time, stage_id}`): checked every client-side reader of `meetup_created` — none reference the dropped fields, so this is a strict privacy improvement, not a regression.
- **Invite-card `label`/`note` shown publicly in the room (C1) vs. `_shape_meetup` gating those same fields via the API (A4)**: initially looked like an inconsistent privacy gate, but tracing both commits confirms this is a deliberate, reviewed design (advertise title/time/label/note publicly, gate only precise GPS + attendee identity behind joining) — not flagging as a regression.
- **Scope creep**: no unrelated file changes; `CLAUDE.md` updates are documentation-only and match the actual admin/test changes already reviewed in prior passes.

Test suite: `python -m pytest tests/test_chat_db.py tests/test_chat_moderation.py tests/test_chat_ws.py tests/test_chat_api.py tests/test_chat_admin_roles.py -q` → **226 passed**, 0 failures (only unrelated `httpx`/pydantic deprecation warnings).
