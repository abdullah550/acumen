import { h, append, replace } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { linkProps } from '../lib/router.js';
import { icon } from '../lib/icons.js';
import { card, banner, chip, stat, empty, chgSpan, seg } from '../ui/kit.js';
import { pageHead, sectionTitle, errorBlock } from './common.js';
import * as L from '../lib/flows.js';

/* Money Flows answers three questions each week: where is money entering, where is it leaving, and which shares sit in the sectors it is
   moving to. The measure is buying pressure from price and volume over 7 and 30 days (not investor-category data), and the page leads with
   the test of whether that measure has predicted anything. */

const SUB = 'Where money has been entering and leaving the Pakistani market, sector by sector, over the last 7 and 30 days. Measured from price and volume; the page opens with whether that has predicted anything.';
const plural = (n, one, many) => `${F.int(n)} ${n === 1 ? one : many}`;
const TH = (text, cls, title) => h('th', { class: cls, scope: 'col', title: title || null }, text);
const dash = () => h('span', { class: 'muted' }, F.NA);
const pct = (x, d = 0) => (F.isNum(x) ? F.pct(x, d, { sign: true }) : F.NA);
const chg = (x, d = 0) => (F.isNum(x) ? chgSpan(x, d) : dash());
const kv = (k, v) => h('span', null, h('span', { class: 'muted' }, k + ' '), v);
const stateChip = (body, s) => chip(L.stateLabel(body, s), L.STATE_TONE[s] || 'outline', { title: L.STATE_HINT[s] });

export default async function render() {
  if (!(D.meta && D.meta.files && D.meta.files.flows)) {
    return { el: h('div', { class: 'stack loose' }, pageHead('Money Flows', SUB), empty('Not in this data yet', 'The next data refresh adds the Money Flows page. Reload the app once it has run.', 'info')) };
  }
  let body;
  try { body = await D.get('flows'); } catch (e) { return { el: errorBlock(e) }; }
  if (!body.ready) {
    return { el: h('div', { class: 'stack loose' }, pageHead('Money Flows', SUB), empty('Not enough history yet', body.note || 'There is not enough price history to measure money flows.', 'info')) };
  }
  const root = h('div', { class: 'stack loose fl-page' });
  append(root, [
    pageHead('Money Flows', SUB, asofChip(body)),
    h('div', { class: 'stack' }, verdictBanner(body), basisBanner(body), summaryStats(body)),
    answers(body),
    reviewCard(body),
    sectorsSection(body),
    candidatesSection(body),
    backtestSection(body),
    archiveSection(body),
    methodSection(body),
  ]);
  return { el: root };
}

function asofChip(body) {
  if (!body.asof) return null;
  const old = F.isNum(F.daysOld(body.asof)) && F.daysOld(body.asof) > 4;
  return chip(old ? `Prices are ${F.daysOld(body.asof)} days old` : `Prices of ${F.date(body.asof, { year: false })}`, old ? 'warn' : 'outline', { icon: 'clock', title: 'Every measure on this page ends on this price date' });
}

/* ======================================================================= what the page says first */
function verdictBanner(body) {
  const v = L.verdictView(body.backtest);
  return banner(v.tone, h('b', null, v.title + ' '), v.text);
}

function basisBanner(body) {
  const note = body.investor && body.investor.note;
  return banner('info', h('b', null, 'Measured from price and volume. '), note || 'This cannot say who was buying.');
}

function summaryStats(body) {
  const c = body.counts || {};
  const m = body.market || {};
  return h('div', { class: 'stats fl-stats' },
    stat('Sectors compared', F.int(c.sectors), `${F.int(c.shares)} liquid shares`, { small: true }),
    stat('Entering', F.int(c.entering), 'both windows above the market', { small: true }),
    stat('Leaving', F.int(c.leaving), 'both windows below the market', { small: true }),
    stat('Candidate shares', F.int(c.candidates), 'pass at least two of three tests', { small: true }),
    stat('Whole market, 30 days', pct(m.pressure30, 1), 'buying pressure', { small: true }),
    stat('Turnover', F.isNum(m.turnover) ? `${F.num(m.turnover, 2)}×` : F.NA, 'last 7 days vs 90-day norm', { small: true }));
}

/* ======================================================================= the three questions */
function sectorRow(r) {
  return h('div', { class: 'fl-row' },
    h('div', { class: 'fl-row-t' }, h('b', null, r.name), r.thin ? chip('few shares', 'outline', { title: `Only ${r.n} liquid shares: read this one loosely` }) : null),
    h('div', { class: 'fl-row-s' }, kv('30 days', chg(r.pressure30)), kv('7 days', chg(r.pressure7)), kv('vs market, 30 days', chg(r.rel30))));
}

function answers(body) {
  const rows = body.sectors || [];
  const entering = L.bySector(rows, 'entering');
  const turning = L.bySector(rows, 'turning_in');
  const leaving = L.bySector(rows, 'leaving').reverse();
  const fading = L.bySector(rows, 'fading').reverse();
  const benAll = L.beneficiaries(body.candidates, 999);
  const ben = benAll.slice(0, 8);
  const list = (main, extra, extraTitle, none) => h('div', { class: 'stack' },
    main.length ? h('div', { class: 'fl-list' }, ...main.slice(0, 5).map(sectorRow)) : h('p', { class: 'muted fs-sm' }, none),
    extra.length ? h('div', { class: 'stack tight' }, h('div', { class: 'ic-l' }, extraTitle), h('div', { class: 'fl-list' }, ...extra.slice(0, 3).map(sectorRow))) : null);
  const benRow = c => h('div', { class: 'fl-row' },
    h('div', { class: 'fl-row-t' }, h('a', { class: 'sym', ...linkProps('/i/' + c.id) }, c.sym), chip(`${c.pillars} of 3 tests`, c.pillars === 3 ? 'up' : 'outline'), c.bargain ? chip('Bargain', 'accent', { title: 'Also on the Bargains list' }) : null),
    h('div', { class: 'fl-row-s' }, h('span', { class: 'muted' }, `${c.name} · ${c.sector}`), c.sector_state ? stateChip(body, c.sector_state) : null));
  const nobody = entering.length || turning.length ? 'No share in those sectors passes two of the three tests this week.' : 'No sector is entering or turning in, so there is nothing to look for.';
  const week = body.report && body.report.week;
  return h('section', { class: 'stack' },
    sectionTitle('This week’s three questions', week ? chip(week, 'outline') : null, 'Sectors are judged against the rest of the market, because in a rising market every sector shows buying.'),
    h('div', { class: 'grid c3 top' },
      card({ title: 'Where is money entering?', info: 'Sectors whose buying pressure, over both the last 7 and the last 30 days, is above the rest of the market.',
        body: list(entering, turning, 'Turning in: the last 7 days are strong, 30 days not yet', 'No sector is above the market on both windows.') }),
      card({ title: 'Where is it leaving?', info: 'Sectors whose buying pressure, over both the last 7 and the last 30 days, is below the rest of the market.',
        body: list(leaving, fading, 'Fading: the last 7 days are weak after earlier support', 'No sector is below the market on both windows.') }),
      card({ title: 'Which shares may benefit?', info: 'Shares in sectors that are entering or turning in that also pass at least two of the three screen tests (accumulating, earnings up, busy trading). A screen for research, not a forecast.',
        body: ben.length ? h('div', { class: 'stack' },
          h('div', { class: 'fl-list' }, ...ben.map(benRow)),
          benAll.length > ben.length ? h('p', { class: 'muted fs-xs', style: { margin: 0 } }, `${F.int(benAll.length - ben.length)} more in the candidate table below.`) : null)
          : h('p', { class: 'muted fs-sm' }, nobody) })));
}

function reviewCard(body) {
  const rep = body.report || {};
  const back = L.backdropItems(body.macro, body.regime);
  return card({
    title: `Review for week ${rep.week ? rep.week.replace(/^\d+-W/, '') : ''}, to ${F.date(rep.asof || body.asof)}`,
    info: 'Written from the numbers on this page each time the data refreshes. Each week’s list of entering and leaving sectors is saved in the weekly record below.',
    body: h('div', { class: 'stack' },
      h('ul', { class: 'bg-list' }, ...(rep.lines || []).map(l => h('li', null, l))),
      rep.changes && rep.changes.length ? h('div', { class: 'stack tight' }, h('div', { class: 'ic-l' }, 'Changed since last week'),
        h('div', { class: 'row wrap' }, ...rep.changes.map(c => h('span', { class: 'chip outline' }, `${c.sector}: ${L.stateLabel(body, c.from)} → ${L.stateLabel(body, c.to)}`)))) : null,
      back.length ? h('div', { class: 'stack tight' }, h('div', { class: 'ic-l' }, 'Backdrop'),
        h('div', { class: 'row wrap' }, ...back.map(b => h('span', { class: 'chip outline' }, h('span', { class: 'muted' }, b.k), ' ' + b.v)),
          h('a', { class: 'btn ghost sm', ...linkProps('/markets') }, 'Markets', icon('right')))) : null),
  });
}

/* ======================================================================= every sector */
function pressureCell(x, rel) {
  const b = L.pressureBar(x);
  return h('div', { class: 'fl-pcell' },
    h('span', { class: ['fl-bar', b.side], 'aria-hidden': 'true' }, h('i', { style: { width: b.width / 2 + '%' } })),
    h('span', { class: 'fl-pnum' }, chg(x), F.isNum(rel) ? h('span', { class: 'fl-rel' }, `${pct(rel)} vs market`) : null));
}

function sectorsSection(body) {
  const rows = body.sectors || [];
  const counts = L.countByState(rows);
  const st = { f: 'all' };
  const host = h('div');
  const opts = [{ value: 'all', label: `All ${counts.all}` }, ...L.STATE_ORDER.filter(s => counts[s]).map(s => ({ value: s, label: `${L.stateLabel(body, s)} ${counts[s]}`, title: L.STATE_HINT[s] }))];
  const filter = seg(opts, 'all', v => { st.f = v; draw(); }, { sm: true, label: 'Show sectors' });
  const name = r => h('div', { class: 'scr-id' }, h('div', { class: 'scr-id-t' }, h('b', null, r.name)), h('div', { class: 'nm', title: r.top.join(', ') }, `${plural(r.n, 'share', 'shares')}${r.thin ? ' (few)' : ''}: ${r.top.join(', ')}`));
  const mil = x => (F.isNum(x) ? `${F.num(x, 0, { sign: true })} m` : F.NA);
  const times = x => (F.isNum(x) ? `${F.num(x, 2)}×` : F.NA);
  const upShare = x => (F.isNum(x) ? `${F.num(x, 0)}%` : F.NA);
  const priceCell = r => h('div', { class: 'fl-pnum fl-right' }, chg(r.rel_ret30, 1), F.isNum(r.breadth30) ? h('span', { class: 'fl-rel' }, `${upShare(r.breadth30)} of shares up`) : null);
  function draw() {
    const part = L.filterSectors(rows, st.f);
    replace(host,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fl-tbl' },
        h('thead', null, h('tr', null, TH('Sector'), TH('State'), TH('30-day pressure', null, 'Net buying as a share of traded value over 30 days'), TH('7-day pressure', null, 'Net buying as a share of traded value over 7 days'),
          TH('Net flow, 30 days', 'r', 'Signed traded value in PKR million'), TH('Turnover', 'r', 'Last 7 days against the 90-day norm'),
          TH('Price vs market, 30 days', 'r', 'The sector’s average price change over 30 days minus the whole market’s, and the share of its shares that rose'))),
        h('tbody', null, ...part.map(r => h('tr', null,
          h('td', null, name(r)), h('td', null, stateChip(body, r.state)), h('td', null, pressureCell(r.pressure30, r.rel30)), h('td', null, pressureCell(r.pressure7, r.rel7)),
          h('td', { class: 'r' }, F.isNum(r.net30) ? chgSpan(r.net30, 0, { text: mil(r.net30) }) : dash()),
          h('td', { class: 'r' }, times(r.turnover)), h('td', { class: 'r' }, priceCell(r))))))),
      h('div', { class: 'ev-cards' }, ...part.map(r => h('div', { class: 'ev-crow' },
        h('div', { class: 'ev-crow-top' }, h('div', { class: 'ev-crow-id' }, h('b', null, r.name), h('div', { class: 'ev-crow-sub' }, `${plural(r.n, 'share', 'shares')}: ${r.top.join(', ')}`)), h('div', { class: 'ev-crow-r' }, stateChip(body, r.state))),
        h('div', { class: 'ev-crow-line' }, kv('30-day pressure', chg(r.pressure30)), kv('vs market', chg(r.rel30))),
        h('div', { class: 'ev-crow-line' }, kv('7-day pressure', chg(r.pressure7)), kv('vs market', chg(r.rel7))),
        h('div', { class: 'ev-crow-line' }, kv('Net flow, 30 days', mil(r.net30)), kv('Turnover', times(r.turnover))),
        h('div', { class: 'ev-crow-line' }, kv('Price vs market', chg(r.rel_ret30, 1)), kv('Shares up', upShare(r.breadth30)))))),
      part.length ? null : h('p', { class: 'muted fs-sm', style: { padding: '12px 16px' } }, 'No sector is in this state today.'));
  }
  draw();
  return h('section', { class: 'stack' },
    sectionTitle('Every sector', chip(plural(rows.length, 'sector', 'sectors'), 'outline'), 'Pressure is the share of a sector’s traded value that was net buying: +50% means three quarters of the value traded on up days. The state compares each sector with the others.'),
    card({ flush: true, body: h('div', null, h('div', { class: 'fl-filter' }, filter), host) }),
    h('p', { class: 'muted fs-xs bg-note' }, 'Net flow is signed traded value over 30 days, in PKR million. Turnover is the last 7 days against the 90-day norm. Price vs market is the sector’s average 30-day price change minus the whole market’s; shares up is the share of its shares that rose over 30 days.'));
}

/* ======================================================================= candidate shares */
function pillarChips(c) {
  return h('div', { class: 'row wrap fl-pillars' }, ...L.pillars(c).map(p => h('span', { class: ['chip', p.on ? 'up' : 'outline', !p.on && 'fl-off'], title: p.hint }, p.on ? icon('check') : null, p.label)));
}

function testsLegend(t) {
  return h('details', { class: 'bg-how' }, h('summary', null, 'What the three tests mean'), h('ul', { class: 'bg-list' },
    h('li', null, h('b', null, 'Accumulating.'), ` 30-day buying pressure in the top third of liquid shares, not negative over the last 7 days, and the price not already up more than ${F.num(t.max_chase_pct, 0)}% in a month.`),
    h('li', null, h('b', null, 'Earnings up.'), ` Profitable, with profit per share up at least ${F.num(t.min_eps_growth_pct, 0)}% on a year earlier.`),
    h('li', null, h('b', null, 'Busy trading.'), ` Turnover over the last 7 days at least ${F.num(t.min_rvol, 2)}× its 90-day norm: a stand-in for institutional interest, not a measure of it.`)));
}

function candidatesSection(body) {
  const rows = body.candidates || [];
  const t = body.thresholds || {};
  const LIMIT = 12;
  const st = { all: false };
  const host = h('div');
  const id = c => h('div', { class: 'scr-id' }, h('div', { class: 'scr-id-t' }, h('a', { class: 'sym', ...linkProps('/i/' + c.id) }, c.sym), c.bargain ? chip('Bargain', 'accent', { title: 'Also on the Bargains list' }) : null), h('div', { class: 'nm', title: c.name }, c.name));
  const sector = c => h('div', null, h('div', null, c.sector), h('div', { class: 'fl-sec-state' }, c.sector_state ? stateChip(body, c.sector_state) : null));
  const times = c => (F.isNum(c.turnover) ? `${F.num(c.turnover, 1)}×` : F.NA);
  const whyText = c => (c.why || []).join('; ') + '.';
  function draw() {
    const part = st.all ? rows : rows.slice(0, LIMIT);
    replace(host,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fl-tbl' },
        h('thead', null, h('tr', null, TH('Share'), TH('Sector'), TH('Tests passed', null, 'Hover a row’s tests for the figures behind them'), TH('30-day pressure', 'r'), TH('Turnover', 'r'), TH('Profit growth', 'r', 'Profit per share against a year earlier'), TH('P/E', 'r'))),
        h('tbody', null, ...part.map(c => h('tr', null,
          h('td', null, id(c)), h('td', null, sector(c)), h('td', { class: 'wrap', title: whyText(c) }, pillarChips(c)), h('td', { class: 'r' }, chg(c.pressure30)),
          h('td', { class: 'r' }, times(c)), h('td', { class: 'r' }, chg(c.eps_growth)), h('td', { class: 'r' }, F.isNum(c.pe) ? F.num(c.pe, 1) : F.NA)))))),
      h('div', { class: 'ev-cards' }, ...part.map(c => h('div', { class: 'ev-crow' },
        h('div', { class: 'ev-crow-top' }, h('div', { class: 'ev-crow-id' }, h('a', { class: 'sym', ...linkProps('/i/' + c.id) }, c.sym), h('div', { class: 'ev-crow-sub', title: c.name }, c.name)), h('div', { class: 'ev-crow-r' }, chip(`${c.pillars} of 3`, c.pillars === 3 ? 'up' : 'outline'))),
        pillarChips(c),
        h('div', { class: 'ev-crow-line' }, kv('Sector', c.sector), c.sector_state ? stateChip(body, c.sector_state) : null),
        h('div', { class: 'ev-crow-line' }, kv('30-day pressure', chg(c.pressure30)), kv('Turnover', times(c))),
        h('div', { class: 'ev-crow-line' }, kv('Profit growth', chg(c.eps_growth)), kv('P/E', F.isNum(c.pe) ? F.num(c.pe, 1) : F.NA)),
        h('p', { class: 'muted fs-xs', style: { margin: 0 } }, whyText(c))))),
      rows.length > LIMIT ? h('div', { class: 'ev-more' }, h('button', { class: 'btn sm', type: 'button', onclick: () => { st.all = !st.all; draw(); } }, st.all ? 'Show fewer' : `Show all ${F.int(rows.length)}`)) : null);
  }
  draw();
  return h('section', { class: 'stack' },
    sectionTitle('Candidate shares', chip(plural(rows.length, 'share', 'shares'), 'outline'), 'Liquid shares that pass at least two of three tests: accumulating, earnings up, busy trading. A screen for where to look, not a list of things to buy.'),
    rows.length ? card({ flush: true, body: host }) : empty('No share passes two of the three tests', 'That is a normal result: the tests are meant to be strict.', 'search'),
    rows.length ? h('div', { class: 'stack' }, testsLegend(t), h('p', { class: 'muted fs-sm bg-note' }, L.screenNote(body.backtest && body.backtest.verdict))) : null);
}

/* ======================================================================= does it predict anything? */
function tStrip(t, thr, scale = 6) {
  const b = L.tBar(t, scale);
  return h('div', { class: 'fl-t', role: 'img', 'aria-label': `t-statistic ${F.isNum(t) ? F.num(t, 1) : 'not available'}; a result needs ${F.num(thr, 1)} either way` },
    h('i', { class: ['fl-t-bar', b.side, F.isNum(t) && Math.abs(t) >= thr && 'hit'], style: { left: b.left + '%', width: b.width + '%' } }),
    h('b', { class: 'fl-t-mid' }), h('b', { class: 'fl-t-mark', style: { left: L.tMark(-thr, scale) + '%' } }), h('b', { class: 'fl-t-mark', style: { left: L.tMark(thr, scale) + '%' } }));
}

function backtestSection(body) {
  const bt = body.backtest || {};
  const v = L.verdictView(bt);
  const { primary, sector, stock } = L.splitTests(bt);
  const ordered = [...primary, ...sector, ...stock];
  const tone = bt.verdict === 'supported' ? chip('Passed', 'up') : bt.verdict === 'too_short' ? chip('Too early', 'outline') : bt.verdict === 'partial' ? chip('Half passed', 'warn') : bt.verdict === 'reversed' ? chip('Wrong way round', 'warn') : chip('Not shown', 'warn');
  const thr = t => (t.primary ? bt.primary_t : bt.other_t);
  const res = t => { const r = L.resultOf(t); return chip(r.label, r.tone); };
  const gap = t => (F.isNum(t.spread) ? h('span', { class: 'mono' }, `${F.num(t.spread, 2, { sign: true })}%`) : dash());
  const label = t => h('div', null, h('div', null, L.testName(t)), t.primary ? chip('Fixed in advance', 'accent', { title: `One of the two tests that carry the verdict: they need |t| of at least ${F.num(bt.primary_t, 1)}` }) : null);
  return h('section', { class: 'stack' },
    sectionTitle('Does it predict anything?', tone, `At every fifth trading day${bt.from ? ` since ${F.date(bt.from)}` : ''}, using only what was known that day, sectors (and, separately, single shares) were ranked by each measure and compared with what they did over the following days. This is what separates a useful signal from an attractive-looking dashboard.`),
    bt.tests && bt.tests.length ? h('div', { class: 'stack' },
      v.lines.length ? card({ title: 'What it found', body: h('ul', { class: 'bg-list' }, ...v.lines.map(l => h('li', null, l))) }) : null,
      h('div', { class: 'stats' },
        stat('Weekly checks', F.int(bt.anchors), `${F.date(bt.from, { year: true })} to ${F.date(bt.to, { year: true })}`, { small: true }),
        stat('A signal needs', `|t| ≥ ${F.num(bt.primary_t, 1)}`, 'for the two tests fixed in advance', { small: true }),
        stat('Tests run', F.int(bt.tests.length), `others need |t| ≥ ${F.num(bt.other_t, 1)}`, { small: true })),
      card({ title: 'Every test', flush: true,
        info: 'Rank correlation: do sectors ranked higher by the measure do better afterwards? 0 is nothing, +1 is perfect. t-statistic: how far that is from chance, allowing for overlapping look-aheads (Newey-West); the strip marks the bar each test must clear. Best minus worst: average later return of the best third of sectors (a fifth of shares) minus the worst, relative to the market, before any trading costs.',
        body: h('div', null,
          h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fl-tbl' },
            h('thead', null, h('tr', null, TH('Test'), TH('Checks', 'r'), TH('Rank correlation', 'r'), TH('t-statistic'), TH('Best minus worst', 'r'), TH('Result'))),
            h('tbody', null, ...ordered.map(t => h('tr', { class: t.primary ? 'fl-prim' : null },
              h('td', null, label(t)), h('td', { class: 'r' }, F.int(t.n)), h('td', { class: 'r' }, F.isNum(t.ic) ? F.num(t.ic, 3, { sign: true }) : F.NA),
              h('td', null, h('div', { class: 'fl-tcell' }, tStrip(t.t, thr(t)), h('span', { class: 'mono' }, F.isNum(t.t) ? F.num(t.t, 1, { sign: true }) : F.NA))), h('td', { class: 'r' }, gap(t)), h('td', null, res(t))))))),
          h('div', { class: 'ev-cards' }, ...ordered.map(t => h('div', { class: ['ev-crow', t.primary && 'fl-prim'] },
            h('div', { class: 'ev-crow-top' }, h('div', { class: 'ev-crow-id' }, h('b', null, L.testName(t)), t.primary ? chip('Fixed in advance', 'accent') : null), h('div', { class: 'ev-crow-r' }, res(t))),
            h('div', { class: 'fl-tcell' }, tStrip(t.t, thr(t)), h('span', { class: 'mono' }, F.isNum(t.t) ? `t ${F.num(t.t, 1, { sign: true })}` : F.NA)),
            h('div', { class: 'ev-crow-line' }, kv('Rank correlation', F.isNum(t.ic) ? F.num(t.ic, 3, { sign: true }) : F.NA), kv('Best minus worst', gap(t)), kv('Checks', F.int(t.n)))))) ) }),
      h('details', { class: 'bg-how' }, h('summary', null, 'What this test can and cannot say'), h('ul', { class: 'bg-list' }, ...(bt.notes || []).map(n => h('li', null, n))))) : null);
}

/* ======================================================================= the weekly record */
function archiveSection(body) {
  const weeks = L.weekRows(body.weeks, body.live);
  const sum = L.liveSummary(body.live);
  const names = list => (list && list.length ? list.join(', ') : F.NA);
  const sp = x => (F.isNum(x) ? chgSpan(x, 2) : h('span', { class: 'muted', title: 'Not enough trading days have passed to score this call' }, 'not yet'));
  return h('section', { class: 'stack' },
    sectionTitle('Weekly record', chip(plural(weeks.length, 'week', 'weeks'), 'outline'), 'Each week’s call is saved as it was made. After 5 and 21 trading days it is scored: the sectors it called entering, against those it called leaving, relative to the market. This is the out-of-sample test the history cannot give.'),
    sum.length ? banner('info', h('b', null, 'Calls scored so far. '), sum.map(x => `After ${x.days} trading days: ${plural(x.n, 'week', 'weeks')}, entering beat leaving in ${F.num(x.hit, 0)}%, by ${F.num(x.mean, 2, { sign: true })}% on average.`).join(' ') + ' Too few weeks to read as evidence yet.')
      : banner('info', h('b', null, 'No call has had time to be scored yet. '), 'The first can be scored 5 trading days after it was made.'),
    card({ flush: true, body: h('div', null,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fl-tbl' },
        h('thead', null, h('tr', null, TH('Week'), TH('To'), TH('Entering'), TH('Leaving'), TH('After 5 days', 'r', 'Entering minus leaving, relative to the market'), TH('After 21 days', 'r'))),
        h('tbody', null, ...weeks.map(w => h('tr', null, h('td', null, h('b', null, w.week)), h('td', null, F.date(w.asof, { year: false })), h('td', { class: 'fl-names' }, names(w.entering)), h('td', { class: 'fl-names' }, names(w.leaving)), h('td', { class: 'r' }, sp(w.spread5)), h('td', { class: 'r' }, sp(w.spread21))))))),
      h('div', { class: 'ev-cards' }, ...weeks.map(w => h('div', { class: 'ev-crow' },
        h('div', { class: 'ev-crow-top' }, h('div', { class: 'ev-crow-id' }, h('b', null, w.week), h('div', { class: 'ev-crow-sub' }, `to ${F.date(w.asof, { year: false })}`))),
        h('div', { class: 'ev-crow-line' }, kv('Entering', names(w.entering))), h('div', { class: 'ev-crow-line' }, kv('Leaving', names(w.leaving))),
        h('div', { class: 'ev-crow-line' }, kv('After 5 days', sp(w.spread5)), kv('After 21 days', sp(w.spread21))))))) }));
}

/* ======================================================================= how it works */
function methodSection(body) {
  const t = body.thresholds || {};
  return card({
    title: 'How to read this page',
    body: h('div', { class: 'grid c2 top' },
      h('div', { class: 'stack' },
        h('div', { class: 'ic-l' }, 'The measure'),
        h('ul', { class: 'bg-list' },
          h('li', null, h('b', null, 'Buying pressure.'), ' On a day a share’s close rose, its traded value counts as buying; on a day it fell, as selling. Over 7 or 30 calendar days, a sector’s pressure is net buying divided by total traded value: between −100% and +100%.'),
          h('li', null, h('b', null, 'Against the market.'), ' A sector is compared with the whole liquid market and with the other sectors, so a rising market does not make every sector look as if money is entering.'),
          h('li', null, h('b', null, 'The four states.'), ` Entering and leaving need both windows at least ${F.num(t.state_z, 1)} standard deviations from the sector average; turning in and fading are a strong or weak last week without the 30-day picture.`),
          h('li', null, h('b', null, 'Sectors compared.'), ` Only sectors with at least ${F.int(t.min_members)} liquid shares; a sector with fewer than five is marked.`))),
      h('div', { class: 'stack' },
        h('div', { class: 'ic-l' }, 'What it is not'),
        h('ul', { class: 'bg-list' },
          h('li', null, 'It is not investor-category data. Whether foreigners, mutual funds, banks or individuals were buying comes from the National Clearing Company’s tables, which this app cannot read. “Busy trading” is only a stand-in for institutional interest.'),
          h('li', null, 'It is not a forecast. The test above is the evidence on that, and the weekly record builds a live one.'),
          h('li', null, 'Net buying by price direction is an approximation: a rise is not always a buyer-initiated day.'),
          h('li', null, 'Earnings growth comes from the latest reported figures, so it can be a quarter or two old.')))),
  });
}
