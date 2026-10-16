// Gusanito Service Worker: offline app shell + offline Zambia vector map (PMTiles)
const CACHE = 'gusanito-v9';
const MAP_URL = '/zambia.pmtiles';
const APP_SHELL = [
  '/vendor/fonts/atkinson-400.woff2', '/vendor/fonts/atkinson-700.woff2',
  '/', '/index.html', '/config.js', '/manifest.json',
  '/vendor/leaflet.css', '/vendor/leaflet.js', '/vendor/protomaps-leaflet.js',
  '/vendor/images/marker-icon.png', '/vendor/images/marker-icon-2x.png', '/vendor/images/marker-shadow.png',
  '/vendor/images/layers.png', '/vendor/images/layers-2x.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', e => {
  if (e.data && e.data.type === 'CHECK_CACHE') e.waitUntil(ensureMap());
});

async function broadcast(msg) {
  (await self.clients.matchAll({ includeUncontrolled: true })).forEach(c => c.postMessage(msg));
}

// Download the whole PMTiles file once (progress reported to the page).
async function ensureMap() {
  const cache = await caches.open(CACHE);
  if (await cache.match(MAP_URL)) return broadcast({ type: 'CACHE_READY' });
  try {
    const res = await fetch(MAP_URL);
    if (!res.ok) return broadcast({ type: 'CACHE_UNAVAILABLE' }); // map file not deployed
    const total = +res.headers.get('content-length') || 0;
    const reader = res.body.getReader();
    const parts = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value); got += value.length;
      if (total) broadcast({ type: 'CACHE_PROGRESS', pct: (got / total) * 100 });
    }
    await cache.put(MAP_URL, new Response(new Blob(parts), { headers: { 'Content-Type': 'application/octet-stream' } }));
    broadcast({ type: 'CACHE_READY' });
  } catch (_) {
    broadcast({ type: 'CACHE_RETRY' }); // offline or interrupted: retried on next load
  }
}

// PMTiles reads the file with HTTP Range requests; Cache API has no ranges, so slice the cached blob.
async function serveMap(req) {
  const cached = await (await caches.open(CACHE)).match(MAP_URL);
  if (!cached) return fetch(req);
  const blob = await cached.blob();
  const m = /bytes=(\d+)-(\d*)/.exec(req.headers.get('range') || '');
  if (!m) return new Response(blob, { headers: { 'Content-Type': 'application/octet-stream' } });
  const start = +m[1], end = Math.min(m[2] ? +m[2] : blob.size - 1, blob.size - 1);
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: { 'Content-Type': 'application/octet-stream', 'Content-Range': `bytes ${start}-${end}/${blob.size}` },
  });
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== 'GET') return;

  if (url.pathname === MAP_URL) return e.respondWith(serveMap(e.request));
  if (url.pathname.startsWith('/api/')) return; // network only
  if (APP_SHELL.includes(url.pathname)) {
    e.respondWith(caches.match(url.pathname).then(c => c || fetch(e.request)));
  }
});
