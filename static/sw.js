// ProFit Studio — Service Worker (se sirve en /sw.js con Service-Worker-Allowed: /).
//
// Privacidad (Ley 1581): este worker NUNCA guarda páginas HTML de la app ni nada que dependa de la
// sesión (fichas de salud, planes, clientas, pagos, comprobantes, media). Solo guarda archivos de
// /static/ (iguales para todas las personas) y la página sin conexión. Por eso al cerrar sesión no
// queda nada personal en Cache Storage; aun así, el mensaje {type: 'logout'} borra la caché de
// estáticos como defensa adicional (base.html lo envía al enviar el formulario de cierre de sesión).
'use strict';

const VERSION = 'v2';
const PRECACHE = `profit-precache-${VERSION}`;
const STATIC_CACHE = `profit-static-${VERSION}`;
const CURRENT_CACHES = [PRECACHE, STATIC_CACHE];
const OFFLINE_URL = '/static/offline.html';

// Rutas que jamás pasan por la caché, aunque una regla futura cambie (segunda barrera).
const NEVER_CACHE = /^\/(media|payments|health|plans|accounts|api|admin|assessments|routines|trainer|whatsapp)(\/|$)/;
// Nombre con huella de ManifestStaticFilesStorage: app.<12 hex>.css. Es inmutable: cache-first.
const HASHED_NAME = /\.[0-9a-f]{12}\.[a-z0-9]+$/i;
const STATIC_EXT = /\.(css|js|mjs|woff2?|ttf|otf|png|jpe?g|webp|avif|gif|svg|ico|json)$/i;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(PRECACHE)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' })))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // Borra profit-v1 (que guardaba cualquier GET) y cualquier versión anterior.
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !CURRENT_CACHES.includes(k)).map((k) => caches.delete(k)));
    if (self.registration.navigationPreload) {
      await self.registration.navigationPreload.enable();
    }
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.origin && event.origin !== self.location.origin) return;
  if (event.data && event.data.type === 'logout') {
    event.waitUntil(caches.delete(STATIC_CACHE));
  }
});

function isCacheableResponse(response) {
  if (!response || !response.ok || response.type !== 'basic') return false;
  const cc = (response.headers.get('Cache-Control') || '').toLowerCase();
  if (/no-store|private/.test(cc)) return false;
  if (/cookie/i.test(response.headers.get('Vary') || '')) return false;
  const type = (response.headers.get('Content-Type') || '').toLowerCase();
  return !type.startsWith('text/html');
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (isCacheableResponse(response)) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

// Estáticos sin huella (desarrollo, offline.html, manifest): red primero para no servir versiones
// viejas; la copia guardada solo se usa sin conexión.
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (isCacheableResponse(response)) {
      const cache = await caches.open(STATIC_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}

async function handleNavigation(event) {
  try {
    const preloaded = await event.preloadResponse;
    if (preloaded) return preloaded;
    return await fetch(event.request);
  } catch (error) {
    const offline = await caches.match(OFFLINE_URL, { cacheName: PRECACHE });
    return offline || Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Documentos: red siempre, nada se guarda. Sin red, página sin conexión.
  if (request.mode === 'navigate') {
    event.respondWith(handleNavigation(event));
    return;
  }

  if (NEVER_CACHE.test(url.pathname)) return;
  if (!url.pathname.startsWith('/static/') || url.search) return;
  if (!STATIC_EXT.test(url.pathname) && url.pathname !== OFFLINE_URL) return;

  event.respondWith(HASHED_NAME.test(url.pathname) ? cacheFirst(request) : networkFirst(request));
});
