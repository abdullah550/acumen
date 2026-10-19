// Settings + personal data (journal, watchlist) kept in localStorage, always guarded.
const KEY = 'acumen.v1';
const KEY_SECRET = 'acumen.key.v1';

const DEFAULTS = {
  theme: 'system',        // system | light | dark
  palette: 'rg',          // rg = green/red, cb = blue/orange (colour-blind friendly)
  grouping: 'intl',       // intl = 1,000,000 | sa = 10,00,000
  density: 'comfortable', // comfortable | compact
  accountCcy: 'PKR',      // PKR | USD
  equity: null,           // null = use the default from the data
  equityMemo: {},         // last typed account size per currency, so switching currency and back does not drift
  profile: 'balanced',    // conservative | balanced | aggressive
  riskPct: null,          // null = profile default
  watchlist: [],
  journal: [],
  ideasFilter: { horizon: 'all', market: 'all', dir: 'all', grade: 'all', actionable: true, sort: 'rank', view: 'cards' },
  screener: { market: 'psx', q: '', sort: 'best', dir: -1, preset: 'all', cols: 'core', f: {} },
  dismissed: {},
  lastVisit: null,
};

let mem = null;
const subs = new Set();

// A deep copy, so nothing a screen does to its own lists can leak back into the defaults.
const fresh = () => JSON.parse(JSON.stringify(DEFAULTS));

function load() {
  if (mem) return mem;
  let raw = null;
  try { raw = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { raw = null; }
  mem = { ...fresh(), ...(raw && typeof raw === 'object' ? raw : {}) };
  mem.ideasFilter = { ...DEFAULTS.ideasFilter, ...(mem.ideasFilter || {}) };
  mem.screener = { ...DEFAULTS.screener, ...(mem.screener || {}) };
  return mem;
}

function persist() {
  try { localStorage.setItem(KEY, JSON.stringify(mem)); } catch { /* storage unavailable: keep in memory */ }
}

export const S = {
  get(k) { return load()[k]; },
  all() { return load(); },
  set(k, v, { silent = false } = {}) {
    load()[k] = v;
    persist();
    if (!silent) subs.forEach(fn => fn(k, v));
  },
  patch(k, obj, opts) { this.set(k, { ...load()[k], ...obj }, opts); },
  on(fn) { subs.add(fn); return () => subs.delete(fn); },
  reset() { mem = fresh(); persist(); subs.forEach(fn => fn('*', null)); },
  // Back to the defaults for everything except what the person created: the journal and the watchlist.
  resetPrefs() {
    const { journal, watchlist } = load();
    mem = { ...fresh(), journal, watchlist };
    persist(); subs.forEach(fn => fn('*', null));
  },
  exportAll() {
    const { journal, watchlist, equity, riskPct, profile, accountCcy } = load();
    return { app: 'acumen', kind: 'user-data', version: 1, exported_at: new Date().toISOString(), journal, watchlist, equity, riskPct, profile, accountCcy };
  },
  importAll(obj) {
    if (!obj || obj.app !== 'acumen' || obj.kind !== 'user-data') throw new Error('Not an Acumen export file');
    const m = load();
    if (Array.isArray(obj.journal)) {
      const have = new Set(m.journal.map(t => t.id));
      const add = obj.journal.filter(t => t && t.id && !have.has(t.id));
      if (add.length) m.journal = [...m.journal, ...add];
    }
    if (Array.isArray(obj.watchlist)) m.watchlist = Array.from(new Set([...m.watchlist, ...obj.watchlist]));
    persist(); subs.forEach(fn => fn('*', null));
  },
};

export const Secret = {
  get() { try { return localStorage.getItem(KEY_SECRET); } catch { return null; } },
  set(v) { try { localStorage.setItem(KEY_SECRET, v); return true; } catch { return false; } },
  clear() { try { localStorage.removeItem(KEY_SECRET); } catch { /* ignore */ } },
};

export function applyAppearance() {
  const root = document.documentElement;
  const st = load();
  if (st.theme === 'light' || st.theme === 'dark') root.setAttribute('data-theme', st.theme); else root.removeAttribute('data-theme');
  if (st.palette === 'cb') root.setAttribute('data-palette', 'cb'); else root.removeAttribute('data-palette');
  root.setAttribute('data-density', st.density === 'compact' ? 'compact' : 'comfortable');
  try { window.dispatchEvent(new Event('acumen:appearance')); } catch { /* ignore */ }
}

try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => window.dispatchEvent(new Event('acumen:appearance')));
} catch { /* old browsers */ }

export function isDark() {
  const t = load().theme;
  if (t === 'dark') return true;
  if (t === 'light') return false;
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches; } catch { return false; }
}
