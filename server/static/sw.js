self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

var SW_VERSION = 'v8';

function swlog(step, detail) {
  return fetch('/chat/api/swlog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src: 'sw', v: SW_VERSION, step: step, detail: detail || null }),
  }).catch(function () { /* best-effort */ });
}

function ackPush(action, url) {
  return self.registration.pushManager.getSubscription().then(function (sub) {
    if (!sub) return;
    return fetch('/chat/api/push/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint, action: action, v: SW_VERSION, url: url || null }),
    });
  }).catch(function () { /* best-effort ack */ });
}

self.addEventListener('push', function (event) {
  var data = event.data ? event.data.json() : {};
  var title = data.title || 'Stone Techno Companion';
  // Tag MUST be unique per notification: iOS drops notificationclick on a
  // notification that replaced an earlier one (same tag + renotify), the tap
  // then opens the app at start_url with no event. Never reuse tags.
  var options = {
    body: data.body || '',
    icon: '/favicon.png',
    badge: '/favicon.png',
    tag: 'stc-' + (data.url || Math.random().toString(36).slice(2)),
    data: { url: data.url || '/' },
  };
  event.waitUntil(
    self.registration.showNotification(title, options).then(function () {
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
    // All local work first: iOS may kill the SW moments after the app
    // foregrounds, so no network before cache write + postMessage.
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
