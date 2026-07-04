self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

function ackPush(action) {
  self.registration.pushManager.getSubscription().then(function (sub) {
    if (!sub) return;
    fetch('/chat/api/push/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint, action: action }),
    }).catch(function () { /* best-effort ack */ });
  });
}

self.addEventListener('push', function (event) {
  var data = event.data ? event.data.json() : {};
  var tag = data.tag || 'stc-notification';

  event.waitUntil(
    self.registration.getNotifications({ tag: tag }).then(function (existing) {
      var count = 1;
      if (existing.length > 0 && existing[0].data && existing[0].data.count) {
        count = existing[0].data.count + 1;
      }
      var title = data.title || 'Stone Techno Companion';
      var body = count > 1
        ? count + ' new messages'
        : (data.body || '');
      return self.registration.showNotification(title, {
        body: body,
        icon: '/favicon.png',
        badge: '/favicon.png',
        tag: tag,
        renotify: count === 1,
        data: { url: data.url || '/', count: count },
      });
    }).then(function () {
      ackPush('delivered');
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.preventDefault();
  event.notification.close();
  ackPush('clicked');
  var targetUrl =
    (event.notification.data && event.notification.data.url) ||
    event.notification.tag ||
    '/';
  var fullUrl = new URL(targetUrl, self.location.origin).href;

  event.waitUntil(
    caches.open('stc-push').then(function (cache) {
      return cache.put('/_push_navigate', new Response(targetUrl));
    }).then(function () {
      return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        if ('navigate' in list[i]) {
          return list[i].navigate(fullUrl).then(function (c) {
            return c.focus();
          });
        }
      }
      return self.clients.openWindow(fullUrl);
    }),
  );
});

self.addEventListener('notificationclose', function (event) {
  ackPush('dismissed');
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
    }).catch(function () { /* re-subscribe best-effort */ })
  );
});
