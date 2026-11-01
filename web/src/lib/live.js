// Live prices, fetched by the browser itself.
//
// While the app is open and visible it asks TradingView's public screener for the prices of the instruments in the current data,
// once a minute per open market, and writes them into the same price table every screen already reads (prices.quotes), so the
// instrument page, positions, journal and position sizing all show them without knowing where they came from. The published
// refresh (a few times a day) still provides the analysis and is the fallback: when this cannot reach TradingView, nothing is lost.
// Forex and crypto stream; PSX arrives 15 minutes delayed (the quote's time says so). Pure logic is in livecore.js.
import { D } from './data.js';
import { S } from './store.js';
import * as C from './livecore.js';

const MARKETS = ['psx', 'fx', 'crypto'];
const LABEL = { psx: 'PSX', fx: 'Forex', crypto: 'Crypto' };
const TICK_MS = 10_000; // how often to look at whether any market is due
const TIMEOUT_MS = 15_000;

const fresh = () => ({ at: null, err: null, n: 0, fails: 0, next: 0, busy: false, ts: null, found: null, total: 0 });
const state = { on: false, timer: null, rows: null, priceObj: null, base: new Map(), winners: new Map(), liveIds: new Set(), m: Object.fromEntries(MARKETS.map(k => [k, fresh()])) };

const enabled = () => S.get('live') !== false;
const emit = (market, ids) => window.dispatchEvent(new CustomEvent('acumen:live', { detail: { market, ids } }));

async function post(req) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    // a text/plain body is a "simple" request: no preflight, which the scanner would not answer for application/json
    const res = await fetch(C.SCANNER + req.path + '/scan', { method: 'POST', body: JSON.stringify(req.body), headers: { 'Content-Type': 'text/plain;charset=UTF-8' }, cache: 'no-store', credentials: 'omit', signal: ctl.signal });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } finally { clearTimeout(timer); }
}

function request(k) {
  const rows = state.rows || [];
  return k === 'psx' ? C.psxRequest(rows) : k === 'fx' ? C.fxRequest(rows) : C.cryptoRequest(rows, state.winners);
}

function quotes(k, scan) {
  const rows = state.rows || [];
  return k === 'psx' ? C.psxQuotes(rows, scan) : k === 'fx' ? C.fxQuotes(rows, scan) : C.cryptoQuotes(rows, scan, state.winners);
}

function apply(k) {
  const prices = D.peek('prices');
  const m = state.m[k];
  if (!prices || !m.found) return [];
  if (state.priceObj !== prices) { state.priceObj = prices; state.base.clear(); state.liveIds.clear(); } // a newly loaded price table starts again from its own quotes
  const ids = C.applyQuotes(prices, m.found, { market: k, ts: m.ts, base: state.base });
  for (const id of ids) state.liveIds.add(id);
  return ids;
}

async function run(k) {
  const m = state.m[k];
  m.busy = true;
  let ids = [];
  try {
    if (!D.peek('prices')) throw new Error('the published prices are not loaded yet');
    const req = request(k);
    if (!req) { m.next = Infinity; return; } // nothing of this market in the data
    const scan = C.parseScan(await post(req));
    const found = quotes(k, scan);
    if (!found.size) throw new Error('the answer held no prices');
    m.found = found;
    m.ts = C.sessionState(k, new Date()).open ? new Date(Date.now() - (k === 'psx' ? C.PSX_DELAY_MIN * 60_000 : 0)).toISOString() : null; // closed: the price is the last close, whose time the published table already has
    ids = apply(k);
    m.at = Date.now(); m.err = null; m.fails = 0; m.n = ids.length;
    m.next = Date.now() + C.nextDelay(k, new Date());
  } catch (e) {
    m.fails += 1;
    m.err = e && e.name === 'AbortError' ? 'TradingView did not answer in time' : e && /^HTTP/.test(e.message) ? e.message : e instanceof TypeError ? 'TradingView could not be reached' : String((e && e.message) || e);
    m.next = Date.now() + C.nextDelay(k, new Date(), m.fails);
  } finally {
    m.busy = false;
    emit(k, ids);
  }
}

async function tick() {
  if (!state.on || !enabled() || document.visibilityState === 'hidden' || navigator.onLine === false) return;
  if (!state.rows) {
    try { state.rows = Array.from((await D.index()).values()); } catch { return; }
    for (const k of MARKETS) state.m[k].total = state.rows.filter(r => r.m === k).length;
  }
  const now = Date.now();
  await Promise.all(MARKETS.filter(k => !state.m[k].busy && state.m[k].next <= now).map(run));
}

function onVisible() {
  if (document.visibilityState !== 'visible') return;
  for (const k of MARKETS) if (!state.m[k].at || Date.now() - state.m[k].at > 45_000) state.m[k].next = Math.min(state.m[k].next, 0);
  tick();
}
const onOnline = () => { for (const k of MARKETS) state.m[k].next = 0; tick(); };

export const Live = {
  /** Start asking. Safe to call again. Synthetic demo data is never mixed with real prices. */
  start() {
    if (state.on || !enabled() || (D.meta && D.meta.demo)) return;
    state.on = true;
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);
    state.timer = setInterval(tick, TICK_MS);
    D.onPrices = () => { const ids = MARKETS.flatMap(apply); emit('all', ids); }; // the published table was reloaded: lay the live prices over it again
    tick();
  },

  /** Stop asking and put the published prices back. */
  stop() {
    state.on = false;
    clearInterval(state.timer);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('online', onOnline);
    D.onPrices = null;
    const prices = D.peek('prices');
    if (prices && prices.quotes) for (const [id, q] of state.base) prices.quotes[id] = q;
    state.base.clear();
    state.liveIds.clear();
    for (const k of MARKETS) Object.assign(state.m[k], { at: null, err: null, n: 0, fails: 0, next: 0, ts: null, found: null });
    emit('all', []);
  },

  /** Ask every market again now (the "Refresh prices" buttons). */
  async refresh() {
    if (!state.on) return;
    for (const k of MARKETS) state.m[k].next = 0;
    await tick();
  },

  setEnabled(on) {
    S.set('live', !!on);
    if (on) this.start(); else this.stop();
  },

  get active() { return state.on && enabled(); },

  /** Whether this instrument's price in the table came from a live answer (and how it should be described). */
  quote(id) {
    const market = id.split(':')[0].toLowerCase();
    const live = state.liveIds.has(id);
    return { live, delayMin: live && market === 'psx' ? C.PSX_DELAY_MIN : 0, open: C.sessionState(market, new Date()).open };
  },

  /** What the status chip and the Settings card show. */
  status(now = Date.now()) {
    const markets = {};
    for (const k of MARKETS) {
      const m = state.m[k];
      const session = C.sessionState(k, new Date(now));
      markets[k] = { label: LABEL[k], at: m.at, err: m.err, n: m.n, total: m.total, open: session.open, session: session.label, delayMin: k === 'psx' ? C.PSX_DELAY_MIN : 0 };
    }
    const summary = C.summarise(markets, now);
    const lines = MARKETS.filter(k => markets[k].total || markets[k].at || markets[k].err).map(k => {
      const x = markets[k];
      const when = x.at ? `updated ${Math.max(1, Math.round((now - x.at) / 1000))} s ago` : x.err ? x.err : 'waiting';
      return `${x.label}: ${x.at ? `${x.n} prices` : 'no prices yet'}${x.delayMin ? `, ${x.delayMin} min delayed` : ''}, ${x.open ? when : `market ${x.session}${x.at ? ', ' + when : ''}`}`;
    });
    return { on: this.active, summary, markets, detail: lines.join('\n') };
  },
};
