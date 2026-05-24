// ProFit Studio — Service Worker
const CACHE = 'profit-v1';
const OFFLINE_URL = '/';

// Archivos a pre-cachear (app shell)
const PRECACHE = [
  '/',
  '/static/manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  // Solo cachear GET de misma origin
  if (e.request.method !== 'GET' || !e.request.url.startsWith(self.location.origin)) return;

  // Estrategia Network First: intenta red, si falla usa caché
  e.respondWith(
    fetch(e.request)
      .then(res => {
        // Actualizar caché con respuesta fresca
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match(OFFLINE_URL)))
  );
});
