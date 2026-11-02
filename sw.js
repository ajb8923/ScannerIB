// Scanner IB · service worker para uso sin conexión
const CACHE = 'scanner-ib-v1';
const LIB = 'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js';
const CORE = ['./', './index.html', './manifest.json', LIB];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const u = new URL(e.request.url);

  // Datos: primero la red; sin conexión, la última copia guardada
  if (u.pathname.endsWith('/data.json')) {
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put('./data.json', cp)); }
      return r;
    }).catch(() => caches.match('./data.json')));
    return;
  }

  // Librería del gráfico: siempre desde la copia guardada si existe
  if (u.href === LIB) {
    e.respondWith(caches.match(LIB).then(m => m || fetch(e.request).then(r => {
      const cp = r.clone(); caches.open(CACHE).then(c => c.put(LIB, cp)); return r;
    })));
    return;
  }

  // Página: primero la red (así recibes las actualizaciones); sin conexión, la copia
  if (u.origin === location.origin) {
    e.respondWith(fetch(e.request).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(e.request, cp)); }
      return r;
    }).catch(() => caches.match(e.request, { ignoreSearch: true })
      .then(m => m || caches.match('./index.html'))));
  }
});
