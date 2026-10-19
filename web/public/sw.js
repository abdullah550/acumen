/* Acumen service worker: offline shell + last-known data.
   - App shell: cache first (versioned by build hash).
   - data/meta.json: network first, fall back to the last copy.
   - other data files: stale-while-revalidate keyed by their ?v= version, so a new refresh is picked up immediately. */
const BUILD = '__BUILD__';
const CORE = __CORE__;   // the app cannot start without these: if one is missing the install fails and is retried
const EXTRA = __EXTRA__; // icons and fonts: cached when available, but never allowed to block the install
const SHELL_CACHE = 'acumen-shell-' + BUILD;
const DATA_CACHE = 'acumen-data-v1';

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL_CACHE);
    await c.addAll(CORE);
    await Promise.allSettled(EXTRA.map(u => c.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k.startsWith('acumen-shell-') && k !== SHELL_CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

async function trimData(cache, max) {
  const keys = await cache.keys();
  if (keys.length > max) await Promise.all(keys.slice(0, keys.length - max).map(k => cache.delete(k)));
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const rel = url.pathname.slice(url.pathname.lastIndexOf('/data/') >= 0 ? url.pathname.lastIndexOf('/data/') + 1 : 0);

  if (/\/data\/meta\.json$/.test(url.pathname)) {
    e.respondWith((async () => {
      const cache = await caches.open(DATA_CACHE);
      try {
        const res = await fetch(req, { cache: 'no-store' });
        if (res.ok) cache.put(url.pathname, res.clone());
        return res;
      } catch (err) {
        const hit = await cache.match(url.pathname);
        if (hit) return hit;
        throw err;
      }
    })());
    return;
  }

  if (/\/data\//.test(url.pathname)) {
    e.respondWith((async () => {
      const cache = await caches.open(DATA_CACHE);
      const hit = await cache.match(req);
      if (hit) return hit; // versioned URL: contents never change
      try {
        const res = await fetch(req);
        if (res.ok) { cache.put(req, res.clone()); trimData(cache, 700); }
        return res;
      } catch (err) {
        // offline and this exact version is not cached: fall back to any older copy of the same file
        const keys = await cache.keys();
        const older = keys.find(k => new URL(k.url).pathname === url.pathname);
        if (older) return cache.match(older);
        throw err;
      }
    })());
    return;
  }

  // app shell
  e.respondWith((async () => {
    const hit = await caches.match(req, { ignoreSearch: false });
    if (hit) return hit;
    try {
      const res = await fetch(req);
      return res;
    } catch (err) {
      if (req.mode === 'navigate') { const idx = await caches.match('index.html'); if (idx) return idx; }
      throw err;
    }
  })());
  void rel;
});
