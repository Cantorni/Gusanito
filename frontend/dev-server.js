// Local/shareable dev server: serves this folder and proxies /api/* to the Functions host, so the PWA,
// the receiver page and the API share one origin (as relative /api paths expect).
// Run from frontend/: node dev-server.js   (needs the API on :7071: backend/tools/local_api.py or `func start`)
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const FUNCTIONS = new URL(process.env.FUNCTIONS_URL || 'http://127.0.0.1:7071');
const STATIC = {
  '/': 'index.html', '/index.html': 'index.html', '/receiver.html': 'receiver.html', '/sw.js': 'sw.js',
  '/config.js': 'config.js', '/queue.js': 'queue.js', '/manifest.json': 'manifest.json', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png',
};
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.geojson': 'application/geo+json', '.png': 'image/png', '.css': 'text/css', '.woff2': 'font/woff2' };

function proxy(req, res) {
  const upstream = http.request(
    { host: FUNCTIONS.hostname, port: FUNCTIONS.port, path: req.url, method: req.method, headers: { ...req.headers, host: FUNCTIONS.host } },
    up => { res.writeHead(up.statusCode, up.headers); up.pipe(res); });
  upstream.on('error', () => {
    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: `Functions host not reachable at ${FUNCTIONS.origin}. Run backend/tools/local_api.py (or func start).` }));
  });
  req.pipe(upstream);
}

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname.startsWith('/api/')) return proxy(req, res);

  // Landing page of the demo (../landing): same origin as the PWA, so it reuses vendor/ and the offline map.
  const LANDING = path.join(ROOT, '..', 'landing');
  const lf = { '/landing': 'index.html', '/landing/': 'index.html', '/landing/index.html': 'index.html', '/landing/data.json': 'data.json', '/landing/zambia.json': 'zambia.json', '/landing/zambia-outline.geojson': 'zambia-outline.geojson', '/landing/diagrama-despliegue.png': 'diagrama-despliegue.png' }[url.pathname];
  if (lf) {
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(lf)], 'Cache-Control': 'no-cache' });
    return fs.createReadStream(path.join(LANDING, lf)).pipe(res);
  }

  // whitelist: listed files, plus vendor/ and the offline map (path.normalize + prefix check blocks ../)
  const f = STATIC[url.pathname] || (/^\/(vendor\/|zambia\.pmtiles$)/.test(url.pathname) ? url.pathname.slice(1) : null);
  const full = f && path.join(ROOT, path.normalize(f));
  if (!f || !full.startsWith(ROOT + path.sep) || !fs.existsSync(full) || !fs.statSync(full).isFile()) {
    res.writeHead(404); return res.end('not found');
  }
  const total = fs.statSync(full).size;
  const type = TYPES[path.extname(f)] || 'application/octet-stream';
  const m = /bytes=(\d+)-(\d*)/.exec(req.headers.range || ''); // PMTiles needs Range support
  if (m) {
    const start = +m[1], end = Math.min(m[2] ? +m[2] : total - 1, total - 1);
    res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${start}-${end}/${total}`, 'Content-Length': end - start + 1 });
    return fs.createReadStream(full, { start, end }).pipe(res);
  }
  res.writeHead(200, { 'Content-Type': type, 'Content-Length': total, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache' });
  fs.createReadStream(full).pipe(res);
}).listen(process.env.PORT || 8787, '127.0.0.1', () => console.log(`Gusanito on http://127.0.0.1:${process.env.PORT || 8787} (API -> ${FUNCTIONS.origin})`));
