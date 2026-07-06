importScripts('shared.js');

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  var raw = event.data ? event.data.text() : '';
  var data = {};
  try { data = JSON.parse(raw); } catch (e) { data = {}; }

  var title = (data.notification && data.notification.title) || data.title || 'Diag Push';
  var body = (data.notification && data.notification.body) || data.body || '';
  var tag = (data.notification && data.notification.tag) || data.tag || 'diag';
  var navUrl = (data.notification && data.notification.navigate) || data.url || '/';
  var strategy = data.strategy || 'default';

  event.waitUntil(
    diagLog('sw', 'push-received', JSON.stringify({
      title: title, body: body, tag: tag, navUrl: navUrl, strategy: strategy,
      hasDeclarative: !!(data.notification && data.notification.navigate),
      rawLength: raw.length,
    })).then(function () {
      return self.registration.showNotification(title, {
        body: body,
        tag: tag,
        renotify: true,
        data: { url: navUrl, strategy: strategy },
      });
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.preventDefault();
  event.notification.close();

  var navUrl = (event.notification.data && event.notification.data.url) || '/';
  var strategy = (event.notification.data && event.notification.data.strategy) || 'default';
  var fullUrl = new URL(navUrl, self.location.origin).href;

  event.waitUntil(
    diagLog('sw', 'click-start', JSON.stringify({ navUrl: navUrl, fullUrl: fullUrl, strategy: strategy }))
    .then(function () {
      return caches.open('diag-push').then(function (cache) {
        return cache.put('/_nav', new Response(navUrl));
      });
    })
    .then(function () {
      return diagLog('sw', 'cache-written', navUrl);
    })
    .then(function () {
      return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    })
    .then(function (list) {
      var clientInfo = list.map(function (c) {
        return { url: c.url, vis: c.visibilityState, focused: c.focused, type: c.type };
      });
      return diagLog('sw', 'clients-found', JSON.stringify(clientInfo)).then(function () {
        return { list: list, info: clientInfo };
      });
    })
    .then(function (result) {
      var list = result.list;

      if (strategy === 'postMessage' || strategy === 'default') {
        for (var i = 0; i < list.length; i++) {
          if (list[i].visibilityState === 'visible' || strategy === 'postMessage') {
            list[i].postMessage({ type: 'navigate', url: fullUrl });
            diagLog('sw', 'action-postMessage', JSON.stringify({ clientUrl: list[i].url, vis: list[i].visibilityState }));
            try { list[i].focus(); } catch (e) {}
            return;
          }
        }
      }

      if (strategy === 'navigate') {
        for (var j = 0; j < list.length; j++) {
          if ('navigate' in list[j]) {
            return diagLog('sw', 'action-navigate', JSON.stringify({ clientUrl: list[j].url }))
              .then(function () {
                return list[j].navigate(fullUrl).then(function (c) {
                  diagLog('sw', 'navigate-resolved', c ? c.url : '(null)');
                  if (c) try { c.focus(); } catch (e) {}
                }).catch(function (err) {
                  diagLog('sw', 'navigate-failed', err.message || String(err));
                });
              });
          }
        }
      }

      if (strategy === 'navigate+catch') {
        for (var k = 0; k < list.length; k++) {
          if ('navigate' in list[k]) {
            return diagLog('sw', 'action-navigate+catch', JSON.stringify({ clientUrl: list[k].url }))
              .then(function () {
                return list[k].navigate(fullUrl).then(function (c) {
                  diagLog('sw', 'navigate-resolved', c ? c.url : '(null)');
                  if (c) try { c.focus(); } catch (e) {}
                }).catch(function (err) {
                  diagLog('sw', 'navigate-catch-fallback', err.message);
                  return self.clients.openWindow(fullUrl).then(function () {
                    diagLog('sw', 'openWindow-after-navigate-fail', fullUrl);
                  }).catch(function (err2) {
                    diagLog('sw', 'openWindow-also-failed', err2.message);
                  });
                });
              });
          }
        }
      }

      if (strategy === 'openWindow' || strategy === 'default') {
        return diagLog('sw', 'action-openWindow', JSON.stringify({ clientCount: list.length, fullUrl: fullUrl }))
          .then(function () {
            return self.clients.openWindow(fullUrl).then(function (c) {
              diagLog('sw', 'openWindow-resolved', c ? c.url : '(null)');
            }).catch(function (err) {
              diagLog('sw', 'openWindow-failed', err.message || String(err));
            });
          });
      }

      if (strategy === 'focus-only') {
        if (list.length > 0) {
          try { list[0].focus(); } catch (e) {}
          return diagLog('sw', 'action-focus-only', 'relied on cache fallback');
        }
        return diagLog('sw', 'action-focus-only-no-clients', 'no clients to focus');
      }

      return diagLog('sw', 'action-none', 'unknown strategy: ' + strategy);
    })
  );
});

self.addEventListener('notificationclose', function () {
  diagLog('sw', 'notification-dismissed', '');
});
