# Implementation spec — Stage A frontend (admin panel XSS fix + UI)

You are an implementation agent. Apply the EXACT changes below. You may Read/Grep/Glob and Edit/Write. You CANNOT run anything — the orchestrator runs all tests/browser checks. Do not claim verification. No emojis anywhere. Match the existing code style in these files (vanilla JS, inline handlers, `esc`/`ago` helpers from shared.js).

## Files you may edit
- `server/chat/admin.html`
- `server/chat/chat.html` (ONE small change only, see C1)

Do NOT touch `.py`, `.js`, tests, or other files.

## Background — the security bug you are fixing (SEC-1 output layer)
`escapeHtml`/`esc` (shared.js) encodes `'` as `&#39;`. Inside an inline handler like
`onclick="adminBan('${esc(u.display_name)}')"` the HTML parser decodes `&#39;` back to `'`
BEFORE the onclick string is compiled as JavaScript, so an attacker-controlled display name can
break out of the JS string literal and run code in the admin's session. HTML-encoding is NOT
sufficient in a JS-string context.

The fix: **no untrusted string may appear inside an inline `onclick` (or other on*) attribute.**
Object IDs (user id, ban id, report id, room id) are UUID/hash/slug — safe charset — and may stay.
Untrusted strings are: `display_name`, `username`, `reported_name`, room `name`/`description`,
report `reason`, `message_snapshot`, ban `reason`, country, modlog `detail`. Those are used inside
handlers ONLY for `confirm()`/`alert()` text. Instead of passing them through markup, pass the id
only and look the record up from the already-rendered in-memory array inside the handler. JS values
held in memory are never re-parsed as code, so this is safe. Keep `esc()` on every HTML-CONTENT
interpolation (`<td>${esc(...)}</td>`, snapshots, etc.) — those are already correct; do not remove them.

## Changes

### F1 — store render data globally for lookup
The rooms and bans renderers already stash `window._roomsData` / `window._bansData`. Add the same for reports and users:
- In `render.reports` (admin.html ~276-306): after `const reports = await api(...)`, add `window._reportsData = reports;`.
- In `loadUsers` (admin.html ~410-426): after `const users = await api(...)`, add `window._usersData = users;`.
- In `loadUserDetail` (admin.html ~468-501): after `const u = await api('/users/' + userId)`, add `window._userDetail = u;` (used by the Delete-user button).

### F2 — reports table handlers (admin.html ~298-302)
Replace the three per-row action buttons so they pass ONLY the report id + action:
```
<button class="btn btn-red" onclick="reportAction('${esc(r.id)}','ban')">Ban</button>
<button class="btn btn-amber" onclick="reportAction('${esc(r.id)}','strike')">Strike</button>
<button class="btn btn-neutral" onclick="reportAction('${esc(r.id)}','dismiss')">Dismiss</button>
```
(`r.id` is a UUID — safe.) Then rewrite `reportAction` (admin.html ~390-404) signature to `reportAction(reportId, action)`; inside, look up the report:
```
const r = (window._reportsData || []).find(x => x.id === reportId);
if (!r) return;
const userId = r.reported_user_id;
const userName = r.reported_name || '[deleted user]';
```
Use `userName` in the `confirm()` text and `userId` for the ban/strike API calls exactly as before. Dismiss path unchanged (no user needed).

### F3 — users table + detail handlers
- Users Warnings dropdown (admin.html ~442-448): the Strike/Mute/Clear buttons already pass only `u.id` (safe) — leave those. Change the Ban/Unban buttons to pass only the id:
  - `<button class="dropdown-item" onclick="adminUnban('${esc(u.id)}')">Unban</button>`
  - `<button class="dropdown-item danger" onclick="adminBan('${esc(u.id)}')">Ban</button>`
  Add an **Unmute** item ABOVE "Clear warnings", shown only when the user is currently muted:
  ```
  ${(u.muted_until && u.muted_until > new Date().toISOString())
      ? `<button class="dropdown-item warn" onclick="adminUnmute('${esc(u.id)}')">Unmute</button>` : ''}
  ```
- Delete-user button in `loadUserDetail` (admin.html ~484): `onclick="event.stopPropagation();adminDeleteUser('${esc(u.id)}')"`.
- Rewrite handlers to look up the name from `window._usersData` (and `window._userDetail` for delete):
  ```
  function _userName(id){ const u=(window._usersData||[]).find(x=>x.id===id)||(window._userDetail&&window._userDetail.id===id?window._userDetail:null); return u?(u.display_name||u.username||id):id; }
  ```
  - `adminBan(userId)` → `const name=_userName(userId);` then existing confirm/API.
  - `adminUnban(userId)` → same.
  - `adminDeleteUser(userId)` → `const name=(window._userDetail&&window._userDetail.id===userId)?(window._userDetail.display_name||window._userDetail.username||userId):_userName(userId);`
  - NEW `adminUnmute(userId)`:
    ```
    async function adminUnmute(userId){
      dbg('[ACTION] unmute', userId);
      if(!confirm('Unmute this user? (strikes and mute count are kept)')) return;
      await api('/unmute/' + userId, {method:'POST', body:'{}'});
      refreshStats(); loadUsers();
    }
    ```

### F4 — banned tab handler (admin.html ~590)
Change to pass only the ban id: `<button class="btn btn-neutral" onclick="unban('${esc(b.ban_id)}')">Unban</button>`. Rewrite `unban(banId)` (admin.html ~593-598) to look up the name from `window._bansData` by `ban_id` for the confirm text; keep the DELETE call.

### F5 — rooms tab handlers (admin.html ~381-382)
`openRoomModal('${esc(r.id)}')`, `setMainRoom('${esc(r.id)}')` already pass only ids — leave. Change delete to pass only the id: `deleteRoom('${esc(r.id)}')`. Rewrite `deleteRoom(roomId)` (admin.html ~786-791) to look up the room name from `window._roomsData` by id for the confirm text.

### F6 — audit remaining on* handlers
Grep admin.html for `onclick=`, `onchange=`, `oninput=`, `onkeydown=`, `ondrag`, `ondrop`, `onerror`. Confirm NONE interpolate an untrusted string (only ids / literals / `this` / `event`). If any other untrusted interpolation exists that this spec missed, apply the same id-only+lookup fix and note it in your report. (Expected remaining interpolations are all ids or none.)

### F7 (ROOM-3) — TTL edit must not silently rewrite a non-preset value
In `openRoomModal` (admin.html ~666-677), the `<select id="rm-ttl">` only lists preset values. When editing a room whose `ttl_minutes` matches no preset, inject a synthetic selected option at the top of the select so Save preserves it:
```
${isEdit && room && room.ttl_minutes != null && ![30,60,360,1440,2880,4320].includes(room.ttl_minutes)
    ? `<option value="${esc(String(room.ttl_minutes))}" selected>${esc(String(room.ttl_minutes))} minutes (current)</option>` : ''}
```
Place it as the first `<option>` inside the select. Leave the preset options as-is.

### F8 (ROOM-11b) — surface total rooms in the stats bar
In `renderStatsHTML` (admin.html ~230-238) add a stat after Messages: `+ \`<div class="stat">Rooms <b>${s.total_rooms}</b></div>\``. (`total_rooms` is already returned by /stats.)

### C1 (MOD-2 client) — chat.html: treat ban close code as terminal
In `server/chat/chat.html`, the WebSocket `onclose` handler currently special-cases only `ev.code === 4001` (sets `currentUser = null; renderAuth()`). Ban closes use `code=4003`. Change the guard so BOTH terminal codes are handled:
`if (ev.code === 4001 || ev.code === 4003) { currentUser = null; renderAuth(); return; }`
Do not change the reconnect/backoff logic for other codes. This stops a just-banned client from silently reconnect-looping.

## Final report format
List each change F1..F8 + C1 with file:line and a one-line note. Report the result of the F6 grep audit explicitly (every remaining `on*=` interpolation and why it is safe). Flag any spec/reality mismatch and what you did instead. Do not claim tests pass.
