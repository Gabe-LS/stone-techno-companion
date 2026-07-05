self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

var SW_VERSION = 'v10';

function swlog(step, detail) {
  return fetch('/chat/api/swlog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src: 'sw', v: SW_VERSION, step: step, detail: detail || null }),
  }).catch(function () {});
}

function ackPush(action, url) {
  return self.registration.pushManager.getSubscription().then(function (sub) {
    if (!sub) return;
    return fetch('/chat/api/push/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint, action: action, v: SW_VERSION, url: url || null }),
    });
  }).catch(function () {});
}

self.addEventListener('push', function (event) {
  var raw = event.data ? event.data.text() : '';
  var data = {};
  try { data = JSON.parse(raw); } catch (e) { data = {}; }

  var title = data.title || 'Stone Techno Companion';
  var body = data.body || '';
  // push_index resets to 0 on every server restart -- data.push_id is a
  // fresh random value per push so restarts can never reuse a still-visible
  // notification's tag (iOS silently drops notificationclick on replaced ones).
  var tag = 'stc-' + (data.room_id || '') + '-' + (data.push_id || data.push_index || Math.random().toString(36).slice(2));
  var options = {
    body: body,
    icon: '/favicon.png',
    badge: '/favicon.png',
    tag: tag,
    data: { url: data.url || '/', roomId: data.room_id, count: data.count },
    silent: data.silent || false,
  };

  event.waitUntil(
    self.registration.getNotifications().then(function (list) {
      if (data.room_id) {
        list.filter(function (n) {
          return n.data && n.data.roomId === data.room_id;
        }).forEach(function (n) { n.close(); });
      }
      return self.registration.showNotification(title, options);
    }).then(function () {
      if (data.total_unread && navigator.setAppBadge) {
        navigator.setAppBadge(data.total_unread);
      }
      return Promise.all([swlog('push-received', data.url), ackPush('delivered', data.url)]);
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.preventDefault();
  event.notification.close();
  var targetUrl =
    (event.notification.data && event.notification.data.url) || '/';
  var fullUrl = new URL(targetUrl, self.location.origin).href;

  event.waitUntil(
    caches.open('stc-push').then(function (cache) {
      return cache.put('/_push_navigate', new Response(targetUrl));
    }).catch(function () {}).then(function () {
      return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    }).then(function (list) {
      if (list.length > 0) {
        list[0].postMessage({ type: 'navigate', url: fullUrl });
        try { list[0].focus(); } catch (e) {}
        return 'postmessage:' + list[0].url + ' vis=' + list[0].visibilityState;
      }
      return self.clients.openWindow(fullUrl).then(function (c) {
        return 'openwindow:' + (c ? c.url : 'null');
      }).catch(function (e) {
        return 'openwindow-failed:' + String(e && e.message);
      });
    }).then(function (result) {
      return Promise.all([swlog('click-done', result), ackPush('clicked', targetUrl)]);
    })
  );
});

self.addEventListener('notificationclose', function (event) {
  event.waitUntil(ackPush('dismissed'));
});

self.addEventListener('pushsubscriptionchange', function (event) {
  if (!event.oldSubscription) return;
  event.waitUntil(
    self.registration.pushManager.subscribe(event.oldSubscription.options).then(function (sub) {
      return fetch('/chat/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          endpoint: sub.endpoint,
          keys: {
            p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(sub.getKey('p256dh')))),
            auth: btoa(String.fromCharCode.apply(null, new Uint8Array(sub.getKey('auth')))),
          },
        }),
      });
    }).catch(function () {})
  );
});
