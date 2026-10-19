import { h, s } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { icon } from '../lib/icons.js';
import { showPop, hidePop } from './kit.js';

/* ---------- colour helpers ---------- */
// Diverging colour for a signed value; intensity caps at 62% so ink stays readable on top in both themes.
export function divColor(v, maxAbs) {
  if (!F.isNum(v) || !maxAbs) return 'var(--surface-2)';
  const t = Math.min(1, Math.abs(v) / maxAbs);
  const pct = Math.round(8 + t * 54);
  return `color-mix(in srgb, ${v >= 0 ? 'var(--up)' : 'var(--down)'} ${pct}%, var(--mid))`;
}
export function seqColor(t) {
  const steps = ['var(--seq-1)', 'var(--seq-2)', 'var(--seq-3)', 'var(--seq-4)', 'var(--seq-5)', 'var(--seq-6)'];
  return steps[Math.max(0, Math.min(steps.length - 1, Math.floor(t * steps.length)))];
}

/* ---------- hover tooltips ---------- */
export function hoverTip(el, build) {
  el.addEventListener('pointermove', e => { showPop({ x: e.clientX, y: e.clientY }, build(), { follow: true }); });
  el.addEventListener('pointerleave', hidePop);
  el.addEventListener('focus', () => { const r = el.getBoundingClientRect(); showPop({ x: r.left + r.width / 2, y: r.top }, build(), { follow: true }); });
  el.addEventListener('blur', hidePop);
}
export const tipRows = (title, rows) => h('div', null, title ? h('div', null, h('b', null, title)) : null, ...rows.filter(Boolean).map(([k, v]) => h('div', { class: 'pr' }, h('span', null, k), h('span', null, v))));

/* ---------- horizontal bars (signed or plain) ---------- */
export function hbars(items, { fmt = v => F.num(v, 1), signed = false, max, labelW = 96, tone } = {}) {
  const m = max ?? Math.max(...items.map(i => Math.abs(i.value ?? 0)), 1e-9);
  return h('div', { class: ['hbars', signed && 'signed'], role: 'list' },
    ...items.map(it => {
      const v = it.value;
      const w = F.isNum(v) ? Math.min(100, (Math.abs(v) / m) * (signed ? 50 : 100)) : 0;
      const pos = !F.isNum(v) || v >= 0;
      const fill = h('div', { class: ['hb-fill', signed ? (pos ? 'pos' : 'neg') : (tone || 'acc')], style: signed ? { width: w + '%', left: pos ? '50%' : (50 - w) + '%' } : { width: w + '%', background: it.color || null } });
      const row = h('div', { class: 'hb-row', role: 'listitem', tabindex: it.tip ? '0' : null },
        h('div', { class: 'hb-l', style: { width: labelW + 'px' }, title: it.label }, it.label),
        h('div', { class: 'hb-track' }, signed ? h('div', { class: 'hb-zero' }) : null, fill),
        h('div', { class: ['hb-v', signed && (pos ? 'up' : 'down')] }, it.text ?? fmt(v)));
      if (it.tip) hoverTip(row, () => it.tip);
      if (it.href) { row.classList.add('link'); row.addEventListener('click', () => it.href()); }
      return row;
    }));
}

/* ---------- stacked proportion bar (breadth, allocation) ---------- */
export function stackBar(parts, { height = 10, labels = true } = {}) {
  const total = parts.reduce((a, p) => a + (p.value || 0), 0) || 1;
  const wrap = h('div', { class: 'stackbar-wrap' },
    h('div', { class: 'stackbar', style: { height: height + 'px' }, role: 'img', 'aria-label': parts.map(p => `${p.label} ${F.num(p.value, 0)}`).join(', ') },
      ...parts.filter(p => p.value > 0).map(p => h('div', { class: 'sb-seg', style: { flexGrow: p.value, background: p.color }, title: `${p.label}: ${F.num(p.value, 0)}` }))),
    labels ? h('div', { class: 'sb-legend' }, ...parts.map(p => h('span', { class: 'sb-key' }, h('i', { style: { background: p.color } }), p.label + ' ', h('b', null, p.text ?? F.num(p.value, 0))))) : null);
  void total;
  return wrap;
}

/* ---------- regime gauge: score in [-1, 1] ---------- */
export function gauge(score, { left = 'Bearish', right = 'Bullish' } = {}) {
  const v = F.isNum(score) ? Math.max(-1, Math.min(1, score)) : 0;
  return h('div', { class: 'gauge', role: 'img', 'aria-label': `Score ${F.num(v, 2)} from minus one to plus one` },
    h('div', { class: 'g-track' }, h('div', { class: 'g-mid' }), h('div', { class: 'g-dot', style: { left: ((v + 1) / 2) * 100 + '%' } })),
    h('div', { class: 'g-lab' }, h('span', null, left), h('span', null, right)));
}

/* ---------- heat tiles (sectors, crypto) ---------- */
export function heatTiles(items, { valueKey = 'value', max, fmt = v => F.pct(v, 1), minW = 118, onClick, wrap = false } = {}) {
  const m = max ?? Math.max(...items.map(i => Math.abs(i[valueKey] ?? 0)), 1e-9);
  return h('div', { class: 'heat', style: { '--tile-min': minW + 'px' } },
    ...items.map(it => {
      const v = it[valueKey];
      const t = h('div', { class: ['tile', onClick && 'link'], style: { background: divColor(v, m) }, tabindex: '0', role: onClick ? 'link' : null },
        h('div', { class: ['tl-name', wrap && 'wrap'], title: it.label }, it.label), h('div', { class: 'tl-val' }, fmt(v)), it.sub ? h('div', { class: 'tl-sub' }, it.sub) : null);
      if (it.tip) hoverTip(t, () => it.tip);
      if (onClick) { t.addEventListener('click', () => onClick(it)); t.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(it); } }); }
      return t;
    }));
}

/* ---------- matrix heatmap (fx strength, correlations) ---------- */
export function heatMatrix(rowLabels, colLabels, matrix, { max, fmt = v => F.num(v, 1), corner = '', color, tip } = {}) {
  const m = max ?? Math.max(...matrix.flat().filter(F.isNum).map(Math.abs), 1e-9);
  return h('div', { class: 'tbl-wrap' }, h('table', { class: 'hmx' },
    h('thead', null, h('tr', null, h('th', null, corner || h('span', { class: 'sr-only' }, 'Row')), ...colLabels.map(c => h('th', null, c)))),
    h('tbody', null, ...rowLabels.map((rl, i) => h('tr', null, h('th', { scope: 'row' }, rl),
      ...colLabels.map((cl, j) => {
        const v = matrix[i] && matrix[i][j];
        const td = h('td', { style: { background: i === j ? 'var(--surface-2)' : color ? color(v) : divColor(v, m) } }, i === j ? '' : F.isNum(v) ? fmt(v) : '');
        if (F.isNum(v) && i !== j) { td.setAttribute('tabindex', '0'); hoverTip(td, () => (tip ? tip(rl, cl, v) : tipRows(`${rl} / ${cl}`, [['Value', fmt(v)]]))); }
        return td;
      }))))));
}

/* ---------- treemap (squarified) ---------- */
export function treemap(items, { w = 600, hgt = 280, value = 'area', color } = {}) {
  const nodes = items.filter(i => i[value] > 0).sort((a, b) => b[value] - a[value]);
  const total = nodes.reduce((a, n) => a + n[value], 0);
  const rects = [];
  const layout = (list, x, y, ww, hh) => {
    if (!list.length) return;
    if (list.length === 1) { rects.push({ n: list[0], x, y, w: ww, h: hh }); return; }
    const sum = list.reduce((a, n) => a + n[value], 0);
    let acc = 0, split = 0;
    for (let i = 0; i < list.length; i++) { acc += list[i][value]; if (acc >= sum / 2) { split = i + 1; break; } }
    split = Math.min(Math.max(split, 1), list.length - 1);
    const a = list.slice(0, split), b = list.slice(split);
    const sa = a.reduce((q, n) => q + n[value], 0) / sum;
    if (ww >= hh) { layout(a, x, y, ww * sa, hh); layout(b, x + ww * sa, y, ww * (1 - sa), hh); }
    else { layout(a, x, y, ww, hh * sa); layout(b, x, y + hh * sa, ww, hh * (1 - sa)); }
  };
  layout(nodes, 0, 0, w, hgt);
  void total;
  return h('div', { class: 'treemap', style: { aspectRatio: `${w} / ${hgt}` } },
    ...rects.map(r => {
      const big = r.w > 70 && r.h > 44;
      const el = h('div', { class: ['tm', r.n.href && 'link'], style: { left: (r.x / w) * 100 + '%', top: (r.y / hgt) * 100 + '%', width: (r.w / w) * 100 + '%', height: (r.h / hgt) * 100 + '%' }, tabindex: '0', role: r.n.href ? 'link' : null, 'aria-label': r.n.aria || null },
        h('div', { class: 'tm-in', style: { background: color ? color(r.n) : (r.n.color || 'var(--surface-2)') } },
          r.w > 54 && r.h > 28 ? h('div', { class: 'tm-s' }, r.n.label) : null,
          big ? h('div', { class: 'tm-v' }, r.n.text) : null));
      if (r.n.tip) hoverTip(el, () => r.n.tip);
      if (r.n.href) { el.addEventListener('click', r.n.href); el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.n.href(); } }); }
      return el;
    }));
}

/* ---------- calibration bucket chart: win rate by conviction bucket with confidence bars ---------- */
// `curve` is the probability the app actually uses, as [[conviction, probability], ...]; it is drawn through the bucket centres
// so the reader can see whether the app follows the differences between buckets or ignores them (a flat line).
export function bucketChart(buckets, { base, height = 190, width = 520, title = 'Win rate', curve, curveNote, minN = 10 } = {}) {
  const W = width, H = height, L = 38, R = 8, T = 10, B = 28;
  const n = buckets.length;
  if (!n) return h('div', { class: 'muted' }, 'No trades in this pool yet.');
  const lo = 0, hi = 1;
  const X = i => L + ((i + 0.5) / n) * (W - L - R);
  const bw = Math.min(46, ((W - L - R) / n) * 0.62);
  const Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'bk', role: 'group', 'aria-label': `${title} by conviction bucket` });
  for (const g of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(s('line', { x1: L, x2: W - R, y1: Y(g), y2: Y(g), class: 'bk-grid' }), s('text', { x: L - 6, y: Y(g) + 4, class: 'bk-ax', 'text-anchor': 'end' }, Math.round(g * 100) + '%'));
  }
  if (F.isNum(base)) svg.append(s('line', { x1: L, x2: W - R, y1: Y(base), y2: Y(base), class: 'bk-base' }));
  buckets.forEach((b, i) => {
    const x = X(i);
    const thin = b.n < minN;
    const bar = s('rect', { x: x - bw / 2, y: Y(b.win), width: bw, height: Math.max(1, Y(0) - Y(b.win)), rx: 3, class: ['bk-bar', thin && 'thin'] });
    svg.append(bar);
    if (b.ci) svg.append(s('line', { x1: x, x2: x, y1: Y(b.ci[0]), y2: Y(b.ci[1]), class: ['bk-ci', thin && 'thin'] }), s('line', { x1: x - 4, x2: x + 4, y1: Y(b.ci[0]), y2: Y(b.ci[0]), class: ['bk-ci', thin && 'thin'] }), s('line', { x1: x - 4, x2: x + 4, y1: Y(b.ci[1]), y2: Y(b.ci[1]), class: ['bk-ci', thin && 'thin'] }));
    svg.append(s('text', { x, y: H - 12, class: 'bk-ax', 'text-anchor': 'middle' }, `${b.lo}–${b.hi}`), s('text', { x, y: H - 1, class: 'bk-n', 'text-anchor': 'middle' }, `n=${b.n}`));
    const hit = s('rect', { x: x - (W - L - R) / n / 2, y: T, width: (W - L - R) / n, height: H - T - B, fill: 'transparent', tabindex: '0' });
    hoverTip(hit, () => tipRows(`Conviction ${b.lo}–${b.hi}`, [['Trades', F.int(b.n)], ['Winners', F.frac(b.win, 1)], b.ci ? ['95% range', `${F.frac(b.ci[0])}–${F.frac(b.ci[1])}`] : null, ['Average result', F.r(b.avg_r)], thin ? ['Note', `Fewer than ${minN} trades: too few to read`] : null]));
    svg.append(hit);
  });
  const pairs = Array.isArray(curve) ? curve.filter(q => Array.isArray(q) && F.isNum(q[0]) && F.isNum(q[1])) : [];
  if (pairs.length > 1) {
    const at = c => {
      if (c <= pairs[0][0]) return pairs[0][1];
      for (let k = 1; k < pairs.length; k++) {
        if (c <= pairs[k][0]) { const [x0, y0] = pairs[k - 1], [x1, y1] = pairs[k]; return y0 + ((y1 - y0) * (c - x0)) / (x1 - x0 || 1); }
      }
      return pairs[pairs.length - 1][1];
    };
    const pts = buckets.map((b, i) => [X(i), Y(at((b.lo + Math.min(b.hi, 100)) / 2))]);
    svg.append(s('path', { d: pts.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(''), class: 'bk-curve' }),
      ...pts.map(q => s('circle', { cx: q[0], cy: q[1], r: 3.5, class: 'bk-curve-dot' })));
  }
  return h('div', { class: 'bk-wrap' }, svg,
    h('div', { class: 'bk-legend' },
      h('span', null, h('i', { class: 'lgd-bar' }), 'Share of trades that finished above zero'),
      h('span', null, h('i', { class: 'lgd-ci' }), '95% range'),
      pairs.length > 1 ? h('span', null, h('i', { class: 'lgd-curve' }), curveNote || 'Win chance Acumen uses') : null,
      F.isNum(base) ? h('span', null, h('i', { class: 'lgd-base' }), `All trades ${F.frac(base)}`) : null,
      buckets.some(b => b.n < minN) ? h('span', null, h('i', { class: 'lgd-bar thin' }), `Faded: fewer than ${minN} trades`) : null));
}

/* ---------- valuation range ("football field") ---------- */
export function rangeBar(models, { low, high, fair, price, fmt = v => F.num(v, 2) }) {
  const vals = [low, high, fair, price, ...models.map(m => m.value)].filter(F.isNum);
  if (vals.length < 3) return null;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1;
  const a = lo - pad, b = hi + pad;
  const X = v => ((v - a) / (b - a)) * 100;
  const row = (label, value, cls, note) => h('div', { class: 'rb-row' },
    h('div', { class: 'rb-l' }, label),
    h('div', { class: 'rb-t' }, F.isNum(low) && F.isNum(high) ? h('div', { class: 'rb-band', style: { left: X(low) + '%', width: X(high) - X(low) + '%' } }) : null,
      h('div', { class: ['rb-dot', cls], style: { left: X(value) + '%' } }),
      F.isNum(price) ? h('div', { class: 'rb-price', style: { left: X(price) + '%' } }) : null),
    h('div', { class: 'rb-v' }, fmt(value)), note ? h('title', null, note) : null);
  return h('div', { class: 'rangebar' },
    ...models.filter(m => F.isNum(m.value)).map(m => row(m.name, m.value, 'm', m.note)),
    F.isNum(fair) ? row('Blended fair value', fair, 'f') : null,
    F.isNum(price) ? h('div', { class: 'rb-legend' }, h('span', null, h('i', { class: 'lg-price' }), 'Current price ' + fmt(price)), h('span', null, h('i', { class: 'lg-band' }), 'Fair-value range')) : null);
}

/* ---------- plain SVG bar series (dividends, EPS) ---------- */
export function miniBars(points, { fmt = v => F.num(v, 1), height = 110 } = {}) {
  const pts = points.filter(p => F.isNum(p.value));
  if (!pts.length) return h('div', { class: 'muted' }, 'No history');
  const W = 320, H = height, L = 6, R = 6, T = 14, B = 22;
  const max = Math.max(...pts.map(p => p.value), 1e-9);
  const bw = Math.min(40, (W - L - R) / pts.length * 0.6);
  const X = i => L + ((i + 0.5) / pts.length) * (W - L - R);
  const Y = v => T + (1 - v / max) * (H - T - B);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'mb', role: 'group' }, s('line', { x1: L, x2: W - R, y1: Y(0), y2: Y(0), class: 'bk-grid' }));
  pts.forEach((p, i) => {
    svg.append(s('rect', { x: X(i) - bw / 2, y: Y(p.value), width: bw, height: Math.max(1, Y(0) - Y(p.value)), rx: 3, class: 'bk-bar' }),
      s('text', { x: X(i), y: Y(p.value) - 4, class: 'bk-n', 'text-anchor': 'middle' }, fmt(p.value)),
      s('text', { x: X(i), y: H - 6, class: 'bk-ax', 'text-anchor': 'middle' }, p.label));
  });
  return h('div', { class: 'mb-wrap' }, svg);
}

export { icon };
