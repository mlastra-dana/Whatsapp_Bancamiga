const CACHE_NAME = 'waba-center-v2';
const APP_SHELL = [
  './',
  './waba-center.html',
  './conversaciones.html',
  './config.js',
  './manifest.webmanifest',
  './assets/brand/logo-danaconnect-horizontal.jpg',
  './assets/brand/favicon-danaconnect-transparent.png',
  './assets/brand/waba-center-icon-192.png',
  './assets/brand/waba-center-icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('./conversaciones.html'))
    );
    return;
  }

  if (url.pathname.includes('/media')) return;

  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        const copy = response.clone();
        if (response.ok && url.origin === self.location.origin) {
          caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        }
        return response;
      });
    })
  );
});
