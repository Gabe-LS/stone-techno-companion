# Implementation spec — Stage C frontend (admin completeness UI)

You are an implementation agent. Apply EXACTLY what is below. You may Read/Grep/Glob and Edit/Write.
You CANNOT run anything; the orchestrator runs browser/test checks. No emojis. CRITICAL: keep the
Stage-A XSS invariant — NO untrusted string may appear inside any inline `on*=` handler. Handlers pass
ONLY ids (UUID/slug/hash) or literals; look names up from in-memory arrays. All displayed text goes
through `esc()` in CONTENT context. Match existing admin.html style (`api()` wrapper prefixes
`/chat/api/admin`, `render` object, `esc`/`ago`, modal overlay, tabs, `window._me`).

## File you may edit
- `server/chat/admin.html` ONLY.

## Backend endpoints available (Stage C, implemented)
- `GET /admin/rooms/{id}/messages?limit=100` → `[{id, user_id, type, content, media_url, moderation_status, created_at, display_name, username}]` (400 for dm rooms).
- `DELETE /admin/messages/{id}` → `{ok}`.
- `GET /admin/reports?status=pending|actioned|dismissed|all` → reports now also include `room_name`.
- `admin_rooms` now includes `participants: [name, name]` on dm rows.
- `GET /admin/settings` → `{room_sort, msg_char_limit, dm_ttl_minutes, room_ttl_minutes, meetup_ttl_minutes}`.
- `PATCH /admin/settings` (super-admin only) accepts those keys.
- `GET /admin/meetups` → `[{id, title, creator_id, creator_name, meetup_time, location_label, created_at, expires_at, attendees}]`.
- `DELETE /admin/meetups/{id}` → `{ok}`.

## Changes

### 1. Reports: status filter + room name + jump-to-user
In `render.reports`:
- Add a small filter control above the table:
  `<div class="toolbar"><select id="report-status" onchange="reloadReports(this.value)">
     <option value="pending">Pending</option><option value="actioned">Actioned</option>
     <option value="dismissed">Dismissed</option><option value="all">All</option></select></div>`
  Track current status in a module var `reportStatus = 'pending'`; fetch `api('/reports?status=' + reportStatus)`.
  Add `function reloadReports(s){ reportStatus = s; render.reports(); }`.
- Add a "Room" column showing `esc(r.room_name || r.room_id || '--')`.
- Make the reported name cell a jump: wrap it so clicking opens the Users tab and expands that user.
  Add `data-jump="${esc(r.reported_user_id)}"` on the cell and an onclick calling
  `jumpToUser('${esc(r.reported_user_id)}')` (id only — safe). Implement:
  ```
  function jumpToUser(userId){ if(!userId) return; searchQuery=''; switchTab('users');
    expandedUser = userId; setTimeout(()=>{ loadUsers(); }, 0); }
  ```
  (Reuse existing `expandedUser`/`loadUsers`; loadUsers already expands `expandedUser` after render.)
- For non-pending statuses, HIDE the Ban/Strike/Dismiss action buttons (they only apply to pending);
  show the resolved `status` + `ago(r.reviewed_at || r.created_at)` instead. Guard the actions cell
  with `${reportStatus === 'pending' ? actionsHtml : esc(r.status)}`.

### 2. Custom reason on ban / strike
Replace the hardcoded confirm-only flows with a reason prompt (keep it simple — `prompt()` is fine and
avoids new modal wiring):
- `adminBan(userId)`: `const reason = prompt('Ban reason:', 'Banned by admin'); if(reason===null) return;`
  then POST `{reason}`. (Drop the separate confirm; the prompt IS the confirm — cancel returns null.)
- `adminStrike(userId)`: `const detail = prompt('Strike reason:', 'Manual admin action'); if(detail===null) return;`
  then POST `{reason:'admin', detail}`.
- `reportAction(reportId,'ban')`: `const reason = prompt('Ban reason:', 'Banned via report'); if(reason===null) return;` then use it.
- Keep `adminMute`/`adminUnmute`/`adminUnban`/`adminDeleteUser` confirm() flows unchanged (mute has no reason field server-side).

### 3. Room messages viewer + single delete
In `render.rooms`, add a "Messages" button in the Actions cell for non-dm/non-meetup rooms (next to
Edit/Delete): `<button class="btn btn-neutral" onclick="viewRoomMessages('${esc(r.id)}')">Messages</button>`.
Implement a modal viewer:
```
async function viewRoomMessages(roomId){
  const m = document.getElementById('modal');
  m.innerHTML = '<h2>Recent messages</h2><div id="rm-msgs" style="max-height:60vh;overflow:auto"></div>' +
    '<div class="modal-actions"><button class="btn btn-neutral" onclick="closeModal()">Close</button></div>';
  document.getElementById('modal-overlay').classList.add('open');
  try {
    const msgs = await api('/rooms/' + roomId + '/messages?limit=100');
    window._roomMsgs = msgs;
    const box = document.getElementById('rm-msgs');
    if(!msgs.length){ box.innerHTML = '<div class="empty">No messages</div>'; return; }
    box.innerHTML = msgs.map(x => `<div class="detail-sub-row" id="msgrow-${esc(x.id)}">
      <b>${esc(x.display_name || x.username || 'unknown')}</b>
      ${x.moderation_status === 'pending' ? '<span class="pill pill-amber">pending</span>' : ''}
      <span style="color:var(--text-muted)">${ago(x.created_at)}</span>
      <button class="btn btn-red" style="float:right;padding:2px 8px" onclick="adminDeleteMessage('${esc(x.id)}')">Delete</button>
      <div style="margin-top:2px">${esc(x.type === 'text' ? x.content : '['+esc(x.type)+']')}</div>
    </div>`).join('');
  } catch(e){ document.getElementById('rm-msgs').innerHTML = '<div class="empty">Failed to load</div>'; }
}
async function adminDeleteMessage(id){
  if(!confirm('Delete this message?')) return;
  try { await api('/messages/' + id, {method:'DELETE'});
    const row = document.getElementById('msgrow-' + id); if(row) row.remove();
  } catch(e){ alert('Failed: ' + e.message); }
}
```
NOTE: `x.content` for text is safe via esc. Do NOT interpolate content into any handler. In the
`[${esc(x.type)}]` fragment esc is redundant-but-harmless; keep the inner text esc'd.

### 4. Rooms tab: DM participant names + meetup delete
- In `render.rooms`, for the Name cell: if `r.type === 'dm'` and `r.participants`, render
  `esc(r.participants.join(' <-> '))` instead of the literal "DM". (Use `esc` on each joined string;
  simplest: `esc((r.participants||[]).join(' <-> ')) || esc(r.name)`.)
- For `r.type === 'meetup'` rows, add a Delete button in Actions:
  `<button class="btn btn-neutral" onclick="deleteMeetup('${esc(r.id)}')">Delete</button>`.
  ```
  async function deleteMeetup(id){
    const room = (window._roomsData||[]).find(r=>r.id===id);
    if(!confirm('Delete meetup "' + (room?room.name:'') + '"? This removes its chat too.')) return;
    try { await api('/meetups/' + id, {method:'DELETE'}); render.rooms(); }
    catch(e){ alert('Failed: ' + e.message); }
  }
  ```

### 5. Settings tab (super-admin only)
Add a `Settings` tab after `Logs` (before Audit is fine too), gated on
`window._me.role === 'super_admin'` exactly like the Admins tab. Add `render.settings`:
```
render.settings = async () => {
  const el = document.getElementById('content');
  try {
    const s = await api('/settings');
    el.innerHTML = `<div class="detail-panel" style="max-width:420px">
      <div class="modal-field"><label>Message char limit</label><input type="number" id="set-msglimit" value="${esc(String(s.msg_char_limit))}"></div>
      <div class="modal-field"><label>Room message TTL (minutes)</label><input type="number" id="set-roomttl" value="${esc(String(s.room_ttl_minutes))}"></div>
      <div class="modal-field"><label>DM TTL (minutes)</label><input type="number" id="set-dmttl" value="${esc(String(s.dm_ttl_minutes))}"></div>
      <div class="modal-field"><label>Meetup TTL after meetup time (minutes)</label><input type="number" id="set-meetupttl" value="${esc(String(s.meetup_ttl_minutes))}"></div>
      <div class="modal-actions"><button class="btn btn-blue" onclick="saveSettings()">Save</button></div>
    </div>`;
  } catch { el.innerHTML = '<div class="empty">Failed to load settings</div>'; }
};
async function saveSettings(){
  const body = {
    msg_char_limit: parseInt(document.getElementById('set-msglimit').value),
    room_ttl_minutes: parseInt(document.getElementById('set-roomttl').value),
    dm_ttl_minutes: parseInt(document.getElementById('set-dmttl').value),
    meetup_ttl_minutes: parseInt(document.getElementById('set-meetupttl').value),
  };
  try { await api('/settings', {method:'PATCH', body: JSON.stringify(body)}); showToast('Settings saved'); }
  catch(e){ alert('Failed: ' + e.message); }
}
```
(Use `showToast` if defined in shared.js — grep to confirm; if not, use `alert('Saved')`.)
Add the tab to the tab bar: `${window._me.role === 'super_admin' ? '<div class="tab" data-tab="settings" onclick="switchTab(\'settings\')">Settings</div>' : ''}`.

## Final report
List each change (1..5) with admin.html line refs. Confirm the XSS invariant held (list every NEW
inline handler and its args — must be ids/literals only). Note deviations (e.g. showToast presence).
Do not claim tests pass.
