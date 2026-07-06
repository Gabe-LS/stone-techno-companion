var SW_VERSION = 'nd-v10';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

function slog(ev, detail, testId) {
  return fetch('/api/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session: self._diagSession || 'unknown',
      src: 'sw',
      ev: ev,
      detail: typeof detail === 'string' ? detail : JSON.stringify(detail),
      test_id: testId || '',
      ts: new Date().toISOString(),
      platform: self._diagPlatform || '',
    }),
  }).catch(function () {});
}

self.addEventListener('message', function (event) {
  if (event.data && event.data.type === 'init') {
    self._diagSession = event.data.session;
    self._diagPlatform = event.data.platform;
    return;
  }
  if (event.data && event.data.type === 'test-getNotifications') {
    var testId = event.data.test_id;
    var filterTag = event.data.filter_tag || undefined;
    var opts = filterTag ? { tag: filterTag } : {};
    self.registration.getNotifications(opts).then(function (list) {
      var mapped = list.map(function (n) {
        return {
          title: n.title,
          body: n.body,
          tag: n.tag,
          data: n.data,
          silent: n.silent,
          renotify: n.renotify,
          requireInteraction: n.requireInteraction,
          actions: n.actions ? n.actions.length : 0,
          timestamp: n.timestamp,
        };
      });
      slog('getNotifications-result', {
        filter_tag: filterTag || null,
        count: list.length,
        notifications: mapped,
      }, testId);
      event.source.postMessage({
        type: 'getNotifications-result',
        test_id: testId,
        count: list.length,
        notifications: mapped,
      });
    }).catch(function (err) {
      slog('getNotifications-error', err.message || String(err), testId);
      event.source.postMessage({
        type: 'getNotifications-result',
        test_id: testId,
        count: -1,
        error: err.message || String(err),
      });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeByTag') {
    var tag = event.data.tag;
    var tid = event.data.test_id;
    self.registration.getNotifications({ tag: tag }).then(function (list) {
      slog('closeByTag-found', { tag: tag, count: list.length }, tid);
      list.forEach(function (n) { n.close(); });
      slog('closeByTag-closed', { tag: tag, closed: list.length }, tid);
      // verify with multiple delays to rule out async processing
      var found = list.length;
      return new Promise(function (r) { setTimeout(r, 500); }).then(function () {
        return self.registration.getNotifications({ tag: tag });
      }).then(function (after500) {
        slog('closeByTag-verify-500ms', { tag: tag, remaining: after500.length }, tid);
        return new Promise(function (r) { setTimeout(r, 2500); });
      }).then(function () {
        return self.registration.getNotifications({ tag: tag });
      }).then(function (after3s) {
        slog('closeByTag-verify-3s', { tag: tag, remaining: after3s.length }, tid);
        event.source.postMessage({
          type: 'closeByTag-result',
          test_id: tid,
          found: found,
          remaining_500ms: after3s.length,
          remaining_3s: after3s.length,
        });
      });
    }).catch(function (err) {
      slog('closeByTag-error', err.message || String(err), tid);
      event.source.postMessage({
        type: 'closeByTag-result',
        test_id: tid,
        found: -1,
        error: err.message || String(err),
      });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeAll') {
    var tid2 = event.data.test_id;
    self.registration.getNotifications().then(function (list) {
      list.forEach(function (n) { n.close(); });
      slog('closeAll', { closed: list.length }, tid2);
      event.source.postMessage({ type: 'closeAll-result', test_id: tid2, closed: list.length });
    }).catch(function (err) {
      slog('closeAll-error', err.message, tid2);
      event.source.postMessage({ type: 'closeAll-result', test_id: tid2, error: err.message });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeUnfiltered') {
    var cuf_tag = event.data.target_tag;
    var cuf_tid = event.data.test_id;
    // get ALL notifications (no tag filter), then close only the matching one
    self.registration.getNotifications().then(function (list) {
      var matching = list.filter(function (n) { return n.tag === cuf_tag; });
      var others = list.filter(function (n) { return n.tag !== cuf_tag; });
      slog('closeUnfiltered-found', { target_tag: cuf_tag, matching: matching.length, total: list.length }, cuf_tid);
      matching.forEach(function (n) { n.close(); });
      // verify at multiple delays
      var results = { found: matching.length, total: list.length };
      return new Promise(function (r) { setTimeout(r, 1000); }).then(function () {
        return self.registration.getNotifications();
      }).then(function (a1) {
        results.after_1s = a1.filter(function (n) { return n.tag === cuf_tag; }).length;
        return new Promise(function (r) { setTimeout(r, 2000); });
      }).then(function () {
        return self.registration.getNotifications();
      }).then(function (a3) {
        results.after_3s = a3.filter(function (n) { return n.tag === cuf_tag; }).length;
        return new Promise(function (r) { setTimeout(r, 5000); });
      }).then(function () {
        return self.registration.getNotifications();
      }).then(function (a8) {
        results.after_8s = a8.filter(function (n) { return n.tag === cuf_tag; }).length;
        slog('closeUnfiltered-verify', results, cuf_tid);
        event.source.postMessage({ type: 'closeUnfiltered-result', test_id: cuf_tid, results: results });
      });
    }).catch(function (err) {
      slog('closeUnfiltered-error', err.message, cuf_tid);
      event.source.postMessage({ type: 'closeUnfiltered-result', test_id: cuf_tid, error: err.message });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeDelayed') {
    var cd_tag = event.data.target_tag;
    var cd_tid = event.data.test_id;
    var cd_use_filter = event.data.use_filter;
    var getOpts = cd_use_filter ? { tag: cd_tag } : {};
    self.registration.getNotifications(getOpts).then(function (list) {
      var toClose = cd_use_filter ? list : list.filter(function (n) { return n.tag === cd_tag; });
      slog('closeDelayed-found', { tag: cd_tag, filtered: cd_use_filter, count: toClose.length }, cd_tid);
      toClose.forEach(function (n) { n.close(); });
      var delays = [1000, 3000, 5000, 8000, 12000];
      var results = { found: toClose.length, filtered: cd_use_filter };
      function checkAt(i) {
        if (i >= delays.length) {
          slog('closeDelayed-done', results, cd_tid);
          event.source.postMessage({ type: 'closeDelayed-result', test_id: cd_tid, results: results });
          return Promise.resolve();
        }
        var ms = delays[i];
        var prevMs = i > 0 ? delays[i - 1] : 0;
        return new Promise(function (r) { setTimeout(r, ms - prevMs); }).then(function () {
          return self.registration.getNotifications(getOpts);
        }).then(function (after) {
          var remaining = cd_use_filter ? after.length : after.filter(function (n) { return n.tag === cd_tag; }).length;
          results['after_' + (ms / 1000) + 's'] = remaining;
          slog('closeDelayed-check', { ms: ms, remaining: remaining }, cd_tid);
          return checkAt(i + 1);
        });
      }
      return checkAt(0);
    }).catch(function (err) {
      slog('closeDelayed-error', err.message, cd_tid);
      event.source.postMessage({ type: 'closeDelayed-result', test_id: cd_tid, error: err.message });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeAllVerified') {
    var cav_tid = event.data.test_id;
    self.registration.getNotifications().then(function (list) {
      slog('closeAllVerified-found', { count: list.length }, cav_tid);
      list.forEach(function (n) { n.close(); });
      var results = { found: list.length };
      return new Promise(function (r) { setTimeout(r, 3000); }).then(function () {
        return self.registration.getNotifications();
      }).then(function (a3) {
        results.after_3s = a3.length;
        return new Promise(function (r) { setTimeout(r, 5000); });
      }).then(function () {
        return self.registration.getNotifications();
      }).then(function (a8) {
        results.after_8s = a8.length;
        slog('closeAllVerified-done', results, cav_tid);
        event.source.postMessage({ type: 'closeAllVerified-result', test_id: cav_tid, results: results });
      });
    }).catch(function (err) {
      slog('closeAllVerified-error', err.message, cav_tid);
      event.source.postMessage({ type: 'closeAllVerified-result', test_id: cav_tid, error: err.message });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeFireForgetSW') {
    var fff_tid = event.data.test_id;
    var fff_group = event.data.group;
    self.registration.getNotifications().then(function (list) {
      var targets = list.filter(function (n) {
        if (!n.data) return false;
        return n.data.group === fff_group || n.data.roomId === fff_group;
      });
      targets.forEach(function (n) { n.close(); });
      slog('closeFireForgetSW', { group: fff_group, closed: targets.length, total: list.length }, fff_tid);
      event.source.postMessage({ type: 'closeFireForgetSW-result', test_id: fff_tid, closed: targets.length });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeByTagFireForget') {
    var cbtff_tag = event.data.tag;
    var cbtff_tid = event.data.test_id;
    self.registration.getNotifications().then(function (list) {
      var targets = list.filter(function (n) { return n.tag === cbtff_tag; });
      targets.forEach(function (n) { n.close(); });
      slog('closeByTagFireForget', { tag: cbtff_tag, closed: targets.length, total: list.length }, cbtff_tid);
      event.source.postMessage({ type: 'closeByTagFireForget-result', test_id: cbtff_tid, closed: targets.length });
    });
    return;
  }
  if (event.data && event.data.type === 'test-closeThenShowSW') {
    var cts_tid = event.data.test_id;
    var cts_roomId = event.data.roomId;
    var cts_title = event.data.newTitle;
    var cts_opts = event.data.newOptions;
    self.registration.getNotifications().then(function (list) {
      var old = list.filter(function (n) { return n.data && n.data.roomId === cts_roomId; });
      old.forEach(function (n) { n.close(); });
      // show new notification immediately after close — NO getNotifications after
      return self.registration.showNotification(cts_title, cts_opts);
    }).then(function () {
      slog('closeThenShowSW', { roomId: cts_roomId, title: cts_title }, cts_tid);
      event.source.postMessage({ type: 'closeThenShowSW-result', test_id: cts_tid, ok: true });
    });
    return;
  }
  if (event.data && event.data.type === 'test-showNotification') {
    var opts2 = event.data.options || {};
    var tid3 = event.data.test_id;
    slog('showNotification-request', { title: event.data.title, options: opts2 }, tid3);
    self.registration.showNotification(event.data.title || 'Test', opts2).then(function () {
      slog('showNotification-done', { tag: opts2.tag || '(none)' }, tid3);
      event.source.postMessage({ type: 'showNotification-result', test_id: tid3, ok: true });
    }).catch(function (err) {
      slog('showNotification-error', err.message, tid3);
      event.source.postMessage({ type: 'showNotification-result', test_id: tid3, ok: false, error: err.message });
    });
    return;
  }
  if (event.data && event.data.type === 'test-pruneAndShow') {
    var pd = event.data;
    var tid4 = pd.test_id;
    var groupField = pd.group_field || 'roomId';
    var groupValue = pd.group_value;
    var newTitle = pd.title || 'Updated';
    var newOpts = pd.options || {};

    self.registration.getNotifications().then(function (list) {
      var matching = list.filter(function (n) {
        return n.data && n.data[groupField] === groupValue;
      });
      slog('pruneAndShow-found', { group: groupValue, matching: matching.length, total: list.length }, tid4);
      matching.forEach(function (n) { n.close(); });
      return self.registration.showNotification(newTitle, newOpts);
    }).then(function () {
      slog('pruneAndShow-done', { group: groupValue }, tid4);
      event.source.postMessage({ type: 'pruneAndShow-result', test_id: tid4, ok: true });
    }).catch(function (err) {
      slog('pruneAndShow-error', err.message, tid4);
      event.source.postMessage({ type: 'pruneAndShow-result', test_id: tid4, ok: false, error: err.message });
    });
    return;
  }
});

self.addEventListener('push', function (event) {
  var raw = event.data ? event.data.text() : '';
  var data = {};
  try { data = JSON.parse(raw); } catch (e) { data = {}; }

  var testId = data.test_id || '';
  var title = data.title || 'Notif Diag Push';
  var options = {
    body: data.body || '',
    icon: data.icon,
    badge: data.badge,
    tag: data.tag || '',
    data: data.data || { test_id: testId, push_index: data.push_index },
    silent: data.silent === true ? true : undefined,
    renotify: data.renotify === true ? true : undefined,
    requireInteraction: data.requireInteraction === true ? true : undefined,
    timestamp: data.timestamp || undefined,
    actions: data.actions || undefined,
    vibrate: data.vibrate || undefined,
    image: data.image || undefined,
  };

  // clean undefineds for platforms that choke on them
  Object.keys(options).forEach(function (k) {
    if (options[k] === undefined) delete options[k];
  });

  var preGetNotifications = null;

  event.waitUntil(
    // snapshot current notifications BEFORE showing new one
    self.registration.getNotifications().then(function (list) {
      preGetNotifications = list.map(function (n) {
        return { title: n.title, tag: n.tag, data: n.data };
      });
      return Promise.resolve();
    }).catch(function () {
      preGetNotifications = 'error';
    }).then(function () {
      return slog('push-received', {
        test_id: testId,
        push_index: data.push_index,
        seq_index: data.seq_index,
        title: title,
        tag: options.tag,
        silent: options.silent,
        renotify: options.renotify,
        requireInteraction: options.requireInteraction,
        actions_count: (options.actions || []).length,
        has_data: !!options.data,
        pre_existing_count: Array.isArray(preGetNotifications) ? preGetNotifications.length : preGetNotifications,
        pre_existing: preGetNotifications,
      }, testId);
    }).then(function () {
      return self.registration.showNotification(title, options);
    }).then(function () {
      return slog('push-shown', { tag: options.tag, test_id: testId }, testId);
    }).then(function () {
      // snapshot AFTER showing to see replacement behavior
      return self.registration.getNotifications();
    }).then(function (postList) {
      var postMapped = postList.map(function (n) {
        return { title: n.title, tag: n.tag, data: n.data };
      });
      return slog('push-post-state', {
        test_id: testId,
        post_count: postList.length,
        post_existing: postMapped,
        pre_count: Array.isArray(preGetNotifications) ? preGetNotifications.length : '?',
        tag_used: options.tag,
      }, testId);
    }).catch(function (err) {
      return slog('push-error', err.message || String(err), testId);
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  var tag = '', title = '', data = {}, action = '';
  try { tag = event.notification.tag; } catch (e) {}
  try { title = event.notification.title; } catch (e) {}
  try { data = event.notification.data || {}; } catch (e) { data = {}; }
  try { action = event.action || ''; } catch (e) {}

  var logPayload = JSON.stringify({
    src: 'sw', ev: 'notificationclick', ts: new Date().toISOString(),
    session: self._diagSession || 'unknown', test_id: data.test_id || '',
    detail: JSON.stringify({
      tag: tag, action: action, title: title, data: data,
      data_is_null: data === null, data_type: typeof data,
    }),
  });

  // LOCAL FIRST: cache breadcrumb before any network call
  event.waitUntil(
    caches.open('notif-diag').then(function (cache) {
      return cache.put('/_last_click', new Response(logPayload));
    }).catch(function () {}).then(function () {
      // sendBeacon is more reliable than fetch for fire-and-forget on iOS
      try {
        navigator.sendBeacon('/api/log', new Blob([logPayload], { type: 'application/json' }));
      } catch (e) {}
      // also try fetch as backup
      return slog('notificationclick', {
        tag: tag, action: action, title: title, data: data,
        data_is_null: data === null, data_type: typeof data,
      }, data.test_id || '');
    }).then(function () {
      try { event.notification.close(); } catch (e) {}
    })
  );
});

self.addEventListener('notificationclose', function (event) {
  var data = {};
  try { data = event.notification.data || {}; } catch (e) {}
  var tag = '';
  try { tag = event.notification.tag; } catch (e) {}
  var title = '';
  try { title = event.notification.title; } catch (e) {}

  event.waitUntil(
    caches.open('notif-diag').then(function (cache) {
      return cache.put('/_last_close', new Response(JSON.stringify({
        ev: 'notificationclose', tag: tag, title: title, ts: new Date().toISOString(),
      })));
    }).catch(function () {}).then(function () {
      try {
        navigator.sendBeacon('/api/log', new Blob([JSON.stringify({
          src: 'sw', ev: 'notificationclose', ts: new Date().toISOString(),
          session: self._diagSession || 'unknown', test_id: data.test_id || '',
          detail: JSON.stringify({ tag: tag, title: title, data: data }),
        })], { type: 'application/json' }));
      } catch (e) {}
      return slog('notificationclose', { tag: tag, title: title, data: data }, data.test_id || '');
    })
  );
});
