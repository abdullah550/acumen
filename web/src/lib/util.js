export const sanitizeId = sym => String(sym).replace(/[^A-Za-z0-9._-]/g, '_');
export const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
export const sum = a => a.reduce((x, y) => x + y, 0);
export const mean = a => (a.length ? sum(a) / a.length : null);
export const uniq = a => Array.from(new Set(a));
export const by = (k, dir = 1) => (a, b) => {
  const x = typeof k === 'function' ? k(a) : a[k];
  const y = typeof k === 'function' ? k(b) : b[k];
  if (x == null && y == null) return 0;
  if (x == null) return 1;
  if (y == null) return -1;
  return x < y ? -dir : x > y ? dir : 0;
};
export const MARKETS = ['psx', 'fx', 'crypto'];
export const MARKET_LABEL = { psx: 'PSX', fx: 'Forex', crypto: 'Crypto' };
export const MARKET_LONG = { psx: 'Pakistan Stock Exchange', fx: 'Forex', crypto: 'Crypto' };
// Calendar days after which a market's latest bar is treated as out of date (weekends and holidays allowed for).
export const STALE_DAYS = { psx: 4, fx: 4, crypto: 2 };
export const HORIZONS = ['short', 'medium', 'long'];
export const HZ_LABEL = { short: 'Swing', medium: 'Positional', long: 'Investment' };
export const HZ_SPAN = { short: 'days to weeks', medium: 'weeks to months', long: 'months to 2 years' };
export const GRADE_ORDER = { A: 4, B: 3, C: 2, D: 1 };
export function toRows(x) { return Array.isArray(x) ? x : []; }
export function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Keeps one point per date (the last one), in date order. Charts need strictly ascending times.
export function dedupeSeries(points) {
  const m = new Map();
  for (const p of points || []) if (p && p[0] != null) m.set(p[0], p);
  return Array.from(m.values()).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
}
export const EXIT_LABEL = {
  target2: 'Both targets',
  target1: 'Target 1, then break-even',
  stop: 'Stopped out',
  time: 'Time stop',
  open: 'Still open',
};
