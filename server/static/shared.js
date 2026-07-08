/* ===== SHARED UTILITIES ===== */

/* --- Debug --- */
/* Debug console output is opt-in in production: run
   localStorage.setItem('stc_debug', '1') and reload to enable,
   localStorage.removeItem('stc_debug') to disable. Failures from verify()
   always reach the console regardless of the flag. */
var DEBUG = (function () {
  try { return localStorage.getItem('stc_debug') === '1'; } catch (e) { return false; }
})();
var _t0 = performance.now();
var _dbgTag = '[app]';
function _ts() { return '+' + ((performance.now() - _t0) | 0) + 'ms'; }
function setDbgTag(tag) { _dbgTag = '[' + tag + ']'; }
function dbg() {
  if (DEBUG) console.log.apply(console, [_ts(), _dbgTag].concat(Array.prototype.slice.call(arguments)));
}
function verify(label, condition, detail) {
  if (condition) {
    if (DEBUG) console.log(_ts(), _dbgTag + ' OK: ' + label, detail || '');
  } else {
    console.error(_ts(), _dbgTag + ' FAIL: ' + label, detail || '');
  }
  return condition;
}

/* --- DOM --- */
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
var esc = escapeHtml;

/* --- Time --- */
function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function ago(iso) {
  if (!iso) return '';
  var d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return 'just now';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  if (d < 86400) return Math.floor(d / 3600) + 'h ago';
  return Math.floor(d / 86400) + 'd ago';
}

/* --- Toast --- */
var _toastTimer = null;
function showToast(msg, duration) {
  dbg('[TOAST] showToast', msg);
  if (!duration) {
    var words = msg.trim().split(/\s+/).length;
    duration = Math.max(4000, 1500 + words * 300);
  }
  var t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function() { t.classList.remove('show'); }, duration);
}

/* --- Storage --- */
function storageGet(key, fallback) {
  try {
    var v = localStorage.getItem(key);
    return v !== null ? v : (fallback !== undefined ? fallback : null);
  } catch (e) {
    return fallback !== undefined ? fallback : null;
  }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); }
  catch (e) { /* quota exceeded or private browsing */ }
}

/* --- Storage (remove) --- */
function storageRemove(key) {
  try { localStorage.removeItem(key); }
  catch (e) { /* private browsing */ }
}

/* --- Icons --- */
var ICON_HAMBURGER = '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" clip-rule="evenodd" d="M20.75 7C20.75 7.41421 20.4142 7.75 20 7.75L4 7.75C3.58579 7.75 3.25 7.41421 3.25 7C3.25 6.58579 3.58579 6.25 4 6.25L20 6.25C20.4142 6.25 20.75 6.58579 20.75 7Z"/><path fill-rule="evenodd" clip-rule="evenodd" d="M20.75 12C20.75 12.4142 20.4142 12.75 20 12.75L4 12.75C3.58579 12.75 3.25 12.4142 3.25 12C3.25 11.5858 3.58579 11.25 4 11.25L20 11.25C20.4142 11.25 20.75 11.5858 20.75 12Z"/><path fill-rule="evenodd" clip-rule="evenodd" d="M20.75 17C20.75 17.4142 20.4142 17.75 20 17.75L4 17.75C3.58579 17.75 3.25 17.4142 3.25 17C3.25 16.5858 3.58579 16.25 4 16.25L20 16.25C20.4142 16.25 20.75 16.5858 20.75 17Z"/></svg>';
var ICON_CALENDAR = '<svg width="24" height="24" viewBox="-1.7 -1.7 27.4 27.4" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 12C2 8.22876 2 6.34315 3.17157 5.17157C4.34315 4 6.22876 4 10 4H14C17.7712 4 19.6569 4 20.8284 5.17157C22 6.34315 22 8.22876 22 12V14C22 17.7712 22 19.6569 20.8284 20.8284C19.6569 22 17.7712 22 14 22H10C6.22876 22 4.34315 22 3.17157 20.8284C2 19.6569 2 17.7712 2 14V12Z"/><path d="M7 4V2.5" stroke-linecap="round"/><path d="M17 4V2.5" stroke-linecap="round"/><path d="M2.5 9H21.5" stroke-linecap="round"/><circle cx="17" cy="13" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="13" r="1" fill="currentColor" stroke="none"/><circle cx="7" cy="13" r="1" fill="currentColor" stroke="none"/><circle cx="17" cy="17" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="17" r="1" fill="currentColor" stroke="none"/><circle cx="7" cy="17" r="1" fill="currentColor" stroke="none"/></svg>';
var ICON_CHAT = '<svg width="24" height="24" viewBox="-1.7 -1.7 27.4 27.4" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 22C14.4183 22 18 18.4183 18 14C18 9.58172 14.4183 6 10 6C5.58172 6 2 9.58172 2 14C2 15.2355 2.28 16.4056 2.78 17.4502C2.95 17.8093 3.01 18.2161 2.91 18.6006L2.58 19.8267C2.32 20.793 3.21 21.677 4.17 21.4185L5.4 21.0904C5.78 20.9876 6.19 21.0479 6.55 21.2198C7.59 21.7199 8.76 22 10 22Z"/><path d="M18 14.5C18.07 14.47 18.13 14.45 18.2 14.42C18.56 14.25 18.97 14.19 19.35 14.29L19.83 14.42C20.79 14.68 21.68 13.79 21.42 12.83L21.29 12.35C21.19 11.97 21.25 11.56 21.42 11.2C21.79 10.38 22 9.46 22 8.5C22 4.91 19.09 2 15.5 2C12.8 2 10.48 3.65 9.5 5.99"/><circle cx="6.5" cy="14" r="0.75" fill="currentColor" stroke="none"/><circle cx="10" cy="14" r="0.75" fill="currentColor" stroke="none"/><circle cx="13.5" cy="14" r="0.75" fill="currentColor" stroke="none"/></svg>';
var ICON_DIRECTION_SWAP = '<svg viewBox="5.25 3.25 13.5 17.5" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M10.6634 3.47789C10.9518 3.77526 10.9445 4.25007 10.6471 4.53843L7.8508 7.25H18C18.4142 7.25 18.75 7.58579 18.75 8C18.75 8.41421 18.4142 8.75 18 8.75H7.8508L10.6471 11.4616C10.9445 11.7499 10.9518 12.2247 10.6634 12.5221C10.3751 12.8195 9.90026 12.8268 9.60289 12.5384L5.47789 8.53843C5.33222 8.39717 5.25 8.20291 5.25 8C5.25 7.79709 5.33222 7.60283 5.47789 7.46158L9.60289 3.46158C9.90026 3.17322 10.3751 3.18053 10.6634 3.47789ZM13.3366 11.4779C13.6249 11.1805 14.0997 11.1732 14.3971 11.4616L18.5221 15.4616C18.6678 15.6028 18.75 15.7971 18.75 16C18.75 16.2029 18.6678 16.3972 18.5221 16.5384L14.3971 20.5384C14.0997 20.8268 13.6249 20.8195 13.3366 20.5221C13.0482 20.2247 13.0555 19.7499 13.3529 19.4616L16.1492 16.75L6 16.75C5.58579 16.75 5.25 16.4142 5.25 16C5.25 15.5858 5.58579 15.25 6 15.25L16.1492 15.25L13.3529 12.5384C13.0555 12.2501 13.0482 11.7753 13.3366 11.4779Z"/></svg>';
var ICON_ARROW_RIGHT = '<svg viewBox="8.25 4.25 7.5 15.5" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M8.51192 4.43057C8.82641 4.161 9.29989 4.19743 9.56946 4.51192L15.5695 11.5119C15.8102 11.7928 15.8102 12.2072 15.5695 12.4881L9.56946 19.4881C9.29989 19.8026 8.82641 19.839 8.51192 19.5695C8.19743 19.2999 8.161 18.8264 8.43057 18.5119L14.0122 12L8.43057 5.48811C8.161 5.17361 8.19743 4.70014 8.51192 4.43057Z"/></svg>';

/* --- Push --- */
function _urlBase64ToUint8Array(base64String) {
  var padding = '='.repeat((4 - base64String.length % 4) % 4);
  var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  var raw = atob(base64);
  var arr = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}
