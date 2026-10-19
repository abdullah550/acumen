import { h, clear } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { linkProps, go } from '../lib/router.js';
import { HZ_LABEL, HZ_SPAN, EXIT_LABEL, dedupeSeries } from '../lib/util.js';
import { chip, stat, explain, banner, empty, gradeBadge, convMeter, chgSpan } from '../ui/kit.js';
import { gauge, rangeBar, miniBars, stackBar } from '../ui/charts.js';
import { lineChart } from '../ui/lwc.js';

const cfgv = () => (D.meta && D.meta.config) || {};
const pts = list => h('ul', { class: 'pts' }, ...(list || []).map(t => h('li', null, t)));
const sgn = v => F.num(v, 2, { sign: true });
const dirTone = d => (d === 'long' ? 'up' : d === 'short' ? 'down' : 'outline');
const section = (title, ...kids) => h('section', { class: 'tab-sec stack' }, title ? h('div', { class: 'label' }, title) : null, ...kids);
const note = (...kids) => h('p', { class: 'fineprint' }, ...kids);

/* A small horizontal meter for 0..1 values. */
function miniMeter(frac, label) {
  const v = F.clip(F.isNum(frac) ? frac : 0, 0, 1);
  return h('div', { class: 'mm', role: 'img', 'aria-label': label || `${Math.round(v * 100)}%` }, h('div', { class: 'mm-f', style: { width: v * 100 + '%' } }));
}

/* ============================== Signals ============================== */
const FACTOR_NAME = { trend: 'Trend', momentum: 'Momentum', reversion: 'Mean reversion', structure: 'Price structure', volume: 'Volume and money flow', pattern: 'Candle patterns', rs: 'Relative strength' };
const FACTOR_ORDER = ['trend', 'momentum', 'reversion', 'structure', 'volume', 'pattern', 'rs'];

function factorRows(sc) {
  const fs = sc.factors || {};
  const used = FACTOR_ORDER.filter(k => fs[k] && fs[k].weight > 0 && F.isNum(fs[k].value));
  const unused = FACTOR_ORDER.filter(k => fs[k] && !used.includes(k));
  if (!used.length) return h('p', { class: 'muted' }, 'Not enough history to score this horizon.');
  const maxC = Math.max(0.05, ...used.map(k => Math.abs(fs[k].contribution ?? fs[k].value * fs[k].weight)));
  const list = h('div', { class: 'frows signed' });
  for (const k of used) {
    const f = fs[k];
    const c = f.contribution ?? f.value * f.weight;
    const w = Math.min(50, (Math.abs(c) / maxC) * 50);
    const pos = c >= 0;
    list.append(h('div', { class: 'frow' },
      h('div', { class: 'fr-top' },
        h('span', { class: 'fr-name' }, FACTOR_NAME[k] || k),
        h('span', { class: 'fr-w' }, F.num(f.weight * 100, 0) + '% weight'),
        h('span', { class: ['fr-v', pos ? 'up' : 'down'] }, sgn(c))),
      h('div', { class: 'hb-track' }, h('div', { class: 'hb-zero' }), h('div', { class: ['hb-fill', pos ? 'pos' : 'neg'], style: { width: w + '%', left: pos ? '50%' : 50 - w + '%' } })),
      h('div', { class: 'fr-d' }, `Reading ${sgn(f.value)}${f.detail ? ' · ' + f.detail : ''}`)));
  }
  const wrap = h('div', { class: 'stack' }, list);
  if (unused.length) wrap.append(h('p', { class: 'muted fr-unused' }, 'Not used at this horizon: ' + unused.map(k => (FACTOR_NAME[k] || k).toLowerCase()).join(', ') + '.'));
  return wrap;
}

function techSummary(detail) {
  const t = detail.technical;
  if (!t) return null;
  const blocks = [['trend', 'Trend'], ['momentum', 'Momentum'], ['volatility', 'Volatility'], ['volume', 'Volume and money flow']]
    .filter(([k]) => t[k] && (t[k].points || []).length && String(t[k].label || '').toLowerCase() !== 'unknown');
  return section('Technical read',
    t.summary ? h('p', { class: 'ink-2' }, t.summary) : null,
    h('div', { class: 'grid auto tech-grid' }, ...blocks.map(([k, name]) => {
      const b = t[k];
      const tone = k === 'volatility' ? 'outline' : b.score >= 0.25 ? 'up' : b.score <= -0.25 ? 'down' : 'outline';
      return h('div', { class: 'tech-card' },
        h('div', { class: 'tc-top' }, h('span', { class: 'label' }, name), chip(b.label || F.NA, tone)),
        pts(b.points));
    })));
}

export function signalsTab({ detail, hz }) {
  const sc = (detail.scores && detail.scores.by_horizon && detail.scores.by_horizon[hz]) || {};
  const plan = detail.plans && detail.plans[hz];
  const thr = (cfgv().signals && cfgv().signals.enter_score) ?? 0.3;
  const tiltMax = (cfgv().signals && cfgv().signals.max_tilt_points) ?? 12;
  const root = h('div', { class: 'stack loose' });

  const dirChip = sc.dir === 'long' ? chip('Leans long', 'up', { icon: 'up' }) : sc.dir === 'short' ? chip('Leans short', 'down', { icon: 'down' }) : chip('No clear direction', 'outline');
  const explainText = !F.isNum(sc.score) ? 'There is not enough price history to score this horizon yet.'
    : sc.dir == null ? `The score sits inside the ±${F.num(thr, 2)} band where Acumen stays out: the evidence is mixed rather than one-sided.`
      : `The score is beyond ±${F.num(thr, 2)}, so the evidence leans ${sc.dir === 'long' ? 'bullish' : 'bearish'}. Conviction scales how far past that band it is (a score of ±0.90 reads as 100).`;
  root.append(h('div', { class: 'grid c2 top sig-top' },
    h('div', { class: 'stack' },
      h('div', { class: 'label' }, `${HZ_LABEL[hz]} composite score`, explain('Seven factors, each scored from −1 (bearish) to +1 (bullish), are weighted for this horizon and added up. Swing leans on momentum and short-term structure; Investment leans on the long trend and relative strength.')),
      h('div', { class: 'row wrap', style: { gap: '12px' } }, h('span', { class: ['big-score', 'mono', F.dirClass(sc.score)] }, F.isNum(sc.score) ? sgn(sc.score) : F.NA), dirChip, sc.grade ? gradeBadge(sc.grade) : null),
      gauge(sc.score),
      h('p', { class: 'ink-2' }, explainText),
      F.isNum(sc.conviction) ? h('div', { class: 'row', style: { maxWidth: '320px' } }, h('span', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, 'Conviction'), h('div', { class: 'grow' }, convMeter(sc.conviction, { calibrated: !!sc.calibrated }))) : null),
    h('div', { class: 'stack' },
      h('div', { class: 'label' }, 'What drives the score', explain('Each bar is that factor’s share of the composite: its reading times its weight at this horizon. Green bars push toward long, red toward short. The line under each bar says what was measured.')),
      factorRows(sc))));

  if (plan && F.isNum(plan.tilt) && plan.tilt !== 0) {
    const fund = plan.factors && plan.factors.fundamental, carry = plan.factors && plan.factors.carry;
    root.append(banner('info', h('b', null, 'Outside the backtest: '),
      `${fund != null ? 'fundamentals' : carry != null ? 'interest-rate carry' : 'other data'} moved conviction by ${F.num(plan.tilt, 1, { sign: true })} points${fund != null ? ` (fundamental score ${sgn(fund)})` : carry != null ? ` (carry score ${sgn(carry)})` : ''}. These inputs cannot be backtested, so their effect is capped at ${tiltMax} points either way.`));
  }
  const tech = techSummary(detail);
  if (tech) root.append(tech);
  return root;
}

/* ============================== Technicals ============================== */
function signalChip(sig) {
  return sig === 'bullish' ? chip('Bullish', 'up', { icon: 'up' }) : sig === 'bearish' ? chip('Bearish', 'down', { icon: 'down' }) : chip('Neutral', 'outline');
}
const readFmt = v => (typeof v !== 'number' ? String(v ?? F.NA) : F.num(v, Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 100 ? 1 : Math.abs(v) >= 0.01 || v === 0 ? 2 : 4));
function readingText(i) {
  const n = String(i.name || '');
  if (typeof i.value === 'number') {
    if (/^supertrend/i.test(n)) return i.value > 0 ? 'Price above the line' : 'Price below the line';
    if (/^ichimoku/i.test(n)) return i.value > 0 ? 'Above the cloud' : i.value < 0 ? 'Below the cloud' : 'Inside the cloud';
  }
  return readFmt(i.value);
}

function levelsBlock(detail) {
  const lv = ((detail.chart && detail.chart.levels) || []).slice().sort((a, b) => b.px - a.px);
  if (!lv.length) return null;
  const last = detail.price && detail.price.last;
  const m = detail.market;
  const rows = [];
  let placed = false;
  const nowRow = () => h('tr', { class: 'now-row' }, h('td', { colspan: 5 }, h('span', { class: 'now-tag' }, 'Now'), h('b', { class: 'mono' }, F.px(last, m))));
  for (const l of lv) {
    if (!placed && F.isNum(last) && l.px < last) { rows.push(nowRow()); placed = true; }
    const dist = F.isNum(last) && last ? (l.px / last - 1) * 100 : null;
    rows.push(h('tr', null,
      h('td', null, l.kind === 'support' ? chip('Support', 'up') : chip('Resistance', 'down')),
      h('td', { class: 'r mono' }, F.px(l.px, m)),
      h('td', { class: 'r' }, F.pct(dist, 1)),
      h('td', { class: 'r' }, l.touches != null ? l.touches + (l.touches === 1 ? ' touch' : ' touches') : F.NA),
      h('td', { class: 'r' }, h('div', { class: 'mm-cell' }, miniMeter(F.clip(l.strength / 0.8, 0, 1), `Strength ${F.num(l.strength, 2)}`)))));
  }
  if (!placed && F.isNum(last)) rows.push(nowRow());
  return section('Support and resistance',
    h('p', { class: 'muted' }, 'Prices where the market has repeatedly turned. They are used to place stops just beyond a level and to flag targets that run into one.'),
    h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', null, 'Type'), h('th', { class: 'r' }, 'Price'), h('th', { class: 'r' }, 'From now'), h('th', { class: 'r' }, 'Tested'), h('th', { class: 'r' }, 'Strength'))),
      h('tbody', null, ...rows)))));
}

export function technicalsTab({ detail }) {
  const t = detail.technical || {};
  const inds = t.indicators || [];
  const root = h('div', { class: 'stack loose' });
  if (inds.length) {
    const c = { bullish: 0, bearish: 0, neutral: 0 };
    for (const i of inds) c[i.signal === 'bullish' ? 'bullish' : i.signal === 'bearish' ? 'bearish' : 'neutral']++;
    root.append(section('Indicator scoreboard',
      stackBar([{ label: 'Bullish', value: c.bullish, color: 'var(--up)' }, { label: 'Neutral', value: c.neutral, color: 'var(--line-strong)' }, { label: 'Bearish', value: c.bearish, color: 'var(--down)' }]),
      h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, h('th', null, 'Indicator'), h('th', { class: 'r' }, 'Reading'), h('th', null, 'Signal'))),
        h('tbody', null, ...inds.map(i => h('tr', null, h('td', null, i.name), h('td', { class: 'r mono' }, readingText(i)), h('td', null, signalChip(i.signal))))))))),
      note('A count of indicators is context, not a vote that decides anything: they overlap heavily. The composite score on the Signals tab weighs related measures together.'));
  }
  const lv = levelsBlock(detail);
  if (lv) root.append(lv);
  const pats = t.patterns || [];
  if (pats.length) {
    root.append(section('Recent candle patterns',
      h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, h('th', null, 'Date'), h('th', null, 'Pattern'), h('th', null, 'Bias'))),
        h('tbody', null, ...pats.map(p => h('tr', null, h('td', null, F.date(p.date, { year: false })), h('td', null, p.name), h('td', null, p.dir === 'long' ? chip('Bullish', 'up', { icon: 'up' }) : p.dir === 'short' ? chip('Bearish', 'down', { icon: 'down' }) : chip('Neutral', 'outline')))))))),
      note('Candle patterns are a minor input. They carry little weight on their own and are not used at every horizon.')));
  }
  if (!root.childNodes.length) return empty('No technical readings yet', 'This instrument does not have enough price history.', 'pulse');
  return root;
}

/* ============================== Fundamentals ============================== */
const SECTOR_WORDS = v => (F.isNum(v) ? `${F.num(Math.abs(v) * 100, 0)}% ${v < 0 ? 'below' : 'above'} sector median` : null);

function yearlyDividends(div) {
  const m = new Map();
  for (const d of div || []) { const y = String(d.date).slice(0, 4); m.set(y, (m.get(y) || 0) + (d.amount || 0)); }
  return Array.from(m.entries()).sort((a, b) => (a[0] < b[0] ? -1 : 1)).slice(-6).map(([label, value]) => ({ label, value: Math.round(value * 100) / 100 }));
}

function valuationBlock(detail) {
  const v = detail.valuation;
  if (!v) return null;
  const price = detail.price && detail.price.last;
  const verdict = String(v.verdict || '').toLowerCase();
  const tone = verdict.includes('under') ? 'up' : verdict.includes('over') ? 'down' : 'outline';
  const label = verdict.includes('under') ? 'Undervalued' : verdict.includes('over') ? 'Overvalued' : 'Fairly valued';
  const ccy = detail.currency || 'PKR';
  const big = Math.abs(v.upside_pct) > 100;
  const models = (v.models || []).filter(m => F.isNum(m.value));
  return section('Valuation',
    h('div', { class: 'row wrap', style: { gap: '10px' } }, chip(label, tone), v.confidence ? chip(v.confidence + ' confidence', 'outline') : null),
    h('div', { class: 'stats' },
      stat('Blended fair value', F.money(v.fair_value, ccy, { compactFrom: 1e9, d: 0 }), `range ${F.num(v.low, 0)} to ${F.num(v.high, 0)}`, { small: true }),
      stat('Price now', F.money(price, ccy, { compactFrom: 1e9, d: price < 100 ? 2 : 0 }), null, { small: true }),
      stat('Gap to fair value', F.pct(v.upside_pct, 0), 'fair value vs price', { small: true, cls: F.dirClass(v.upside_pct) }),
      stat('Margin of safety', F.pct(v.margin_of_safety_pct, 0), 'price below fair value', { small: true, info: 'How far the price sits under the blended fair value, as a share of fair value. Higher means a bigger cushion if the estimate is a bit too generous.' })),
    models.length ? rangeBar(models, { low: v.low, high: v.high, fair: v.fair_value, price, fmt: x => F.num(x, x < 100 ? 2 : 0) }) : null,
    models.length ? h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', null, 'Model'), h('th', { class: 'r' }, 'Value'), h('th', { class: 'r' }, 'Weight'), h('th', null, 'How it is built')),),
      h('tbody', null, ...models.map(m => h('tr', null, h('td', null, m.name), h('td', { class: 'r mono' }, F.num(m.value, m.value < 100 ? 2 : 0)), h('td', { class: 'r' }, F.num((m.weight || 0) * 100, 0) + '%'), h('td', { class: 'wrap muted' }, m.note || ''))))))) : null,
    (v.notes || []).length ? h('ul', { class: 'pts' }, ...v.notes.map(n => h('li', null, n))) : null,
    big ? banner('warn', h('b', null, 'Treat a gap this large with suspicion. '), 'It usually means an input is off: stale earnings, a one-off profit, or a sector where P/E and book value are not meaningful. Check the latest financial statements before relying on it.') : null,
    note('Fair value comes from simple textbook models and a cost-of-equity assumption. It is a rough guide to relative cheapness, never a price target, and it is not backtested.'));
}

function psxFundamentals(detail) {
  const f = detail.fundamental;
  const m = f.metrics || {}, rel = f.sector_rel || {}, sc = f.scores || {};
  const root = h('div', { class: 'stack loose' });
  root.append(banner('info', h('b', null, 'How fundamentals are used. '), `They tilt conviction by a few points at most (${((cfgv().signals || {}).max_tilt_points) ?? 12} either way, more at longer horizons) and are never backtested. Price action decides whether there is a trade; fundamentals nudge how much to trust it.`));
  const tiles = [
    ['P/E', F.isNum(m.pe) ? F.num(m.pe, 1) : F.NA, SECTOR_WORDS(rel.pe_vs_sector), 'Price divided by yearly earnings per share. Lower can mean cheaper, or a business the market expects to shrink.'],
    ['P/B', F.isNum(m.pb) ? F.num(m.pb, 2) : F.NA, SECTOR_WORDS(rel.pb_vs_sector), 'Price divided by book value (net assets) per share. Below 1 means the market values the company at less than its accounting net worth.'],
    ['Dividend yield', F.isNum(m.dy) ? F.pct(m.dy, 1, { sign: false }) : F.NA, SECTOR_WORDS(rel.dy_vs_sector), 'Yearly dividend as a share of the price.'],
    ['Return on equity', F.isNum(m.roe) ? F.pct(m.roe, 1, { sign: false }) : F.NA, SECTOR_WORDS(rel.roe_vs_sector), 'Profit earned per rupee of shareholder capital.'],
    ['EPS', F.isNum(m.eps) ? F.num(m.eps, 2) : F.NA, F.isNum(m.eps_growth_pct) ? F.pct(m.eps_growth_pct, 1) + ' vs last year' : null, 'Earnings per share, latest year.'],
    ['Book value / share', F.isNum(m.bvps) ? F.num(m.bvps, 1) : F.NA, null, null],
    ['Payout ratio', F.isNum(m.payout_pct) ? F.pct(m.payout_pct, 0, { sign: false }) : F.NA, null, 'Share of earnings paid out as dividends.'],
    ['Net margin', F.isNum(m.net_margin_pct) ? F.pct(m.net_margin_pct, 1, { sign: false }) : F.NA, null, 'Profit as a share of revenue.'],
    ['Market cap', F.isNum(m.mcap) ? F.money(m.mcap, detail.currency || 'PKR') : F.NA, null, null],
  ].filter(([, v]) => v !== F.NA);
  root.append(section('Key figures', h('div', { class: 'stats fund-tiles' }, ...tiles.map(([l, v, s, i]) => stat(l, v, s, { small: true, info: i })))));

  const scoreItems = [['value', 'Value'], ['quality', 'Quality'], ['income', 'Income'], ['growth', 'Growth'], ['safety', 'Safety'], ['composite', 'Overall']].filter(([k]) => F.isNum(sc[k]));
  if (scoreItems.length) {
    const max = 1;
    root.append(section('Fundamental scores, −1 to +1',
      h('div', { class: 'hbars signed' }, ...scoreItems.map(([k, label]) => {
        const v = sc[k]; const w = Math.min(50, (Math.abs(v) / max) * 50); const pos = v >= 0;
        return h('div', { class: ['hb-row', k === 'composite' && 'strong'] },
          h('div', { class: 'hb-l', style: { width: '76px' } }, label),
          h('div', { class: 'hb-track' }, h('div', { class: 'hb-zero' }), h('div', { class: ['hb-fill', pos ? 'pos' : 'neg'], style: { width: w + '%', left: pos ? '50%' : 50 - w + '%' } })),
          h('div', { class: ['hb-v', pos ? 'up' : 'down'] }, sgn(v)));
      })),
      F.isNum(f.coverage) && f.coverage < 1 ? note(`Based on ${F.num(f.coverage * 100, 0)}% of the figures Acumen looks for; the rest were not available.`) : null));
  }
  if ((f.flags || []).length) root.append(section('Notable', h('ul', { class: 'bul info' }, ...f.flags.map(t => h('li', null, icon('info'), h('span', null, t))))));

  const hist = f.history || {};
  const divs = yearlyDividends(hist.dividends);
  const eps = (hist.eps || []).map(e => ({ label: String(e.period), value: e.value }));
  if (divs.length || eps.length) {
    root.append(section('History', h('div', { class: 'grid c2 top' },
      divs.length ? h('div', { class: 'stack tight' }, h('div', { class: 'muted fs-xs' }, 'Dividends per share, by year'), miniBars(divs, { fmt: v => F.num(v, 1) })) : null,
      eps.length ? h('div', { class: 'stack tight' }, h('div', { class: 'muted fs-xs' }, 'Earnings per share'), miniBars(eps, { fmt: v => F.num(v, 1) })) : null)));
  }
  const val = valuationBlock(detail);
  if (val) root.append(val);
  return root;
}

function cryptoFundamentals(detail) {
  const f = detail.fundamental || {}, m = f.metrics || {}, p = detail.profile || {};
  const root = h('div', { class: 'stack loose' });
  root.append(banner('info', h('b', null, 'Coins have no earnings to value. '), 'These are market-structure facts. They are shown for context and do not change the trade plan, except through the price behaviour they influence.'));
  const tiles = [
    ['Market cap', F.isNum(m.mcap ?? p.mcap) ? F.money(m.mcap ?? p.mcap, '$') : F.NA, null, null],
    ['Rank by market cap', F.isNum(m.rank ?? p.rank) ? '#' + F.int(m.rank ?? p.rank) : F.NA, null, null],
    ['24h volume / market cap', F.isNum(m.vol_mcap) ? F.pct(m.vol_mcap * 100, 1, { sign: false }) : F.NA, null, 'How much of the coin’s value changes hands in a day. Very low values mean thin trading; very high values mean heavy speculation.'],
    ['Supply in circulation', F.isNum(m.circ_supply_pct) ? F.pct(m.circ_supply_pct, 0, { sign: false }) : F.NA, F.isNum(p.circ_supply) ? F.compact(p.circ_supply) + ' coins' : null, 'Share of the maximum supply already issued. The rest can still be unlocked, which dilutes holders.'],
    ['All-time high', F.isNum(p.ath) ? F.money(p.ath, '$', { compactFrom: 1e9, d: p.ath < 1 ? 4 : 2 }) : F.NA, p.ath_date ? F.date(p.ath_date, { year: true }) : null, null],
    ['From all-time high', F.isNum(m.ath_dd_pct ?? p.ath_dd_pct) ? F.pct(m.ath_dd_pct ?? p.ath_dd_pct, 0) : F.NA, null, null],
  ].filter(([, v]) => v !== F.NA);
  root.append(section('Market structure', h('div', { class: 'stats fund-tiles' }, ...tiles.map(([l, v, s, i]) => stat(l, v, s, { small: true, info: i })))));
  if ((f.flags || []).length) root.append(section('Notable', h('ul', { class: 'bul info' }, ...f.flags.map(t => h('li', null, icon('info'), h('span', null, t))))));
  return root;
}

function fxProfile(detail) {
  const p = detail.profile || {};
  const root = h('div', { class: 'stack loose' });
  const carry = p.carry_pct;
  root.append(banner('info', h('b', null, 'Carry is context, not income. '), 'It is the approximate yearly interest you would earn (or pay) for holding this pair long, from the gap between the two policy rates. Brokers add spreads and swap fees, and Acumen lets carry move conviction only by a few points.'));
  const tiles = [
    ['Pair', `${p.base || ''}/${p.quote || ''}`, p.group ? p.group + ' pair' : null, null],
    ['Carry if long', F.isNum(carry) ? F.pct(carry, 2) + ' a year' : F.NA, F.isNum(carry) ? (carry >= 0 ? 'you earn the rate gap' : 'you pay the rate gap') : null, 'Interest-rate difference between the base and quote currency. Positive means a long position earns it.'],
    ['Pip size', F.isNum(p.pip) ? String(p.pip) : F.NA, null, 'The smallest price step most brokers quote for this pair.'],
    ['Retail brokers', p.retail_tradable === false ? 'Rarely offered' : 'Widely offered', p.synthetic ? 'cross rate built from two pairs' : null, null],
  ].filter(([, v]) => v !== F.NA);
  root.append(section('Pair profile', h('div', { class: 'stats fund-tiles' }, ...tiles.map(([l, v, s, i]) => stat(l, v, s, { small: true, info: i })))));
  return root;
}

export function fundamentalsTab({ detail }) {
  if (detail.market === 'psx') {
    if (detail.fundamental && detail.fundamental.metrics) return psxFundamentals(detail);
    return empty('No fundamentals for this instrument', detail.kind === 'index' ? 'Fundamentals do not apply to an index. See Markets for breadth and sector context.' : 'Company figures were not available from the data sources at the last refresh.', 'book');
  }
  if (detail.market === 'crypto') return cryptoFundamentals(detail);
  return fxProfile(detail);
}

/* ============================== Backtest ============================== */
export function backtestTab({ detail, hz }) {
  const b = detail.backtest && detail.backtest.horizons && detail.backtest.horizons[hz];
  const st = (b && b.stats) || {};
  const root = h('div', { class: 'stack loose' });
  root.append(h('p', { class: 'ink-2' }, `How the ${HZ_LABEL[hz]} rules (entry, stop, targets, trading costs) would have done on ${detail.sym}’s own past. It is a sanity check on this one instrument, not a forecast. Win chances in the plan come from the larger pool of similar trades across the market.`));
  if (!st.n) {
    root.append(empty(`No past ${HZ_LABEL[hz]} trades on ${detail.sym}`, 'The setup has not triggered on this instrument in the history Acumen holds, so the pooled evidence does most of the work.', 'clock'));
    return root;
  }
  const oos = b.oos || st.oos;
  root.append(h('div', { class: 'stats' },
    stat('Past trades', F.int(st.n), st.reliable ? null : 'too few to rely on', { small: true }),
    stat('Winners', F.frac(st.win), 'ended in profit', { small: true }),
    stat('Average result', F.r(st.avg_r), 'per trade, after costs', { small: true, cls: F.dirClass(st.avg_r), info: '1R is the amount risked on a trade (entry to stop). +0.50R means half of the risked amount was made on average per trade.' }),
    stat('Profit factor', F.isNum(st.pf) ? F.num(st.pf, 2) : F.NA, 'gross wins ÷ gross losses', { small: true }),
    stat('Worst slump', F.isNum(st.max_dd_r) ? F.num(-st.max_dd_r, 1) + 'R' : F.NA, 'peak to trough, in R', { small: true, cls: 'down' }),
    stat('Typical holding', F.isNum(st.avg_bars) ? F.num(st.avg_bars, 0) + ' sessions' : F.NA, null, { small: true })));
  if (oos && oos.n) root.append(banner(oos.n >= 10 ? 'info' : 'warn', h('b', null, 'Newest trades, kept apart from the older ones: '), `${F.int(oos.n)} ${oos.n === 1 ? 'trade' : 'trades'}, ${F.frac(oos.win)} winners, average ${F.r(oos.avg_r)}.${oos.n < 10 ? ' That is too few to say anything.' : ''}`));
  if (!st.reliable) root.append(h('div', null, chip('Small sample: treat as an anecdote', 'warn', { icon: 'alert' })));

  const eq = dedupeSeries(b.equity || []);
  let handle = null, dead = false;
  if (eq.length >= 2) {
    const host = h('div', { class: 'eq-chart' });
    root.append(section('Cumulative result, in R', host, note('Each point is a closed trade. A rising line means the rules made money on this instrument; long flat or falling stretches are normal.')));
    requestAnimationFrame(() => requestAnimationFrame(() => { if (!dead) handle = lineChart(host, eq, { area: false, precision: 1, height: 220 }); }));
  }

  const cal = b.calibration;
  if (cal && cal.n_trades) {
    const status = cal.status === 'calibrated' || cal.status === 'validated' ? chip('Validated out of sample', 'up', { icon: 'check' }) : cal.status === 'insufficient' ? chip('Insufficient history', 'warn') : chip('Not validated', 'warn');
    root.append(section('Pooled evidence used for the odds',
      h('div', { class: 'row wrap', style: { gap: '10px' } }, status, h('span', { class: 'ink-2' }, `${F.int(cal.n_trades)} similar trades across this market: ${F.frac(cal.win)} winners, average ${F.r(cal.avg_r)}.`)),
      note('Unvalidated means conviction did not reliably rank newer trades above older ones in this pool, so the plan uses plain base rates instead of trusting the score.'),
      h('div', null, h('a', { class: 'btn sm', ...linkProps('/evidence', { pm: detail.market, ph: hz }) }, icon('shield'), 'See the full check for this pool'))));
  }

  const trades = (b.trades || []).slice().reverse();
  if (trades.length) {
    const body = h('tbody');
    let shown = 0;
    const more = h('button', { class: 'btn sm', type: 'button', onclick: () => draw(shown + 15) }, 'Show more');
    const draw = n => {
      const part = trades.slice(0, n); shown = part.length;
      clear(body);
      for (const t of part) {
        body.append(h('tr', null,
          h('td', null, F.date(t.entry_date, { year: true })), h('td', null, F.date(t.exit_date, { year: true })),
          h('td', null, t.dir === 'short' ? chip('Short', 'down', { title: t.setup || null }) : chip('Long', 'up', { title: t.setup || null })),
          h('td', { class: 'r mono' }, F.px(t.entry, detail.market)), h('td', { class: 'r mono' }, F.px(t.exit, detail.market)),
          h('td', { class: ['r', F.dirClass(t.r)] }, F.r(t.r)), h('td', { class: 'r' }, F.int(t.bars)),
          h('td', { class: 'muted' }, EXIT_LABEL[t.reason] || t.reason || F.NA), h('td', { class: 'muted' }, t.setup || F.NA)));
      }
      more.hidden = shown >= trades.length;
    };
    draw(10);
    root.append(section(`Latest trades (${trades.length})`,
      h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, h('th', null, 'Entered'), h('th', null, 'Exited'), h('th', null, 'Side'), h('th', { class: 'r' }, 'In'), h('th', { class: 'r' }, 'Out'), h('th', { class: 'r' }, 'Result'), h('th', { class: 'r' }, 'Sessions'), h('th', null, 'Ended by'))),
        body))),
      h('div', null, more)));
  }
  return { el: root, destroy() { dead = true; if (handle) handle.destroy(); } };
}

/* ============================== Risk ============================== */
export function riskTab({ detail }) {
  const r = detail.risk;
  if (!r) return empty('No risk figures yet', 'Risk statistics need at least a few months of price history.', 'shield');
  const liq = r.liquidity || {};
  const ccy = detail.currency || (detail.market === 'crypto' ? 'USD' : '');
  const tiles = [
    ['Volatility, yearly', F.isNum(r.vol_ann) ? F.pct(r.vol_ann, 1, { sign: false }) : F.NA, F.isNum(r.vol_ann) ? `a typical year moves about ±${F.num(r.vol_ann, 0)}%` : null, 'Annualised standard deviation of daily returns. Roughly two years in three end within this distance of where they started, before drift.'],
    ['Beta', F.isNum(r.beta) ? F.num(r.beta, 2) : F.NA, r.beta_vs ? 'vs ' + r.beta_vs : null, 'Sensitivity to the benchmark. 1.0 moves with it; 1.5 moves half as much again; below 1 is calmer than the benchmark.'],
    ['Bad day, 1 in 20', F.isNum(r.var95_1d) ? F.pct(r.var95_1d, 2) : F.NA, 'on the worst 5% of days the loss is at least this', 'Historical 1-day value at risk at 95%: on 1 trading day in 20, the loss was at least this large.'],
    ['Average on the worst days', F.isNum(r.cvar95_1d) ? F.pct(r.cvar95_1d, 2) : F.NA, 'expected shortfall', 'Average loss on the worst 5% of days. It describes the tail better than the 1-in-20 figure alone.'],
    ['Worst fall, 1 year', F.isNum(r.max_dd_1y) ? F.pct(r.max_dd_1y, 1) : F.NA, 'peak to trough', 'Largest drop from a high to a later low in the past year. Ask yourself whether you could hold through it.'],
    ['Downside volatility', F.isNum(r.downside_dev) ? F.pct(r.downside_dev, 1, { sign: false }) : F.NA, 'falling days only', 'Like volatility, but counting only losing days.'],
    ['Sharpe, 1 year', F.isNum(r.sharpe_1y) ? F.num(r.sharpe_1y, 2) : F.NA, 'return per unit of risk', 'Excess return over the risk-free rate divided by volatility. Above 1 is good; a single year is a noisy measure.'],
    ['Sortino, 1 year', F.isNum(r.sortino_1y) ? F.num(r.sortino_1y, 2) : F.NA, 'penalises only losses', null],
    ['Opening gaps', F.isNum(r.gap_freq) ? F.pct(r.gap_freq, 0, { sign: false }) + ' of sessions' : F.NA, 'open far from the last close', 'Gaps can carry price straight through a stop, so the exit fills worse than planned.'],
    ['Correlation to benchmark', F.isNum(r.corr_benchmark) ? F.num(r.corr_benchmark, 2) : F.NA, r.beta_vs ? 'vs ' + r.beta_vs : null, 'Near 1: moves with the benchmark, little diversification. Near 0: moves on its own.'],
  ].filter(([, v]) => v !== F.NA);
  const root = h('div', { class: 'stack loose' });
  root.append(section('Risk profile, last year', h('div', { class: 'stats risk-tiles' }, ...tiles.map(([l, v, s, i]) => stat(l, v, s, { small: true, info: i })))));
  const tier = liq.tier;
  const tierText = { A: 'Highly liquid: large orders fill close to the quoted price.', B: 'Liquid enough for normal retail size.', C: 'Thin: expect slippage and keep size small.', D: 'Very thin: prices can move sharply against even small orders.' }[tier];
  root.append(section('Liquidity',
    h('div', { class: 'row wrap', style: { gap: '12px' } }, tier ? h('span', { class: ['grade', tier === 'A' ? 'A' : tier === 'B' ? 'B' : tier === 'C' ? 'C' : 'D'], title: 'Liquidity tier ' + tier }, tier) : null, h('span', { class: 'ink-2' }, tierText || 'Liquidity could not be measured.')),
    h('dl', { class: 'kv narrow' },
      F.isNum(liq.adv_value) ? [h('dt', null, 'Typical traded value per day'), h('dd', null, F.money(liq.adv_value, ccy))] : null,
      F.isNum(liq.days_to_exit_1pct_equity) ? [h('dt', null, 'Days to exit 1% of equity at 10% of volume'), h('dd', null, F.num(liq.days_to_exit_1pct_equity, 1))] : null)));
  return root;
}

/* ============================== Peers ============================== */
export function peersTab({ detail }) {
  const peers = detail.peers || [];
  if (!peers.length) return empty('No peers listed', 'Peers are chosen from the same sector (PSX), currency group (forex) or market-cap band (crypto).', 'layers');
  const hasPe = peers.some(p => F.isNum(p.pe));
  const m = detail.market;
  return h('div', { class: 'stack' },
    h('p', { class: 'muted' }, m === 'psx' ? `Closest companies in ${detail.sector || 'the same sector'}.` : m === 'fx' ? 'Pairs that share a currency with this one, so they often move together.' : 'Coins of similar size.'),
    h('div', { class: 'card tbl-card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', { class: 'r', title: 'Highest conviction across the three horizons' }, 'Conviction'), h('th', { class: 'r' }, '1 month'), hasPe ? h('th', { class: 'r' }, 'P/E') : null)),
      h('tbody', null, ...peers.map(p => h('tr', { class: 'link', onclick: () => go('/i/' + p.id) },
        h('td', null, h('a', { class: 'sym', ...linkProps('/i/' + p.id), onclick: e => e.stopPropagation() }, p.sym), h('div', { class: 'nm' }, p.name)),
        h('td', { class: 'r' }, h('div', { style: { width: '96px', marginLeft: 'auto' } }, convMeter(p.conviction, { calibrated: false }))),
        h('td', { class: 'r' }, chgSpan(p.chg1m, 1)),
        hasPe ? h('td', { class: 'r' }, F.isNum(p.pe) ? F.num(p.pe, 1) : F.NA) : null)))))),
    h('div', null, h('a', { class: 'btn sm', ...linkProps('/screener', { m, sec: m === 'psx' ? detail.sector : null }) }, icon('filter'), 'Compare in the screener')));
}

/* ============================== registry ============================== */
export const TABS = [
  { id: 'signals', label: 'Signals', render: signalsTab },
  { id: 'technicals', label: 'Technicals', render: technicalsTab },
  { id: 'fundamentals', label: d => (d.market === 'fx' ? 'Carry and profile' : d.market === 'crypto' ? 'Coin facts' : 'Fundamentals'), render: fundamentalsTab, show: d => d.kind !== 'index' || d.market !== 'psx' },
  { id: 'backtest', label: 'Backtest', render: backtestTab },
  { id: 'risk', label: 'Risk', render: riskTab },
  { id: 'peers', label: 'Peers', render: peersTab, show: d => (d.peers || []).length > 0 },
];
export const tabLabel = (t, detail) => (typeof t.label === 'function' ? t.label(detail) : t.label);
export const visibleTabs = detail => TABS.filter(t => !t.show || t.show(detail));
void HZ_SPAN;
