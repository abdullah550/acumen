// Hash router with an in-memory fallback (used when the page is hosted where the hash is not ours, e.g. a preview).
const MEMORY = typeof __ARTIFACT__ !== 'undefined' && __ARTIFACT__;
let mem = '/';
const subs = new Set();
let cur = null;

export function parse(raw) {
  let h = raw || '/';
  if (h.startsWith('#')) h = h.slice(1);
  if (!h.startsWith('/')) h = '/' + h;
  const [pathPart, qs = ''] = h.split('?');
  const path = pathPart.replace(/\/+$/, '') || '/';
  const query = {};
  for (const [k, v] of new URLSearchParams(qs)) query[k] = v;
  const segs = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { path, segs, query, raw: h };
}

export function build(path, query) {
  const q = query && Object.keys(query).length ? '?' + new URLSearchParams(Object.entries(query).filter(([, v]) => v != null && v !== '')).toString() : '';
  const segs = path.split('/').filter(Boolean).map(encodeURIComponent);
  return '/' + segs.join('/') + q;
}

export function here() {
  return MEMORY ? parse(mem) : parse(location.hash);
}

export function href(path, query) { return MEMORY ? '#' : '#' + build(path, query); }

export function go(path, query, { replace = false } = {}) {
  const target = build(path, query);
  if (MEMORY) {
    mem = target;
    emit();
    return;
  }
  const next = '#' + target;
  if (location.hash === next) { emit(); return; }
  if (replace) history.replaceState(null, '', next); else location.hash = next;
  if (replace) emit();
}

// Re-builds the current location with some query values changed. Segments come from the decoded list so ids such as
// "PSX:HBL" are encoded exactly once.
function withQuery(patch) {
  const r = here();
  const q = { ...r.query, ...patch };
  for (const k of Object.keys(q)) if (q[k] == null || q[k] === '') delete q[k];
  return build('/' + r.segs.join('/'), q);
}

export function setQuery(patch, { replace = true } = {}) {
  const target = withQuery(patch);
  if (MEMORY) { mem = target; emit(); return; }
  const next = '#' + target;
  if (location.hash === next) return;
  if (replace) { history.replaceState(null, '', next); emit(); } else location.hash = next;
}

// Updates the address (so a reload or a shared link keeps the view) without re-rendering the screen.
export function replaceQuery(patch) {
  const target = withQuery(patch);
  if (MEMORY) { mem = target; return; }
  try { history.replaceState(null, '', '#' + target); } catch { /* ignore */ }
}

function emit() {
  const r = here();
  cur = r;
  subs.forEach(fn => fn(r));
}

export function onRoute(fn) { subs.add(fn); return () => subs.delete(fn); }

export function start() {
  if (!MEMORY) window.addEventListener('hashchange', emit);
  emit();
}

// Links: <a href> work in hash mode; in memory mode we intercept clicks.
export function linkProps(path, query) {
  return {
    href: href(path, query),
    onclick: MEMORY ? (e => { e.preventDefault(); go(path, query); }) : null,
  };
}

export const current = () => cur || here();
export { MEMORY };
