var CACHE_NAME = 'shadematch-static-v1';

var PRECACHE_URLS = [
  '/static/mixbox.js',
  '/static/main.js',
  '/static/timer.js',
  '/static/cookie-consent.js',
  '/static/cookie-consent.css',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(PRECACHE_URLS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names
          .filter(function (name) { return name !== CACHE_NAME; })
          .map(function (name) { return caches.delete(name); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  // Network-first for navigation and API/session endpoints
  if (event.request.mode === 'navigate' ||
      url.pathname.startsWith('/save_session') ||
      url.pathname.startsWith('/register') ||
      url.pathname.startsWith('/calculate') ||
      url.pathname.startsWith('/login')) {
    event.respondWith(
      fetch(event.request).catch(function () {
        return caches.match(event.request);
      })
    );
    return;
  }

  // Cache-first for precached static assets
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(function (cached) {
        return cached || fetch(event.request).then(function (response) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function (cache) {
            cache.put(event.request, clone);
          });
          return response;
        });
      })
    );
    return;
  }

  // Default: network only
  event.respondWith(fetch(event.request));
});
