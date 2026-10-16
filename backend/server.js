// Local/shareable dev server: serves ../frontend and runs the Azure Functions handlers in this folder.
// Run from backend/: pnpm install && node server.js   (reads backend/.env)
const http = require('http');
const fs = require('fs');
const path = require('path');

for (const line of (fs.existsSync(path.join(__dirname, '.env')) ? fs.readFileSync(path.join(__dirname, '.env'), 'utf8') : '').split('\n')) {
  const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
  if (m && m[2] && !(m[1] in process.env)) process.env[m[1]] = m[2];
}

const ROOT = path.join(__dirname, '..', 'frontend');
const STATIC = {
  '/': 'index.html', '/index.html': 'index.html', '/receiver.html': 'receiver.html', '/sw.js': 'sw.js',
  '/manifest.json': 'manifest.json', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png',
};
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.css': 'text/css', '.woff2': 'font/woff2' };
const ROUTES = {
  '/api/maps-token': require('./maps-token'),
  '/api/analyze-point': require('./analyze-point'),
  '/api/report': require('./report'),
};
const MAX_BODY = 9_000_000;

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  const handler = ROUTES[url.pathname];

  if (!handler) {
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
    return fs.createReadStream(full).pipe(res);
  }

  const chunks = [];
  let size = 0;
  req.on('data', c => {
    size += c.length;
    if (size > MAX_BODY) { res.writeHead(413); res.end('too large'); req.destroy(); } else chunks.push(c);
  });
  req.on('end', async () => {
    if (res.writableEnded) return;
    let body;
    try { body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined; } catch { res.writeHead(400); return res.end('bad json'); }
    const ctx = { res: null, log: console.log };
    try {
      await handler(ctx, { method: req.method, query: Object.fromEntries(url.searchParams), body, headers: req.headers });
    } catch (e) {
      console.error(e);
      ctx.res = { status: 500, body: 'server error' };
    }
    const { status = 200, headers = {}, body: out = '' } = ctx.res;
    res.writeHead(status, headers);
    res.end(out);
  });
}).listen(process.env.PORT || 8787, '127.0.0.1', () => console.log(`Gusanito on http://127.0.0.1:${process.env.PORT || 8787}`));
