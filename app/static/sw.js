// DiagTrack Service Worker
const CACHE_NAME = 'diagtrack-shell-v1';
const PRECACHE_URLS = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/icons.svg',
  '/static/img/brand-mark.svg',
  '/static/manifest.json',
  '/static/fonts/ibm-plex-sans-latin-400-normal.woff2',
  '/static/fonts/ibm-plex-sans-latin-500-normal.woff2',
  '/static/fonts/ibm-plex-sans-latin-600-normal.woff2'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_URLS).catch((err) => {
        console.warn('DiagTrack precache error:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // For static assets: cache-first with network fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((res) => {
          if (res.ok && res.type === 'basic') {
            const clone = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, clone));
          }
          return res;
        });
      })
    );
    return;
  }

  // For page navigations: network-first, fallback to cached home page
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => {
        return caches.match(req).then((cached) => {
          return cached || caches.match('/');
        });
      })
    );
  }
});
