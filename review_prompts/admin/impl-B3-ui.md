# Implementation spec — Stage B3 (admin.html: identity, roles, Admins + Audit tabs)

You are an implementation agent. Apply EXACTLY what is below. Read `docs/admin-multiadmin.md` FIRST (authoritative contract). You may Read/Grep/Glob and Edit/Write. You CANNOT run anything; the orchestrator runs browser/test checks. No emojis. Match existing admin.html style (vanilla JS, `esc`/`ago` helpers, `api()` wrapper, the `render` object, `switchTab`, inline handlers passing ONLY ids — never untrusted strings, per the Stage-A XSS fix; keep that invariant).

## File you may edit
- `server/chat/admin.html` ONLY.

## Backend endpoints available (Stage B2, already implemented)
- `GET /admin/me` → `{role, kind, label, email_hash}` (role is 'admin' or 'super_admin').
- `GET /admin/audit?limit=50&offset=0` → `[{id, actor, action, target_user_id, target_name, target_room_id, detail, created_at}]`.
- `GET /admin/admins` (super only) → `[{email_hash, role, label, permanent, added_by, created_at}]`.
- `POST /admin/admins` (super only) `{email, role, label}` → the new row.
- `DELETE /admin/admins/{email_hash}` (super only) → `{ok}`.
- `POST /chat/api/logout` OR existing logout (see note in your B2 report; if unsure, call `/chat/api/logout`).
- The `api()` helper prefixes `/chat/api/admin`, so use `api('/me')`, `api('/audit?...')`,
  `api('/admins')`, `api('/admins', {method:'POST',...})`, `api('/admins/'+hash, {method:'DELETE'})`.
  For logout use a raw `fetch('/chat/api/logout', {method:'POST', credentials:'include'})`.

## Changes

### 1. Fetch identity on init, store globally
In `init()` (~200-228), after the successful `api('/stats')` call, also fetch identity:
`try { window._me = await api('/me'); } catch { window._me = {role:'admin', kind:'cookie', label:'admin'}; }`
Do this BEFORE building the tabs/app HTML so the tab list can be role-gated.

### 2. Render the tab bar with role gating + identity chip
In the `init()` HTML template that builds `<div class="tabs" id="tabs">...`:
- Append two tabs after the existing "Logs" tab:
  `<div class="tab" data-tab="audit" onclick="switchTab('audit')">Audit</div>`
  and, ONLY when `window._me.role === 'super_admin'`:
  `<div class="tab" data-tab="admins" onclick="switchTab('admins')">Admins</div>`
  Use a template expression: `${window._me.role === 'super_admin' ? '<div class="tab" data-tab="admins" onclick="switchTab(\'admins\')">Admins</div>' : ''}`.
- Add an identity chip on the right side of the tab bar. Simplest: after the tabs `<div>`, before
  `<div class="content">`, the tabs bar is a flex row; append a right-aligned span. Concretely, add
  inside the `.tabs` container (or immediately after it) an element:
  `<div class="admin-me" id="admin-me"></div>` and populate it in init with:
  `document.getElementById('admin-me').innerHTML = renderMeChip();`
  where:
```
function renderMeChip() {
  const m = window._me || {role:'admin', label:'admin'};
  const pill = m.role === 'super_admin' ? '<span class="pill pill-amber">super-admin</span>' : '<span class="pill pill-blue">admin</span>';
  return `<span style="color:var(--text-sec)">${esc(m.label || '')}</span> ${pill} <button class="btn btn-neutral" style="padding:3px 10px" onclick="adminLogout()">Log out</button>`;
}
```
  Add minimal CSS for `.admin-me` in the `<style>` block: `.admin-me{margin-left:auto;display:flex;align-items:center;gap:8px;padding:6px 12px;font-size:12px;white-space:nowrap}` and make the `.tabs` a flex row that allows the chip to push right (the `.tabs` rule already sets `display:flex`; ensure `align-items:center`). If placing inside `.tabs` is awkward, place the chip in the `.stats-bar` right side instead — either is acceptable; keep it visible and not overlapping content.

### 3. Logout
```
async function adminLogout() {
  dbg('[AUTH] logout');
  const m = window._me || {};
  if (m.kind === 'token') { sessionStorage.removeItem('chat_admin_token'); token=''; renderLogin(); return; }
  try { await fetch('/chat/api/logout', {method:'POST', credentials:'include'}); } catch {}
  location.reload();
}
```

### 4. Audit tab renderer
Add `audit` to the `render` object (mirror the `log` renderer with its own offset/items state):
```
let auditOffset = 0, auditItems = [], _auditFilter = '';
render.audit = async () => {
  dbg('[RENDER] audit');
  const el = document.getElementById('content');
  auditOffset = 0; auditItems = [];
  el.innerHTML = `<div class="toolbar"><input type="text" id="audit-search" placeholder="Search audit" oninput="filterAudit(this.value)"></div><div id="audit-list"></div>`;
  await loadMoreAudit();
};
async function loadMoreAudit() {
  try {
    const items = await api('/audit?limit=50&offset=' + auditOffset);
    auditItems = auditItems.concat(items); auditOffset += items.length;
    renderAudit(items.length === 50);
  } catch { (document.getElementById('audit-list')||document.getElementById('content')).innerHTML = '<div class="empty">Failed to load audit</div>'; }
}
function renderAudit(hasMore) {
  const el = document.getElementById('audit-list') || document.getElementById('content');
  const items = _auditFilter ? auditItems.filter(i =>
    (i.actor||'').toLowerCase().includes(_auditFilter) ||
    (i.action||'').toLowerCase().includes(_auditFilter) ||
    (i.target_name||'').toLowerCase().includes(_auditFilter) ||
    (i.detail||'').toLowerCase().includes(_auditFilter)) : auditItems;
  if (!items.length) { el.innerHTML = '<div class="empty">No admin actions</div>'; return; }
  el.innerHTML = `<table class="tbl"><thead><tr><th class="col-narrow">Actor</th><th class="col-narrow">Action</th><th>Target</th><th>Detail</th><th class="col-narrow">Time</th></tr></thead><tbody>` +
    items.map(i => `<tr>
      <td class="col-narrow" style="color:var(--text-sec)">${esc(i.actor)}</td>
      <td class="col-narrow"><span class="pill pill-gray">${esc(i.action)}</span></td>
      <td>${esc(i.target_name || i.target_room_id || (i.target_user_id ? i.target_user_id.slice(0,8) : '--'))}</td>
      <td class="truncate" style="color:var(--text-sec)">${esc(i.detail || '')}</td>
      <td class="col-narrow" style="color:var(--text-muted)">${ago(i.created_at)}</td>
    </tr>`).join('') + '</tbody></table>' +
    (hasMore && !_auditFilter ? '<div class="load-more"><button onclick="loadMoreAudit()">Load more</button></div>' : '');
}
function filterAudit(q){ _auditFilter=q.toLowerCase(); renderAudit(auditOffset>0 && auditItems.length===auditOffset); }
```

### 5. Admins tab renderer (super-admin only)
Add `render.admins` and helpers. Store the list globally for id-only lookups (XSS invariant):
```
render.admins = async () => {
  dbg('[RENDER] admins');
  const el = document.getElementById('content');
  try {
    const admins = await api('/admins');
    window._adminsData = admins;
    let html = `<div class="toolbar"><button class="btn btn-blue" onclick="openAdminModal()">Add admin</button></div>`;
    html += `<table class="tbl"><thead><tr><th>Label</th><th class="col-narrow">Role</th><th class="col-narrow">Added by</th><th class="col-narrow">Added</th><th class="col-actions"></th></tr></thead><tbody>` +
      admins.map(a => `<tr>
        <td>${esc(a.label || a.email_hash.slice(0,12))}${a.permanent ? ' <span class="pill pill-gray">permanent</span>' : ''}</td>
        <td class="col-narrow">${a.role === 'super_admin' ? '<span class="pill pill-amber">super-admin</span>' : '<span class="pill pill-blue">admin</span>'}</td>
        <td class="col-narrow" style="color:var(--text-muted)">${esc(a.added_by || '')}</td>
        <td class="col-narrow" style="color:var(--text-muted)">${a.created_at ? ago(a.created_at) : '--'}</td>
        <td class="col-actions">${a.permanent ? '' : `<button class="btn btn-neutral" onclick="removeAdmin('${esc(a.email_hash)}')">Remove</button>`}</td>
      </tr>`).join('') + '</tbody></table>';
    el.innerHTML = html;
  } catch { el.innerHTML = '<div class="empty">Failed to load admins</div>'; }
};
function openAdminModal() {
  const m = document.getElementById('modal');
  m.innerHTML = `<h2>Add admin</h2>
    <div class="modal-field"><label>Email</label><input type="email" id="adm-email" placeholder="person@example.com"></div>
    <div class="modal-field"><label>Label (shown in list)</label><input type="text" id="adm-label" placeholder="Name / role note"></div>
    <div class="modal-field"><label>Role</label><select id="adm-role"><option value="admin">admin (moderator)</option><option value="super_admin">super-admin</option></select></div>
    <div class="modal-actions"><button class="btn btn-neutral" onclick="closeModal()">Cancel</button><button class="btn btn-blue" onclick="saveAdmin()">Add</button></div>`;
  document.getElementById('modal-overlay').classList.add('open');
}
async function saveAdmin() {
  const email = document.getElementById('adm-email')?.value.trim();
  const label = document.getElementById('adm-label')?.value.trim() || '';
  const role = document.getElementById('adm-role')?.value || 'admin';
  if (!email) return;
  try {
    await api('/admins', {method:'POST', body: JSON.stringify({email, label, role})});
    closeModal(); render.admins();
  } catch (e) { alert('Failed: ' + e.message); }
}
async function removeAdmin(emailHash) {
  const a = (window._adminsData || []).find(x => x.email_hash === emailHash);
  if (!confirm('Remove admin ' + (a ? (a.label || emailHash.slice(0,12)) : '') + '?')) return;
  try { await api('/admins/' + emailHash, {method:'DELETE'}); render.admins(); }
  catch (e) { alert('Failed: ' + e.message); }
}
```
Note: `openAdminModal` reuses the existing modal overlay; it does NOT set `_modalRoomId` or the room
key handler — do not wire the room Enter-handler to it. Keep it simple (buttons only).

### 6. Role-gate destructive controls (cosmetic; server still enforces)
When `window._me.role !== 'super_admin'`, omit these buttons in their renderers:
- Rooms tab Delete-room button (in `render.rooms`, the Delete `<button>`): wrap its emission in
  `${window._me && window._me.role === 'super_admin' ? '<button ...>Delete</button>' : ''}`.
- Users Warnings dropdown: omit "Clear warnings" and the Ban/Unban entries? NO — Ban stays for
  admins. Only omit **Unban** and **Clear warnings** for non-super (those are super-only server-side).
  Keep Strike/Mute/Unmute/Ban for all admins.
- Delete-user button in `loadUserDetail`: omit for non-super.
- Banned tab Unban button (`filterBans`): omit for non-super (show a muted "—" instead).
Guard each with `window._me && window._me.role === 'super_admin'`. Since `window._me` is set in init
before any render, it is safe to read. Do NOT change the id-only onclick pattern from Stage A.

## Final report
List each change (1..6) with admin.html line refs. Confirm no untrusted string was introduced into
any inline handler (only ids/labels via esc in CONTENT context, ids in onclick). Note any deviation.
Do not claim tests pass.
