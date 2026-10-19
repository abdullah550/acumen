import { h, clear, debounce } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import { linkProps, replaceQuery } from '../lib/router.js';
import { HORIZONS, HZ_LABEL, HZ_SPAN, STALE_DAYS } from '../lib/util.js';
import * as J from '../lib/journal.js';
import * as Z from '../lib/sizing.js';
import { chip, mkChip, hzGlyph, gradeBadge, actionChip, isResearch, convMeter, stat, seg, tabs, pillToggle, select, banner, empty, chgSpan } from '../ui/kit.js';
import { ticket, starButton, BLOCKER_TEXT } from '../ui/ticket.js';
import { createPriceChart, OVERLAYS, PANES, overlaySwatch } from '../ui/pricechart.js';
import { palette } from '../ui/lwc.js';
import { errorBlock, rankIdeas, pageHead } from './common.js';
import { visibleTabs, tabLabel } from './instrument-tabs.js';

const KIND = { equity: 'Stock', index: 'Index', etf: 'ETF', reit: 'REIT', fx: 'Currency pair', crypto: 'Coin', metal: 'Metal', commodity: 'Commodity' };
const lc = s => String(s || '').replace(/^./, c => c.toLowerCase());

/* ---------- loading ---------- */
async function loadDetail(raw) {
  try { return await D.detail(raw); } catch (e) {
    if (e && e.code === 'DECRYPT') throw e;
    const [m, ...rest] = raw.split(':');
    const alt = m.toUpperCase() + ':' + rest.join(':').toUpperCase();
    if (alt !== raw) { try { return await D.detail(alt); } catch { /* fall through */ } }
    throw e;
  }
}

function notFound(id, err) {
  const missing = /HTTP 404|Unknown/.test(String((err && err.message) || ''));
  if (!missing) return errorBlock(err);
  return h('div', { class: 'stack loose' },
    pageHead('Instrument not found', `Acumen has no analysis for “${id}”. It may not be in the tracked list, or it was added after the last data refresh.`),
    h('div', { class: 'row' }, h('a', { class: 'btn', ...linkProps('/screener') }, icon('filter'), 'Browse the screener')));
}

/* ---------- small derivations ---------- */
function livePrice(detail) {
  const base = detail.price || {};
  const q = ((D.peek('prices') || {}).quotes || {})[detail.id];
  if (q && F.isNum(q[0])) return { px: q[0], chg: F.isNum(q[1]) ? q[1] : base.chg_1d, ts: q[2] || base.ts };
  return { px: base.last, chg: base.chg_1d, ts: base.ts };
}

function yearRange(detail) {
  const c = detail.chart;
  if (!c || !c.high || c.high.length < 30) return null;
  const hi = c.high.slice(-252).filter(F.isNum), lo = c.low.slice(-252).filter(F.isNum);
  if (!hi.length || !lo.length) return null;
  return { lo: Math.min(...lo), hi: Math.max(...hi), days: Math.min(252, c.high.length) };
}

function initialHorizon(detail, wanted) {
  if (wanted && HORIZONS.includes(wanted)) return wanted;
  const plans = Object.values(detail.plans || {}).filter(Boolean);
  if (plans.length) return rankIdeas(plans)[0].horizon;
  const sc = (detail.scores && detail.scores.by_horizon) || {};
  return HORIZONS.slice().sort((a, b) => Math.abs((sc[b] || {}).score || 0) - Math.abs((sc[a] || {}).score || 0))[0] || 'medium';
}

function factsFor(detail, row) {
  const m = detail.market, f = (detail.fundamental && detail.fundamental.metrics) || {}, p = detail.profile || {}, r = detail.risk || {};
  const out = [];
  if (m === 'psx') {
    if (F.isNum(f.mcap ?? p.mcap)) out.push(['Market cap', F.money(f.mcap ?? p.mcap, 'PKR')]);
    if (F.isNum(f.pe)) out.push(['P/E', F.num(f.pe, 1)]);
    if (F.isNum(f.pb)) out.push(['P/B', F.num(f.pb, 2)]);
    if (F.isNum(f.dy)) out.push(['Dividend yield', F.pct(f.dy, 1, { sign: false })]);
  } else if (m === 'fx') {
    if (F.isNum(p.carry_pct)) out.push(['Carry if long', F.pct(p.carry_pct, 2) + ' a year']);
    if (p.group) out.push(['Group', p.group[0].toUpperCase() + p.group.slice(1)]);
  } else {
    if (F.isNum(p.mcap)) out.push(['Market cap', F.money(p.mcap, '$')]);
    if (F.isNum(p.rank)) out.push(['Rank', '#' + F.int(p.rank)]);
    if (F.isNum(p.ath_dd_pct)) out.push(['From all-time high', F.pct(p.ath_dd_pct, 0)]);
  }
  if (row) {
    if (F.isNum(row.atrp)) out.push(['Typical daily range', F.pct(row.atrp, 1, { sign: false }), 'ATR as a share of price']);
    if (F.isNum(row.rsi)) out.push(['RSI (14)', F.num(row.rsi, 0), row.rsi >= 70 ? 'stretched up' : row.rsi <= 30 ? 'stretched down' : null]);
  }
  if (r.liquidity && r.liquidity.tier) out.push(['Liquidity', 'Tier ' + r.liquidity.tier, F.isNum(r.liquidity.adv_value) ? F.compact(r.liquidity.adv_value) + ' a day' : null]);
  return out.slice(0, 8);
}

/* ---------- header ---------- */
function headerCard(detail, row, lp) {
  const m = detail.market;
  const age = F.daysOld(detail.asof);
  const stale = age != null && age > (STALE_DAYS[m] ?? 4);
  const ccy = m === 'psx' ? 'PKR' : m === 'crypto' ? 'USD' : '';
  const sector = m === 'psx' && detail.sector ? h('a', { class: 'chip outline', ...linkProps('/screener', { m: 'psx', sec: detail.sector }), title: 'More from this sector' }, detail.sector) : null;
  const perf = row ? [['1D', row.chg1d], ['1W', row.chg1w], ['1M', row.chg1m], ['3M', row.chg3m], ['6M', row.chg6m], ['1Y', row.chg1y], ['YTD', row.ytd]].filter(([, v]) => F.isNum(v)) : [];
  const yr = yearRange(detail);
  const pos = yr && yr.hi > yr.lo ? F.clip((lp.px - yr.lo) / (yr.hi - yr.lo), 0, 1) : null;
  const fromHigh = yr && F.isNum(lp.px) ? (lp.px / yr.hi - 1) * 100 : null;
  const facts = factsFor(detail, row);
  const quoteTime = lp.ts && String(lp.ts) !== String(detail.price && detail.price.ts) ? F.dateTime(lp.ts) : null;
  return h('section', { class: 'card pad inst-head' },
    h('div', { class: 'ih-left' },
      h('div', { class: 'ih-id' },
        h('div', { class: 'row wrap', style: { gap: '6px' } }, mkChip(m), chip(KIND[detail.kind] || detail.kind || 'Instrument', 'outline'), sector, !detail.tradable ? chip('Reference only', 'warn', { title: 'Most retail brokers do not offer this, so it is never marked actionable' }) : null),
        h('div', { class: 'ih-title' }, h('h1', { class: 'ih-sym mono' }, detail.sym), starButton(detail.id)),
        h('div', { class: 'ih-name' }, detail.name)),
      h('div', { class: 'ih-price' },
        h('div', { class: 'ih-px mono' }, F.px(lp.px, m), ccy ? h('span', { class: 'ih-ccy' }, ccy) : null),
        h('div', { class: 'row wrap', style: { gap: '8px' } },
          chgSpan(lp.chg, 2, { cls: 'ih-chg' }),
          chip(`Close ${F.date(detail.asof, { year: false })}`, stale ? 'warn' : 'outline', { icon: 'clock', title: stale ? `Data is ${age} days old, older than expected for this market` : 'Date of the last daily bar used in the analysis' }),
          stale ? chip(`${age} days old`, 'warn', { icon: 'alert' }) : null,
          quoteTime ? chip('Quote ' + quoteTime, 'outline') : null)),
      perf.length ? h('div', { class: 'perf', role: 'list', 'aria-label': 'Performance' }, ...perf.map(([k, v]) => h('div', { class: 'pf-cell', role: 'listitem' }, h('span', null, k), chgSpan(v, 1)))) : null),
    h('div', { class: 'ih-right' },
      yr ? h('div', { class: 'rng', role: 'img', 'aria-label': `52-week range ${F.pxr(yr.lo, lp.px, m)} to ${F.pxr(yr.hi, lp.px, m)}, price is ${F.num(Math.abs(fromHigh), 1)}% below the high` },
        h('div', { class: 'label' }, yr.days >= 240 ? '52-week range' : `${yr.days}-session range`),
        h('div', { class: 'rng-track' }, h('div', { class: 'rng-dot', style: { left: pos * 100 + '%' } })),
        h('div', { class: 'rng-ends' }, h('span', null, F.pxr(yr.lo, lp.px, m)), h('span', { class: 'muted' }, fromHigh != null ? (fromHigh >= -0.05 ? 'at the high' : `${F.num(Math.abs(fromHigh), 1)}% below the high`) : ''), h('span', null, F.pxr(yr.hi, lp.px, m)))) : null,
      facts.length ? h('div', { class: 'ih-facts' }, ...facts.map(([l, v, s]) => stat(l, v, s, { small: true }))) : null));
}

/* ---------- user's open positions ---------- */
function positionsCard(detail) {
  const open = J.list().filter(t => t.idea === detail.id && t.status === 'open');
  if (!open.length) return null;
  const acct = Z.acctCcy();
  return h('section', { class: 'card pad mypos' },
    h('div', { class: 'label' }, open.length === 1 ? 'Your open position' : `Your open positions (${open.length})`),
    ...open.map(t => {
      const ev = J.evaluate(t);
      return h('div', { class: 'mp-row' },
        h('div', { class: 'row wrap', style: { gap: '8px' } }, t.dir === 'short' ? chip('Short', 'down') : chip('Long', 'up'), h('b', { class: 'mono' }, F.num(t.qty, t.market === 'crypto' ? 4 : 0)), h('span', { class: 'muted' }, 'at'), h('b', { class: 'mono' }, F.px(t.entry, t.market)),
          h('span', { class: 'muted' }, 'since ' + F.date(t.opened, { year: true }))),
        h('div', { class: 'row wrap', style: { gap: '10px' } },
          F.isNum(ev.px) ? h('span', null, 'Now ', h('b', { class: 'mono' }, F.px(ev.px, t.market))) : null,
          F.isNum(ev.pl) ? h('span', { class: F.dirClass(ev.pl) }, h('b', null, `${ev.pl >= 0 ? '+' : ''}${F.money(ev.pl, acct, { compactFrom: 1e7, d: 0 })}`)) : null,
          F.isNum(ev.pct) ? chgSpan(ev.pct, 1) : null,
          F.isNum(ev.r) ? h('span', { class: 'muted' }, F.r(ev.r, 1)) : null,
          ev.flags.includes('stop') ? chip('Stop level reached', 'down', { icon: 'alert' }) : null,
          ev.flags.includes('t2') ? chip('Target 2 reached', 'up', { icon: 'target' }) : ev.flags.includes('t1') ? chip('Target 1 reached', 'up', { icon: 'target' }) : null));
    }));
}

/* ---------- horizon tiles ---------- */
function noSetupShort(sc) {
  if (!sc || !F.isNum(sc.score)) return 'Not enough history';
  return sc.dir == null ? 'Mixed signals, no plan' : 'Too weak for a plan';
}

function horizonTiles(detail, getHz, setHz) {
  const wrap = h('div', { class: 'hz-tiles', role: 'tablist', 'aria-label': 'Time horizon' });
  const draw = () => {
    clear(wrap);
    for (const hz of HORIZONS) {
      const sc = (detail.scores && detail.scores.by_horizon && detail.scores.by_horizon[hz]) || {};
      const plan = detail.plans && detail.plans[hz];
      const sel = hz === getHz();
      const t1 = plan && (plan.targets || [])[0];
      let foot;
      if (plan && plan.actionable) foot = t1 ? `Target 1 ${F.pct(t1.pct, 1)} in ${F.dayRange(t1.eta_days)}` : 'Plan ready';
      else if (plan) foot = (plan.action === 'AVOID' ? 'Avoid: ' : 'Reference only: ') + lc(BLOCKER_TEXT[(plan.blockers || [])[0]] || 'does not pass the checks');
      else foot = noSetupShort(sc);
      wrap.append(h('button', { type: 'button', role: 'tab', class: ['hz-tile', sel && 'sel', !plan && 'none'], 'aria-selected': String(sel), onclick: () => setHz(hz) },
        h('div', { class: 'hz-top' }, h('span', { class: 'hz-name' }, hzGlyph(hz), HZ_LABEL[hz]), h('span', { class: 'hz-span' }, HZ_SPAN[hz])),
        h('div', { class: 'hz-main' }, plan ? actionChip(plan.action, isResearch(plan)) : chip('No setup', 'outline'), plan ? gradeBadge(plan.grade, null, isResearch(plan)) : null),
        h('div', { class: 'hz-meter' }, convMeter(plan ? plan.conviction : sc.conviction, { calibrated: plan ? plan.calibrated : false, compact: false })),
        h('div', { class: 'hz-foot' }, foot)));
    }
  };
  draw();
  wrap.redraw = draw;
  return wrap;
}

/* ---------- selected-horizon plan ---------- */
function noSetupCard(detail, hz) {
  const sc = (detail.scores && detail.scores.by_horizon && detail.scores.by_horizon[hz]) || {};
  const sg = (D.meta && D.meta.config && D.meta.config.signals) || {};
  const thr = sg.enter_score ?? 0.3;
  let why;
  if (!F.isNum(sc.score)) why = `${detail.sym} does not have enough price history to be scored at this horizon yet.`;
  else if (sc.dir == null) why = `The composite score is ${F.num(sc.score, 2, { sign: true })}, inside the ±${F.num(thr, 2)} band where Acumen stays out. The evidence is mixed rather than one-sided, so there is no entry, stop or target to publish.`;
  else if (sc.grade === 'D' || (F.isNum(sc.conviction) && sc.conviction < 45)) why = `The signal leans ${sc.dir} (score ${F.num(sc.score, 2, { sign: true })}) but its conviction of ${F.num(sc.conviction, 0)} grades as D. Acumen only publishes a plan from grade C (45) upward.`;
  else why = 'The signal leans one way, but a workable stop and target ladder could not be built from the current price structure.';
  const other = HORIZONS.filter(x => x !== hz && detail.plans && detail.plans[x]);
  return h('section', { class: 'card pad plan-card stack' },
    h('div', { class: 'plan-head' }, h('div', { class: 'label' }, `${HZ_LABEL[hz]} horizon`), h('span', { class: 'muted' }, HZ_SPAN[hz])),
    h('div', { class: 'row wrap', style: { gap: '8px' } }, chip('No trade plan', 'outline', { icon: 'info' }), sc.grade ? gradeBadge(sc.grade) : null),
    h('p', { class: 'ink-2' }, why),
    h('p', { class: 'muted' }, 'Staying out is a legitimate result. The Signals tab shows what is holding the score back.'),
    other.length ? h('div', { class: 'row wrap', style: { gap: '8px' } }, h('span', { class: 'muted' }, 'Plans exist for:'), ...other.map(x => chip(HZ_LABEL[x], 'accent'))) : null);
}

function planCard(detail, hz) {
  const plan = detail.plans && detail.plans[hz];
  if (!plan) return noSetupCard(detail, hz);
  return h('section', { class: 'card pad plan-card' },
    h('div', { class: 'plan-head' }, h('div', { class: 'label' }, `${HZ_LABEL[hz]} plan`), h('span', { class: 'muted' }, HZ_SPAN[hz])),
    h('div', { class: 'row wrap', style: { gap: '8px', margin: '6px 0 14px' } }, actionChip(plan.action, isResearch(plan)), plan.setup ? chip(plan.setup, 'outline') : null, gradeBadge(plan.grade, null, isResearch(plan)), h('div', { style: { flex: '1 1 120px', maxWidth: '220px' } }, convMeter(plan.conviction, { calibrated: plan.calibrated }))),
    ticket(plan, { showHead: false }));
}

/* ---------- chart ---------- */
function chartCard(detail, getPlan, cleanups) {
  const m = detail.market;
  const saved = S.get('chartPrefs') || {};
  const prefs = { range: '6M', tf: 'D', overlays: ['sma50', 'sma200'], pane: 'volume', plan: true, levels: true, markers: true, ...saved };
  if (!Array.isArray(prefs.overlays)) prefs.overlays = ['sma50', 'sma200'];
  const save = () => S.set('chartPrefs', prefs, { silent: true });
  const chartH = () => (innerWidth >= 1100 ? 520 : innerWidth >= 760 ? 480 : 420);
  const host = h('div', { class: 'chart-host' });
  host.style.setProperty('--chart-h', chartH() + 'px');
  let pc = null, dead = false;
  const planFor = () => { const p = getPlan(); return p && p.action !== 'AVOID' ? p : null; };

  const hasVol = (detail.chart.volume || []).some(v => F.isNum(v) && v > 0);
  const paneList = PANES.filter(p => hasVol || !['volume', 'obv', 'cmf', 'mfi'].includes(p.id));
  if (!paneList.some(p => p.id === prefs.pane)) prefs.pane = hasVol ? 'volume' : 'rsi';
  const rangeSeg = seg(['1M', '3M', '6M', '1Y', 'All'], prefs.range, v => { prefs.range = v; save(); if (pc) pc.setRange(v); }, { sm: true, label: 'Date range' });
  const adv = h('div', { class: ['chart-adv', prefs.tf === 'W' && 'off'] });
  const tfSeg = seg([{ value: 'D', label: 'Daily' }, { value: 'W', label: 'Weekly' }], prefs.tf, v => { prefs.tf = v; save(); adv.classList.toggle('off', v === 'W'); paneSel.disabled = v === 'W'; if (pc) pc.setTimeframe(v); setCaption(); }, { sm: true, label: 'Bar size' });
  const paneSel = select(paneList.map(p => ({ value: p.id, label: p.id === 'none' ? 'No indicator panel' : p.label })), prefs.pane, v => { prefs.pane = v; save(); if (pc) pc.setPane(v); }, { 'aria-label': 'Indicator panel under the chart', class: 'select sm' });
  paneSel.disabled = prefs.tf === 'W';
  const swatches = [];
  for (const o of OVERLAYS) {
    const pill = pillToggle(o.label, prefs.overlays.includes(o.id), on => {
      prefs.overlays = on ? Array.from(new Set([...prefs.overlays, o.id])) : prefs.overlays.filter(x => x !== o.id); save();
      if (pc) pc.setOverlay(o.id, on);
    }, overlaySwatch(o));
    swatches.push([o, pill]);
    adv.append(pill);
  }
  adv.append(h('span', { class: 'adv-sep', 'aria-hidden': 'true' }),
    pillToggle('Trade plan', prefs.plan, on => { prefs.plan = on; save(); if (pc) pc.toggle('plan', on); setCaption(); }),
    pillToggle('Support and resistance', prefs.levels, on => { prefs.levels = on; save(); if (pc) pc.toggle('levels', on); setCaption(); }),
    pillToggle('Patterns', prefs.markers, on => { prefs.markers = on; save(); if (pc) pc.toggle('markers', on); setCaption(); }));
  adv.hidden = innerWidth < 760;
  const advBtn = h('button', { class: 'btn sm', type: 'button', 'aria-expanded': String(!adv.hidden), onclick: () => { adv.hidden = !adv.hidden; advBtn.setAttribute('aria-expanded', String(!adv.hidden)); } }, icon('sliders'), 'Indicators');

  const caption = h('p', { class: 'chart-note' });
  const setCaption = () => {
    const p = planFor();
    const d = prefs.tf === 'D';
    const parts = [];
    if (p && prefs.plan && d) parts.push(`Lines: the ${HZ_LABEL[p.horizon]} plan’s entry (long dashes), stop and targets. The scale stretches to fit them.`);
    if (prefs.levels && d) parts.push('Fine dashes: support (S) and resistance (R).');
    if (prefs.markers && d) parts.push('Arrows: candle patterns, up for bullish and down for bearish.');
    caption.textContent = parts.join(' ');
    caption.hidden = !parts.length;
  };

  // price table for people who prefer numbers (and screen readers)
  const tblHost = h('div', { class: 'tbl-wrap' });
  let tblBuilt = false;
  const details = h('details', { class: 'price-table' }, h('summary', null, 'Show the price table'), tblHost);
  details.addEventListener('toggle', () => {
    if (!details.open || tblBuilt) return;
    tblBuilt = true;
    const c = detail.chart, n = c.dates.length;
    const rows = [];
    for (let i = n - 1; i >= Math.max(0, n - 30); i--) {
      const prev = i > 0 ? c.close[i - 1] : null;
      rows.push(h('tr', null, h('td', null, F.date(c.dates[i], { year: true })), h('td', { class: 'r mono' }, F.px(c.open[i], m)), h('td', { class: 'r mono' }, F.px(c.high[i], m)), h('td', { class: 'r mono' }, F.px(c.low[i], m)), h('td', { class: 'r mono' }, F.px(c.close[i], m)),
        h('td', { class: 'r' }, prev ? chgSpan((c.close[i] / prev - 1) * 100, 2, { noArrow: true }) : F.NA), h('td', { class: 'r' }, F.isNum(c.volume[i]) ? F.compact(c.volume[i]) : F.NA)));
    }
    tblHost.append(h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', null, 'Date'), h('th', { class: 'r' }, 'Open'), h('th', { class: 'r' }, 'High'), h('th', { class: 'r' }, 'Low'), h('th', { class: 'r' }, 'Close'), h('th', { class: 'r' }, 'Change'), h('th', { class: 'r' }, 'Volume'))), h('tbody', null, ...rows)));
  });

  const card = h('section', { class: 'card chart-card' },
    h('div', { class: 'chart-bar' }, h('div', { class: 'chart-tools' }, rangeSeg, tfSeg, h('div', { class: 'grow' }), paneSel, advBtn), adv),
    host, caption, details);

  requestAnimationFrame(() => {
    if (dead) return;
    pc = createPriceChart(host, detail, { range: prefs.range, tf: prefs.tf, overlays: new Set(prefs.overlays), pane: prefs.pane, plan: prefs.plan, levels: prefs.levels, markers: prefs.markers, planIdea: planFor() });
    pc.height(chartH());
  });
  setCaption();

  const onResize = debounce(() => { host.style.setProperty('--chart-h', chartH() + 'px'); if (pc) pc.height(chartH()); }, 150);
  window.addEventListener('resize', onResize);
  const onAppearance = () => requestAnimationFrame(() => { const c = palette(); for (const [o, pill] of swatches) { const sw = pill.querySelector('.sw'); if (sw) sw.style.background = overlaySwatch(o, c); } });
  window.addEventListener('acumen:appearance', onAppearance);
  cleanups.push(() => { dead = true; window.removeEventListener('resize', onResize); window.removeEventListener('acumen:appearance', onAppearance); if (pc) pc.destroy(); });
  return { el: card, planChanged() { if (pc) pc.setPlan(planFor()); setCaption(); } };
}

/* ---------- screen ---------- */
export default async function render(route) {
  const id = route.segs[1];
  if (!id) return { el: notFound('', new Error('Unknown instrument')) };
  let detail;
  try { detail = await loadDetail(id); } catch (e) { return { el: notFound(id, e) }; }
  const [idx] = await Promise.all([D.index().catch(() => null), D.get('prices').catch(() => null)]);
  const row = idx ? idx.get(detail.id) : null;
  const q = route.query || {};
  const state = { hz: initialHorizon(detail, q.h) };
  const cleanups = [];
  const root = h('div', { class: 'stack inst' });

  for (const n of detail.notes || []) root.append(banner('info', n));
  root.append(headerCard(detail, row, livePrice(detail)));
  if (!detail.tradable) root.append(banner('info', h('b', null, 'For reference. '), 'Most retail brokers do not offer this instrument, so Acumen never marks it actionable. Use it as context for the instruments you can trade.'));
  const pos = positionsCard(detail);
  if (pos) root.append(pos);

  const planHost = h('div');
  const tabHost = h('div', { class: 'tab-body' });
  const tiles = horizonTiles(detail, () => state.hz, setHz);
  root.append(tiles);

  const chart = chartCard(detail, () => detail.plans && detail.plans[state.hz], cleanups);

  const tabList = visibleTabs(detail);
  let curTab = tabList.some(t => t.id === q.t) ? q.t : 'signals';
  let tabHandle = null;
  function drawTab() {
    if (tabHandle && tabHandle.destroy) { try { tabHandle.destroy(); } catch { /* ignore */ } }
    tabHandle = null;
    const t = tabList.find(x => x.id === curTab) || tabList[0];
    let out;
    try { out = t.render({ detail, row, hz: state.hz, plan: detail.plans && detail.plans[state.hz] }); } catch (e) { out = errorBlock(e); }
    const el = out && out.nodeType ? out : out.el;
    if (out && !out.nodeType) tabHandle = out;
    tabHost.replaceChildren(el);
  }
  const tabsEl = tabs(tabList.map(t => ({ value: t.id, label: tabLabel(t, detail) })), curTab, v => { curTab = v; replaceQuery({ t: v === 'signals' ? null : v }); drawTab(); });
  const tabsCard = h('section', { class: 'card tabs-card' }, h('div', { class: 'card-tabs' }, tabsEl), h('div', { class: 'card-b' }, tabHost));

  function drawPlan() { planHost.replaceChildren(planCard(detail, state.hz)); }
  function setHz(hz) {
    if (hz === state.hz) return;
    state.hz = hz;
    replaceQuery({ h: hz });
    tiles.redraw();
    drawPlan();
    grid.classList.toggle('no-plan', !(detail.plans && detail.plans[hz]));
    chart.planChanged();
    drawTab();
  }
  drawPlan();
  drawTab();

  const grid = h('div', { class: ['inst-grid', !(detail.plans && detail.plans[state.hz]) && 'no-plan'] },
    h('div', { class: 'ig-chart' }, chart.el),
    h('div', { class: 'ig-plan' }, planHost),
    h('div', { class: 'ig-tabs' }, tabsCard));
  root.append(grid);
  root.append(h('p', { class: 'fineprint' }, 'Model estimates for research, not investment advice. Past and simulated results do not guarantee future results. Charts by ',
    h('a', { href: 'https://www.tradingview.com/', target: '_blank', rel: 'noopener noreferrer' }, 'TradingView Lightweight Charts™'), '.'));

  return { el: root, destroy() { for (const fn of cleanups) { try { fn(); } catch { /* ignore */ } } if (tabHandle && tabHandle.destroy) { try { tabHandle.destroy(); } catch { /* ignore */ } } } };
}
