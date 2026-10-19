import { S } from './store.js';

export const NA = '—';
export const isNum = x => typeof x === 'number' && Number.isFinite(x);
const MINUS = '−';

function group(intStr) {
  if (S.get('grouping') === 'sa') {
    if (intStr.length <= 3) return intStr;
    const last = intStr.slice(-3);
    const rest = intStr.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',');
    return rest + ',' + last;
  }
  return intStr.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

export function num(x, d = 2, opt = {}) {
  if (!isNum(x)) return NA;
  const a = Math.abs(x);
  const f = a.toFixed(d);
  const [i, fr] = f.split('.');
  let out = group(i) + (fr ? '.' + fr : '');
  const zero = Number(f) === 0;
  if (x < 0 && !zero) out = MINUS + out;
  else if (opt.sign && !zero) out = '+' + out;
  return out;
}

export function int(x) { return num(x, 0); }

export function pct(x, d = 1, opt = {}) {
  if (!isNum(x)) return NA;
  const sign = opt.sign !== false;
  return num(x, d, { sign }) + '%';
}

// fraction (0.55) -> "55%"
export function frac(x, d = 0) {
  if (!isNum(x)) return NA;
  return num(x * 100, d) + '%';
}

export function compact(x, d = 1) {
  if (!isNum(x)) return NA;
  const a = Math.abs(x);
  const sign = x < 0 ? MINUS : '';
  const sa = S.get('grouping') === 'sa';
  const t = (v, unit) => {
    const dd = v >= 100 ? 0 : v >= 10 ? Math.min(d, 1) : d;
    return sign + trimZeros(v.toFixed(dd)) + (sa ? ' ' : '') + unit;
  };
  if (sa) {
    if (a >= 1e11) return t(a / 1e11, 'kharab');
    if (a >= 1e9) return t(a / 1e9, 'arab');
    if (a >= 1e7) return t(a / 1e7, 'crore');
    if (a >= 1e5) return t(a / 1e5, 'lakh');
    return sign + group(Math.round(a).toString());
  }
  if (a >= 1e12) return t(a / 1e12, 'T');
  if (a >= 1e9) return t(a / 1e9, 'B');
  if (a >= 1e6) return t(a / 1e6, 'M');
  if (a >= 1e3) return t(a / 1e3, 'K');
  return sign + trimZeros(a.toFixed(a >= 100 ? 0 : d));
}

function trimZeros(s) { return s.includes('.') ? s.replace(/\.?0+$/, '') : s; }

export function money(x, ccy, opt = {}) {
  if (!isNum(x)) return NA;
  const c = ccy || '';
  const big = Math.abs(x) >= (opt.compactFrom ?? 1e6);
  const body = big ? compact(x) : num(x, opt.d ?? (Math.abs(x) < 100 ? 2 : 0));
  return (c ? c + ' ' : '') + body;
}

// Decimal places that suit a price of this size and market.
export function decimals(x, hint) {
  const a = Math.abs(x);
  const m = typeof hint === 'string' ? hint : (hint && (hint.m || hint.market)) || '';
  if (m === 'fx') return a >= 1000 ? 2 : a >= 100 ? 3 : a >= 10 ? 4 : 5;
  if (m === 'crypto') return a >= 10 ? 2 : a >= 1 ? 3 : a >= 0.1 ? 4 : a >= 0.01 ? 5 : a >= 0.0001 ? 6 : 8;
  return 2;
}

// Price with sensible decimals for the instrument kind.
export function px(x, hint) {
  if (!isNum(x)) return NA;
  return num(x, decimals(x, hint));
}

// Price formatted with the decimals suited to a reference price, so related levels (stop, entry, targets) line up.
export function pxr(x, ref, hint) {
  if (!isNum(x)) return NA;
  return num(x, decimals(isNum(ref) ? ref : x, hint));
}

export function chgText(x, d = 1) { return pct(x, d, { sign: true }); }
export function dirClass(x) { return !isNum(x) || x === 0 ? 'flat' : x > 0 ? 'up' : 'down'; }

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export function parseDate(d) {
  if (!d) return null;
  if (d instanceof Date) return d;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  const t = new Date(d);
  return Number.isNaN(t.getTime()) ? null : t;
}

export function date(d, opt = {}) {
  const t = parseDate(d);
  if (!t) return NA;
  const base = `${t.getDate()} ${MON[t.getMonth()]}`;
  const yr = opt.year === false ? '' : (opt.year === true || t.getFullYear() !== new Date().getFullYear() ? ' ' + t.getFullYear() : '');
  return (opt.dow ? DOW[t.getDay()] + ' ' : '') + base + yr;
}

export function dateTime(iso) {
  const t = parseDate(iso);
  if (!t) return NA;
  const hh = String(t.getHours()).padStart(2, '0');
  const mm = String(t.getMinutes()).padStart(2, '0');
  return `${t.getDate()} ${MON[t.getMonth()]}, ${hh}:${mm}`;
}

export function ago(iso) {
  const t = parseDate(iso);
  if (!t) return NA;
  const s = Math.max(0, (Date.now() - t.getTime()) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  const d = Math.round(s / 86400);
  return d + (d === 1 ? ' day ago' : ' days ago');
}

// calendar days between a YYYY-MM-DD date and today (local)
export function daysOld(asof) {
  const t = parseDate(asof);
  if (!t) return null;
  const now = new Date();
  const a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((a - t) / 86400000);
}

export function dayRange(r) {
  if (!Array.isArray(r) || r.length < 2) return NA;
  const [a, b] = r;
  if (!isNum(a) || !isNum(b)) return NA;
  const u = x => (x >= 60 && x < 700 ? [x / 30.4, 'mo'] : x >= 700 ? [x / 365, 'yr'] : [x, 'd']);
  const A = u(a), B = u(b);
  if (A[1] === B[1]) return `${trimZeros(A[0].toFixed(A[1] === 'd' ? 0 : 1))}–${trimZeros(B[0].toFixed(B[1] === 'd' ? 0 : 1))} ${B[1] === 'd' ? 'days' : B[1] === 'mo' ? 'months' : 'years'}`;
  return `${trimZeros(A[0].toFixed(A[1] === 'd' ? 0 : 1))} ${A[1]} – ${trimZeros(B[0].toFixed(B[1] === 'd' ? 0 : 1))} ${B[1]}`;
}

export function days(n) {
  if (!isNum(n)) return NA;
  if (n < 60) return Math.round(n) + ' days';
  if (n < 700) return trimZeros((n / 30.4).toFixed(1)) + ' months';
  return trimZeros((n / 365).toFixed(1)) + ' years';
}

export function r(x, d = 2) { return isNum(x) ? num(x, d, { sign: true }) + 'R' : NA; }
export function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
export function clip(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
export function csvCell(v) { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
