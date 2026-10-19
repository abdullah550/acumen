import { h, replace, append } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { linkProps, replaceQuery } from '../lib/router.js';
import { MARKETS, MARKET_LABEL, HORIZONS, HZ_LABEL, HZ_SPAN, EXIT_LABEL, dedupeSeries, by } from '../lib/util.js';
import { card, banner, chip, mkChip, hzChip, stat, empty, explain, seg, chgSpan } from '../ui/kit.js';
import { bucketChart } from '../ui/charts.js';
import { lineChart, LineStyle } from '../ui/lwc.js';
import { edgeChart, edgeVerdict, statusOf, pct0 } from '../ui/evcharts.js';
import { icon } from '../lib/icons.js';
import { pageHead, sectionTitle, errorBlock } from './common.js';

/* The Evidence screen answers one question: how much should these numbers be trusted?
   It shows three kinds of proof, from strongest to weakest: the live record (ideas followed forward in time), the
   out-of-sample check on the historical test (does conviction rank trades it has not seen?) and the comparison with
   random entries (is the result just the market drifting?). It never hides a bad result. */

const MIN_LIVE = 30;       // finished ideas before the live record is worth reading
const MIN_ROW = 10;        // finished ideas before a single market/horizon row is worth reading
const REASON = { ...EXIT_LABEL, expired: 'Never entered', skipped: 'Skipped' };

const narrow = () => window.matchMedia('(max-width: 760px)').matches;
const poolName = (m, hz) => `${MARKET_LABEL[m]} ${HZ_LABEL[hz]}`;
const plural = (n, one, many) => `${F.int(n)} ${n === 1 ? one : many}`;
function list(a) { return a.length <= 1 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]; }
const marketOf = id => String(id || '').split(':')[0].toLowerCase();

function collectPools(cal) {
  const out = [];
  for (const m of MARKETS) {
    for (const hz of HORIZONS) {
      const p = cal && cal.markets && cal.markets[m] && cal.markets[m].horizons && cal.markets[m].horizons[hz];
      if (p) out.push({ m, hz, p, key: `${m}:${hz}`, name: poolName(m, hz) });
    }
  }
  return out;
}

export default async function render(route) {
  let cal, led;
  try { [cal, led] = await Promise.all([D.get('calibration'), D.get('ledger')]); } catch (e) { return { el: errorBlock(e) }; }
  let idx = new Map();
  try { idx = await D.index(); } catch { /* names fall back to the ids */ }

  const ctx = { cal, led: led || {}, idx, pools: collectPools(cal), dead: false, cleanups: [] };
  const when = cal && cal.generated_at ? F.date(String(cal.generated_at).slice(0, 10), { year: true }) : null;
  const root = h('div', { class: 'stack loose' });
  append(root, [
    pageHead('Evidence', 'How far these numbers can be trusted: the live record of ideas Acumen has issued, a historical test of its conviction scores, and what the method cannot tell you.',
      when ? chip(`Test run ${when}`, 'outline', { icon: 'clock', title: 'When the historical test was last recomputed' }) : null),
    verdictCard(ctx),
    liveSection(ctx),
    testSection(ctx, route),
    limitsSection(ctx),
  ]);
  return { el: root, destroy() { ctx.dead = true; for (const fn of ctx.cleanups.splice(0)) { try { fn(); } catch { /* already gone */ } } } };
}

/* ======================================================================= verdict */
function liveVerdict(st) {
  const closed = st.closed || 0;
  const since = st.since ? F.date(st.since, { year: true }) : null;
  if (!closed) {
    return {
      chip: chip('No results yet', 'outline', { icon: 'clock' }),
      text: st.open
        ? `${plural(st.open, 'idea is', 'ideas are')} being followed${since ? ` (recording began ${since})` : ''}. None has finished, so there is nothing to judge yet. Swing ideas can finish within weeks; investment ideas take months.`
        : 'No idea has been recorded yet. The ledger starts with the first actionable idea.',
    };
  }
  const line = `${plural(closed, 'idea has', 'ideas have')} finished: ${F.frac(st.win)} won, ${F.r(st.avg_r)} per idea on average after costs.`;
  if (closed < MIN_LIVE) return { chip: chip('Too few to judge', 'warn', { icon: 'alert' }), text: `${line} Fewer than about ${MIN_LIVE} results say very little; luck alone can produce them.` };
  const pos = st.avg_r > 0;
  return { chip: chip(pos ? 'Positive so far' : 'Negative so far', pos ? 'up' : 'warn', { icon: pos ? 'up' : 'down' }), text: line + (closed < 100 ? ' It is still a short record: a few months of results can be luck.' : '') };
}

function testVerdict(pools, ok) {
  const n = pools.length;
  if (!n) return { chip: chip('Not run', 'outline'), text: 'This build contains no historical test.' };
  if (!ok.length) return { chip: chip('None validated', 'warn', { icon: 'alert' }), text: `Higher conviction did not reliably mean better results on newer trades in any of the ${n} market and horizon pools, so conviction works as a screening rank only.` };
  if (ok.length === n) return { chip: chip('All validated', 'up', { icon: 'check' }), text: 'Conviction ranked newer trades correctly in every pool. It was fitted on older trades and checked on newer ones it had not seen.' };
  return {
    chip: chip(`${ok.length} of ${n} validated`, 'outline', { icon: 'info' }),
    text: `Conviction ranked newer trades correctly in ${list(ok.map(x => x.name))}. In the other ${n - ok.length} it did not, or there were too few trades to tell.`,
  };
}

function randomVerdict(measured, clear, worse, untested = []) {
  const m = measured.length;
  if (!m) return { chip: chip(untested.length ? 'Too little history' : 'Not measured', 'outline'), text: untested.length ? 'No market and horizon has enough history yet to compare the plan with random entries, so nothing can be marked actionable. The longer Acumen runs, the more it can test.' : 'This build has no comparison with random entries.' };
  const drift = 'so part of the past profit may be the market drifting rather than skill.';
  const gate = 'Only a pool that passes can produce actionable ideas; ideas from the others are research candidates.';
  const more = untested.length ? ` ${plural(untested.length, 'pool has', 'pools have')} too little history to test.` : '';
  if (!clear.length) return { chip: chip('No clear edge', 'warn', { icon: 'alert' }), text: `In none of the ${m} tested pools did the plan beat random entries (same stops, targets and costs) clearly enough to rule out luck. Past profit may be the market drifting rather than skill. Nothing is marked actionable.${more}` };
  if (clear.length === m && !untested.length) return { chip: chip(`All ${m} pools`, 'up', { icon: 'check' }), text: `The plan beat random entries (same stops, targets and costs) clearly enough to rule out luck in every pool, and the newer trades agree. ${gate}` };
  return {
    chip: chip(`${clear.length} of ${m} pools`, 'outline', { icon: 'info' }),
    text: `The plan beat random entries (same stops, targets and costs) clearly enough to rule out luck in ${list(clear.map(x => x.name))}. In the other ${m - clear.length} tested ${m - clear.length === 1 ? 'pool' : 'pools'} the gap was within noise${worse.length ? ' or negative' : ''}, ${drift}${more} ${gate}`,
  };
}

function verdictRow(label, v, info) {
  return h('div', { class: 'ev-row' },
    h('div', { class: 'ev-row-h' }, h('div', { class: 'label' }, label, info ? explain(info) : null), v.chip),
    h('p', { class: 'ev-row-t' }, v.text));
}

function verdictCard({ led, pools }) {
  const st = led.stats || {};
  const ok = pools.filter(x => x.p.status === 'calibrated');
  const measured = pools.filter(x => !['unknown', 'untested'].includes(edgeVerdict(x.p).k));
  const clear = measured.filter(x => edgeVerdict(x.p).k === 'clear');
  const worse = measured.filter(x => edgeVerdict(x.p).k === 'worse');
  const untested = pools.filter(x => edgeVerdict(x.p).k === 'untested');
  return card({
    title: 'Where things stand',
    info: 'Three independent checks, from the hardest to fake to the easiest. A live record cannot be flattered by hindsight. The out-of-sample check asks whether conviction ranked trades the model had not seen. The random-entry comparison asks whether the result is just the market drifting.',
    cls: 'accent',
    body: h('div', { class: 'stack' },
      h('div', { class: 'ev-verdict' },
        verdictRow('Live record', liveVerdict(st), 'Ideas Acumen has issued since it started recording, followed forward in time with the same entry, stop and target rules as the historical test.'),
        verdictRow('Out-of-sample check', testVerdict(pools, ok), 'A pool is one market and one horizon. The conviction curve is fitted on the older 60% of its trades and must rank the newer 40% correctly before Acumen treats conviction as a probability.'),
        verdictRow('Against random entries', randomVerdict(measured, clear, worse, untested), 'For every pool Acumen also simulates entering at random with the same stops and targets. Each plan trade is compared with random entries opened in the same direction on its own day or the next few days, so the market\u2019s own moves cancel out. The uncertainty allows for trades that overlap in time and for each instrument\u2019s own luck. If the plan cannot beat random entries clearly enough to rule out luck, or the newer trades disagree, the pool cannot produce actionable ideas.')),
      h('p', { class: 'ev-note' }, icon('info'), h('span', null, 'Where conviction is not validated, ideas show the pool’s average win chance instead of a score-specific one, and the suggested risk per trade is halved.'))),
  });
}

/* ======================================================================= live record */
function liveSection(ctx) {
  const { led, idx } = ctx;
  const st = led.stats || {};
  const open = led.open || [];
  const closedRows = led.closed || [];
  const pending = open.filter(o => o.status === 'pending').length;
  const filled = open.length - pending;
  const sec = h('section', { class: 'stack' });
  sec.append(sectionTitle('Live record', st.since ? chip(`Recording since ${F.date(st.since, { year: true })}`, 'outline', { icon: 'clock' }) : null,
    'Every actionable idea is written to a ledger the day it is issued and followed with the same rules as the historical test. This is the one part of the evidence that cannot be flattered by hindsight.'));

  sec.append(card({
    body: h('div', { class: 'stats ev-stats' },
      stat('Waiting for entry', F.int(pending), 'Price has not reached the entry yet'),
      stat('In a trade', F.int(filled), 'Entered; stop and targets are live'),
      stat('Finished', F.int(st.closed || 0), 'Hit a target, the stop or the time limit'),
      stat('Won', F.isNum(st.win) ? F.frac(st.win) : F.NA, 'Finished above zero'),
      stat('Average result', F.isNum(st.avg_r) ? F.r(st.avg_r) : F.NA, 'Per idea, after costs', { info: 'R is the amount risked on an idea: the distance from entry to stop. +1R means it made as much as it risked, −1R means it lost the whole planned risk. Results are after the costs set in the configuration.' })),
  }));

  if (!st.closed) {
    sec.append(banner('info', h('b', null, 'No idea has finished yet. '), `Results appear here by themselves after each refresh. Until about ${MIN_LIVE} ideas have finished, treat the live record as too small to judge; today the numbers lean on the historical test below.`));
  } else {
    sec.append(groupTable(st));
    const pts = runningTotal(closedRows);
    if (closedRows.length >= 5 && pts.length >= 3) sec.append(totalChart(ctx, pts, closedRows.length, st.closed));
  }
  sec.append(openTable(open, idx));
  if (closedRows.length) sec.append(closedTable(closedRows, idx));
  return sec;
}

function groupTable(st) {
  const rows = [
    ...MARKETS.map(m => ({ label: MARKET_LABEL[m], v: (st.by_market || {})[m] })),
    ...HORIZONS.map(hz => ({ label: HZ_LABEL[hz], v: (st.by_horizon || {})[hz] })),
  ];
  const cell = (v, fn) => (v && v.closed >= MIN_ROW ? fn(v) : h('span', { class: 'muted' }, v && v.closed ? fn(v) : F.NA));
  return card({
    title: 'By market and by horizon',
    info: `Rows with fewer than ${MIN_ROW} finished ideas are greyed out: too few to read anything into.`,
    flush: true,
    body: h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Group'), h('th', { class: 'r', scope: 'col' }, 'Finished'), h('th', { class: 'r', scope: 'col' }, 'Won'), h('th', { class: 'r', scope: 'col' }, 'Average result'))),
      h('tbody', null, ...rows.map(r => h('tr', null,
        h('td', null, r.label),
        h('td', { class: 'r' }, F.int(r.v ? r.v.closed : 0)),
        h('td', { class: 'r' }, cell(r.v, v => F.frac(v.win))),
        h('td', { class: 'r' }, cell(r.v, v => F.r(v.avg_r)))))))),
  });
}

// Running total of results, oldest first. Several ideas finishing on one day collapse to that day's last total.
function runningTotal(rows) {
  const asc = rows.filter(r => F.isNum(r.r) && r.closed).slice().sort(by('closed', 1));
  let cum = 0;
  return dedupeSeries(asc.map(r => { cum += r.r; return [r.closed, Math.round(cum * 1000) / 1000]; }));
}

function totalChart(ctx, pts, shown, total) {
  const box = h('div', { class: 'chartbox ev-total', role: 'group', 'aria-label': `Running total of results in R across ${shown} finished ideas, ending at ${F.r(pts[pts.length - 1][1])}.` });
  let handle = null;
  const build = () => {
    if (ctx.dead) return;
    handle = lineChart(box, pts, { precision: 1, height: 220 });
    const zero = handle.series.createPriceLine({ price: 0, color: getComputedStyle(document.documentElement).getPropertyValue('--crosshair').trim() || '#888', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false });
    const base = handle.recolor;
    handle.recolor = c => { base(c); zero.applyOptions({ color: c.cross }); };
  };
  requestAnimationFrame(() => requestAnimationFrame(build));
  ctx.cleanups.push(() => { if (handle) handle.destroy(); });
  return card({
    title: 'Running total of results',
    info: 'Each finished idea adds its result in R to the total. A line that drifts up means the ideas made more than they risked, after costs. It starts at zero.',
    body: h('div', { class: 'stack tight' }, box,
      total > shown ? h('div', { class: 'muted fs-xs' }, `The latest ${shown} of ${F.int(total)} finished ideas.`) : null),
  });
}

function idOf(r, idx) {
  const row = idx.get(r.id);
  return { row, sym: row ? row.sym : String(r.id).split(':').slice(1).join(':') || r.id, mk: marketOf(r.id) };
}
function nameCell(r, idx) {
  const { row, sym, mk } = idOf(r, idx);
  return h('td', null,
    h('div', { class: 'scr-id' }, h('div', { class: 'scr-id-t' },
      h('a', { class: 'sym', ...linkProps('/i/' + r.id, { h: r.horizon }) }, sym), MARKET_LABEL[mk] ? mkChip(mk) : null),
    row ? h('div', { class: 'nm', title: row.name }, row.name) : null));
}
const sideChip = d => (d === 'long' ? chip('Long', 'up', { icon: 'up' }) : chip('Short', 'down', { icon: 'down' }));

// One finished or followed idea as a small card, for phones where a wide table would scroll sideways.
function ideaRow(r, idx, right, lines) {
  const { row, sym, mk } = idOf(r, idx);
  return h('div', { class: 'ev-crow' },
    h('div', { class: 'ev-crow-top' },
      h('div', { class: 'ev-crow-id' },
        h('div', { class: 'row' }, h('a', { class: 'sym', ...linkProps('/i/' + r.id, { h: r.horizon }) }, sym), MARKET_LABEL[mk] ? mkChip(mk) : null),
        row ? h('div', { class: 'ev-crow-sub', title: row.name }, row.name) : null),
      h('div', { class: 'ev-crow-r' }, right)),
    h('div', { class: 'row wrap' }, sideChip(r.dir), hzChip(r.horizon)),
    ...lines.map(l => h('div', { class: 'ev-crow-line' }, ...l)));
}
const kvs = (k, v) => h('span', null, h('span', { class: 'muted' }, k + ' '), v);

function openTable(rows, idx) {
  if (!rows.length) return card({ title: 'Ideas being followed', body: empty('Nothing is being followed yet', 'The ledger records an idea on the day it becomes actionable.', 'clock') });
  const LIMIT = 12;
  const st = { m: 'all', all: false };
  const host = h('div');
  const count = m => rows.filter(r => marketOf(r.id) === m).length;
  const marketsHere = MARKETS.filter(m => count(m));
  const filter = marketsHere.length > 1
    ? h('div', { class: 'ev-toolbar' }, seg([{ value: 'all', label: `All ${rows.length}` }, ...marketsHere.map(m => ({ value: m, label: `${MARKET_LABEL[m]} ${count(m)}` }))], st.m, v => { st.m = v; st.all = false; draw(); }, { sm: true, label: 'Market' }))
    : null;
  const statusChip = r => (r.status === 'filled' ? chip('In trade', 'accent') : chip('Waiting', 'outline', { icon: 'clock', title: 'The price has not reached the entry yet' }));
  function draw() {
    const f = rows.filter(r => st.m === 'all' || marketOf(r.id) === st.m);
    const part = st.all ? f : f.slice(0, LIMIT);
    replace(host,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fixed-first ev-open' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, 'Idea'), h('th', { scope: 'col' }, 'Horizon'), h('th', { scope: 'col' }, 'Side'), h('th', { scope: 'col' }, 'Issued'),
          h('th', { scope: 'col' }, 'Status'), h('th', { class: 'r', scope: 'col' }, 'Entry'), h('th', { class: 'r', scope: 'col' }, 'Stop'), h('th', { class: 'r', scope: 'col' }, 'Target 1'), h('th', { class: 'r', scope: 'col' }, 'Result now'))),
        h('tbody', null, ...part.map(r => {
          const px = x => F.pxr(x, r.entry, marketOf(r.id));
          return h('tr', null,
            nameCell(r, idx), h('td', null, hzChip(r.horizon)), h('td', null, sideChip(r.dir)), h('td', null, F.date(r.issued)), h('td', null, statusChip(r)),
            h('td', { class: 'r' }, px(r.entry)), h('td', { class: 'r' }, px(r.stop)), h('td', { class: 'r' }, px(r.target1)),
            h('td', { class: 'r' }, r.status === 'filled' && F.isNum(r.r_now) ? chgSpan(r.r_now, 2, { text: F.r(r.r_now) }) : h('span', { class: 'muted' }, F.NA)));
        })))),
      h('div', { class: 'ev-cards' }, ...part.map(r => {
        const px = x => F.pxr(x, r.entry, marketOf(r.id));
        return ideaRow(r, idx,
          h('div', { class: 'stack tight ev-end' }, statusChip(r), r.status === 'filled' && F.isNum(r.r_now) ? chgSpan(r.r_now, 2, { text: F.r(r.r_now) }) : null),
          [[kvs('Issued', F.date(r.issued))], [kvs('Entry', px(r.entry)), kvs('Stop', px(r.stop)), kvs('Target 1', px(r.target1))]]);
      })),
      f.length > LIMIT ? h('div', { class: 'ev-more' }, h('button', { class: 'btn sm', type: 'button', onclick: () => { st.all = !st.all; draw(); } }, st.all ? 'Show fewer' : `Show all ${f.length}`)) : null);
  }
  draw();
  return card({
    title: 'Ideas being followed', flush: true,
    info: 'Ideas issued and not finished. “Waiting” means the price has not reached the entry yet; the idea expires if it does not get there in time. “Result now” is the open result in R at the latest close.',
    body: h('div', null, filter, host),
  });
}

function closedTable(rows, idx) {
  const STEP = 25;
  const st = { n: STEP };
  const host = h('div');
  const res = r => chgSpan(r.r, 2, { text: F.r(r.r) });
  function draw() {
    const part = rows.slice(0, st.n);
    replace(host,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fixed-first ev-open' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, 'Idea'), h('th', { scope: 'col' }, 'Horizon'), h('th', { scope: 'col' }, 'Side'), h('th', { scope: 'col' }, 'Issued'),
          h('th', { scope: 'col' }, 'Finished'), h('th', { class: 'r', scope: 'col' }, 'Result'), h('th', { scope: 'col' }, 'How it ended'))),
        h('tbody', null, ...part.map(r => h('tr', null,
          nameCell(r, idx), h('td', null, hzChip(r.horizon)), h('td', null, sideChip(r.dir)), h('td', null, F.date(r.issued)),
          h('td', null, F.date(r.closed)), h('td', { class: 'r' }, res(r)), h('td', null, REASON[r.reason] || r.reason || F.NA)))))),
      h('div', { class: 'ev-cards' }, ...part.map(r => ideaRow(r, idx, res(r),
        [[kvs('Issued', F.date(r.issued)), kvs('Finished', F.date(r.closed))], [h('span', null, REASON[r.reason] || r.reason || F.NA)]]))),
      rows.length > STEP ? h('div', { class: 'ev-more' },
        h('span', { class: 'muted fs-xs' }, `Showing the latest ${F.int(Math.min(st.n, rows.length))} of ${F.int(rows.length)}`),
        st.n < rows.length ? h('button', { class: 'btn sm', type: 'button', onclick: () => { st.n += STEP; draw(); } }, `Show ${Math.min(STEP, rows.length - st.n)} more`) : null,
        st.n > STEP ? h('button', { class: 'btn sm ghost', type: 'button', onclick: () => { st.n = STEP; draw(); } }, 'Show fewer') : null) : null);
  }
  draw();
  return card({ title: 'Finished ideas', flush: true, body: host });
}

/* ======================================================================= historical test */
function testSection(ctx, route) {
  const { cal, pools } = ctx;
  const sec = h('section', { class: 'stack' });
  const per = Array.isArray(cal.period) && cal.period.length === 2 ? cal.period : null;
  sec.append(sectionTitle('Historical test', null,
    `The plan rules were run bar by bar over every instrument’s history${per ? ` (${F.date(per[0], { year: true })} to ${F.date(per[1], { year: true })})` : ''}, with costs, then pooled by market and horizon so there are enough trades to measure anything. No rule was tuned on this data.`));
  if (!pools.length) { sec.append(card({ body: empty('No historical test in this build', 'The latest build did not produce a calibration file.', 'clock') })); return sec; }

  const q = route.query || {};
  let sel = pools.find(x => x.m === q.pm && x.hz === q.ph) || pools.find(x => x.key === 'psx:medium') || pools[0];
  const tableHost = h('div');
  const detailHost = h('div', { class: 'stack ev-detail' });

  function choose(x, user) {
    sel = x;
    replaceQuery({ pm: x.m, ph: x.hz });
    drawTable();
    drawDetail();
    if (user) detailHost.scrollIntoView({ block: 'nearest', behavior: window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  function drawTable() {
    replace(tableHost, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl ev-pools' },
      h('thead', null, h('tr', null,
        h('th', { scope: 'col' }, 'Market and horizon'),
        h('th', { scope: 'col', class: 'hide-sm' }, 'Conviction check'),
        h('th', { class: 'r hide-sm', scope: 'col' }, 'Trades'),
        h('th', { class: 'r hide-sm', scope: 'col' }, 'Won'),
        h('th', { class: 'r', scope: 'col' }, 'Average result'),
        h('th', { class: 'r', scope: 'col' }, 'Against random entries'))),
      h('tbody', null, ...pools.map(x => {
        const stt = statusOf(x.p);
        const v = edgeVerdict(x.p);
        const on = x === sel;
        return h('tr', { class: ['link', on && 'sel'], onclick: e => { if (!e.target.closest('button')) choose(x, true); } },
          h('td', null, h('button', { class: 'ev-pick', type: 'button', 'aria-pressed': String(on), 'aria-label': `${x.name}, show details`, onclick: () => choose(x, true) }, mkChip(x.m), h('span', null, HZ_LABEL[x.hz])),
            h('div', { class: 'ev-st-sm' }, chip(stt.label, stt.tone, { icon: stt.icon }))),
          h('td', { class: 'hide-sm' }, chip(stt.label, stt.tone, { icon: stt.icon })),
          h('td', { class: 'r hide-sm' }, F.int(x.p.n_trades)),
          h('td', { class: 'r hide-sm' }, F.isNum(x.p.win) ? F.frac(x.p.win) : F.NA),
          h('td', { class: 'r' }, F.isNum(x.p.avg_r) ? F.r(x.p.avg_r) : F.NA),
          h('td', { class: 'r' }, v.k === 'unknown' ? h('span', { class: 'muted' }, F.NA)
            : v.k === 'untested' ? h('span', { class: 'muted ev-noise', title: v.why || '' }, 'too little history')
              : h('span', { class: 'ev-edge-cell' }, F.r(x.p.edge_r), v.k === 'clear' ? chip('Clear', 'up') : v.k === 'worse' ? chip('Worse', 'down') : h('span', { class: 'muted ev-noise' }, v.label === 'Not confirmed' ? 'not confirmed' : 'noise'))));
      })))));
  }
  function drawDetail() { replace(detailHost, poolDetail(ctx, sel)); }

  drawTable();
  drawDetail();
  sec.append(card({
    title: 'Every market and horizon',
    info: 'A pool is all trades of one market and one horizon. “Against random entries” is the plan’s average result minus the average of random entries with the same stops, targets and costs; “Clear” needs the whole 95% range above zero, “noise” means luck cannot be ruled out. Click a row for the detail.',
    flush: true, body: tableHost,
  }), detailHost);
  return sec;
}

function poolDetail(ctx, x) {
  const { p } = x;
  const stt = statusOf(p);
  const per = Array.isArray(ctx.cal.period) && ctx.cal.period.length === 2 ? ctx.cal.period : null;
  const kids = [];
  kids.push(h('div', { class: 'ev-pool-h' },
    h('div', { class: 'row wrap' }, mkChip(x.m), h('h3', { class: 'ev-pool-t' }, HZ_LABEL[x.hz]), h('span', { class: 'muted fs-sm' }, HZ_SPAN[x.hz])),
    chip(stt.label, stt.tone, { icon: stt.icon })));
  if (p.note) kids.push(banner(p.status === 'unvalidated' ? 'warn' : 'info', p.note));
  if (!p.n_trades) { kids.push(card({ body: empty('No trades in this pool', 'The rules produced no trades in the test window.', 'clock') })); return h('div', { class: 'stack' }, ...kids); }

  const eta = p.eta_bars && Array.isArray(p.eta_bars.t1) ? p.eta_bars.t1 : null;
  kids.push(card({
    body: h('div', { class: 'stack' },
      h('div', { class: 'stats ev-stats' },
        stat('Trades', F.int(p.n_trades), per ? `${F.date(per[0], { year: true })} to ${F.date(per[1], { year: true })}` : null),
        stat('Won', F.frac(p.win), 'Finished above zero, after costs'),
        stat('Average result', F.r(p.avg_r), 'Per trade, after costs', { info: 'R is the amount risked on a trade: the distance from entry to stop. +0.20R means a trade made a fifth of what it risked on average.' }),
        stat('Reached target 1', F.frac(p.hit1), 'Before the stop'),
        stat('Reached target 2', F.frac(p.hit2), 'Before the stop'),
        stat('Time to target 1', eta ? `${eta[0]}–${eta[1]} days` : F.NA, p.eta_bars && p.eta_bars.empirical ? 'Middle half of trades that got there' : 'Assumed: too few trades to measure', { small: true, info: 'Daily bars from entry to target 1, for trades that reached it. The range runs from the quarter that got there fastest to the quarter that took longest.' })),
      F.isNum(p.close_basis_share) && p.close_basis_share > 0.02
        ? h('div', { class: 'muted fs-xs' }, `In ${F.frac(p.close_basis_share)} of these trades the stop and targets were tested on closing prices only, because highs and lows were not available. Intraday moves can make those results look better or worse than they were.`) : null),
  }));

  kids.push(h('div', { class: 'grid c2 top' }, randomCard(p), rankCard(p)));
  kids.push(bucketCard(p));
  return h('div', { class: 'stack loose' }, ...kids);
}

function randomCard(p) {
  const v = edgeVerdict(p);
  const body = [];
  const pool = p.edge || {};
  if (v.k === 'unknown') {
    body.push(h('p', { class: 'muted' }, p.baseline ? 'The comparison could not be computed for this pool.' : 'There were too few random-entry trades to compare with.'));
  } else if (v.k === 'untested') {
    body.push(h('div', null, chip(v.label, v.tone, { icon: 'info' })),
      h('p', { class: 'ev-p' }, `This pool cannot be tested yet: ${v.why}. Until it can, ideas from it are research candidates and none is marked actionable.`),
      p.baseline ? h('dl', { class: 'kv' },
        h('dt', null, 'Acumen plan'), h('dd', null, `${F.r(p.avg_r)} · ${F.frac(p.win)} won · ${F.int(p.n_trades)} trades`),
        h('dt', null, 'Random entries'), h('dd', null, `${F.r(p.baseline.avg_r)} · ${F.frac(p.baseline.win)} won · ${F.int(p.baseline.n)} trades`)) : null);
  } else {
    const e = p.edge_r;
    const lvl = pct0(v.level || 0.95);
    const range = `${F.r(v.lo)} to ${F.r(v.hi)}`;
    const recent = F.isNum(pool.recent_r) ? ` In the newest 40% of trades the difference was ${F.r(pool.recent_r)}.` : '';
    const text = v.k === 'clear'
      ? `The plan did better than random entries by ${F.r(e)} per trade (${lvl}% range ${range}), and the newer trades agree.${recent} That is more than chance alone explains. The range allows for trades that overlap in time and for each instrument\u2019s own luck, but not for every bias (the list of stocks only holds companies that still trade, and Acumen examines several pools at once), so treat it as encouraging rather than proven.`
      : v.k === 'worse'
        ? `The plan did worse than random entries by ${F.r(-e)} per trade (${lvl}% range ${range}). Ideas from this pool are research candidates only.`
        : v.label === 'Not confirmed'
          ? `The plan beat random entries by ${F.r(e)} per trade over the whole history (${lvl}% range ${range}), but ${pool.why || 'the newer trades do not confirm it'}. Ideas from this pool stay research candidates.`
          : `The plan averaged ${F.r(Math.abs(e))} per trade ${e >= 0 ? 'above' : 'below'} random entries, but the ${lvl}% range (${range}) includes zero, so luck cannot be ruled out. Part of the result may be the market drifting. Ideas from this pool stay research candidates.`;
    body.push(h('div', null, chip(v.label, v.tone, { icon: v.k === 'clear' ? 'check' : v.k === 'worse' ? 'down' : 'info' })),
      h('p', { class: 'ev-p' }, text),
      edgeChart({ edge: e, lo: v.lo, hi: v.hi, level: v.level || 0.95, kind: v.k, strat: { avg_r: p.avg_r, n: p.n_trades }, base: p.baseline, width: narrow() ? 380 : 520 }),
      h('dl', { class: 'kv' },
        F.isNum(pool.plan_r) ? h('dt', null, 'Plan, before costs') : null,
        F.isNum(pool.plan_r) ? h('dd', null, `${F.r(pool.plan_r)} per trade · ${F.int(pool.matched)} of ${F.int(pool.n)} trades compared`) : null,
        F.isNum(pool.base_r) ? h('dt', null, 'Random entries, same days') : null,
        F.isNum(pool.base_r) ? h('dd', null, `${F.r(pool.base_r)} per trade · same direction, opened on the trade\u2019s day or the next ${F.int(pool.match_days)}`) : null,
        h('dt', null, 'Plan, after costs'), h('dd', null, `${F.r(p.avg_r)} · ${F.frac(p.win)} won · ${F.int(p.n_trades)} trades`),
        h('dt', null, 'All random entries'), h('dd', null, `${F.r(p.baseline.avg_r)} · ${F.frac(p.baseline.win)} won · ${F.int(p.baseline.n)} trades`),
        F.isNum(pool.spans) ? h('dt', null, 'Independent evidence') : null,
        F.isNum(pool.spans) ? h('dd', null, `${F.int(pool.instruments)} instruments · about ${F.num(pool.spans, 0)} separate stretches of ${F.int(pool.lag_days)} days`) : null,
        F.isNum(pool.p) ? h('dt', null, 'Chance of this by luck') : null, F.isNum(pool.p) ? h('dd', null, `${pool.p < 0.001 ? 'under 0.1' : F.num(pool.p * 100, pool.p < 0.1 ? 1 : 0)}% (clear needs under ${F.num((pool.alpha || 0.01) * 100, 0)}%)`) : null));
  }
  return card({
    title: 'Against random entries',
    info: 'Random entries use the same instruments and the same stops and targets, but enter on days picked without looking at any signal (long only where short selling is not offered). Each plan trade is compared, before costs, with the random entries opened in the same direction on its own day or the next few days, so what the market did cancels out and only what the plan added is left. The range allows for trades that overlap in time (they share price moves) and for each instrument\u2019s own luck. If the plan cannot beat them clearly enough to rule out luck, the result is mostly the market drifting up or down, and the pool cannot produce actionable ideas.',
    body: h('div', { class: 'stack' }, ...body),
  });
}

function rankCard(p) {
  const o = p.oos || {};
  const title = 'Does higher conviction mean better results?';
  const info = 'The curve is fitted on the older 60% of the trades and then asked to rank the newer 40%, which it has not seen. Passing needs all three marks below. If it does not pass, Acumen ignores the score and uses the pool’s average instead.';
  if (!F.isNum(o.spearman)) {
    return card({ title, info, body: h('div', { class: 'stack' }, chip('Not tested', 'outline', { icon: 'info' }), h('p', { class: 'ev-p' }, 'This pool has too few trades to hold back a newer slice for the check, so conviction is not used as a probability here. The win chance shown on ideas is the pool average.')) });
  }
  const ci = Array.isArray(o.rho_ci) ? o.rho_ci : null;
  const tests = [
    { ok: o.spearman > 0, t: 'Higher conviction went with higher results', v: `rank correlation ${F.num(o.spearman, 2, { sign: true })}${ci ? `, 90% range ${F.num(ci[0], 2, { sign: true })} to ${F.num(ci[1], 2, { sign: true })}` : ''}` },
    { ok: F.isNum(o.p) && o.p < 0.05, t: 'Unlikely to be luck', v: `p = ${F.num(o.p, 3)} when whole months are re-sampled (needs below 0.05)` },
    { ok: F.isNum(o.top_half_avg_r) && F.isNum(o.bottom_half_avg_r) && o.top_half_avg_r > o.bottom_half_avg_r, t: 'Higher-conviction half beat the lower half', v: `${F.r(o.top_half_avg_r)} against ${F.r(o.bottom_half_avg_r)} per trade` },
  ];
  const sentence = o.passed
    ? `On the ${F.int(o.n)} newest trades, those with higher conviction did better than those with lower conviction, and the gap is unlikely to be luck. Acumen therefore uses the conviction curve for this pool.`
    : `On the ${F.int(o.n)} newest trades, higher conviction did not reliably mean better results. Acumen therefore ignores the score here and shows the pool average for every idea.`;
  return card({
    title, info,
    body: h('div', { class: 'stack' },
      h('div', null, o.passed ? chip('Passed', 'up', { icon: 'check' }) : chip('Not passed', 'warn', { icon: 'alert' })),
      h('p', { class: 'ev-p' }, sentence),
      h('ul', { class: 'ev-tests' }, ...tests.map(x => h('li', { class: x.ok ? 'ok' : 'no' },
        icon(x.ok ? 'checkCircle' : 'xCircle'),
        h('div', null, h('div', { class: 'ev-test-t' }, x.t, h('span', { class: 'sr-only' }, x.ok ? ': passed' : ': not passed')), h('div', { class: 'muted fs-xs' }, x.v)))))),
  });
}

// Probability the app shows at a given conviction, read off the stored curve.
function mapAt(pairs, c) {
  if (!Array.isArray(pairs) || pairs.length < 2) return null;
  if (c <= pairs[0][0]) return pairs[0][1];
  for (let k = 1; k < pairs.length; k++) {
    if (c <= pairs[k][0]) { const [x0, y0] = pairs[k - 1], [x1, y1] = pairs[k]; return y0 + ((y1 - y0) * (c - x0)) / (x1 - x0 || 1); }
  }
  return pairs[pairs.length - 1][1];
}

function bucketCard(p) {
  const flat = p.status !== 'calibrated';
  const note = flat ? 'Win chance Acumen uses (flat: the score is not trusted here)' : 'Win chance Acumen uses';
  const tbl = h('details', { class: 'price-table' }, h('summary', null, 'Show the numbers as a table'),
    h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, ['Conviction', 'Trades', 'Won', '95% range', 'Average result'].map((c, i) => h('th', { class: i ? 'r' : null, scope: 'col' }, c)))),
      h('tbody', null, ...p.buckets.map(b => h('tr', null,
        h('td', null, `${b.lo}–${b.hi}`), h('td', { class: 'r' }, F.int(b.n)), h('td', { class: 'r' }, F.frac(b.win, 1)),
        h('td', { class: 'r' }, b.ci ? `${F.frac(b.ci[0])}–${F.frac(b.ci[1])}` : F.NA), h('td', { class: 'r' }, F.r(b.avg_r))))))));
  const lo = mapAt(p.mapping, 40), hi = mapAt(p.mapping, 90);
  const reading = flat
    ? `The bars do not climb reliably with conviction, so Acumen does not follow them. Every idea in this pool is shown with the pool average win chance (${F.frac(p.win)}), whatever its score.`
    : `Higher conviction went with a higher win rate here, and the line follows it: an idea at conviction 40 is shown with a ${F.frac(lo)} win chance, one at conviction 90 with ${F.frac(hi)}.`;
  return card({
    title: 'Conviction against results',
    info: 'Trades are grouped by their conviction at entry. Each bar is the share of that group that finished above zero, with its 95% range. The black line is the win chance Acumen would show for an idea at that conviction. When the bars do not climb, or the check above failed, the line is flat on purpose.',
    body: p.buckets.length
      ? h('div', { class: 'ev-bk' },
        bucketChart(p.buckets, { base: p.win, curve: p.mapping, curveNote: note, width: narrow() ? 380 : 640, height: narrow() ? 230 : 250 }),
        h('div', { class: 'stack' },
          h('p', { class: 'ev-p' }, reading),
          h('p', { class: 'ev-p muted' }, 'Groups with few trades jump around by chance, which is why their 95% ranges are wide. Before the line is drawn, thin groups are pulled toward the pool average so a handful of lucky trades cannot read as a certainty.'),
          tbl))
      : empty('No conviction groups', 'There were too few trades above the minimum conviction to group.', 'clock'),
  });
}

/* ======================================================================= limits */
const ALWAYS = [
  'Markets change. A method that worked in one period can stop working, and a long test is not a promise about the next year.',
  'Prices come from free public sources. They can be late, revised or wrong; the app shows how old each market’s data is but cannot catch every error.',
];

const GLOSSARY = [
  ['R', 'One R is the amount risked on a trade: the distance from entry to stop. +1R means the trade made as much as it risked; −1R means it lost the whole planned risk. All results are after costs.'],
  ['Conviction', 'A score from 0 to 100 that blends trend, momentum, volume, volatility, valuation (where available) and the market regime. It ranks ideas. It becomes a probability only in pools that pass the out-of-sample check.'],
  ['Pool', 'All trades of one market and one horizon, combined so there are enough of them to measure anything.'],
  ['Validated', 'The conviction curve was fitted on the older 60% of a pool’s trades and then ranked the newer 40% correctly: positive rank correlation, unlikely to be luck when whole months are re-sampled, and the higher half beat the lower half.'],
  ['Random entries', 'The same instruments, stops, targets and costs, but entering on random days (long only where short selling is not offered). It shows how much of a result is simply the market drifting.'],
  ['Within noise', 'The difference is smaller than the random variation in the data, so the test cannot tell skill from luck.'],
  ['Optimistic ranges', 'Trades opened in the same weeks are not independent, because instruments move together. That makes every 95% range narrower than it should be.'],
];

function limitsSection(ctx) {
  const items = [...(ctx.cal.caveats || []), ...ALWAYS];
  return h('section', { class: 'stack' },
    sectionTitle('What this evidence cannot tell you', null, 'These limits apply to every number on this screen and to the probabilities shown on ideas.'),
    card({ body: h('ul', { class: 'ev-limits' }, ...items.map(t => h('li', null, t))) }),
    h('details', { class: 'card ev-gloss' },
      h('summary', null, h('span', { class: 'label' }, 'How to read these numbers')),
      h('dl', { class: 'ev-dl' }, ...GLOSSARY.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]))),
    h('p', { class: 'muted fs-sm ev-disclaimer' }, 'Everything Acumen shows is an estimate from a model and past prices. It is information, not advice, and not a promise of future results. You decide what to trade and you carry the risk.'));
}
