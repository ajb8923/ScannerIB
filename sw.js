// Scanner IB · service worker para uso sin conexión
const CACHE = 'scanner-ib-v2';
const LIBS = [
  'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js',
  'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js'
];
const CORE = ['./', './index.html', './manifest.json', ...LIBS];

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

  // Librerías: siempre desde la copia guardada si existe
  if (LIBS.includes(u.href)) {
    e.respondWith(caches.match(u.href).then(m => m || fetch(e.request).then(r => {
      const cp = r.clone(); caches.open(CACHE).then(c => c.put(u.href, cp)); return r;
    })));
    return;
  }
  if (u.origin !== location.origin) return;

  // Datos (data.json, history.json): primero la red; sin conexión, la última copia
  // Página: igual, así recibes las actualizaciones cuando hay conexión
  const key = u.pathname.endsWith('.json') ? u.origin + u.pathname : e.request;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(key, cp)); }
    return r;
  }).catch(() => caches.match(key, { ignoreSearch: true })
    .then(m => m || caches.match('./index.html'))));
});
