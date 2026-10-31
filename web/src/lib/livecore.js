// Live prices, the pure part: which tickers to ask TradingView's public screener for, how to read its answer, which answers to
// distrust, and when each market is open. No DOM and no network here (live.js does those), so node --test can exercise all of it.
//
// Why the browser can do this itself: the scanner answers any origin and a text/plain body needs no preflight (checked from
// GitHub's servers with the site's own origin, 2026-10-31). PSX comes 15 minutes delayed, forex and crypto stream.

export const SCANNER = 'https://scanner.tradingview.com/';
export const COLUMNS = ['name', 'close', 'change']; // the answer's `d` array follows this order
export const PSX_DELAY_MIN = 15;
export const PEGS = { AED: 3.6725, SAR: 3.75 }; // units per US dollar, fixed by the central banks (as in acumen/fetch/fx.py)

const FX_VENUES = ['FX_IDC', 'OANDA', 'FX'];
const FX_EXTRA = { XAUUSD: ['TVC:GOLD'], XAGUSD: ['TVC:SILVER'] }; // no forex venue lists silver; TVC's spot price is the same market
// Where a coin trades, best first: the price is taken from the first venue that lists it.
export const CRYPTO_VENUES = [['BINANCE', 'USDT'], ['COINBASE', 'USD'], ['OKX', 'USDT'], ['KUCOIN', 'USDT'], ['KRAKEN', 'USD'], ['BYBIT', 'USDT']];
// A live price this far from the one the last refresh published is another instrument (a coin that shares a ticker, a bonus issue),
// not a move: it is left out and the published price stays.
export const GUARD = { psx: [0.5, 2], fx: [0.8, 1.25], crypto: [0.5, 2] };
const TICKER_OK = /^[A-Z0-9][A-Z0-9._-]{0,19}$/;

export const isNum = x => typeof x === 'number' && Number.isFinite(x);
const num = x => (typeof x === 'number' && Number.isFinite(x) ? x : null);

// ------------------------------------------------------------------------------------------------ requests and answers
export function scanBody(tickers) {
  return { symbols: { tickers, query: { types: [] } }, columns: COLUMNS, options: { lang: 'en' }, range: [0, 2000] };
}

/** `Map(ticker -> {px, chg})` from a scan answer; rows without a positive price are dropped. */
export function parseScan(payload) {
  const out = new Map();
  const data = payload && Array.isArray(payload.data) ? payload.data : [];
  for (const it of data) {
    if (!it || typeof it.s !== 'string' || !Array.isArray(it.d)) continue;
    const px = num(it.d[1]);
    if (px == null || px <= 0) continue;
    out.set(it.s, { px, chg: num(it.d[2]) });
  }
  return out;
}

const rowsOf = (rows, market) => rows.filter(r => r && r.m === market && typeof r.sym === 'string');

// ------------------------------------------------------------------------------------------------ PSX
export function psxRequest(rows) {
  const tickers = rowsOf(rows, 'psx').map(r => 'PSX:' + r.sym).filter(t => TICKER_OK.test(t.slice(4)));
  return tickers.length ? { path: 'pakistan', body: scanBody(tickers) } : null;
}

export function psxQuotes(rows, scan) {
  const out = new Map();
  for (const r of rowsOf(rows, 'psx')) {
    const q = scan.get('PSX:' + r.sym);
    if (q) out.set(r.id, q);
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ forex, metals, rupee crosses
const split = sym => (sym.length === 6 ? [sym.slice(0, 3), sym.slice(3)] : null);
/** BASE/PKR for any base but the dollar: priced from the dollar rate and the base currency's own dollar rate, as the history is. */
export const isSynthetic = sym => { const p = split(sym); return !!p && p[1] === 'PKR' && p[0] !== 'USD'; };

function fxTickers(sym) {
  return [...FX_VENUES.map(v => `${v}:${sym}`), ...(FX_EXTRA[sym] || [])];
}

export function fxRequest(rows) {
  const symbols = new Set();
  for (const r of rowsOf(rows, 'fx')) {
    const p = split(r.sym);
    if (!p) continue;
    if (isSynthetic(r.sym)) {
      symbols.add('USDPKR');
      if (!(p[0] in PEGS)) { symbols.add(p[0] + 'USD'); symbols.add('USD' + p[0]); }
    } else symbols.add(r.sym);
  }
  const tickers = [...symbols].flatMap(fxTickers);
  return tickers.length ? { path: 'global', body: scanBody(tickers) } : null;
}

function pickFx(scan, sym) {
  for (const t of fxTickers(sym)) { const q = scan.get(t); if (q) return q; }
  return null;
}

export function fxQuotes(rows, scan) {
  const out = new Map();
  const usdpkr = pickFx(scan, 'USDPKR');
  for (const r of rowsOf(rows, 'fx')) {
    const p = split(r.sym);
    if (!p) continue;
    if (!isSynthetic(r.sym)) {
      const q = pickFx(scan, r.sym);
      if (q) out.set(r.id, q);
      continue;
    }
    if (!usdpkr) continue;
    const [base] = p;
    if (base in PEGS) { out.set(r.id, { px: usdpkr.px / PEGS[base], chg: usdpkr.chg }); continue; }
    const direct = pickFx(scan, base + 'USD'); // dollars per unit of the base currency
    const inverse = pickFx(scan, 'USD' + base); // units of the base currency per dollar
    const chg = (a, b, inv) => (isNum(a) && isNum(b) ? ((1 + a / 100) * (inv ? 1 / (1 + b / 100) : 1 + b / 100) - 1) * 100 : null);
    if (direct) out.set(r.id, { px: usdpkr.px * direct.px, chg: chg(usdpkr.chg, direct.chg, false) });
    else if (inverse) out.set(r.id, { px: usdpkr.px / inverse.px, chg: chg(usdpkr.chg, inverse.chg, true) });
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ crypto
export const cryptoCandidates = sym => CRYPTO_VENUES.map(([v, q]) => `${v}:${sym}${q}`);

/** Coins with a known venue ask for that ticker only; the others ask every venue until one lists them. `winners` is `Map(sym -> ticker)`. */
export function cryptoRequest(rows, winners) {
  const tickers = [];
  for (const r of rowsOf(rows, 'crypto')) {
    if (!TICKER_OK.test(r.sym)) continue;
    const known = winners.get(r.sym);
    tickers.push(...(known ? [known] : cryptoCandidates(r.sym)));
  }
  return tickers.length ? { path: 'crypto', body: scanBody(tickers) } : null;
}

/** Prices by coin; also records which venue answered for each coin (and forgets a venue that stopped answering). */
export function cryptoQuotes(rows, scan, winners) {
  const out = new Map();
  for (const r of rowsOf(rows, 'crypto')) {
    if (!TICKER_OK.test(r.sym)) continue;
    const known = winners.get(r.sym);
    let hit = known && scan.get(known) ? known : null;
    if (!hit) {
      winners.delete(r.sym);
      hit = cryptoCandidates(r.sym).find(t => scan.has(t)) || null;
      if (hit) winners.set(r.sym, hit);
    }
    if (hit) out.set(r.id, scan.get(hit));
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ merging
/**
 * Writes live quotes into the app's price table (`prices.quotes[id] = [price, change %, time]`), the one every screen already reads.
 * `base` remembers the table's own value for each id, so the sanity check always compares with what the last refresh published.
 * `ts` is the time the price refers to; without one the published time stays (a market that is closed has no newer time). Returns the ids that changed.
 */
export function applyQuotes(prices, found, { market, ts, base }) {
  const changed = [];
  if (!prices || !prices.quotes) return changed;
  const [lo, hi] = GUARD[market] || [0.5, 2];
  for (const [id, q] of found) {
    const cur = prices.quotes[id];
    if (!cur || !isNum(q.px) || q.px <= 0) continue; // not an instrument of this build, or no price
    if (!base.has(id)) base.set(id, cur);
    const was = base.get(id);
    if (was && isNum(was[0]) && was[0] > 0) {
      const ratio = q.px / was[0];
      if (ratio < lo || ratio > hi) continue;
    }
    prices.quotes[id] = [q.px, isNum(q.chg) ? q.chg : was ? was[1] : null, ts || (was ? was[2] : null)];
    changed.push(id);
  }
  return changed;
}

// ------------------------------------------------------------------------------------------------ when
/** Whether a market is trading now. Loose on purpose (holidays are unknown): it only sets how often to ask. */
export function sessionState(market, now = new Date()) {
  const day = now.getUTCDay();
  const mins = now.getUTCHours() * 60 + now.getUTCMinutes();
  if (market === 'crypto') return { open: true, label: 'open' };
  if (market === 'fx') {
    const closed = day === 6 || (day === 5 && mins >= 22 * 60) || (day === 0 && mins < 21 * 60);
    return { open: !closed, label: closed ? 'closed for the weekend' : 'open' };
  }
  const pk = mins + 300; // Pakistan time is UTC+5 all year
  const pkDay = (day + Math.floor(pk / 1440)) % 7;
  const pkMin = pk % 1440;
  const open = pkDay >= 1 && pkDay <= 5 && pkMin >= 9 * 60 && pkMin <= 16 * 60 + 45;
  return { open, label: open ? 'open' : 'closed' };
}

export const REFRESH_MS = 60_000; // while a market is open
export const IDLE_MS = 15 * 60_000; // while it is closed: the price cannot move, so only now and then
export const BACKOFF_MS = [30_000, 60_000, 120_000, 300_000, 600_000];

export function nextDelay(market, now, fails = 0) {
  if (fails > 0) return BACKOFF_MS[Math.min(fails - 1, BACKOFF_MS.length - 1)];
  return sessionState(market, now).open ? REFRESH_MS : IDLE_MS;
}

/** The label a person sees: "Live" while something answered a moment ago, else why not. */
export function summarise(markets, now = Date.now(), { fresh = 3 * 60_000 } = {}) {
  const list = Object.entries(markets);
  const answered = list.filter(([, m]) => m.at);
  const recent = answered.filter(([, m]) => now - m.at <= fresh);
  if (recent.length) return { tone: 'live', text: 'Live' };
  if (answered.length) return { tone: 'stale', text: 'Live paused' };
  if (list.some(([, m]) => m.err)) return { tone: 'error', text: 'Live offline' };
  return { tone: 'wait', text: 'Live…' };
}
