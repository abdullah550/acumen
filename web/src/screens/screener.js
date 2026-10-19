import { h, clear, replace, debounce, download } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import { linkProps, go } from '../lib/router.js';
import { HORIZONS, HZ_LABEL, MARKETS, MARKET_LABEL, GRADE_ORDER, by } from '../lib/util.js';
import { seg, select, field, empty, chip, mkChip, chgSpan, sparkline, toast, explain } from '../ui/kit.js';
import { signalBadge } from '../ui/signals.js';
import { pageHead, errorBlock, ideaKey } from './common.js';

const PAGE = 100;
const LIQ_ORDER = { A: 4, B: 3, C: 2, D: 1 };

/* ---------- filters ---------- */
const DEFAULT_F = { sec: 'all', dir: 'any', hz: 'any', minConv: '', grade: 'any', rsiMin: '', rsiMax: '', trend: 'any', peMax: '', dyMin: '', roeMin: '', upsideMin: '', liq: 'any', tradable: false, watch: false };

const PRESETS = [
  { id: 'all', label: 'All', info: 'Everything in the selected market.' },
  { id: 'best', label: 'Best setups', info: 'Instruments that have at least one actionable trade idea today.', test: (r, c) => c.act.has(r.id) },
  { id: 'momentum', label: 'Momentum', info: 'Price above both the 50-day and 200-day averages and up more than 10% over three months.', test: r => r.above50 && r.above200 && r.chg3m > 10 },
  { id: 'breakout', label: 'Near 52-week high', info: 'Within 3% of the 52-week high and above the 200-day average.', test: r => F.isNum(r.hi52d) && r.hi52d >= -3 && r.above200 },
  { id: 'oversold', label: 'Oversold', info: 'RSI(14) below 30: sold hard recently. Can bounce, or keep falling.', test: r => F.isNum(r.rsi) && r.rsi < 30 },
  { id: 'overbought', label: 'Overbought', info: 'RSI(14) above 70: has run hard. Often a reason to wait, not to chase.', test: r => F.isNum(r.rsi) && r.rsi > 70 },
  { id: 'value', label: 'Cheap on earnings', info: 'PSX only: P/E between 0 and 8 and price below 1.5 times book value.', test: r => r.m === 'psx' && F.isNum(r.pe) && r.pe > 0 && r.pe < 8 && F.isNum(r.pb) && r.pb < 1.5 },
  { id: 'income', label: 'High dividend', info: 'PSX only: dividend yield of 6% or more.', test: r => r.m === 'psx' && F.isNum(r.dy) && r.dy >= 6 },
  { id: 'quality', label: 'Quality', info: 'PSX only: return on equity of 15% or more and P/E under 15.', test: r => r.m === 'psx' && F.isNum(r.roe) && r.roe >= 15 && F.isNum(r.pe) && r.pe > 0 && r.pe < 15 },
  { id: 'upside', label: 'Below fair value', info: 'PSX only: price at least 30% under the blended fair value (a rough model estimate).', test: r => F.isNum(r.upside) && r.upside >= 30 },
  { id: 'calm', label: 'Low volatility', info: 'Thirty-day volatility in the calmest third of this market.', test: (r, c) => F.isNum(r.vol30) && r.vol30 <= (c.calmCut[r.m] ?? 0) },
];

function passes(r, f, ctx) {
  if (f.tradable && !r.tradable) return false;
  if (f.watch && !ctx.watch.has(r.id)) return false;
  if (f.sec !== 'all' && r.sec !== f.sec) return false;
  if (f.liq !== 'any' && (LIQ_ORDER[r.liq] || 0) < LIQ_ORDER[f.liq]) return false;
  if (f.trend === 'above50' && !r.above50) return false;
  if (f.trend === 'above200' && !r.above200) return false;
  if (f.trend === 'both' && !(r.above50 && r.above200)) return false;
  if (f.trend === 'below200' && r.above200 !== false) return false;
  const num = v => (v === '' || v == null ? null : Number(v));
  const rsiMin = num(f.rsiMin), rsiMax = num(f.rsiMax), peMax = num(f.peMax), dyMin = num(f.dyMin), roeMin = num(f.roeMin), upMin = num(f.upsideMin);
  if (rsiMin != null && !(r.rsi >= rsiMin)) return false;
  if (rsiMax != null && !(r.rsi <= rsiMax)) return false;
  if (peMax != null && !(r.pe > 0 && r.pe <= peMax)) return false;
  if (dyMin != null && !(r.dy >= dyMin)) return false;
  if (roeMin != null && !(r.roe >= roeMin)) return false;
  if (upMin != null && !(r.upside >= upMin)) return false;
  // signal filters look at the chosen horizon (or any)
  const hzs = f.hz === 'any' ? HORIZONS : [f.hz];
  if (f.dir !== 'any' || f.minConv !== '' || f.grade !== 'any') {
    const minC = num(f.minConv) ?? 0;
    const ok = hzs.some(hz => {
      const s = r.sig && r.sig[hz];
      if (!s || s.dir == null) return false;
      if (f.dir !== 'any' && s.dir !== f.dir) return false;
      if (s.conv < minC) return false;
      if (f.grade !== 'any' && (GRADE_ORDER[s.grade] || 0) < GRADE_ORDER[f.grade]) return false;
      return true;
    });
    if (!ok) return false;
  }
  return true;
}

/* ---------- cells ---------- */
const sigCell = (r, hz, c) => signalBadge(r, hz, c.ideas);

const liqChip = t => (t ? chip(t, 'outline', { title: `Liquidity tier ${t}: A trades most easily, D is thin` }) : F.NA);
const pc = (v, d = 1) => (F.isNum(v) ? chgSpan(v, d) : F.NA);
const plain = (v, d = 1) => (F.isNum(v) ? F.num(v, d) : F.NA);
const pcPlain = (v, d = 1) => (F.isNum(v) ? F.pct(v, d, { sign: false }) : F.NA);

const COLSETS = {
  core: [
    { k: 'px', label: 'Price', r: true, get: r => r.px, cell: r => h('span', { class: 'mono' }, F.px(r.px, r.m)) },
    { k: 'chg1d', label: '1D', r: true, get: r => r.chg1d, cell: r => pc(r.chg1d) },
    { k: 'chg1m', label: '1M', r: true, get: r => r.chg1m, cell: r => pc(r.chg1m) },
    { k: 'chg1y', label: '1Y', r: true, get: r => r.chg1y, cell: r => pc(r.chg1y, 0) },
    { k: 'spark', label: 'Last 30 days', nosort: true, cell: r => (r.spark ? h('div', { class: 'spark-cell' }, sparkline(r.spark, { w: 90, hgt: 24, area: false })) : F.NA) },
    ...HORIZONS.map(hz => ({ k: 'sig_' + hz, label: HZ_LABEL[hz], title: `${HZ_LABEL[hz]} signal: direction, grade and conviction. A solid badge is an actionable idea, an outlined one is reference only, a faded one is a weak signal with no plan.`, get: r => (r.sig && r.sig[hz] && r.sig[hz].dir ? r.sig[hz].conv : null), cell: (r, c) => sigCell(r, hz, c) })),
    { k: 'rsi', label: 'RSI', r: true, title: 'RSI(14): above 70 is stretched up, below 30 stretched down', get: r => r.rsi, cell: r => plain(r.rsi, 0) },
    { k: 'liq', label: 'Liquidity', get: r => LIQ_ORDER[r.liq] || 0, cell: r => liqChip(r.liq) },
  ],
  fund: [
    { k: 'px', label: 'Price', r: true, get: r => r.px, cell: r => h('span', { class: 'mono' }, F.px(r.px, r.m)) },
    { k: 'mcap', m: ['psx', 'crypto'], label: 'Market cap', r: true, get: r => r.mcap, cell: r => (F.isNum(r.mcap) ? F.compact(r.mcap) : F.NA) },
    { k: 'pe', m: ['psx'], label: 'P/E', r: true, title: 'Price ÷ yearly earnings per share', get: r => (r.pe > 0 ? r.pe : null), cell: r => plain(r.pe > 0 ? r.pe : null, 1) },
    { k: 'pb', m: ['psx'], label: 'P/B', r: true, title: 'Price ÷ book value per share', get: r => r.pb, cell: r => plain(r.pb, 2) },
    { k: 'dy', m: ['psx'], label: 'Div. yield', r: true, get: r => r.dy, cell: r => pcPlain(r.dy, 1) },
    { k: 'roe', m: ['psx'], label: 'ROE', r: true, title: 'Return on equity', get: r => r.roe, cell: r => pcPlain(r.roe, 1) },
    { k: 'eps', m: ['psx'], label: 'EPS', r: true, get: r => r.eps, cell: r => plain(r.eps, 2) },
    { k: 'fv', m: ['psx'], label: 'Fair value', r: true, title: 'Blended fair value from simple models. Rough guide only.', get: r => r.fv, cell: r => plain(r.fv, 0) },
    { k: 'upside', m: ['psx'], label: 'Gap to fair value', r: true, get: r => r.upside, cell: r => pc(r.upside, 0) },
    { k: 'carry', m: ['fx'], label: 'Carry (FX)', r: true, title: 'Approximate yearly interest earned holding the pair long', get: r => r.carry, cell: r => (F.isNum(r.carry) ? F.pct(r.carry, 2) : F.NA) },
  ],
  tech: [
    { k: 'px', label: 'Price', r: true, get: r => r.px, cell: r => h('span', { class: 'mono' }, F.px(r.px, r.m)) },
    { k: 'chg1w', label: '1W', r: true, get: r => r.chg1w, cell: r => pc(r.chg1w) },
    { k: 'chg3m', label: '3M', r: true, get: r => r.chg3m, cell: r => pc(r.chg3m, 0) },
    { k: 'rsi', label: 'RSI', r: true, get: r => r.rsi, cell: r => plain(r.rsi, 0) },
    { k: 'adx', label: 'ADX', r: true, title: 'Trend strength: above 25 is trending', get: r => r.adx, cell: r => plain(r.adx, 0) },
    { k: 'atrp', label: 'ATR %', r: true, title: 'Typical daily range as a share of price', get: r => r.atrp, cell: r => pcPlain(r.atrp, 1) },
    { k: 'd50', label: 'vs 50-day', r: true, get: r => r.d50, cell: r => pc(r.d50, 1) },
    { k: 'd200', label: 'vs 200-day', r: true, get: r => r.d200, cell: r => pc(r.d200, 1) },
    { k: 'hi52d', label: 'From 52w high', r: true, get: r => r.hi52d, cell: r => pc(r.hi52d, 1) },
    { k: 'lo52d', label: 'From 52w low', r: true, get: r => r.lo52d, cell: r => pc(r.lo52d, 0) },
    { k: 'rvol', m: ['psx', 'crypto'], label: 'Rel. volume', r: true, title: 'Today’s volume ÷ 20-day average', get: r => r.rvol, cell: r => (F.isNum(r.rvol) ? F.num(r.rvol, 1) + '×' : F.NA) },
  ],
  risk: [
    { k: 'px', label: 'Price', r: true, get: r => r.px, cell: r => h('span', { class: 'mono' }, F.px(r.px, r.m)) },
    { k: 'vol30', label: 'Volatility 30d', r: true, get: r => r.vol30, cell: r => pcPlain(r.vol30, 0) },
    { k: 'mdd1y', label: 'Worst fall 1Y', r: true, get: r => r.mdd1y, cell: r => pc(r.mdd1y, 0) },
    { k: 'sharpe1y', label: 'Sharpe 1Y', r: true, get: r => r.sharpe1y, cell: r => plain(r.sharpe1y, 2) },
    { k: 'beta', m: ['psx', 'crypto'], label: 'Beta', r: true, get: r => r.beta, cell: r => plain(r.beta, 2) },
    { k: 'adv', m: ['psx', 'crypto'], label: 'Traded per day', r: true, get: r => r.adv, cell: r => (F.isNum(r.adv) ? F.compact(r.adv) : F.NA) },
    { k: 'liq', label: 'Liquidity', get: r => LIQ_ORDER[r.liq] || 0, cell: r => liqChip(r.liq) },
  ],
};
const COLSET_LABEL = { core: 'Overview', fund: 'Fundamentals', tech: 'Technicals', risk: 'Risk' };

/* ---------- ranking used by "Best first" ---------- */
function bestKey(r, ctx) {
  const ideas = HORIZONS.map(hz => ctx.ideas.get(r.id + '|' + hz)).filter(Boolean);
  if (ideas.length) {
    return Math.max(...ideas.map(i => (i.actionable ? 1e6 : 0) + (i.calibrated ? 1e5 : 0) + (GRADE_ORDER[i.grade] || 0) * 1e4 + i.conviction * 10 + (i.exp_r ?? 0)));
  }
  const convs = HORIZONS.map(hz => (r.sig && r.sig[hz] && r.sig[hz].dir ? r.sig[hz].conv : 0));
  return Math.max(0, ...convs) / 10;
}

function loadState(route) {
  const saved = S.get('screener') || {};
  const st = { market: 'psx', q: '', sort: 'best', dir: -1, preset: 'all', cols: 'core', ...saved };
  st.f = { ...DEFAULT_F, ...(saved.f || {}) };
  const q = route.query || {};
  if (q.m && (q.m === 'all' || MARKETS.includes(q.m))) st.market = q.m;
  if (q.sec) { st.f.sec = q.sec; if (!q.m) st.market = 'psx'; }
  if (q.q != null) st.q = q.q;
  if (q.preset && PRESETS.some(p => p.id === q.preset)) st.preset = q.preset;
  if (!COLSETS[st.cols]) st.cols = 'core';
  return st;
}

export default async function render(route) {
  let ins;
  try { ins = await D.get('instruments'); } catch (e) { return { el: errorBlock(e) }; }
  const ideasFile = await D.get('ideas').catch(() => null);
  const ideas = new Map();
  const act = new Set();
  for (const i of (ideasFile && ideasFile.ideas) || []) { ideas.set(ideaKey(i), i); if (i.actionable) act.add(i.id); }
  const rows = ins.rows;
  const st = loadState(route);
  const save = () => S.set('screener', st, { silent: true });

  // volatility cut-offs per market for the "Low volatility" preset
  const calmCut = {};
  for (const m of MARKETS) {
    const v = rows.filter(r => r.m === m && F.isNum(r.vol30)).map(r => r.vol30).sort((a, b) => a - b);
    if (v.length) calmCut[m] = v[Math.floor(v.length / 3)];
  }
  const ctx = { ideas, act, calmCut, watch: new Set(S.get('watchlist') || []) };
  const sectors = Array.from(new Set(rows.filter(r => r.m === 'psx' && r.sec).map(r => r.sec))).sort();
  if (st.f.sec !== 'all' && !sectors.includes(st.f.sec)) st.f.sec = 'all';
  if (st.market !== 'psx' && st.market !== 'all') st.f.sec = 'all';

  let shown = PAGE;
  const root = h('div', { class: 'stack' });
  const body = h('div');
  const countEl = h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } });
  const moreBtn = h('button', { class: 'btn', type: 'button', onclick: () => { shown += PAGE * 2; draw(); } }, 'Show more');

  /* controls */
  const marketSeg = seg([{ value: 'all', label: 'All' }, ...MARKETS.map(m => ({ value: m, label: MARKET_LABEL[m] }))], st.market, v => { st.market = v; if (v !== 'psx' && v !== 'all') st.f.sec = 'all'; keepSort(); shown = PAGE; save(); buildAdv(); updateBadge(); draw(); }, { label: 'Market' });
  const colSeg = seg(Object.keys(COLSETS).map(k => ({ value: k, label: COLSET_LABEL[k] })), st.cols, v => { st.cols = v; keepSort(); save(); draw(); }, { sm: true, label: 'Columns' });
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search symbol or name', 'aria-label': 'Search instruments', value: st.q, oninput: debounce(e => { st.q = e.target.value.trim(); shown = PAGE; save(); draw(); }, 120) });
  const presetRow = h('div', { class: 'preset-row', role: 'group', 'aria-label': 'Quick screens' });
  const drawPresets = () => {
    clear(presetRow);
    for (const p of PRESETS) {
      const b = h('button', { class: 'pill-toggle', type: 'button', 'aria-pressed': String(st.preset === p.id), title: p.info, onclick: () => { st.preset = p.id; shown = PAGE; save(); drawPresets(); draw(); } }, p.label);
      presetRow.append(b);
    }
  };

  const inp = (key, ph, label) => h('input', { class: 'input mono', inputmode: 'decimal', placeholder: ph, value: st.f[key], 'aria-label': label, oninput: debounce(e => { st.f[key] = e.target.value.trim(); shown = PAGE; save(); draw(); updateBadge(); }, 150) });
  const sel = (key, opts, label) => select(opts, st.f[key], v => { st.f[key] = v; shown = PAGE; save(); draw(); updateBadge(); }, { 'aria-label': label || key });
  const chk = (key, label) => h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: st.f[key], onchange: e => { st.f[key] = e.target.checked; shown = PAGE; save(); draw(); updateBadge(); } }), label);
  let secField = null;
  const advGrid = h('div', { class: 'adv-grid' });
  // Controls read their starting value from st.f, so rebuilding the grid is how "Clear all filters" resets them.
  function buildAdv() {
    secField = field('Sector (PSX)', sel('sec', [{ value: 'all', label: 'All sectors' }, ...sectors.map(x => ({ value: x, label: x }))], 'Sector'));
    advGrid.replaceChildren(
      secField,
      field('Signal direction', sel('dir', [{ value: 'any', label: 'Long or short' }, { value: 'long', label: 'Leans long' }, { value: 'short', label: 'Leans short' }], 'Signal direction')),
      field('Signal horizon', sel('hz', [{ value: 'any', label: 'Any horizon' }, ...HORIZONS.map(x => ({ value: x, label: HZ_LABEL[x] }))], 'Signal horizon')),
      field('Min. conviction', inp('minConv', '0 to 100', 'Minimum conviction')),
      field('Min. grade', sel('grade', [{ value: 'any', label: 'Any' }, { value: 'C', label: 'C or better' }, { value: 'B', label: 'B or better' }, { value: 'A', label: 'A only' }], 'Minimum grade')),
      field('Trend', sel('trend', [{ value: 'any', label: 'Any' }, { value: 'above50', label: 'Above 50-day average' }, { value: 'above200', label: 'Above 200-day average' }, { value: 'both', label: 'Above both' }, { value: 'below200', label: 'Below 200-day average' }], 'Trend')),
      field('RSI between', h('div', { class: 'row nowrap' }, inp('rsiMin', 'min', 'RSI minimum'), h('span', { class: 'muted' }, 'to'), inp('rsiMax', 'max', 'RSI maximum'))),
      field('Max. P/E', inp('peMax', 'e.g. 10', 'Maximum P/E')),
      field('Min. dividend yield %', inp('dyMin', 'e.g. 5', 'Minimum dividend yield')),
      field('Min. ROE %', inp('roeMin', 'e.g. 15', 'Minimum ROE')),
      field('Min. gap to fair value %', inp('upsideMin', 'e.g. 25', 'Minimum gap to fair value')),
      field('Liquidity at least', sel('liq', [{ value: 'any', label: 'Any tier' }, { value: 'B', label: 'Tier B or better' }, { value: 'A', label: 'Tier A only' }], 'Liquidity')),
      h('div', { class: 'field' }, h('label', null, 'Also'), h('div', { class: 'stack tight' }, chk('tradable', 'Only things retail brokers offer'), chk('watch', 'Watchlist only'))));
    sync();
  }
  const badge = h('span', { class: 'chip accent', hidden: true });
  const advDetails = h('details', { class: 'adv-filters' }, h('summary', null, icon('filter'), 'More filters', badge), advGrid, h('div', { class: 'row', style: { justifyContent: 'flex-end', marginTop: 'var(--s-3)' } },
    h('button', { class: 'btn sm', type: 'button', onclick: () => { st.f = { ...DEFAULT_F }; st.preset = 'all'; st.q = ''; search.value = ''; shown = PAGE; save(); buildAdv(); updateBadge(); drawPresets(); draw(); } }, 'Clear all filters')));
  const activeCount = () => Object.keys(DEFAULT_F).filter(k => String(st.f[k]) !== String(DEFAULT_F[k])).length;
  function updateBadge() { const n = activeCount(); badge.hidden = !n; badge.textContent = n + ' active'; }
  function sync() { if (secField) secField.hidden = !(st.market === 'psx' || st.market === 'all'); }
  if (activeCount()) advDetails.open = true;
  buildAdv(); updateBadge(); drawPresets();

  root.append(pageHead('Screener', 'Every stock, currency pair and coin Acumen tracks. Filter, sort and open any row for the full analysis.', [h('button', { class: 'btn', type: 'button', onclick: () => exportCsv() }, icon('download'), 'CSV')]),
    h('section', { class: 'card pad filters' },
      h('div', { class: 'f-row' }, h('div', { class: 'f-cell' }, h('div', { class: 'label' }, 'Market'), marketSeg), h('div', { class: 'f-cell grow' }, h('div', { class: 'label' }, 'Search'), search), h('div', { class: 'f-cell' }, h('div', { class: 'label' }, 'Columns'), colSeg)),
      h('div', { class: 'stack tight' }, h('div', { class: 'label' }, 'Quick screens', explain('One-tap screens built from simple rules. Hover a button to see its rule. They are starting points for research, not recommendations.')), presetRow),
      advDetails),
    countEl, body);

  // Columns that make no sense for the market on screen (P/E for a currency pair) are left out.
  function colsNow() { return COLSETS[st.cols].filter(c => st.market === 'all' || !c.m || c.m.includes(st.market)); }
  function colKeys() { return colsNow().map(c => c.k); }
  function keepSort() { if (st.sort !== 'best' && st.sort !== 'sym' && !colKeys().includes(st.sort)) st.sort = 'best'; }

  function filtered() {
    const preset = PRESETS.find(p => p.id === st.preset) || PRESETS[0];
    const q = st.q.toLowerCase();
    let list = rows.filter(r => (st.market === 'all' || r.m === st.market) && (!q || r.sym.toLowerCase().includes(q) || (r.name || '').toLowerCase().includes(q))
      && (!preset.test || preset.test(r, ctx)) && passes(r, st.f, ctx));
    if (st.sort === 'best') list = list.map(r => [bestKey(r, ctx), r]).sort((a, b) => b[0] - a[0]).map(x => x[1]);
    else if (st.sort === 'sym') list = list.sort(by(r => r.sym, st.dir < 0 ? -1 : 1));
    else {
      const col = Object.values(COLSETS).flat().find(c => c.k === st.sort);
      if (col && col.get) list = list.sort(by(r => col.get(r), st.dir));
    }
    return list;
  }

  function exportCsv() {
    const list = filtered();
    const cols = colsNow().filter(c => !c.nosort || c.k.startsWith('sig_'));
    const head = ['symbol', 'name', 'market', 'sector', ...cols.map(c => c.label)];
    const lines = [head.join(',')];
    const val = (c, r) => {
      if (c.k.startsWith('sig_')) {
        const hz = c.k.slice(4);
        const idea = ideas.get(r.id + '|' + hz);
        const sg = r.sig && r.sig[hz];
        if (idea) return `${idea.dir} ${idea.grade} ${idea.conviction}${idea.actionable ? '' : ' (reference)'}`;
        return sg && sg.dir ? `${sg.dir} weak ${sg.conv}` : '';
      }
      if (c.k === 'liq') return r.liq || '';
      return c.get ? c.get(r) : '';
    };
    for (const r of list) lines.push([r.sym, r.name, r.m, r.sec || '', ...cols.map(c => val(c, r))].map(F.csvCell).join(','));
    toast(download('acumen-screener.csv', lines.join('\n'), 'text/csv') ? `Exported ${list.length} rows` : 'Could not export');
  }

  function th(c) {
    const on = st.sort === c.k;
    return h('th', { class: [c.r && 'r', !c.nosort && 'sortable'], title: c.title || null, 'aria-sort': on ? (st.dir < 0 ? 'descending' : 'ascending') : null,
      onclick: c.nosort ? null : () => { if (st.sort === c.k) st.dir = -st.dir; else { st.sort = c.k; st.dir = c.k === 'sym' ? 1 : -1; } save(); draw(); } },
    c.label, on ? h('span', { class: 'arr' }, st.dir > 0 ? '▲' : '▼') : null);
  }

  function draw() {
    const list = filtered();
    const part = list.slice(0, shown);
    const total = rows.filter(r => st.market === 'all' || r.m === st.market).length;
    countEl.textContent = `${F.int(list.length)} of ${F.int(total)} ${list.length === 1 ? 'instrument' : 'instruments'}${list.length > part.length ? `, showing the first ${F.int(part.length)}` : ''}${st.sort === 'best' ? ', best setups first' : ''}`;
    if (!list.length) {
      body.replaceChildren(empty('Nothing matches', 'Loosen a filter or pick “All” under Quick screens.', 'filter'));
      return;
    }
    const cols = colsNow();
    const symTh = h('th', { class: 'sortable', onclick: () => { if (st.sort === 'sym') st.dir = -st.dir; else { st.sort = 'sym'; st.dir = 1; } save(); draw(); }, 'aria-sort': st.sort === 'sym' ? (st.dir < 0 ? 'descending' : 'ascending') : null }, 'Instrument', st.sort === 'sym' ? h('span', { class: 'arr' }, st.dir > 0 ? '▲' : '▼') : null);
    const table = h('table', { class: 'tbl fixed-first scr' },
      h('thead', null, h('tr', null, symTh, ...cols.map(th))),
      h('tbody', null, ...part.map(r => h('tr', { class: 'link', onclick: e => { if (e.target.closest('a,button')) return; go('/i/' + r.id); } },
        h('td', null, h('div', { class: 'scr-id' }, h('div', { class: 'scr-id-t' }, h('a', { class: 'sym', ...linkProps('/i/' + r.id) }, r.sym), st.market === 'all' ? mkChip(r.m) : null, !r.tradable ? chip('ref', 'outline', { title: 'Shown for information; most retail brokers do not offer it' }) : null), h('div', { class: 'nm' }, r.name + (r.sec && st.market !== 'fx' ? ' · ' + r.sec : '')))),
        ...cols.map(c => h('td', { class: [c.r && 'r'] }, c.cell(r, ctx)))))));
    replace(body, h('div', { class: 'card' }, h('div', { class: 'tbl-wrap scr-wrap' }, table)), list.length > part.length ? h('div', { class: 'row', style: { justifyContent: 'center', marginTop: 'var(--s-4)' } }, moreBtn) : null);
  }
  draw();
  return { el: root };
}
