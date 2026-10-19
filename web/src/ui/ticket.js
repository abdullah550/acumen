import { h, s, clear } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { S } from '../lib/store.js';
import { go, linkProps } from '../lib/router.js';
import { HZ_LABEL, HZ_SPAN } from '../lib/util.js';
import * as Z from '../lib/sizing.js';
import * as J from '../lib/journal.js';
import { copyText } from '../lib/dom.js';
import { chip, mkChip, hzChip, gradeBadge, actionChip, researchChip, isResearch, convMeter, stat, explain, banner, sheet, toast, field } from './kit.js';

export const BLOCKER_TEXT = {
  short_psx: 'Short selling is not practical for retail investors on PSX',
  not_tradable: 'Not offered by most retail brokers',
  stale: 'Market data is out of date',
  illiquid: 'Too thinly traded',
  no_edge: 'Signals in this market and horizon have not been shown to beat random entries',
  no_evidence: 'Not enough comparable past trades yet',
  negative_expectancy: 'Past expected result after costs is not positive',
  rr: 'Reward is too small for the risk',
  wide_stop: 'Stop would be too far from the entry to size sensibly',
};

const pxf = idea => n => F.pxr(n, idea.price, idea.market);
const T = (idea, i) => (idea.targets || [])[i];

/* ---------- price ladder (to scale) ---------- */
export function ladder(idea) {
  const W = 300, H = 290, top = 16, bot = 16, tx = 122;
  const e = idea.entry || {};
  const tg = idea.targets || [];
  const levels = [idea.stop, e.px, e.low, e.high, idea.price, ...tg.map(t => t.px)].filter(F.isNum);
  if (levels.length < 3) return h('div', { class: 'muted' }, 'No plan levels');
  let lo = Math.min(...levels), hi = Math.max(...levels);
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.02 || 1;
  lo -= pad; hi += pad;
  const Y = p => top + ((hi - p) / (hi - lo)) * (H - top - bot);
  const p = pxf(idea);
  const risk = Math.abs((e.px ?? idea.price) - idea.stop) || 1;
  const sign = idea.dir === 'short' ? -1 : 1;
  const pc = v => F.pct(((v / (e.px ?? idea.price)) - 1) * 100, 1);

  const rows = [
    { key: 'stop', y: Y(idea.stop), name: 'Stop', val: p(idea.stop), side: `${pc(idea.stop)} · −1R`, cls: 'ld-stop' },
    { key: 'entry', y: Y(e.px ?? idea.price), name: e.type === 'limit' ? 'Entry' : 'Entry', val: p(e.px ?? idea.price), side: e.type === 'limit' ? 'limit order' : 'at open', cls: 'ld-entry' },
    ...tg.map((t, i) => ({ key: 't' + i, y: Y(t.px), name: 'T' + (i + 1), val: p(t.px), side: `${pc(t.px)} · +${F.num(Math.abs(t.px - (e.px ?? idea.price)) / risk, 1)}R${F.isNum(t.prob) ? ' · ' + Math.round(t.prob * 100) + '%' : ''}`, cls: 'ld-tgt' })),
  ];
  const now = { key: 'now', y: Y(idea.price), name: 'Now', val: p(idea.price), side: '', cls: 'ld-now' };
  // spread labels so text never overlaps
  const labels = [...rows, now].sort((a, b) => a.y - b.y);
  const gap = 15;
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < gap) labels[i].ly = (labels[i - 1].ly ?? labels[i - 1].y) + gap;
  for (const l of labels) if (l.ly == null) l.ly = l.y;
  for (let i = labels.length - 2; i >= 0; i--) if (labels[i + 1].ly > H - 4) { labels[i + 1].ly = H - 4; if (labels[i].ly > labels[i + 1].ly - gap) labels[i].ly = labels[i + 1].ly - gap; }

  const lastT = tg.length ? tg[tg.length - 1].px : null;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ladder', role: 'img', 'aria-label': `Price ladder: stop ${p(idea.stop)}, entry ${p(e.px ?? idea.price)}, targets ${tg.map(t => p(t.px)).join(', ')}` });
  const band = (a, b, cls) => s('rect', { x: tx - 7, y: Math.min(Y(a), Y(b)), width: 14, height: Math.abs(Y(a) - Y(b)), class: cls });
  svg.append(
    band(idea.stop, e.px ?? idea.price, 'ld-band-risk'),
    lastT != null ? band(e.px ?? idea.price, lastT, 'ld-band-reward') : null,
    F.isNum(e.low) && F.isNum(e.high) ? s('rect', { x: tx - 14, y: Math.min(Y(e.low), Y(e.high)), width: 28, height: Math.max(3, Math.abs(Y(e.low) - Y(e.high))), rx: 3, class: 'ld-zone' }) : null,
  );
  for (const r of rows) {
    svg.append(s('line', { x1: tx - 18, x2: tx + 18, y1: r.y, y2: r.y, class: ['ld-line', r.cls] }));
  }
  // current price marker
  svg.append(s('path', { d: `M${tx + 22} ${now.y} l8 -5 v10 z`, class: 'ld-nowmark' }), s('line', { x1: tx - 18, x2: tx + 20, y1: now.y, y2: now.y, class: 'ld-nowline' }));
  for (const l of labels) {
    const isNow = l.key === 'now';
    const cls = ['ld-txt', l.cls];
    svg.append(
      s('text', { x: tx - 24, y: l.ly + 4, 'text-anchor': 'end', class: [...cls, 'ld-name'] }, l.name + '  ', s('tspan', { class: 'ld-val' }, l.val)),
      isNow ? null : s('text', { x: tx + 26, y: l.ly + 4, class: [...cls, 'ld-side'] }, l.side),
      Math.abs(l.ly - l.y) > 2 ? s('path', { d: `M${tx - 20} ${l.y} L${tx - 22} ${l.ly}`, class: 'ld-leader' }) : null,
    );
  }
  void sign;
  return h('div', { class: 'ladder-wrap' }, svg);
}

/* ---------- compact horizontal plan bar ---------- */
export function miniPlan(idea) {
  const e = idea.entry || {};
  const tg = idea.targets || [];
  const levels = [idea.stop, e.px, idea.price, ...tg.map(t => t.px)].filter(F.isNum);
  const lo = Math.min(...levels), hi = Math.max(...levels), span = hi - lo || 1;
  const X = v => ((v - lo) / span) * 100;
  const a = X(idea.stop), b = X(e.px ?? idea.price), c = tg.length ? X(tg[tg.length - 1].px) : b;
  const seg = (x1, x2, cls) => h('div', { class: ['mp-seg', cls], style: { left: Math.min(x1, x2) + '%', width: Math.abs(x2 - x1) + '%' } });
  return h('div', { class: 'miniplan', 'aria-hidden': 'true' },
    h('div', { class: 'mp-track' },
      seg(a, b, 'risk'), seg(b, c, 'reward'),
      ...tg.map((t, i) => h('div', { class: 'mp-tick', style: { left: X(t.px) + '%' } })),
      h('div', { class: 'mp-entry', style: { left: b + '%' } }),
      h('div', { class: 'mp-now', style: { left: X(idea.price) + '%' } })));
}

/* ---------- outcome probabilities ---------- */
export function outcomeBar(idea) {
  const t1 = T(idea, 0), t2 = T(idea, 1);
  if (!t1 || !F.isNum(t1.prob)) return null;
  const p1 = F.clip(t1.prob, 0, 1);
  const p2 = t2 && F.isNum(t2.prob) ? F.clip(Math.min(t2.prob, p1), 0, 1) : 0;
  const parts = [
    { k: 'stop', v: 1 - p1, label: 'Stopped out first', cls: 'oc-stop' },
    { k: 't1', v: p1 - p2, label: t2 ? 'Reaches T1 only' : 'Reaches T1', cls: 'oc-t1' },
  ];
  if (t2) parts.push({ k: 't2', v: p2, label: 'Reaches T2', cls: 'oc-t2' });
  return h('div', { class: 'outcome' },
    h('div', { class: 'oc-bar', role: 'img', 'aria-label': parts.map(x => `${x.label} ${Math.round(x.v * 100)}%`).join(', ') },
      ...parts.filter(x => x.v > 0.004).map(x => h('div', { class: ['oc-seg', x.cls], style: { flexGrow: Math.max(x.v, 0.02) } }))),
    h('div', { class: 'oc-legend' }, ...parts.map(x => h('span', { class: 'oc-key' }, h('i', { class: ['oc-sw', x.cls] }), x.label + ' ', h('b', null, Math.round(x.v * 100) + '%')))));
}

/* ---------- playbook in plain words ---------- */
export function playbook(idea) {
  const p = pxf(idea);
  const e = idea.entry || {};
  const short = idea.dir === 'short';
  const steps = [];
  if (e.type === 'limit') {
    steps.push([short ? 'Sell short at a limit' : 'Buy at a limit', `${short ? 'Sell' : 'Buy'} with a limit order at ${p(e.px)}${F.isNum(e.low) ? ` (acceptable zone ${p(e.low)} to ${p(e.high)})` : ''}. Keep the order for up to ${e.valid_bars} sessions, then cancel it.`, e.note]);
  } else {
    steps.push([short ? 'Sell short at the open' : 'Buy at the open', `${short ? 'Sell' : 'Buy'} at the next session's open. Last price ${p(idea.price)}.`, e.note]);
  }
  steps.push(['Protect with a stop', `Place a stop-loss at ${p(idea.stop)} (${F.pct(idea.stop_pct, 1)} from entry). That is your 1R: the most you accept to lose on this idea.`]);
  const tg = idea.targets || [];
  tg.forEach((t, i) => {
    const last = i === tg.length - 1;
    const share = F.isNum(t.scale_out) ? Math.round(t.scale_out * 100) : (last ? 100 : 50);
    steps.push([`Take profit ${i + 1}`,
      `${last && i > 0 ? 'Sell the rest' : `Sell ${share}% of the position`} at ${p(t.px)} (${F.pct(t.pct, 1)}, ${F.num(t.r, 1)}R). Modelled chance of getting there before the stop: ${F.isNum(t.prob) ? Math.round(t.prob * 100) + '%' : 'n/a'}${t.eta_days ? `, typically ${F.dayRange(t.eta_days)}` : ''}.`,
      i === 0 && tg.length > 1 ? 'After target 1 is hit, move the stop to your entry price so the rest cannot turn into a loss.' : null]);
  });
  if (idea.time_stop_days) steps.push(['Time stop', `If neither target has been reached after about ${F.days(idea.time_stop_days)}, close the position: a trade that goes nowhere ties up capital.`]);
  return h('ol', { class: 'playbook' }, ...steps.map(([t, body, sub], i) =>
    h('li', null, h('span', { class: 'pb-n' }, i + 1), h('div', null, h('div', { class: 'pb-t' }, t), h('div', { class: 'pb-b' }, body), sub ? h('div', { class: 'pb-s' }, sub) : null))));
}

/* ---------- position sizing ---------- */
export function sizingBlock(idea, { onSize } = {}) {
  const root = h('div', { class: 'sizing' });
  let eq = Z.equity();
  let risk = Z.ideaRiskPct(idea);
  const out = h('div', { class: 'sz-out' });
  const eqIn = h('input', { class: 'input mono', inputmode: 'decimal', value: String(Math.round(eq)), 'aria-label': 'Account equity' });
  const rkIn = h('input', { class: 'input mono', inputmode: 'decimal', value: String(risk), 'aria-label': 'Risk percent' });
  const ccy = Z.acctCcy();
  const calc = () => {
    const sz = Z.size(idea, { eq, riskPct: risk });
    clear(out);
    if (onSize) onSize(sz);
    if (!sz.ok) { out.append(h('div', { class: 'muted' }, sz.notes.join(' '))); return; }
    const money = v => F.money(v, sz.rate ? sz.acct : sz.quoteCcy, { compactFrom: 1e7 });
    const rows = [
      [idea.market === 'psx' ? 'Shares to buy' : idea.market === 'fx' ? 'Position (units)' : 'Quantity', F.num(sz.units, idea.market === 'crypto' ? Math.max(0, 4 - Math.floor(Math.log10(Math.max(sz.units, 1e-9)))) : 0) + (sz.lots != null ? `  (${F.num(sz.lots, 2)} lots)` : '')],
      ['Position value', money(sz.notional) + (eq > 0 ? `  (${F.num(sz.notional / eq * 100, 1)}% of equity)` : '')],
      ['Loss if stopped', money(-sz.maxLoss) + `  (${F.num(-sz.effRiskPct, 2)}%)`],
      sz.gainT1 != null ? ['Gain at target 1' + (T(idea, 1) ? ' (partial)' : ''), money(sz.gainT1)] : null,
      sz.gainBoth != null ? ['Gain at both targets', money(sz.gainBoth)] : null,
    ].filter(Boolean);
    out.append(h('dl', { class: 'kv' }, ...rows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])));
    const notes = [...sz.notes];
    if (sz.binding !== 'risk') notes.unshift(`Size is limited by the ${sz.binding}, so you risk less than the budget.`);
    if (notes.length) out.append(h('ul', { class: 'sz-notes' }, ...notes.map(n => h('li', null, n))));
  };
  const parse = v => Number(String(v).replace(/[, ]/g, ''));
  eqIn.addEventListener('input', () => { const v = parse(eqIn.value); if (v > 0) { eq = v; S.set('equity', v, { silent: true }); calc(); } });
  rkIn.addEventListener('input', () => { const v = parse(rkIn.value); if (v > 0 && v <= 10) { risk = v; calc(); } });
  root.append(
    h('div', { class: 'sz-in' },
      field(`Account equity (${ccy})`, eqIn),
      field(h('span', null, 'Risk on this idea (%)', explain(`Suggested ${Z.ideaRiskPct(idea)}% = your base risk ${Z.baseRiskPct()}% × grade ${idea.grade} factor ${Z.GRADE_RISK_MULT[idea.grade] ?? 0.25}${idea.calibrated ? '' : ' × 0.5 because probabilities for this pool are not validated'}. Change the base risk in Settings.`)), rkIn)),
    out);
  calc();
  root.recalc = calc;
  root.getSize = () => Z.size(idea, { eq, riskPct: risk });
  return root;
}

/* ---------- watchlist ---------- */
export function starButton(id, { label = false } = {}) {
  const on = () => (S.get('watchlist') || []).includes(id);
  const sym = String(id).split(':').slice(1).join(':') || id;
  const btn = h('button', { class: ['icon-btn', 'star', on() && 'on'], type: 'button', 'aria-pressed': String(on()), title: 'Watchlist', 'aria-label': `Watchlist: ${sym}`, dataset: { id } }, icon('star'), label ? h('span', null, 'Watch') : null);
  btn.addEventListener('click', e => {
    e.stopPropagation(); e.preventDefault();
    const cur = S.get('watchlist') || [];
    const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id];
    S.set('watchlist', next);
    // every star for this instrument on screen follows the change
    for (const b of document.querySelectorAll('.star')) {
      if (b.dataset.id !== id) continue;
      b.classList.toggle('on', next.includes(id));
      b.setAttribute('aria-pressed', String(next.includes(id)));
    }
    toast(next.includes(id) ? 'Added to watchlist' : 'Removed from watchlist');
  });
  return btn;
}

/* ---------- log trade form ---------- */
export function openLogTrade(idea, sz) {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const ent = h('input', { class: 'input mono', inputmode: 'decimal', value: String((idea.entry && idea.entry.px) ?? idea.price) });
  const qty = h('input', { class: 'input mono', inputmode: 'decimal', value: sz && sz.ok ? String(sz.units) : '' });
  const dt = h('input', { class: 'input', type: 'date', value: iso });
  const note = h('input', { class: 'input', placeholder: 'Optional note (broker, reason...)' });
  const err = h('div', { class: 'err' });
  const sh = sheet({
    title: 'Log this trade', center: true,
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted' }, 'Records the trade on this device only. Acumen will track it against the plan levels each time prices refresh.'),
      h('div', { class: 'grid c2 keep' }, field('Actual entry price', ent), field('Quantity', qty)),
      field('Date', dt), field('Note', note), err,
      h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
        h('button', { class: 'btn', type: 'button', onclick: () => sh.close() }, 'Cancel'),
        h('button', {
          class: 'btn primary', type: 'button', onclick: () => {
            const e = Number(ent.value.replace(/,/g, '')), q = Number(qty.value.replace(/,/g, ''));
            if (!(e > 0) || !(q > 0)) { err.textContent = 'Enter a positive price and quantity.'; return; }
            const tg = idea.targets || [];
            J.add({
              idea: idea.id, sym: idea.sym, name: idea.name, market: idea.market, horizon: idea.horizon, dir: idea.dir,
              ccy: Z.quoteCcy(idea), opened: dt.value || iso, entry: e, qty: q, stop: idea.stop,
              t1: tg[0] ? tg[0].px : null, t2: tg[1] ? tg[1].px : null, scale1: tg[0] && F.isNum(tg[0].scale_out) ? tg[0].scale_out : 0.5,
              plannedEntry: idea.entry && idea.entry.px, grade: idea.grade, note: note.value.trim(),
            });
            sh.close(); toast('Saved to My positions');
          },
        }, 'Save trade'))),
  });
}

/* ---------- full ticket ---------- */
export function ticket(idea, { sheetMode = false, showHead = true } = {}) {
  const p = pxf(idea);
  const root = h('div', { class: 'ticket stack' });
  const blockers = idea.blockers || [];
  const avoid = idea.action === 'AVOID';
  let sizingEl = null;

  if (showHead) {
    root.append(h('div', { class: 'tk-head' },
      h('div', { class: 'row wrap', style: { gap: '8px' } }, actionChip(idea.action, isResearch(idea)), mkChip(idea.market), hzChip(idea.horizon), idea.setup ? chip(idea.setup, 'outline') : null),
      h('div', { class: 'tk-title' },
        h('a', { class: 'tk-sym mono', ...linkProps('/i/' + idea.id, { h: idea.horizon }) }, idea.sym),
        h('span', { class: 'muted tk-name' }, idea.name)),
      h('div', { class: 'tk-conv' }, gradeBadge(idea.grade, null, isResearch(idea)), convMeter(idea.conviction, { calibrated: idea.calibrated }))));
  }

  if (!idea.actionable) {
    root.append(banner('warn', h('b', null, avoid ? 'Avoid, do not buy. ' : 'Reference only, not actionable. '),
      blockers.map(b => BLOCKER_TEXT[b] || b).join('; ') || 'This idea does not pass the actionability checks.'));
  }

  root.append(h('div', { class: 'tk-stats' },
    stat('Modelled win chance', F.frac(idea.p_win), 'ends in profit after costs', { info: 'Probability that the whole trade, including partial exits, ends positive after trading costs. It comes from the pooled backtest of similar signals and is capped by how reliable that history was.' }),
    stat('Expected result', F.r(idea.exp_r), F.isNum(idea.exp_pct) ? `${F.pct(idea.exp_pct, 1)} of entry on average` : null, { cls: idea.actionable ? F.dirClass(idea.exp_r) : null, info: 'Average outcome per trade in units of the money risked (R), after costs. +0.30R means 30% of the risked amount on average over many trades, not on any single one.' }),
    stat('Reward : risk', F.isNum(idea.rr) ? '1 : ' + F.num(idea.rr, 1) : F.NA, 'to the last target'),
    stat('Time frame', F.dayRange(idea.targets && idea.targets[0] && idea.targets[0].eta_days) || F.NA, `${HZ_LABEL[idea.horizon]}: ${HZ_SPAN[idea.horizon]}`, { info: 'Typical calendar days for the first target to be reached, based on how long similar past trades took. Calendar days, not trading days.' })));

  root.append(h('div', { class: 'tk-body' },
    h('div', { class: 'tk-ladder' }, h('div', { class: 'label' }, 'Trade plan, drawn to price scale'), ladder(idea), outcomeBar(idea)),
    h('div', { class: 'tk-play' }, h('div', { class: 'label' }, 'What to do'), avoid ? h('p', { class: 'ink-2' }, 'Short selling is not practical for most retail investors on the PSX, so this is not a trade. Read it as a reason not to buy this stock now, or to reduce an existing holding.') : playbook(idea))));

  if (!avoid) {
    sizingEl = sizingBlock(idea);
    root.append(h('div', { class: 'tk-size' }, h('div', { class: 'label' }, 'Position size for your account'), sizingEl));
  }

  const reasons = idea.reasons || [], warns = idea.warnings || [];
  if (reasons.length) root.append(h('div', null, h('div', { class: 'label' }, 'Why Acumen likes it'), h('ul', { class: 'bul ok' }, ...reasons.map(r => h('li', null, icon('check'), h('span', null, r))))));
  if (warns.length) root.append(h('div', null, h('div', { class: 'label' }, 'Cautions'), h('ul', { class: 'bul warn' }, ...warns.map(r => h('li', null, icon('alert'), h('span', null, r))))));

  const bt = idea.backtest || {}, ev = idea.evidence || {};
  root.append(h('div', { class: 'tk-evidence' },
    h('div', { class: 'label' }, 'Evidence behind the numbers', explain('How this setup performed in simulated trades with the same rules (entry, stop, targets, costs). Past simulated results do not guarantee future results. Unvalidated means conviction did not reliably rank newer trades above older ones, so Acumen uses plain base rates instead of trusting the score. An idea is only actionable when its market and horizon also beat random entries (same stops, targets and costs) clearly enough to rule out luck.')),
    h('p', { class: 'ink-2', style: { fontSize: 'var(--fs-sm)' } },
      bt.n ? `On ${idea.sym} itself: ${bt.n} past ${bt.n === 1 ? 'trade' : 'trades'}, ${F.frac(bt.win)} winners, average ${F.r(bt.avg_r)}${bt.reliable ? '' : ' (too few to rely on)'}. ` : `No past trades of this kind on ${idea.sym}. `,
      ev.pool_n ? `Across ${F.int(ev.pool_n)} similar trades in this market and horizon: ${F.frac(ev.pool_win)} winners, average ${F.r(ev.pool_avg_r)}. ` : '',
      ev.status ? chip(ev.status === 'calibrated' || ev.status === 'validated' ? 'Validated out of sample' : ev.status === 'unvalidated' ? 'Not validated' : 'Insufficient history', ev.status === 'calibrated' || ev.status === 'validated' ? 'up' : 'warn') : null,
      ' ', ev.skill ? chip('Beats random entries', 'up', { icon: 'check' }) : chip('Not shown to beat random entries', 'warn', { icon: 'alert' })),
    h('div', null, h('a', { class: 'btn ghost sm', ...linkProps('/evidence', { pm: idea.market, ph: idea.horizon }) }, icon('shield'), 'See the full check for this pool'))));

  const actions = h('div', { class: 'tk-actions' },
    !sheetMode ? null : h('a', { class: 'btn primary', ...linkProps('/i/' + idea.id, { h: idea.horizon }), onclick: sheetMode ? (() => { /* sheet closes via route change */ }) : null }, icon('layers'), 'Full analysis'),
    avoid ? null : h('button', { class: 'btn', type: 'button', onclick: () => openLogTrade(idea, sizingEl && sizingEl.getSize()) }, icon('edit'), 'Log trade'),
    starButton(idea.id, { label: true }),
    h('button', { class: 'btn ghost', type: 'button', onclick: async () => { const ok = await copyText(Z.planText(idea, sizingEl && sizingEl.getSize())); toast(ok ? 'Plan copied' : 'Could not copy'); } }, icon('copy'), 'Copy plan'));
  root.append(actions);
  root.append(h('p', { class: 'fineprint' }, 'Model estimate for research. Not investment advice. Markets can gap through stops and past results do not guarantee future results.'));
  return root;
}

let openSheet = null;
export function openTicket(idea) {
  if (openSheet) openSheet.close();
  openSheet = sheet({ title: h('span', { class: 'row', style: { gap: '8px' } }, idea.sym, h('span', { class: 'muted', style: { fontWeight: 400, fontSize: 'var(--fs-sm)' } }, idea.name)), body: ticket(idea, { sheetMode: true, showHead: true }), onClose: () => { openSheet = null; } });
  openSheet.content.addEventListener('click', e => { const a = e.target.closest('a[href^="#/"]'); if (a) openSheet && openSheet.close(); });
  return openSheet;
}
export const closeTicket = () => { if (openSheet) openSheet.close(); };

/* ---------- list card ---------- */
export function ideaCard(idea) {
  const p = pxf(idea);
  const e = idea.entry || {};
  const t1 = T(idea, 0), t2 = T(idea, 1);
  const cell = (label, value, sub, cls, odds) => h('div', { class: 'ic-cell' }, h('div', { class: 'ic-l' }, label), h('div', { class: ['ic-v', cls] }, value), sub ? h('div', { class: 'ic-s' }, sub) : null, odds ? h('div', { class: 'ic-o' }, odds) : null);
  const label = `${idea.action} ${idea.sym}, ${HZ_LABEL[idea.horizon]}, grade ${idea.grade}`;
  // One real button covers the card (so a click anywhere opens the plan); the symbol link and the star sit above it.
  const research = isResearch(idea);
  const card = h('article', { class: ['idea-card', !idea.actionable && 'dim'] },
    h('button', { class: 'ic-open', type: 'button', 'aria-label': `Open the plan: ${label}${research ? ' (research only, not actionable)' : ''}`, onclick: () => openTicket(idea) }),
    h('div', { class: 'ic-top' },
      actionChip(idea.action, research),
      h('div', { class: 'ic-id' }, h('a', { class: 'ic-sym mono', ...linkProps('/i/' + idea.id, { h: idea.horizon }) }, idea.sym), h('span', { class: 'ic-name' }, idea.name)),
      gradeBadge(idea.grade, null, research), starButton(idea.id)),
    h('div', { class: 'ic-tags' }, research ? researchChip() : null, mkChip(idea.market), hzChip(idea.horizon), idea.setup ? h('span', { class: 'ic-setup' }, idea.setup) : null),
    miniPlan(idea),
    h('div', { class: 'ic-grid' },
      cell(e.type === 'limit' ? 'Buy limit' : 'Entry', p(e.px ?? idea.price), e.type === 'limit' ? 'wait for pullback' : 'next open'),
      cell('Stop', p(idea.stop), F.pct(idea.stop_pct, 1), 'down'),
      t1 ? cell('Target 1', p(t1.px), F.pct(t1.pct, 1), 'up', F.isNum(t1.prob) ? Math.round(t1.prob * 100) + '% chance' : null) : null,
      t2 ? cell('Target 2', p(t2.px), F.pct(t2.pct, 1), 'up', F.isNum(t2.prob) ? Math.round(t2.prob * 100) + '% chance' : null) : null),
    h('div', { class: 'ic-foot' },
      h('div', { class: 'ic-metric' }, h('span', null, 'Win'), h('b', null, F.frac(idea.p_win))),
      h('div', { class: 'ic-metric' }, h('span', null, 'Exp.'), h('b', { class: idea.actionable ? F.dirClass(idea.exp_r) : null }, F.r(idea.exp_r))),
      h('div', { class: 'ic-metric' }, h('span', null, 'R:R'), h('b', null, F.isNum(idea.rr) ? F.num(idea.rr, 1) : F.NA)),
      h('div', { class: 'ic-metric' }, h('span', null, 'Time'), h('b', null, F.dayRange(t1 && t1.eta_days) || F.NA)),
      h('div', { class: 'grow' }), convMeter(idea.conviction, { calibrated: idea.calibrated })));
  return card;
}
