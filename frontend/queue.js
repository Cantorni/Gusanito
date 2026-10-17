// Offline-first writes: photo reports and SMS subscriptions wait in IndexedDB until there is signal.
// Loaded by index.html and by sw.js (Background Sync), so both flush the same queue. Every request is
// idempotent on the server (report id, phone number), so a double flush never stores anything twice.
const GusanitoQueue = (() => {
  const DB = 'gusanito', STORE = 'outbox';

  function open() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'key' });
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }

  async function run(mode, fn) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const req = fn(t.objectStore(STORE));
      t.oncomplete = () => { db.close(); resolve(req.result); };
      t.onerror = () => { db.close(); reject(t.error); };
    });
  }

  // item: { key, path, body }. Same key replaces the queued one (a newer subscription for that phone wins).
  const add = item => run('readwrite', s => s.put({ ...item, queuedAt: Date.now() }));
  const all = () => run('readonly', s => s.getAll());
  const remove = key => run('readwrite', s => s.delete(key));

  let flushing = null;
  // Sends oldest first and stops at the first network failure or server error (retried later).
  // A 4xx other than 408/429 can never succeed (invalid body): it is dropped so it does not block the rest.
  function flush(apiBase) {
    if (flushing) return flushing;
    flushing = (async () => {
      let sent = 0, dropped = 0;
      const items = (await all()).sort((a, b) => a.queuedAt - b.queuedAt);
      for (const item of items) {
        let r;
        try {
          r = await fetch(apiBase + item.path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item.body) });
        } catch (_) { break; }
        if (r.ok) { await remove(item.key); sent++; }
        else if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) { await remove(item.key); dropped++; }
        else break;
      }
      return { sent, dropped, pending: (await all()).length };
    })().finally(() => { flushing = null; });
    return flushing;
  }

  return { add, all, flush };
})();
