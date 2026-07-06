var DIAG_DB_NAME = 'push-diag';
var DIAG_STORE = 'log';

function _openDB() {
  return new Promise(function (resolve, reject) {
    var req = indexedDB.open(DIAG_DB_NAME, 1);
    req.onupgradeneeded = function () {
      var db = req.result;
      if (!db.objectStoreNames.contains(DIAG_STORE)) {
        db.createObjectStore(DIAG_STORE, { autoIncrement: true });
      }
    };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}

function diagLog(source, event, detail) {
  var entry = {
    ts: new Date().toISOString(),
    ms: performance.now ? Math.round(performance.now()) : 0,
    src: source,
    ev: event,
    detail: detail || '',
    url: (typeof location !== 'undefined') ? location.pathname + location.search + location.hash : '(sw)',
    vis: (typeof document !== 'undefined') ? document.visibilityState : '(sw)',
    ua: (typeof navigator !== 'undefined') ? navigator.userAgent.slice(0, 120) : '',
  };
  return _openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(DIAG_STORE, 'readwrite');
      tx.objectStore(DIAG_STORE).add(entry);
      tx.oncomplete = function () { resolve(entry); };
      tx.onerror = function () { reject(tx.error); };
    });
  }).catch(function (e) {
    if (typeof console !== 'undefined') console.warn('[DIAG] log failed:', e);
    return entry;
  });
}

function diagReadAll() {
  return _openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(DIAG_STORE, 'readonly');
      var req = tx.objectStore(DIAG_STORE).getAll();
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  });
}

function diagClear() {
  return _openDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(DIAG_STORE, 'readwrite');
      tx.objectStore(DIAG_STORE).clear();
      tx.oncomplete = function () { resolve(); };
      tx.onerror = function () { reject(tx.error); };
    });
  });
}

function diagCheckCache() {
  if (typeof caches === 'undefined') return Promise.resolve(null);
  return caches.open('diag-push').then(function (c) {
    return c.match('/_nav').then(function (r) {
      return r ? r.text() : null;
    });
  }).catch(function () { return null; });
}

function diagDeleteCache() {
  if (typeof caches === 'undefined') return Promise.resolve();
  return caches.open('diag-push').then(function (c) {
    return c.delete('/_nav');
  }).catch(function () {});
}
