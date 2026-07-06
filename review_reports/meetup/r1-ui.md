- ID: ui-1
- Severity: high
- Confidence: likely
- Location: server/chat/chat.html:96-115 (`.msg-image`, `.msg-video`, `.msg-card`, `.meetup-join-wide`), rendered inside `.msg-bubble` (line 87: `max-width: min(70%, 480px)`)
- Finding: `.msg-card` (used by `.card-meetup`) and `.meetup-join-wide` both use a fixed `width: 260px`, but their parent `.msg-bubble` caps at `max-width: min(70%, 480px)` minus its own padding. On common narrow phones (e.g. 375px-wide viewports), 70% of the available message-row width minus bubble padding is well under 260px, so the fixed-width card/button will not shrink and will overflow the bubble's rounded background — either spilling visually past the bubble edge or forcing a horizontal scrollbar in `.content` (which has no explicit `overflow-x: hidden`). This is unique to meetup/location cards; every other card-like element (`.msg-image`, `.msg-video`) uses `max-width` (which does shrink) instead of a fixed `width`.
- Recommendation: Change `.msg-card` and `.meetup-join-wide` to `max-width: 260px; width: 100%` (or `min(260px, 100%)`) so they shrink to fit the bubble on narrow viewports, matching the `max-width` pattern already used for images/videos. While there, replace the four repeated `260px` literals with a shared CSS variable (e.g. `--card-width`) so the meetup card can't drift out of sync with `.msg-card`/media sizing in future edits.
- Effort: S
- Risk of change: low

- ID: ui-2
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:3245
- Finding: The "GPS" button inside the meetup modal renders as `📍 GPS` — a raw emoji glyph. Every other action button in this file (Create Meetup, Share Location, Share Photo, Share Video, send, emoji-picker, react, etc.) uses the inline SVG icon sprite with no emoji. This is the only emoji-as-icon in the meetup feature and breaks both the project's no-emoji convention and the established SVG-icon button pattern.
- Recommendation: Replace `📍` with the existing location SVG (already defined and used for the "Share Location" action button and `.card-location` icon, chat.html:2051/2209) so the GPS button matches the rest of the icon system.
- Effort: S
- Risk of change: low

- ID: ui-3
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:1494-1509 (WS handlers) vs. 2223 (actual render)
- Finding: The `meetup_updated` and `meetup_expired` WS handlers look up the invite-card join button via `document.querySelector(... .meetup-join-btn)`, but the button is actually rendered with class `meetup-join meetup-join-wide` (line 2223) — `.meetup-join-btn` does not exist anywhere in the rendered markup. As a result: (1) `meetup_updated` never live-updates the "Join"/"Joined" text, the `going` count span, or the button's joined-state styling when another user joins/leaves in real time — the invite card only reflects RSVP state from its own one-off `loadMeetupJoinState` fetch at render time. (2) `meetup_expired` dims the card (`opacity: 0.5`) but the `?.remove()` on the same wrong selector silently no-ops, so the "Join" button stays fully interactive on an expired/deleted meetup card. Additionally, the dead code toggles class `active` (`mBtn.classList.toggle('active', going)`), but no `.meetup-join.active` CSS rule exists — only `.meetup-join.joined` (line 113) — so even a corrected selector would not visually reflect joined state without also fixing the class name.
- Recommendation: Fix the selector to `.meetup-join` and the class to `joined` in both handlers; for `meetup_updated` also update the `.meetup-going` span using the same `' · ' + count + ' going'` format `loadMeetupJoinState` uses (line 3190) instead of the inconsistent `count + ' going'` currently written, so live updates match the initial render format. For `meetup_expired`, after removing/disabling the join button also add a `pointer-events:none` or actually delete it so an expired card can't be tapped.
- Effort: S
- Risk of change: low

- ID: ui-4
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:1844-1868 (`loadMeetups`, desktop tab list) vs. 2738-2750 (`_loadMenuSection('meetups')`, mobile hamburger list)
- Finding: The desktop "Meetups" tab list renders each meetup with a notification/"going" bell button, an online count, and `data-meetup-info`/`data-room-type` attributes used by `updateSidebarCount` for live count refreshes. The mobile hamburger "Meetups" section renders the same data with none of that — no bell, no online count, and no `data-room-type`/`data-meetup-info`, so a mobile user browsing meetups from the hamburger menu has no way to RSVP/toggle "going" from the list (must open the room or find the original invite card) and never sees live attendee-count refreshes there. This is a duplicated render path that has silently drifted out of sync with its desktop counterpart.
- Recommendation: Either have `_loadMenuSection('meetups')` reuse the same row-builder as `loadMeetups` (extract the `<li>` template into one shared function), or, if the omission is intentional for mobile, make it consistent (still add the bell so join/leave is possible without opening the room).
- Effort: M
- Risk of change: low

- ID: ui-5
- Severity: high
- Confidence: certain
- Location: server/chat/chat.html:2036-2038 (`renderChatView`), server/chat_api.py:959-978 / server/chat_db.py:816-820 (`get_rooms_by_event` filters `type IN ('stage','general')`)
- Finding: `renderChatView`'s member-count text is computed as `currentRoomObj = rooms.find(r => r.id === currentRoom); memberCount = ... (currentRoomObj ? currentRoomObj.member_count : 0)`. The client-side `rooms` array is populated exclusively from `GET /rooms`, which server-side only returns rooms with `type IN ('stage', 'general')` — meetup rooms are never in it. So every time a meetup room is opened (from the list, an invite card, or a `/chat/m/{id}` deep link), the header always shows "0 members" regardless of actual attendee count, on both mobile and desktop layouts.
- Recommendation: When `currentRoomType === 'meetup'`, source the header count from the meetup's `attendee_count` (already fetched by `loadMeetups`/`GET /meetups/{id}`) instead of the group-rooms `rooms` array — e.g. cache the meetup list response and look up by id, or fetch `/meetups/{id}` when opening a meetup room.
- Effort: S
- Risk of change: low

- ID: ui-6
- Severity: medium
- Confidence: likely
- Location: server/chat/chat.html:69 (`.room-name`), used at 1858-1861 and 2745-2746 for meetup titles
- Finding: `.room-name` has no `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`, unlike `.card-title` (line 109) which explicitly truncates. Regular room names are short and admin-controlled, so this was never an issue, but meetup titles are free-text user input up to 60 characters (`maxlength="60"`, chat.html:3234). A long title will wrap to multiple lines inside a `.room-item` that has a fixed `height: var(--header-h)` and no `overflow: hidden` (line 66), so the wrapped text will visually overflow the row height and bleed into/overlap the row below, in both the desktop Meetups tab and the mobile hamburger Meetups section.
- Recommendation: Add `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` to `.room-name` (safe for existing short room names, fixes meetup titles), same as already done for `.card-title`.
- Effort: S
- Risk of change: low

- ID: ui-7
- Severity: medium
- Confidence: certain
- Location: server/chat/chat.html:1864 (`loadMeetups` bell), 1890-1898 (`_toggleMeetupGoing`)
- Finding: The meetup list's per-row toggle reuses the exact same bell icon and `aria-label` wording ("Get notified" / "Mute meetup") as the plain notification-mute bell used for group rooms (`_renderRoomItemsHtml`, line 1685, `_toggleRoomMembership`). But unlike the group-room bell (which only mutes/unmutes notifications), `_toggleMeetupGoing` calls `POST`/`DELETE /meetups/{id}/join` — the actual RSVP endpoint that adds/removes the user from `meetup_attendees`, changes the attendee count everyone else sees, and (per the manifest's room-access gating) affects whether the user can access the meetup's chat room. Presenting an RSVP/attendance action as a notification-mute toggle is misleading — a user tapping the bell to "quiet" a meetup they're attending may not realize they've un-RSVP'd and potentially lost room access.
- Recommendation: Give the meetup row a distinct affordance (e.g. a "Going"/checkmark icon and "Join"/"Leave" labels) instead of reusing the bell metaphor, or if bell semantics are intentional, decouple notification muting from attendee membership server-side so the icon's meaning matches its effect.
- Effort: M
- Risk of change: medium

- ID: ui-8
- Severity: low
- Confidence: certain
- Location: server/chat/admin.html:397, 427
- Finding: `typePill(t)` maps `general`→`pill-blue`, `stage`→`pill-green`, `dm`→`pill-amber`, and falls through to `pill-gray` for anything else — which is the only color meetup rooms get in the admin Rooms tab's Type column. Meetup is a first-class room type (has its own delete action, TTL setting, etc.) but is visually indistinguishable from a generic/unknown type in this table.
- Recommendation: Add an explicit `t === 'meetup' ? 'pill-purple' : ...` (or similar) branch so meetup rooms get their own consistent color, matching the treatment `dm` already receives.
- Effort: S
- Risk of change: low

- ID: ui-9
- Severity: low
- Confidence: likely
- Location: server/chat/chat.html:2224 (`setTimeout(() => loadMeetupJoinState(mid), 0)`), 3183-3197
- Finding: Every time a `meetup_invite` message is rendered — including on `renderMessages()` re-renders that happen for unrelated reasons (block/unblock a user, profile updates, reconnects) — a fresh network fetch to `/meetups/{id}` is fired to repopulate the "Join"/"Joined" text and "N going" count, which are blank until that fetch resolves. In a room with multiple meetup invites, or on a slow connection, this causes the join button label and attendee count to visibly flash/reset on every re-render instead of preserving already-known state.
- Recommendation: Cache the last-known join state per `meetup_id` (e.g. in the existing `messagesByRoom`/a small in-memory map) and render it synchronously in `renderMessage`, only re-fetching on first render or on an explicit `meetup_updated` event (once ui-3 is fixed).
- Effort: M
- Risk of change: low
