import { h, s } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { hoverTip, tipRows } from './charts.js';

// Charts and labels for the Evidence screen. Everything here is plain SVG/HTML so it prints, scales and works offline.

export const Z = 1.96;

// Result of the out-of-sample check, as the engine reports it (calibration.json: status).
export const STATUS = {
  calibrated: { label: 'Validated', tone: 'up', icon: 'check' },
  unvalidated: { label: 'Not validated', tone: 'warn', icon: 'alert' },
  insufficient: { label: 'Too few trades', tone: 'outline', icon: 'info' },
};
export const statusOf = p => STATUS[p && p.status] || STATUS.insufficient;

// How the plan compares with random entries that use the same stops, targets and costs. The engine runs the test
// (calibration.json: edge): each plan trade is compared, before costs, with random entries opened in the same direction on its
// own day or the next few days, so the market's own moves cancel. The range allows for trades that overlap in time and for each
// instrument's own luck (the engine's Newey-West and instrument-cluster estimate), and a pool only counts as "clear" when the
// whole range is above zero and the newer trades agree. Only a clear pool may produce actionable ideas.
export const pct0 = x => Math.round(x * 100);
export function edgeVerdict(p) {
  const e = p && p.edge;
  if (e && e.tested && Array.isArray(e.ci) && F.isNum(e.edge_r)) {
    const [lo, hi] = e.ci;
    const base = { lo, hi, level: e.level, why: e.why };
    if (e.passed) return { k: 'clear', label: 'Clear edge', tone: 'up', ...base };
    if (hi < 0) return { k: 'worse', label: 'Worse than random', tone: 'down', ...base };
    return { k: 'noise', label: lo > 0 ? 'Not confirmed' : 'Within noise', tone: 'outline', ...base };
  }
  if (e && !e.tested && e.why) return { k: 'untested', label: 'Too little history', tone: 'outline', why: e.why };
  // data files written before the engine ran this test: the plain comparison, trades treated as independent
  if (!p || !F.isNum(p.edge_r) || !F.isNum(p.edge_se) || !p.baseline) return { k: 'unknown', label: 'Not measured', tone: 'outline' };
  const lo = p.edge_r - Z * p.edge_se, hi = p.edge_r + Z * p.edge_se;
  if (lo > 0) return { k: 'clear', label: 'Clear edge', tone: 'up', lo, hi, level: 0.95 };
  if (hi < 0) return { k: 'worse', label: 'Worse than random', tone: 'down', lo, hi, level: 0.95 };
  return { k: 'noise', label: 'Within noise', tone: 'outline', lo, hi, level: 0.95 };
}

const NICE = [0.06, 0.1, 0.2, 0.4, 0.6, 0.8, 1, 2, 4, 6, 8, 10, 20];
const niceCeil = x => NICE.find(v => v >= x) ?? Math.ceil(x);

// A single estimate with its range on a symmetric axis around zero ("same as random entries").
// The mark is coloured by the verdict but the verdict is always written next to the chart as well.
export function edgeChart({ edge, lo, hi, level = 0.95, kind = 'noise', strat, base, width = 520 }) {
  const rng = `${pct0(level)}% range`;
  const A = niceCeil(Math.max(Math.abs(lo), Math.abs(hi), 0.08) * 1.1);
  const W = width, H = 100, L = 26, R = 26, yW = 52, yA = 76;
  const X = x => L + ((x + A) / (2 * A)) * (W - L - R);
  const col = kind === 'clear' ? 'var(--up)' : kind === 'worse' ? 'var(--down)' : 'var(--ink-2)';
  const ticks = [-A, -A / 2, 0, A / 2, A];
  const dec = [0, 1, 2, 3].find(d => ticks.every(t => Math.abs(t * 10 ** d - Math.round(t * 10 ** d)) < 1e-9)) ?? 2;
  const tick = x => (Math.abs(x) < 1e-9 ? '0' : F.num(x, dec, { sign: true }));
  const label = `Difference against random entries: ${F.r(edge)} per trade, ${pct0(level)} percent range ${F.r(lo)} to ${F.r(hi)}.`;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'bk ev-edge', role: 'group', 'aria-label': label });
  svg.append(s('line', { x1: X(-A), x2: X(A), y1: yA, y2: yA, class: 'ev-axis' }));
  for (const t of ticks) {
    svg.append(s('line', { x1: X(t), x2: X(t), y1: yA, y2: yA + 4, class: 'ev-axis' }),
      s('text', { x: X(t), y: yA + 17, class: 'bk-ax', 'text-anchor': 'middle' }, tick(t)));
  }
  svg.append(s('line', { x1: X(0), x2: X(0), y1: 20, y2: yA, class: 'ev-zero' }));
  svg.append(s('line', { x1: X(lo), x2: X(hi), y1: yW, y2: yW, class: 'ev-whisk', stroke: col }),
    s('line', { x1: X(lo), x2: X(lo), y1: yW - 6, y2: yW + 6, class: 'ev-whisk', stroke: col }),
    s('line', { x1: X(hi), x2: X(hi), y1: yW - 6, y2: yW + 6, class: 'ev-whisk', stroke: col }),
    s('circle', { cx: X(edge), cy: yW, r: 6, class: 'ev-dot', fill: col }));
  const lx = Math.max(L + 24, Math.min(W - R - 24, X(edge)));
  svg.append(s('text', { x: lx, y: yW - 14, class: 'ev-val', 'text-anchor': 'middle' }, F.r(edge)));
  const hit = s('rect', { x: 0, y: 0, width: W, height: H, fill: 'transparent', tabindex: '0' });
  hoverTip(hit, () => tipRows('Against random entries', [
    ['Difference per trade', F.r(edge)], [rng.replace(/^./, c => c.toUpperCase()).replace('range', 'range'), `${F.r(lo)} to ${F.r(hi)}`],
    strat ? ['Acumen plan', `${F.r(strat.avg_r)} · ${F.int(strat.n)} trades`] : null,
    base ? ['Random entries', `${F.r(base.avg_r)} · ${F.int(base.n)} trades`] : null]));
  svg.append(hit);
  return h('div', { class: 'bk-wrap' }, svg,
    h('div', { class: 'ev-cap' }, h('span', null, '← Worse than random entries'), h('span', null, 'Better than random entries →')));
}
