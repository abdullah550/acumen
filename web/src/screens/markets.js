import { h, replace, append } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { go, linkProps, replaceQuery } from '../lib/router.js';
import { MARKET_LABEL, dedupeSeries, by } from '../lib/util.js';
import { card, banner, seg, tabs, chip, chgSpan, stat, empty, skeleton, pillToggle } from '../ui/kit.js';
import { gauge, heatTiles, hbars, treemap, heatMatrix, divColor, tipRows } from '../ui/charts.js';
import { makeChart, palette, chartOptions, LineSeries, AreaSeries } from '../ui/lwc.js';
import { MACRO_GROUPS, macroTile } from '../ui/macro.js';
import { freshness, breadthBlock } from '../ui/market.js';
import { pageHead, sectionTitle, errorBlock } from './common.js';

const TABS = [
  { value: 'psx', label: 'Pakistan (PSX)' },
  { value: 'fx', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
  { value: 'macro', label: 'Macro and rates' },
];

const REGIME_INFO = {
  psx: 'Compares the index with its 50-day and 200-day averages, the slope of the 200-day average and how far the index sits below its one-year high, then scores the result from -1 (bearish) to +1 (bullish). The score feeds the conviction of every PSX idea.',
  crypto: 'The same method as the PSX regime, applied to Bitcoin as the benchmark for the whole crypto market. The score feeds the conviction of every crypto idea.',
  fx: 'Two votes: the S&P 500 above or below its 200-day average, and the VIX below 18 (calm) or above 25 (stressed). The score is their average, so +1 is risk-on and -1 is risk-off. The dollar trend is shown beside it but does not change the score.',
};

const enter = fn => e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(e); } };
const narrow = () => window.matchMedia('(max-width: 760px)').matches;

export default async function render(route) {
  let ov;
  try { ov = await D.get('overview'); } catch (e) { return { el: errorBlock(e) }; }

  let cur = TABS.some(t => t.value === route.query.m) ? route.query.m : 'psx';
  let gen = 0;
  const cleanups = [];
  const root = h('div', { class: 'stack loose' });
  const tabsHost = h('div');
  const host = h('div', { class: 'stack loose' });

  // Tabs build their content with this: afterLayout runs once the tab is on the page and has a width (charts need one),
  // onClean registers things to tear down when the tab changes.
  const ctx = {
    ov,
    onClean: fn => { cleanups.push(fn); },
    afterLayout(fn) { const g = gen; requestAnimationFrame(() => requestAnimationFrame(() => { if (g === gen) fn(); })); },
  };
  const stop = () => { gen++; for (const fn of cleanups.splice(0)) { try { fn(); } catch { /* already gone */ } } };

  const BUILD = { psx: psxTab, fx: fxTab, crypto: cryptoTab, macro: macroTab };
  async function drawTab() {
    stop();
    const mine = gen;
    replace(host, h('div', { class: 'stack' }, skeleton('100%', 90), skeleton('100%', 320)));
    let el;
    try { el = await BUILD[cur](ctx); } catch (e) { el = errorBlock(e, () => drawTab()); }
    if (mine !== gen) return;
    replace(host, el);
  }
  function drawTabs() {
    replace(tabsHost, tabs(TABS, cur, v => { cur = v; replaceQuery({ m: v === 'psx' ? null : v }); drawTabs(); drawTab(); }));
  }

  root.append(pageHead('Markets', 'The backdrop behind every idea: how the index is trending, which sectors lead, which currencies are strong, what crypto is doing and where interest rates sit.'), tabsHost, host);
  drawTabs();
  drawTab();
  return { el: root, destroy: stop };
}

/* ---------- shared pieces ---------- */
function statusRow(m, d) {
  const { age, stale } = freshness(m, d.asof);
  const c = d.counts || {};
  return h('div', { class: 'stack' },
    h('div', { class: 'mk-status' },
      h('div', { class: 'row wrap' },
        chip(`Close ${F.date(d.asof, { year: false })}`, stale ? 'warn' : 'outline', { icon: 'clock', title: 'Date of the latest daily bar behind these numbers' }),
        stale ? chip(`${age} days old`, 'warn', { icon: 'alert' }) : null,
        F.isNum(c.analysed) ? h('span', { class: 'muted fs-sm' }, `${F.int(c.analysed)} of ${F.int(c.instruments)} instruments analysed · ${F.int(c.ideas)} ideas`) : null),
      h('div', { class: 'row wrap' },
        h('a', { class: 'btn sm', ...linkProps('/screener', { m }) }, icon('table'), 'Screener'),
        h('a', { class: 'btn sm', ...linkProps('/ideas', { m }) }, icon('bolt'), `${MARKET_LABEL[m]} ideas`))),
    stale ? banner('warn', h('b', null, `The latest ${MARKET_LABEL[m]} data is ${age} days old. `), 'Everything below is from the last close we have, not today. Open Data & Settings to see which source stopped updating.') : null);
}

const noData = (title, text) => card({ body: empty(title, text, 'globe') });

function dlRows(rows) {
  return h('dl', { class: 'kv' }, ...rows.filter(Boolean).flatMap(([k, v, tip]) => [h('dt', { title: tip || null }, k), h('dd', null, v)]));
}

// Regime card shared by PSX and crypto (index/BTC vs its averages) and forex (risk appetite and the dollar).
function regimeCard(m, d) {
  const rg = d.regime || {};
  const body = [
    h('div', { class: 'mc-regime' }, rg.label || F.NA),
    gauge(rg.score, m === 'fx' ? { left: 'Risk-off', right: 'Risk-on' } : undefined),
    h('div', { class: 'fs-xs muted' }, F.isNum(rg.score) ? `Score ${F.num(rg.score, 2, { sign: true })} on a scale from -1 to +1` : 'Not enough history to score'),
    rg.note ? h('p', { class: 'mc-note' }, rg.note) : null,
  ];
  const t = rg.trend;
  if (t) {
    const px = x => (m === 'crypto' ? F.px(x, 'crypto') : F.num(x, 0));
    const rel = (a, b) => (F.isNum(a) && F.isNum(b) && b ? (a / b - 1) * 100 : null);
    const slope = t.slope_200_pct;
    body.push(dlRows([
      [t.symbol || 'Level', px(t.close)],
      ['Against 50-day average', h('span', null, chgSpan(rel(t.close, t.sma50), 1), h('span', { class: 'muted' }, ' · ' + px(t.sma50)))],
      ['Against 200-day average', h('span', null, chgSpan(rel(t.close, t.sma200), 1), h('span', { class: 'muted' }, ' · ' + px(t.sma200)))],
      F.isNum(slope) ? ['200-day average, last 20 days', h('span', null, chgSpan(slope, 2), h('span', { class: 'muted' }, slope >= 0 ? ' rising' : ' falling'))] : null,
      rg.volatility && F.isNum(rg.volatility.daily_vol_pct) ? ['Typical daily move', `${F.num(rg.volatility.daily_vol_pct, 2)}%`, 'Standard deviation of daily returns over the last 14 days'] : null,
      rg.volatility && F.isNum(rg.volatility.percentile) ? ['Volatility against the past year', `higher than ${rg.volatility.percentile}% of days`] : null,
    ]));
  }
  if (m === 'fx' && rg.dxy) {
    body.push(dlRows([['US dollar index', F.num(rg.dxy.close, 2)], ['Dollar index, 1 month', chgSpan(rg.dxy.chg_1m, 2)]]));
  }
  const bb = breadthBlock(rg.breadth);
  if (bb) body.push(bb);
  return card({ title: m === 'fx' ? 'Risk appetite and the dollar' : 'Market regime', info: REGIME_INFO[m], body: h('div', { class: 'stack' }, ...body) });
}

/* ---------- sortable table used for sectors and coins ---------- */
function sortTable({ cols, rows, sort, dir = -1, cls = '' }) {
  const st = { sort, dir };
  const host = h('div', { class: 'tbl-wrap' });
  const th = c => {
    const on = st.sort === c.k;
    const flip = () => { if (st.sort === c.k) st.dir = -st.dir; else { st.sort = c.k; st.dir = c.first ? 1 : -1; } draw(); };
    return h('th', {
      class: [c.r && 'r', !c.nosort && 'sortable', c.cls], title: c.title || null, scope: 'col',
      tabindex: c.nosort ? null : '0', 'aria-sort': on ? (st.dir < 0 ? 'descending' : 'ascending') : null,
      onclick: c.nosort ? null : flip, onkeydown: c.nosort ? null : enter(flip),
    }, c.label, on ? h('span', { class: 'arr' }, st.dir > 0 ? '▲' : '▼') : null);
  };
  function draw() {
    const col = cols.find(c => c.k === st.sort) || cols[0];
    const list = rows.slice().sort(by(col.val || (r => r[col.k]), st.dir));
    replace(host, h('table', { class: ['tbl', 'fixed-first', cls] },
      h('thead', null, h('tr', null, ...cols.map(th))),
      h('tbody', null, ...list.map(r => h('tr', { class: r.href ? 'link' : null, onclick: r.href ? e => { if (!e.target.closest('a,button')) go(r.href); } : null },
        ...cols.map(c => h('td', { class: [c.r && 'r', c.cls] }, c.cell(r))))))));
  }
  draw();
  return host;
}

// A signed value drawn as a small bar either side of zero, with the number beside it.
function biBar(v, max, text) {
  const w = F.isNum(v) && max ? Math.min(50, (Math.abs(v) / max) * 50) : 0;
  return h('div', { class: 'bibar' },
    h('div', { class: 'bb-track' }, h('i', { class: 'bb-zero' }), w ? h('i', { class: ['bb-fill', v >= 0 ? 'pos' : 'neg'], style: { width: w + '%', left: v >= 0 ? '50%' : (50 - w) + '%' } }) : null),
    h('span', { class: ['bb-v', F.dirClass(v)] }, text));
}

/* ---------- movers ---------- */
function moverCard(m, title, kind, list) {
  const last = kind === 'active' ? 'Traded value' : 'Change';
  return card({
    title, flush: true,
    body: h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl mv' },
      h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', { class: 'r' }, 'Price'), h('th', { class: 'r' }, last))),
      h('tbody', null, ...list.map(r => h('tr', { class: 'link', onclick: e => { if (!e.target.closest('a')) go('/i/' + r.id); } },
        h('td', null, h('div', { class: 'sym' }, h('a', { ...linkProps('/i/' + r.id) }, r.sym)), h('div', { class: 'nm', title: r.name }, r.name)),
        h('td', { class: 'r' }, F.px(r.px, m)),
        h('td', { class: 'r' }, kind === 'active' ? F.compact(r.value) : chgSpan(r.chg_1d, 2))))))),
  });
}
function moversSection(m, d) {
  const mv = d.movers || {};
  const defs = [['Top gainers', 'gainers'], ['Top losers', 'losers'], ['Most active', 'active']].filter(([, k]) => (mv[k] || []).length);
  if (!defs.length) return null;
  const sub = m === 'psx' ? 'Biggest moves on the latest session among stocks liquid enough to trade, and the most traded by value.' : m === 'fx' ? 'Biggest moves on the latest session among the pairs and metals Acumen tracks.' : 'Biggest 24-hour moves among coins liquid enough to trade, and the most traded by value.';
  return h('section', { class: 'stack' }, sectionTitle('Movers', null, sub), h('div', { class: ['grid', defs.length === 2 ? 'c2' : 'c3', 'top'] }, ...defs.map(([t, k]) => moverCard(m, t, k, mv[k]))));
}

/* ======================================================================== PSX */
function psxTab(ctx) {
  const d = ctx.ov.markets.psx;
  if (!d) return noData('No PSX data in this build', 'The latest build has no Pakistan Stock Exchange section.');
  const wrap = h('div', { class: 'stack loose' });
  const hasIndex = d.index && (d.index.series || []).length > 2;
  wrap.append(statusRow('psx', d));
  wrap.append(h('div', { class: 'grid mk-top' }, hasIndex ? indexCard(ctx, d.index) : card({ title: 'Index', body: empty('No index history', 'The benchmark series is missing from this build.', 'pulse') }), regimeCard('psx', d)));
  const sec = sectorsSection(d);
  if (sec) wrap.append(sec);
  const mv = moversSection('psx', d);
  if (mv) wrap.append(mv);
  return wrap;
}

const RANGE_DAYS = { '1M': 30, '3M': 91, '6M': 182 };
const isoDay = t => `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;

function indexCard(ctx, ix) {
  const pts = dedupeSeries(ix.series);
  if (pts.length !== (ix.series || []).length) { ix = { ...ix, ma50: null, ma200: null }; }
  const dates = pts.map(p => p[0]);
  const closes = pts.map(p => p[1]);
  const n = closes.length;
  const prec = closes[n - 1] >= 1000 ? 0 : 2;
  const sma = len => { const out = []; let acc = 0; for (let i = 0; i < n; i++) { acc += closes[i]; if (i >= len) acc -= closes[i - len]; out.push(i >= len - 1 ? acc / len : null); } return out; };
  // The engine supplies both averages computed on the full history; derive them here only for older data files without them.
  const given = (arr, len) => (Array.isArray(arr) && arr.length === n ? arr : sma(len));
  const a50 = given(ix.ma50, 50), a200 = given(ix.ma200, 200);
  const has200 = a200.some(v => v != null);
  const spanDays = Math.round((F.parseDate(dates[n - 1]) - F.parseDate(dates[0])) / 864e5);
  const state = { range: spanDays > 200 ? '6M' : 'All', s50: true, s200: has200 };

  let handle = null, base = null, l50 = null, l200 = null;
  const root = h('section', { class: 'card mk-index' });
  const setSw = c => { root.style.setProperty('--sw50', c.cat[1]); root.style.setProperty('--sw200', c.cat[5]); };
  setSw(palette());

  const box = h('div', { class: 'chartbox', role: 'group', 'aria-label': `Line chart of ${ix.name || 'the index'}, ${dates[0]} to ${dates[n - 1]}, closing at ${F.num(closes[n - 1], prec)}. The same figures are in the table below the chart.` });
  const legend = h('div', { class: 'chart-legend' });
  const legendFor = i => {
    const cl = closes[i], pv = i > 0 ? closes[i - 1] : null;
    const chg = pv ? (cl / pv - 1) * 100 : null;
    replace(legend,
      h('span', null, F.date(dates[i], { year: true })),
      h('span', null, 'Close ', h('b', null, F.num(cl, prec))),
      F.isNum(chg) ? h('span', { class: ['chg', F.dirClass(chg)] }, F.pct(chg, 2)) : null,
      state.s50 && a50[i] != null ? h('span', null, h('i', { class: 'lg-sw', style: { background: 'var(--sw50)' } }), '50-day ', h('b', null, F.num(a50[i], prec))) : null,
      state.s200 && a200[i] != null ? h('span', null, h('i', { class: 'lg-sw', style: { background: 'var(--sw200)' } }), '200-day ', h('b', null, F.num(a200[i], prec))) : null);
  };
  legendFor(n - 1);

  const applyRange = () => {
    if (!handle) return;
    const ts = handle.chart.timeScale();
    const days = RANGE_DAYS[state.range];
    let first = 0;
    if (days) {
      const from = isoDay(new Date(F.parseDate(dates[n - 1]).getTime() - days * 864e5));
      first = Math.max(0, dates.findIndex(d => d >= from));
    }
    // a few bars of empty space either side keeps the first axis label and the last price tag inside the frame
    try { ts.setVisibleLogicalRange({ from: first - 5, to: n - 1 + 4 }); } catch { ts.fitContent(); }
  };

  const lineData = arr => { const out = []; for (let i = 0; i < n; i++) if (arr[i] != null) out.push({ time: dates[i], value: arr[i] }); return out; };
  const fmtOpt = { type: 'price', precision: prec, minMove: prec ? 0.01 : 1 };
  const baseOpts = c => ({ lineColor: c.ink, topColor: c.ink + '26', bottomColor: c.ink + '00', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: fmtOpt });
  const smaOpts = (color, visible) => ({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: fmtOpt, visible });

  function build() {
    const c = palette();
    handle = makeChart(box, {
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      kineticScroll: { touch: true, mouse: false },
    });
    const chart = handle.chart;
    base = chart.addSeries(AreaSeries, baseOpts(c));
    base.setData(lineData(closes));
    l50 = chart.addSeries(LineSeries, smaOpts(c.cat[1], state.s50));
    l50.setData(lineData(a50));
    l200 = chart.addSeries(LineSeries, smaOpts(c.cat[5], state.s200));
    l200.setData(lineData(a200));
    applyRange();
    chart.subscribeCrosshairMove(p => {
      if (!p || !p.time) { legendFor(n - 1); return; }
      const i = dates.indexOf(p.time);
      legendFor(i >= 0 ? i : n - 1);
    });
    handle.recolor = cc => {
      chart.applyOptions(chartOptions(cc));
      base.applyOptions(baseOpts(cc));
      l50.applyOptions({ color: cc.cat[1] });
      l200.applyOptions({ color: cc.cat[5] });
      setSw(cc);
    };
  }
  ctx.afterLayout(build);
  ctx.onClean(() => { if (handle) handle.destroy(); });

  const rangeOpts = [...Object.keys(RANGE_DAYS).filter(k => spanDays > RANGE_DAYS[k] * 0.9), 'All'].map(v => ({ value: v, label: v, title: v === 'All' ? `Everything in this build, about ${Math.max(1, Math.round(spanDays / 30.4))} months` : null }));
  const rangeSeg = seg(rangeOpts, state.range, v => { state.range = v; applyRange(); }, { sm: true, label: 'Chart range' });
  const t50 = pillToggle('50-day average', state.s50, v => { state.s50 = v; if (l50) l50.applyOptions({ visible: v }); legendFor(n - 1); }, 'var(--sw50)');
  const t200 = has200 ? pillToggle('200-day average', state.s200, v => { state.s200 = v; if (l200) l200.applyOptions({ visible: v }); legendFor(n - 1); }, 'var(--sw200)') : null;

  const tableHost = h('div');
  const details = h('details', { class: 'price-table' }, h('summary', null, 'Show the data as a table'), tableHost);
  details.addEventListener('toggle', () => {
    if (!details.open || tableHost.firstChild) return;
    const rows = pts.map((p, i) => ({ date: p[0], close: p[1], chg: i ? (p[1] / pts[i - 1][1] - 1) * 100 : null, a50: a50[i], a200: a200[i] })).reverse();
    replace(tableHost, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', null, 'Date'), h('th', { class: 'r' }, 'Close'), h('th', { class: 'r' }, 'Change'), h('th', { class: 'r' }, '50-day'), h('th', { class: 'r' }, '200-day'))),
      h('tbody', null, ...rows.map(r => h('tr', null, h('td', null, F.date(r.date, { year: true })), h('td', { class: 'r' }, F.num(r.close, prec)), h('td', { class: 'r' }, F.isNum(r.chg) ? chgSpan(r.chg, 2) : F.NA), h('td', { class: 'r' }, F.isNum(r.a50) ? F.num(r.a50, prec) : F.NA), h('td', { class: 'r' }, F.isNum(r.a200) ? F.num(r.a200, prec) : F.NA)))))));
  });

  append(root, [
    h('div', { class: 'mk-index-head' },
      h('div', { class: 'mk-index-id' },
        h('div', { class: 'label' }, ix.name || 'Index'),
        h('div', { class: 'row mk-index-px' }, h('div', { class: 'mc-big' }, F.num(ix.close, prec)), chgSpan(ix.chg_1d, 2), h('span', { class: 'muted fs-xs' }, 'today'))),
      h('div', { class: 'perf' }, ...[['1W', ix.chg_1w], ['1M', ix.chg_1m], ['YTD', ix.chg_ytd]].map(([k, v]) => h('div', { class: 'pf-cell' }, h('span', null, k), chgSpan(v, 2))))),
    h('div', { class: 'mk-index-bar' }, rangeSeg, h('div', { class: 'row wrap' }, t50, t200)),
    h('div', { class: 'mk-chart' }, box, legend),
    has200 ? null : h('div', { class: 'chart-note' }, 'The 200-day average needs 200 trading days of history, which this build does not have yet.'),
    details]);
  return root;
}

/* ---------- sectors ---------- */
const SECTOR_SPANS = [{ value: 'chg_1d', label: '1D' }, { value: 'chg_1w', label: '1W' }, { value: 'chg_1m', label: '1M' }, { value: 'chg_3m', label: '3M' }];

function sectorsSection(d) {
  const secs = d.sectors || [];
  if (!secs.length) return null;
  let span = 'chg_1m';
  const heatHost = h('div');
  const drawHeat = () => {
    const sorted = secs.slice().sort(by(x => x[span], -1));
    const mx = Math.max(...secs.map(x => Math.abs(x[span] ?? 0)), 0.5);
    replace(heatHost, heatTiles(sorted.map(x => ({
      label: x.name, value: x[span], sub: `${x.n} stocks · ${F.num(x.pct_above_50, 0)}% above 50-day`,
      tip: tipRows(x.name, [['1 day', F.pct(x.chg_1d)], ['1 week', F.pct(x.chg_1w)], ['1 month', F.pct(x.chg_1m)], ['3 months', F.pct(x.chg_3m)], ['Against the index (3M)', F.pct(x.rs)], ['Stocks', String(x.n)]]),
    })), { max: mx, minW: narrow() ? 138 : 168, wrap: true, onClick: it => go('/screener', { m: 'psx', sec: it.label }) }));
  };
  drawHeat();
  const spanSeg = seg(SECTOR_SPANS, span, v => { span = v; drawHeat(); }, { sm: true, label: 'Sector period' });
  const maxRs = Math.max(...secs.map(x => Math.abs(x.rs ?? 0)), 1);
  const maxSc = Math.max(...secs.map(x => Math.abs(x.score ?? 0)), 0.25);
  const cols = [
    { k: 'name', label: 'Sector', first: true, cell: x => h('a', { class: 'sec-link', ...linkProps('/screener', { m: 'psx', sec: x.name }) }, x.name) },
    { k: 'n', label: 'Stocks', r: true, cls: 'hide-sm', cell: x => String(x.n) },
    { k: 'chg_1d', label: '1D', r: true, cls: 'hide-sm', cell: x => chgSpan(x.chg_1d, 1) },
    { k: 'chg_1w', label: '1W', r: true, cls: 'hide-sm', cell: x => chgSpan(x.chg_1w, 1) },
    { k: 'chg_1m', label: '1M', r: true, cell: x => chgSpan(x.chg_1m, 1) },
    { k: 'chg_3m', label: '3M', r: true, cls: 'hide-sm', cell: x => chgSpan(x.chg_3m, 1) },
    { k: 'rs', label: 'Vs index', r: true, title: 'Median 3-month move of the sector minus the index 3-month move. Positive means the sector is leading the market.', cell: x => biBar(x.rs, maxRs, F.pct(x.rs, 1)) },
    { k: 'pct_above_50', label: 'Above 50-day', r: true, cls: 'hide-sm', title: 'Share of the sector’s stocks trading above their 50-day average', cell: x => (F.isNum(x.pct_above_50) ? F.num(x.pct_above_50, 0) + '%' : F.NA) },
    { k: 'score', label: 'Signal', r: true, title: 'Average positional-horizon signal score of the sector’s stocks, from -1 (bearish) to +1 (bullish)', cell: x => biBar(x.score, maxSc, F.num(x.score, 2, { sign: true })) },
    { k: 'top', label: 'Leaders', nosort: true, cls: 'hide-sm', title: 'The two stocks with the strongest positional long setups', cell: x => h('span', { class: 'leaders' }, ...(x.top || []).flatMap((sym, i) => [i ? ', ' : null, h('a', { class: 'sym', ...linkProps('/i/PSX:' + sym) }, sym)])) },
  ];
  const table = sortTable({ cols, rows: secs.map(x => ({ ...x, href: null })), sort: 'score', dir: -1, cls: 'sectors' });

  return h('section', { class: 'stack' },
    sectionTitle('Sectors', null, 'Median move of the stocks in each sector. Tap a sector to see its stocks in the screener.'),
    card({ title: 'Sector heat map', info: 'Each tile is the median price change of the stocks in that sector over the period you pick, which keeps one outlier from skewing it. Green tiles are rising, red are falling, and a deeper shade is a bigger move.', actions: [spanSeg], body: heatHost }),
    card({ title: 'Sector scorecard', info: 'Click a column title to sort. "Against index" is the sector’s 3-month move minus the index’s, so it shows leadership. "Signal" is the average score from Acumen’s positional-horizon analysis of the sector’s stocks.', flush: true, body: table }));
}

/* ======================================================================== Forex */
function fxTab(ctx) {
  const d = ctx.ov.markets.fx;
  if (!d) return noData('No forex data in this build', 'The latest build has no currency section.');
  const wrap = h('div', { class: 'stack loose' });
  wrap.append(statusRow('fx', d));
  wrap.append(h('div', { class: 'grid mk-two' }, strengthCard(d), regimeCard('fx', d)));
  const mx = matrixCard(d);
  if (mx) wrap.append(mx);
  const mv = moversSection('fx', d);
  if (mv) wrap.append(mv);
  return wrap;
}

const FX_SPANS = [{ value: 'chg_1d', label: '1D' }, { value: 'chg_1w', label: '1W' }, { value: 'chg_1m', label: '1M' }];
const FX_SPAN_WORD = { chg_1d: 'today', chg_1w: 'this week', chg_1m: 'this month' };

function strengthCard(d) {
  const st = d.strength || [];
  if (!st.length) return card({ title: 'Currency strength', body: empty('Not enough currency data', 'Strength needs history for most of the major pairs.', 'swap') });
  let span = 'chg_1w';
  const host = h('div', { class: 'stack' });
  const draw = () => {
    const sorted = st.slice().sort(by(x => x[span], -1));
    const top = sorted[0], bot = sorted[sorted.length - 1];
    replace(host,
      hbars(sorted.map(x => ({
        label: x.ccy, value: x[span], text: F.pct(x[span], 2),
        tip: tipRows(x.ccy, [['Today', F.pct(x.chg_1d, 2)], ['1 week', F.pct(x.chg_1w, 2)], ['1 month', F.pct(x.chg_1m, 2)], ['Rank this week', `${x.rank} of ${st.length}`]]),
      })), { signed: true, labelW: 44 }),
      F.isNum(top[span]) && F.isNum(bot[span]) ? h('p', { class: 'mc-note' }, h('b', null, top.ccy), ` is the strongest currency ${FX_SPAN_WORD[span]} (${F.pct(top[span], 2)}) and `, h('b', null, bot.ccy), ` the weakest (${F.pct(bot[span], 2)}). The widest gaps are where trends between currencies are most one-sided.`) : null);
  };
  draw();
  const spanSeg = seg(FX_SPANS, span, v => { span = v; draw(); }, { sm: true, label: 'Strength period' });
  return card({ title: 'Currency strength', info: 'Average move of each currency against the other seven majors over the period. It strips out the noise of any single pair, so you can see which currencies are driving the moves.', actions: [spanSeg], body: host });
}

function matrixCard(d) {
  const mx = d.matrix || {};
  const ccys = mx.ccys || [];
  if (!ccys.length || !(mx.chg_1w || []).length) return null;
  const dec = narrow() ? 1 : 2;
  const tip = (rl, cl, v) => h('div', null, tipRows(`${rl} against ${cl}`, [['Past week', F.pct(v, 2)]]), h('div', { class: 'muted' }, v >= 0 ? `${rl} gained on ${cl}` : `${rl} lost to ${cl}`));
  return h('section', { class: 'stack' },
    sectionTitle('Who is beating whom', null, 'The past week’s move of every major currency against every other.'),
    card({
      title: 'Cross-currency moves, 1 week', info: 'Each cell is how far the row currency moved against the column currency over the past week. Shading follows the size of the move: your gain colour when the row currency gained, your loss colour when it lost. Read a row to see how one currency did against all the others.',
      body: h('div', { class: 'stack' }, heatMatrix(ccys, ccys, mx.chg_1w, { fmt: v => F.num(v, dec), corner: 'vs', tip }),
        h('p', { class: 'fs-xs muted' }, 'Row currency against column currency, in percent. Mirror cells always have the opposite sign.')),
    }));
}

/* ======================================================================== Crypto */
const fearGreed = v => (v <= 24 ? 'Extreme fear' : v <= 44 ? 'Fear' : v <= 55 ? 'Neutral' : v <= 74 ? 'Greed' : 'Extreme greed');
const CRYPTO_SPANS = [{ value: 'chg_1d', label: '24h' }, { value: 'chg_7d', label: '7d' }, { value: 'chg_30d', label: '30d' }];

async function cryptoTab(ctx) {
  const d = ctx.ov.markets.crypto;
  if (!d) return noData('No crypto data in this build', 'The latest build has no crypto section.');
  const idx = await D.index().catch(() => null);
  const wrap = h('div', { class: 'stack loose' });
  wrap.append(statusRow('crypto', d));
  const g = d.global;
  if (g) {
    const fg = g.fear_greed;
    wrap.append(card({
      title: 'Whole market',
      body: h('div', { class: 'stats' },
        stat('Total market cap', F.money(g.total_mcap, '$', { compactFrom: 1e3 }), F.isNum(g.mcap_chg_24h) ? F.pct(g.mcap_chg_24h, 1) + ' in 24 hours' : null, { small: true }),
        stat('Bitcoin share', F.isNum(g.btc_dominance) ? F.num(g.btc_dominance, 1) + '%' : F.NA, 'of total market cap', { small: true, info: 'Bitcoin dominance. A rising share usually means money is moving into the safest crypto asset; a falling share means traders are taking more risk in other coins.' }),
        stat('Ethereum share', F.isNum(g.eth_dominance) ? F.num(g.eth_dominance, 1) + '%' : F.NA, F.isNum(g.btc_dominance) && F.isNum(g.eth_dominance) ? `all other coins ${F.num(100 - g.btc_dominance - g.eth_dominance, 1)}%` : null, { small: true }),
        F.isNum(fg) ? stat('Fear and greed', `${F.num(fg, 0)} · ${fearGreed(fg)}`, 'scale 0 to 100', { small: true, info: 'A sentiment gauge for crypto from 0 (extreme fear) to 100 (extreme greed). Very high readings often come near short-term tops and very low ones near washouts, but it is a sentiment read, not a signal on its own.' }) : null),
    }));
  }
  wrap.append(h('div', { class: 'grid mk-top' }, cryptoMapCard(d), regimeCard('crypto', d)));
  const coins = coinsCard(d, idx);
  if (coins) wrap.append(coins);
  const mv = moversSection('crypto', d);
  if (mv) wrap.append(mv);
  return wrap;
}

function cryptoMapCard(d) {
  const heat = (d.heat || []).filter(x => x.mcap > 0).slice(0, 40);
  if (!heat.length) return card({ title: 'Market map', body: empty('No coin data', 'The build has no market-cap ranking.', 'grid') });
  let span = 'chg_1d';
  const host = h('div');
  const dims = narrow() ? { w: 360, hgt: 440 } : { w: 760, hgt: 360 };
  const draw = () => {
    const mx = Math.max(...heat.map(x => Math.abs(x[span] ?? 0)), 1);
    replace(host, treemap(heat.map(x => ({
      label: x.sym, area: Math.sqrt(x.mcap), text: F.pct(x[span], 1), color: divColor(x[span], mx), aria: `${x.name}, ${F.pct(x[span], 1)}`,
      tip: tipRows(x.name, [['Market cap', F.money(x.mcap, '$')], ['24 hours', F.pct(x.chg_1d)], ['7 days', F.pct(x.chg_7d)], ['30 days', F.pct(x.chg_30d)]]),
      href: () => go('/i/' + x.id),
    })), dims));
  };
  draw();
  const spanSeg = seg(CRYPTO_SPANS, span, v => { span = v; draw(); }, { sm: true, label: 'Map period' });
  return card({
    title: 'Market map', info: 'Every tile is a coin. Tile area follows the square root of its market cap, so big coins are bigger but small ones stay readable. Shade shows the price change over the period; tap a tile to open the coin.',
    actions: [spanSeg], body: h('div', { class: 'stack' }, host, h('p', { class: 'fs-xs muted' }, `Top ${heat.length} coins by market cap.`)),
  });
}

function coinsCard(d, idx) {
  const heat = (d.heat || []).filter(x => x.mcap > 0);
  if (!heat.length) return null;
  const rows = heat.map((x, i) => ({ ...x, rank: i + 1, px: idx && idx.get(x.id) ? idx.get(x.id).px : null, href: '/i/' + x.id }));
  const cols = [
    { k: 'sym', label: 'Coin', first: true, cell: x => h('div', { class: 'coin-id' }, h('span', { class: 'rk muted' }, String(x.rank)), h('div', { class: 'scr-id' }, h('a', { class: 'sym', ...linkProps('/i/' + x.id) }, x.sym), h('div', { class: 'nm', title: x.name }, x.name))) },
    { k: 'px', label: 'Price', r: true, cell: x => (F.isNum(x.px) ? '$' + F.px(x.px, 'crypto') : F.NA) },
    { k: 'mcap', label: 'Market cap', r: true, cls: 'hide-sm', cell: x => F.money(x.mcap, '$') },
    { k: 'chg_1d', label: '24h', r: true, cell: x => chgSpan(x.chg_1d, 1) },
    { k: 'chg_7d', label: '7d', r: true, cell: x => chgSpan(x.chg_7d, 1) },
    { k: 'chg_30d', label: '30d', r: true, cls: 'hide-sm', cell: x => chgSpan(x.chg_30d, 1) },
  ];
  return h('section', { class: 'stack' },
    sectionTitle('Largest coins'),
    card({ flush: true, body: sortTable({ cols, rows, sort: 'mcap', dir: -1, cls: 'coins' }) }));
}

/* ======================================================================== Macro and rates */
const RATE_MAJORS = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'NZD', 'CAD', 'SEK', 'NOK'];

async function macroTab() {
  const mac = await D.get('macro');
  const series = mac.series || {};
  const wrap = h('div', { class: 'stack loose' });
  const ages = Object.values(series).map(x => F.daysOld(x.asof)).filter(F.isNum);
  const oldest = ages.length ? Math.max(...ages) : null;
  wrap.append(h('div', { class: 'stack' },
    h('div', { class: 'mk-status' }, h('div', { class: 'row wrap' }, chip(`Updated ${F.dateTime(mac.generated_at)}`, 'outline', { icon: 'clock' }), oldest != null && oldest > 5 ? chip(`oldest series ${oldest} days old`, 'warn', { icon: 'alert' }) : null)),
    oldest != null && oldest > 5 ? banner('warn', h('b', null, 'Some of these series have not updated lately. '), 'Check the dates on each tile and the source status in Data & Settings.') : null));

  const listed = new Set();
  const groups = [];
  for (const gp of MACRO_GROUPS) {
    const tiles = gp.keys.filter(k => series[k]).map(k => { listed.add(k); return macroTile(k, series[k], { full: true }); });
    if (tiles.length) groups.push(h('section', { class: 'stack macro-group' }, h('h2', { class: 'label' }, gp.title), h('div', { class: 'macro-grid' }, ...tiles)));
  }
  const other = Object.keys(series).filter(k => !listed.has(k));
  if (other.length) groups.push(h('section', { class: 'stack macro-group' }, h('h2', { class: 'label' }, 'Other'), h('div', { class: 'macro-grid' }, ...other.map(k => macroTile(k, series[k], { full: true })))));
  if (groups.length) wrap.append(h('div', { class: 'macro-groups' }, ...groups));
  else wrap.append(noData('No cross-asset data in this build', 'The macro file has no series.'));

  wrap.append(h('div', { class: 'grid mk-two' }, h('div', { class: 'stack' }, pakistanCard(mac), rulesCard()), ratesCard(mac)));
  return wrap;
}

function pakistanCard(mac) {
  const pk = mac.pakistan || {};
  if (!F.isNum(pk.policy_rate)) return card({ title: 'Pakistan rates and inflation', body: empty('No Pakistan rate data', 'The build did not include the policy rate.', 'brief') });
  const age = F.daysOld(pk.asof);
  const old = age != null && age > 60;
  return card({
    title: 'Pakistan rates and inflation',
    info: 'The State Bank of Pakistan policy rate sets the price of money in the economy. Inflation is the year-on-year rise in consumer prices. The real rate is the first minus the second.',
    body: h('div', { class: 'stack' },
      h('div', { class: 'stats' },
        stat('Policy rate', F.num(pk.policy_rate, 2) + '%', 'State Bank of Pakistan', { small: true }),
        stat('Inflation', F.isNum(pk.cpi_yoy) ? F.num(pk.cpi_yoy, 1) + '%' : F.NA, 'consumer prices, year on year', { small: true }),
        stat('Real policy rate', F.isNum(pk.real_rate) ? F.num(pk.real_rate, 1, { sign: true }) + '%' : F.NA, 'policy rate minus inflation', { small: true, info: 'A positive real rate means savers earn more than inflation. The higher it is, the more attractive risk-free T-bills and bonds look against shares, and the more room the central bank has to cut if inflation stays low.' })),
      old ? banner('warn', h('b', null, `These figures are ${age} days old. `), 'Check sbp.org.pk and pbs.gov.pk for the latest policy rate and CPI.') : null,
      h('div', { class: 'fs-xs muted' }, `As of ${F.date(pk.asof, { year: true })}${pk.note ? ' · ' + pk.note : ''}`)),
  });
}

function ratesCard(mac) {
  const rt = mac.rates || {};
  const pol = rt.policy_pct || {};
  const all = Object.keys(pol).filter(k => F.isNum(pol[k]));
  if (!all.length) return card({ title: 'Central-bank policy rates', body: empty('No rate table', 'Add a rates table to the configuration.', 'table') });
  const sets = { majors: ['PKR', ...RATE_MAJORS.filter(k => k in pol)], emerging: ['PKR', ...all.filter(k => k !== 'PKR' && !RATE_MAJORS.includes(k))] };
  let which = 'majors';
  const host = h('div', { class: 'stack' });
  const draw = () => {
    const keys = sets[which].filter(k => F.isNum(pol[k])).sort((a, b) => pol[b] - pol[a]);
    const bars = hbars(keys.map(k => ({ label: k, value: pol[k], text: F.num(pol[k], 2) + '%', color: k === 'PKR' ? 'var(--accent)' : 'color-mix(in srgb, var(--ink-3) 50%, var(--surface-2))' })), { labelW: 40 });
    bars.querySelectorAll('.hb-row').forEach((row, i) => { if (keys[i] === 'PKR') row.classList.add('strong'); });
    replace(host, bars);
  };
  draw();
  const whichSeg = seg([{ value: 'majors', label: 'Major currencies' }, { value: 'emerging', label: 'Emerging' }], which, v => { which = v; draw(); }, { sm: true, label: 'Currency group' });
  return card({
    title: 'Central-bank policy rates', info: 'The overnight policy rate of each currency’s central bank. Money tends to earn more in high-rate currencies (carry), which is one input to Acumen’s forex scores. The rupee is highlighted for comparison.',
    actions: [whichSeg],
    body: h('div', { class: 'stack' },
      rt.stale ? banner('warn', h('b', null, `This table was last checked on ${F.date(rt.as_of, { year: true })}. `), 'Central banks change rates often. It is kept by hand in your Acumen configuration file (the rates section), so update it after rate decisions; forex carry scores use it.') : null,
      host,
      h('div', { class: 'fs-xs muted' }, `Last checked ${F.date(rt.as_of, { year: true })}`)),
  });
}

function rulesCard() {
  const pts = [
    ['A stronger dollar and rising US yields', 'tend to pressure emerging-market currencies and risk assets, including the rupee and frontier equities. A weaker dollar usually eases that pressure.'],
    ['Oil prices', 'matter to Pakistan because it imports most of its fuel. A sustained rise in Brent raises the import bill and inflation and can weigh on the rupee. Oil and gas producers tend to gain while fuel-heavy businesses suffer.'],
    ['The policy rate', 'sets what risk-free T-bills pay. Higher rates make shares compete harder for money and lower what future profits are worth today; the start of a rate-cutting cycle has often helped Pakistani equities.'],
    ['The VIX', 'is a fear gauge for US stocks. Below about 18 is calm and above about 25 is stressed. Acumen reads those two levels when it scores forex risk appetite.'],
    ['Gold', 'often rises when real yields or the dollar fall and when investors want safety, though it can fall in a sudden dollar rally.'],
  ];
  return card({
    title: 'How to read the backdrop', info: 'These are broad relationships, not forecasts. Markets break them often, so use them as context for the ideas Acumen produces, never as a reason to trade on their own.',
    body: h('ul', { class: 'pts' }, ...pts.map(([a, b]) => h('li', null, h('b', null, a), ' ', b))),
  });
}
