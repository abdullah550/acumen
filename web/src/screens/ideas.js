import { h, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import { download } from '../lib/dom.js';
import { here, linkProps } from '../lib/router.js';
import { HORIZONS, HZ_LABEL, HZ_SPAN, MARKETS, MARKET_LABEL, GRADE_ORDER, by } from '../lib/util.js';
import { seg, select, pillToggle, banner, empty, mkChip, hzChip, gradeBadge, actionChip, isResearch, convMeter, toast, explain } from '../ui/kit.js';
import { ideaCard, openTicket } from '../ui/ticket.js';
import { pageHead, errorBlock, rankIdeas } from './common.js';

const SORTS = [
  { value: 'rank', label: 'Best first' },
  { value: 'conviction', label: 'Conviction' },
  { value: 'exp_r', label: 'Expected result' },
  { value: 'p_win', label: 'Win chance' },
  { value: 'rr', label: 'Reward : risk' },
  { value: 'eta', label: 'Soonest target' },
];
const PAGE = 24;

function applyQuery(f, q) {
  const o = { ...f };
  if (q.h && (q.h === 'all' || HORIZONS.includes(q.h))) o.horizon = q.h;
  if (q.m && (q.m === 'all' || MARKETS.includes(q.m))) o.market = q.m;
  if (q.d && ['all', 'long', 'short'].includes(q.d)) o.dir = q.d;
  if (q.g && ['all', 'A', 'B', 'C'].includes(q.g)) o.grade = q.g;
  return o;
}

export default async function render(route) {
  let file;
  try { file = await D.get('ideas'); } catch (e) { return { el: errorBlock(e) }; }
  D.get('instruments').catch(() => null);
  const all = file.ideas;
  const anyActionable = all.some(i => i.actionable);  // with none, the "actionable only" filter would hide everything and mislead
  let f = applyQuery(S.get('ideasFilter'), route.query || {});
  if (!SORTS.some(s => s.value === f.sort)) f.sort = 'rank';
  let q = '';
  let shown = PAGE;
  let sortDir = -1;
  let tableSort = null;
  const save = () => S.set('ideasFilter', f, { silent: true });

  const root = h('div', { class: 'stack' });
  const count = h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } });
  const listHost = h('div');
  const moreBtn = h('button', { class: 'btn', type: 'button', onclick: () => { shown += PAGE; draw(); } }, 'Show more');

  const pools = (D.peek('calibration') || {});
  const unvalidated = (() => {
    const c = D.peek('overview') && D.peek('overview').calibration;
    return c && !c.calibrated ? c.note : (c && c.note) || null;
  })();

  const hzSeg = seg([{ value: 'all', label: 'All' }, ...HORIZONS.map(x => ({ value: x, label: HZ_LABEL[x], title: HZ_SPAN[x] }))], f.horizon, v => { f.horizon = v; shown = PAGE; save(); draw(); }, { label: 'Horizon' });
  const mkSeg = seg([{ value: 'all', label: 'All' }, ...MARKETS.map(x => ({ value: x, label: MARKET_LABEL[x] }))], f.market, v => { f.market = v; shown = PAGE; save(); draw(); }, { label: 'Market' });
  const dirSeg = seg([{ value: 'all', label: 'Long & short' }, { value: 'long', label: 'Long' }, { value: 'short', label: 'Short' }], f.dir, v => { f.dir = v; shown = PAGE; save(); draw(); }, { label: 'Direction' });
  const gradeSel = select([{ value: 'all', label: 'Any grade' }, { value: 'A', label: 'Grade A only' }, { value: 'B', label: 'Grade B or better' }, { value: 'C', label: 'Grade C or better' }], f.grade, v => { f.grade = v; shown = PAGE; save(); draw(); }, { 'aria-label': 'Minimum grade' });
  const sortSel = select(SORTS, f.sort, v => { f.sort = v; tableSort = null; save(); draw(); }, { 'aria-label': 'Sort' });
  const actTog = pillToggle('Actionable only', f.actionable, v => { f.actionable = v; shown = PAGE; save(); draw(); });
  const watchTog = pillToggle('Watchlist', f.watch, v => { f.watch = v; shown = PAGE; save(); draw(); });
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Filter by symbol or name', 'aria-label': 'Filter ideas', oninput: e => { q = e.target.value.trim().toLowerCase(); shown = PAGE; draw(); } });
  const viewSeg = seg([{ value: 'cards', label: 'Cards', icon: 'grid' }, { value: 'table', label: 'Table', icon: 'list' }], f.view || 'cards', v => { f.view = v; save(); draw(); }, { sm: true, label: 'View' });

  root.append(pageHead('Trade Ideas', 'Every qualified setup with entry, stop, targets, odds and timeline. Short selling is flagged AVOID on the PSX.', [viewSeg,
    h('button', { class: 'btn', type: 'button', onclick: () => exportCsv() }, icon('download'), 'CSV')]));
  if (!anyActionable) root.append(banner('warn', h('b', null, 'Nothing is marked actionable right now. '), 'No market and horizon has yet shown that its signals beat random entries with the same stops, targets and costs, so every idea here is a research candidate: a plan worth studying, not a recommendation. The Evidence page shows each test.',
    h('a', { class: 'btn ghost sm', style: { marginLeft: '8px' }, ...linkProps('/evidence') }, icon('shield'), 'See the tests')));
  if (unvalidated) root.append(banner('info', h('b', null, 'How to read the scores. '), unvalidated.replace(/\.?\s*$/, '.') + ' A hatched meter marks ideas from a pool where conviction has not yet proven it can rank outcomes; those odds are plain base rates, so give them less weight.'));
  root.append(h('div', { class: 'filters card pad' },
    h('div', { class: 'f-row' }, h('div', { class: 'f-cell' }, h('div', { class: 'label' }, 'Time horizon', explain('Swing: days to weeks. Positional: weeks to months. Investment: months to two years. Stops and targets scale with the horizon.')), hzSeg),
      h('div', { class: 'f-cell' }, h('div', { class: 'label' }, 'Market'), mkSeg),
      h('div', { class: 'f-cell' }, h('div', { class: 'label' }, 'Direction'), dirSeg)),
    h('div', { class: 'f-row' }, h('div', { class: 'f-cell grow' }, search), h('div', { class: 'f-cell' }, gradeSel), h('div', { class: 'f-cell' }, sortSel), anyActionable ? actTog : null, watchTog)));
  root.append(count, listHost);

  const filtered = () => {
    const wl = new Set(S.get('watchlist') || []);
    let list = all.filter(i =>
      (f.horizon === 'all' || i.horizon === f.horizon) &&
      (f.market === 'all' || i.market === f.market) &&
      (f.dir === 'all' || i.dir === f.dir) &&
      (!(f.actionable && anyActionable) || i.actionable) &&
      (!f.watch || wl.has(i.id)) &&
      (f.grade === 'all' || (GRADE_ORDER[i.grade] || 0) >= GRADE_ORDER[f.grade]) &&
      (!q || i.sym.toLowerCase().includes(q) || (i.name || '').toLowerCase().includes(q)));
    if (tableSort) {
      const k = tableSort;
      list = list.sort(by(i => k === 'eta' ? (i.targets && i.targets[0] && i.targets[0].eta_days ? i.targets[0].eta_days[0] : null) : k === 'sym' ? i.sym : k === 'grade' ? GRADE_ORDER[i.grade] : i[k], sortDir));
    } else if (f.sort === 'rank') list = rankIdeas(list);
    else if (f.sort === 'eta') list = list.sort(by(i => (i.targets && i.targets[0] && i.targets[0].eta_days ? i.targets[0].eta_days[0] : null), 1));
    else list = list.sort(by(f.sort, -1));
    return list;
  };

  function exportCsv() {
    const rows = filtered();
    const head = ['symbol', 'name', 'market', 'horizon', 'action', 'grade', 'conviction', 'entry', 'stop', 'target1', 'target2', 'rr', 'p_win', 'exp_r', 'eta_days_t1', 'setup'];
    const lines = [head.join(',')];
    for (const i of rows) {
      const t = i.targets || [];
      lines.push([i.sym, i.name, i.market, i.horizon, i.action, i.grade, i.conviction, i.entry && i.entry.px, i.stop, t[0] && t[0].px, t[1] && t[1].px, i.rr, i.p_win, i.exp_r, t[0] && t[0].eta_days ? t[0].eta_days.join('-') : '', i.setup].map(F.csvCell).join(','));
    }
    toast(download('acumen-ideas.csv', lines.join('\n'), 'text/csv') ? `Exported ${rows.length} ideas` : 'Could not export');
  }

  function tableView(rows) {
    const th = (key, label, cls) => h('th', { class: [cls, key && 'sortable'], onclick: key ? () => { if (tableSort === key) sortDir = -sortDir; else { tableSort = key; sortDir = key === 'sym' || key === 'eta' ? 1 : -1; } draw(); } : null },
      label, tableSort === key ? h('span', { class: 'arr' }, sortDir > 0 ? '▲' : '▼') : null);
    return h('div', { class: 'card' }, h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl fixed-first' },
      h('thead', null, h('tr', null, th('sym', 'Instrument'), th(null, 'Action'), th(null, 'Horizon'), th('grade', 'Grade'), th('conviction', 'Conv.', 'r'), th(null, 'Entry', 'r'), th(null, 'Stop', 'r'), th(null, 'Target 1', 'r'), th(null, 'Target 2', 'r'), th('rr', 'R:R', 'r'), th('p_win', 'Win', 'r'), th('exp_r', 'Exp.', 'r'), th('eta', 'Time to T1', 'r'))),
      h('tbody', null, ...rows.map(i => {
        const t = i.targets || [];
        const p = n => F.px(n, i.market);
        const research = isResearch(i);
        return h('tr', { class: ['link', !i.actionable && 'dim'], onclick: () => openTicket(i) },
          h('td', null, h('div', { class: 'sym' }, i.sym), h('div', { class: 'nm' }, research ? `${i.name} · research only` : i.name)),
          h('td', null, actionChip(i.action, research)), h('td', null, hzChip(i.horizon)), h('td', null, gradeBadge(i.grade, null, research)),
          h('td', { class: 'r' }, h('div', { style: { width: '92px', marginLeft: 'auto' } }, convMeter(i.conviction, { calibrated: i.calibrated }))),
          h('td', { class: 'r mono' }, p(i.entry && i.entry.px)), h('td', { class: 'r mono' }, p(i.stop)),
          h('td', { class: 'r mono' }, t[0] ? p(t[0].px) : F.NA), h('td', { class: 'r mono' }, t[1] ? p(t[1].px) : F.NA),
          h('td', { class: 'r' }, F.isNum(i.rr) ? F.num(i.rr, 1) : F.NA), h('td', { class: 'r' }, F.frac(i.p_win)),
          h('td', { class: ['r', i.actionable ? F.dirClass(i.exp_r) : null] }, F.r(i.exp_r)), h('td', { class: 'r' }, F.dayRange(t[0] && t[0].eta_days)));
      })))));
  }

  function draw() {
    const rows = filtered();
    const part = rows.slice(0, shown);
    count.textContent = rows.length ? `${F.int(rows.length)} ${rows.length === 1 ? 'idea' : 'ideas'}${rows.length > part.length ? `, showing ${part.length}` : ''}` : '';
    if (!rows.length) {
      listHost.replaceChildren(empty('No ideas match these filters', f.actionable && anyActionable ? 'Turn off "Actionable only" to see reference ideas, or relax the horizon, market or grade.' : 'Relax a filter to see more.', 'bolt'));
      return;
    }
    const body = (f.view || 'cards') === 'table' ? tableView(part) : h('div', { class: 'grid auto ideas-grid' }, ...part.map(ideaCard));
    replace(listHost, body, rows.length > part.length ? h('div', { class: 'row', style: { justifyContent: 'center', marginTop: 'var(--s-4)' } }, moreBtn) : null);
  }
  draw();
  void pools;
  return { el: root };
}
