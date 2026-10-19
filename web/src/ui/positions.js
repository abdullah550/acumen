import { h, clear, download } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import * as J from '../lib/journal.js';
import * as Z from '../lib/sizing.js';
import { HZ_LABEL, by } from '../lib/util.js';
import { linkProps, go } from '../lib/router.js';
import { sheet, ask, seg, field, chip, chgSpan, stat, toast, banner, explain } from './kit.js';

const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const parse = v => { const t = String(v == null ? '' : v).replace(/[,\s]/g, ''); return t === '' ? null : Number(t); };
const tidy = (x, m) => (F.isNum(x) ? String(Number(x.toFixed(F.decimals(x, m)))) : '');
const qtyText = t => F.num(t.qty, t.market === 'crypto' ? 4 : 0);
const money = (v, opt = {}) => F.money(v, Z.acctCcy(), { compactFrom: 1e7, d: 0, ...opt });
const signedMoney = v => (F.isNum(v) ? (v > 0 ? '+' : '') + money(v) : F.NA);

/* ---------- add / edit form ---------- */
export async function openPositionForm({ trade = null, instrument = null, onDone = () => {} } = {}) {
  const edit = !!trade;
  let inst = instrument;
  let rows = [];
  let ideas = [];
  try { rows = Array.from((await D.index()).values()); } catch { /* data not ready */ }
  try { ideas = (await D.get('ideas')).ideas || []; } catch { /* no ideas file */ }
  if (edit && !inst) inst = rows.find(r => r.id === trade.idea) || { id: trade.idea, m: trade.market, sym: trade.sym, name: trade.name, px: null };

  let dir = edit ? trade.dir : 'long';
  let horizon = edit ? trade.horizon : null;
  let grade = edit ? trade.grade : null;
  const inp = (v, extra = {}) => h('input', { class: 'input mono', inputmode: 'decimal', autocomplete: 'off', value: v == null ? '' : String(v), ...extra });
  const entry = inp(edit ? trade.entry : '');
  const qty = inp(edit ? trade.qty : '');
  const stop = inp(edit && trade.stop != null ? trade.stop : '', { placeholder: 'Optional' });
  const t1 = inp(edit && trade.t1 != null ? trade.t1 : '', { placeholder: 'Optional' });
  const t2 = inp(edit && trade.t2 != null ? trade.t2 : '', { placeholder: 'Optional' });
  const dt = h('input', { class: 'input', type: 'date', value: edit ? trade.opened || today() : today() });
  const note = h('input', { class: 'input', placeholder: 'Optional note (broker, reason...)', value: edit ? trade.note || '' : '' });
  const err = h('div', { class: 'err', role: 'alert' });
  const qtyField = field('Quantity', qty);
  const qtyLab = qtyField.querySelector('label');
  const unitLabel = () => {
    const m = inst && inst.m;
    qtyLab.textContent = m === 'psx' ? 'Shares' : m === 'fx' ? 'Units (100,000 = 1 lot)' : m === 'crypto' ? 'Coins' : 'Quantity';
  };

  /* instrument picker (add mode) */
  const chosenBox = h('div', { class: 'pick-chosen' });
  const search = h('input', { class: 'input', type: 'search', placeholder: 'Search symbol or name', autocomplete: 'off', 'aria-label': 'Instrument' });
  const list = h('div', { class: 'pick-list', role: 'listbox' });
  const planRow = h('div', { class: 'preset-row' });
  const planWrap = h('div', { class: 'stack tight', hidden: true }, h('div', { class: 'fs-xs muted' }, 'Fill the plan levels from Acumen’s idea for this instrument (optional):'), planRow);

  function applyIdea(i) {
    dir = i.dir; horizon = i.horizon; grade = i.grade;
    entry.value = tidy((i.entry && i.entry.px) ?? i.price, i.market);
    stop.value = tidy(i.stop, i.market);
    const tg = i.targets || [];
    t1.value = tg[0] ? tidy(tg[0].px, i.market) : '';
    t2.value = tg[1] ? tidy(tg[1].px, i.market) : '';
    const sz = Z.size(i);
    if (sz.ok) qty.value = String(sz.units);
    dirSeg.set(dir);
  }
  function drawPlans() {
    clear(planRow);
    const mine = inst ? ideas.filter(i => i.id === inst.id && i.targets && i.targets.length) : [];
    planWrap.hidden = edit || !mine.length;
    for (const i of mine) {
      planRow.append(h('button', { type: 'button', class: 'pill-toggle', onclick: () => applyIdea(i), title: i.actionable ? 'Actionable idea' : 'Reference only: Acumen does not recommend acting on it' },
        `${HZ_LABEL[i.horizon]} ${i.dir}${i.actionable ? '' : ' (reference)'}`));
    }
  }
  function drawChosen() {
    clear(chosenBox);
    if (!inst) { chosenBox.hidden = true; search.hidden = false; list.hidden = false; return; }
    chosenBox.hidden = false; search.hidden = true; list.hidden = true;
    chosenBox.append(h('div', { class: 'row', style: { gap: '10px' } }, h('b', { class: 'mono' }, inst.sym), h('span', { class: 'muted' }, inst.name),
      edit ? null : h('button', { class: 'btn sm', type: 'button', onclick: () => { inst = null; drawChosen(); drawPlans(); unitLabel(); search.value = ''; search.focus(); } }, 'Change')));
  }
  function choose(r) {
    inst = r;
    if (!entry.value && F.isNum(r.px)) entry.value = String(r.px);
    drawChosen(); drawPlans(); unitLabel();
  }
  function drawList() {
    clear(list);
    const q = search.value.trim().toLowerCase();
    if (!q) return;
    const sc = r => { const s = r.sym.toLowerCase(), n = (r.name || '').toLowerCase(); return s === q ? 0 : s.startsWith(q) ? 1 : n.startsWith(q) ? 2 : s.includes(q) ? 3 : n.includes(q) ? 4 : 9; };
    const hits = rows.map(r => [sc(r), r]).filter(x => x[0] < 9).sort((a, b) => a[0] - b[0] || a[1].sym.length - b[1].sym.length).slice(0, 6).map(x => x[1]);
    if (!hits.length) { list.append(h('div', { class: 'muted fs-xs', style: { padding: '8px 10px' } }, 'No match. Try a ticker like HUBC, a pair like EURUSD or a coin like BTC.')); return; }
    for (const r of hits) list.append(h('button', { type: 'button', class: 'pick-row', role: 'option', onclick: () => choose(r) }, h('b', { class: 'mono' }, r.sym), h('span', { class: 'muted nm' }, r.name), h('span', { class: 'chip outline' }, r.m === 'psx' ? 'PSX' : r.m === 'fx' ? 'Forex' : 'Crypto')));
  }
  search.addEventListener('input', drawList);

  const dirSeg = seg([{ value: 'long', label: 'Long (bought)' }, { value: 'short', label: 'Short (sold)' }], dir, v => { dir = v; }, { label: 'Side' });
  if (edit) dirSeg.querySelectorAll('button').forEach(b => { b.disabled = true; });

  function save() {
    err.textContent = '';
    if (!inst) { err.textContent = 'Pick the instrument first.'; return; }
    const e = parse(entry.value), q = parse(qty.value);
    if (!(e > 0)) { err.textContent = 'Enter the price you traded at.'; return; }
    if (!(q > 0)) { err.textContent = 'Enter a quantity above zero.'; return; }
    const s = parse(stop.value), a = parse(t1.value), b = parse(t2.value);
    const long = dir !== 'short';
    if (s != null && !(s > 0)) { err.textContent = 'The stop must be a positive price, or left blank.'; return; }
    // a stop exactly at the entry price is a legitimate break-even stop
    if (s != null && (long ? s > e : s < e)) { err.textContent = long ? 'For a long position the stop sits at or below the entry price.' : 'For a short position the stop sits at or above the entry price.'; return; }
    for (const [lab, v] of [['Target 1', a], ['Target 2', b]]) {
      if (v != null && !(v > 0)) { err.textContent = `${lab} must be a positive price, or left blank.`; return; }
      if (v != null && (long ? v <= e : v >= e)) { err.textContent = long ? `${lab} for a long position sits above the entry price.` : `${lab} for a short position sits below the entry price.`; return; }
    }
    const patch = { entry: e, qty: q, stop: s, t1: a, t2: b, opened: dt.value || today(), note: note.value.trim() };
    if (edit) J.update(trade.id, patch);
    else {
      J.add({
        idea: inst.id, sym: inst.sym, name: inst.name, market: inst.m, horizon, dir, ccy: Z.quoteCcy({ market: inst.m, sym: inst.sym }),
        scale1: 0.5, plannedEntry: null, grade, ...patch,
      });
    }
    sh.close(); toast(edit ? 'Position updated' : 'Position saved'); onDone();
  }

  const breakEven = h('button', { class: 'btn sm', type: 'button', onclick: () => { stop.value = entry.value; } }, 'Move stop to entry');
  const body = h('div', { class: 'stack' },
    h('p', { class: 'muted' }, edit ? 'Change the levels or note. Acumen compares them with each new price.' : 'Record a trade you took. It stays on this device only and Acumen tracks it against your stop and targets each time prices refresh.'),
    edit ? null : h('div', { class: 'field' }, h('div', { class: 'flabel' }, 'Instrument'), search, list),
    chosenBox, planWrap,
    edit ? null : h('div', { class: 'field' }, h('div', { class: 'flabel' }, 'Side'), dirSeg),
    h('div', { class: 'grid c2 keep' }, field('Entry price', entry), qtyField),
    h('div', { class: 'grid c2 keep' }, field('Stop price', stop), field('Date opened', dt)),
    h('div', { class: 'grid c2 keep' }, field('Target 1', t1), field('Target 2', t2)),
    edit ? h('div', { class: 'row' }, breakEven) : null,
    field('Note', note), err,
    h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
      h('button', { class: 'btn', type: 'button', onclick: () => sh.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, edit ? 'Save changes' : 'Save position')));
  const sh = sheet({ title: edit ? `Edit ${trade.sym}` : 'Add a position', body, center: true });
  drawChosen(); drawPlans(); unitLabel();
  if (!inst) search.focus();
}

/* ---------- close ---------- */
export function openClosePosition(t, onDone = () => {}) {
  const ev = J.evaluate(t);
  const exit = h('input', { class: 'input mono', inputmode: 'decimal', autocomplete: 'off', value: F.isNum(ev.px) ? String(ev.px) : '' });
  const dt = h('input', { class: 'input', type: 'date', value: today() });
  const note = h('input', { class: 'input', placeholder: 'Optional note (what happened)' });
  const out = h('div', { class: 'close-result' });
  const err = h('div', { class: 'err', role: 'alert' });
  const calc = () => {
    const x = parse(exit.value);
    clear(out);
    if (!(x > 0)) return;
    const sign = t.dir === 'short' ? -1 : 1;
    const mv = (x - t.entry) * sign;
    const pl = mv * t.qty * ev.rate;
    const risk = F.isNum(t.stop) ? Math.abs(t.entry - t.stop) : 0;
    out.append(h('span', null, 'Result: '), h('b', { class: F.dirClass(pl) }, signedMoney(pl)), h('span', { class: 'muted' }, `  (${F.pct(mv / t.entry * 100, 1)}${risk ? ', ' + F.r(mv / risk, 1) : ''})`));
  };
  exit.addEventListener('input', calc); calc();
  const sh = sheet({
    title: `Close ${t.sym}`, center: true,
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted' }, `${t.dir === 'short' ? 'Short' : 'Long'} ${qtyText(t)} at ${F.px(t.entry, t.market)}. Enter the price you actually exited at.`),
      h('div', { class: 'grid c2 keep' }, field('Exit price', exit), field('Date closed', dt)),
      field('Note', note), out, err,
      h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
        h('button', { class: 'btn', type: 'button', onclick: () => sh.close() }, 'Cancel'),
        h('button', {
          class: 'btn primary', type: 'button', onclick: () => {
            const x = parse(exit.value);
            if (!(x > 0)) { err.textContent = 'Enter the exit price.'; return; }
            J.update(t.id, { status: 'closed', exit: x, closed: dt.value || today(), closeNote: note.value.trim() });
            sh.close(); toast('Position closed'); onDone();
          },
        }, 'Close position'))),
  });
}

export async function deletePosition(t, onDone = () => {}) {
  const ok = await ask({ title: 'Delete this position?', text: `${t.sym} will be removed from the journal on this device. This cannot be undone. Export first if you want a copy.`, confirm: 'Delete', danger: true });
  if (ok) { J.remove(t.id); toast('Deleted'); onDone(); }
}

/* ---------- backup ---------- */
export function exportData() {
  const stamp = today();
  const ok = download(`acumen-my-data-${stamp}.json`, JSON.stringify(S.exportAll(), null, 2), 'application/json');
  toast(ok ? 'Exported your positions and watchlist' : 'Could not export');
}

export function importData(onDone = () => {}) {
  const inp = h('input', { type: 'file', accept: 'application/json,.json', style: { display: 'none' } });
  inp.addEventListener('change', async () => {
    const f = inp.files && inp.files[0];
    inp.remove();
    if (!f) return;
    try {
      const obj = JSON.parse(await f.text());
      const before = { j: J.list().length, w: (S.get('watchlist') || []).length };
      S.importAll(obj);
      const after = { j: J.list().length, w: (S.get('watchlist') || []).length };
      const nj = after.j - before.j, nw = after.w - before.w;
      toast(`Imported ${nj} new ${nj === 1 ? 'position' : 'positions'} and ${nw} new watchlist ${nw === 1 ? 'item' : 'items'}`);
      onDone();
    } catch (e) { toast(e && e.message ? e.message : 'That file could not be imported'); }
  });
  document.body.append(inp);
  inp.click();
}

export function backupButtons(onDone) {
  return [
    h('button', { class: 'btn sm', type: 'button', onclick: exportData }, icon('download'), 'Export'),
    h('button', { class: 'btn sm', type: 'button', onclick: () => importData(onDone) }, icon('upload'), 'Import'),
  ];
}

/* ---------- the journal view ---------- */
function flagChips(ev) {
  const out = [];
  if (ev.flags.includes('stop')) out.push(chip('Stop level reached', 'down', { icon: 'alert' }));
  if (ev.flags.includes('t2')) out.push(chip('Target 2 reached', 'up', { icon: 'target' }));
  else if (ev.flags.includes('t1')) out.push(chip('Target 1 reached', 'up', { icon: 'target' }));
  return out;
}

export function journalView({ onChange = () => {} } = {}) {
  const root = h('div', { class: 'stack' });
  let closedShown = 10;

  const redraw = () => { draw(); onChange(); };

  async function refreshPrices(btn) {
    btn.disabled = true;
    try { await D.refreshPrices(); toast('Prices refreshed'); } catch { toast('Could not refresh prices. Showing the last ones.'); }
    btn.disabled = false;
    draw();
  }

  function head(open) {
    const p = D.peek('prices');
    const asof = p && p.generated_at;
    const rf = h('button', { class: 'btn sm', type: 'button', onclick: e => refreshPrices(e.currentTarget) }, icon('refresh'), 'Refresh prices');
    return h('div', { class: 'row wrap between', style: { gap: '10px' } },
      h('div', { class: 'muted fs-xs' }, open.length && asof ? `Prices as of ${F.dateTime(asof)} (${F.ago(asof)}). Saved on this device only.` : 'Saved on this device only. Use Export to back it up or move it to another device.'),
      h('div', { class: 'row wrap', style: { gap: '8px' } }, open.length ? rf : null,
        h('button', { class: 'btn sm primary', type: 'button', onclick: () => openPositionForm({ onDone: redraw }) }, icon('plus'), 'Add a position'), ...backupButtons(redraw)));
  }

  function summary(open, closed) {
    const evs = open.map(t => ({ t, ev: J.evaluate(t) }));
    const priced = evs.filter(x => F.isNum(x.ev.pl));
    const pl = priced.reduce((a, x) => a + x.ev.pl, 0);
    const cost = priced.reduce((a, x) => a + x.ev.cost, 0);
    const value = evs.reduce((a, x) => a + (x.ev.value ?? x.ev.cost), 0);
    const riskAmt = evs.reduce((a, x) => a + (x.ev.riskAmt || 0), 0);
    const unstopped = evs.filter(x => x.ev.riskAmt == null).length;
    const eq = Z.equity();
    const heat = eq > 0 ? riskAmt / eq * 100 : null;
    const cap = Z.profileCfg().max_heat_pct;
    const cl = closed.map(t => J.evaluate(t));
    const realised = cl.reduce((a, e) => a + (e.pl || 0), 0);
    const wins = cl.filter(e => e.pl > 0).length;
    const rs = cl.map(e => e.r).filter(F.isNum);
    const avgR = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null;
    return h('div', { class: 'stats' },
      stat('Open positions', String(open.length), open.length ? `${money(value)} at current prices` : 'None right now'),
      stat('Unrealised result', open.length ? signedMoney(pl) : F.NA, cost ? `${F.pct(pl / cost * 100, 1)} on ${money(cost)} cost` : null, { cls: F.dirClass(pl) }),
      stat('At risk to stops', open.length ? money(riskAmt) : F.NA, heat != null && open.length ? `${F.num(heat, 2)}% of your account${cap ? ` (budget ${F.num(cap, 1)}%)` : ''}${unstopped ? ` · ${unstopped} without a stop` : ''}` : null,
        { cls: heat != null && cap && heat > cap ? 'down' : null, info: 'What you would lose from your entry prices if every stop were hit, as a share of the account size in Settings. Positions without a stop are not counted.' }),
      stat('Closed trades', String(closed.length), closed.length ? `${F.num(wins / closed.length * 100, 0)}% won${avgR != null ? `, average ${F.r(avgR, 2)}` : ''}` : 'Nothing closed yet'),
      stat('Realised result', closed.length ? signedMoney(realised) : F.NA, null, { cls: F.dirClass(realised) }));
  }

  // Phone layout: one card per position instead of a ten-column table.
  function posCard(t, closedView) {
    const ev = J.evaluate(t);
    const px = n => F.px(n, t.market);
    const levels = closedView ? null : [F.isNum(t.stop) ? `Stop ${px(t.stop)}` : 'No stop', F.isNum(t.t1) ? `T1 ${px(t.t1)}` : null, F.isNum(t.t2) ? `T2 ${px(t.t2)}` : null].filter(Boolean).join(' · ');
    const held = closedView ? J.heldDays(t) : null;
    return h('div', { class: ['pos-card', !closedView && ev.flags.includes('stop') && 'alert'] },
      h('div', { class: 'pc-top' },
        h('div', { class: 'pc-id' }, h('div', { class: 'row', style: { gap: '8px' } }, h('a', { class: 'sym', ...linkProps('/i/' + t.idea) }, t.sym), t.dir === 'short' ? chip('Short', 'down') : chip('Long', 'up')), h('div', { class: 'nm muted fs-xs' }, t.name)),
        h('div', { class: 'pc-pl' }, F.isNum(ev.pl) ? h('b', { class: F.dirClass(ev.pl) }, signedMoney(ev.pl)) : F.NA,
          h('div', { class: 'fs-xs muted' }, F.isNum(ev.pct) ? `${F.pct(ev.pct, 1)}${F.isNum(ev.r) ? ' · ' + F.r(ev.r, 1) : ''}` : ''))),
      h('div', { class: 'pc-line mono' }, `${qtyText(t)} @ ${px(t.entry)}`, h('span', { class: 'muted' }, ' → '), closedView ? `exit ${px(t.exit)}` : F.isNum(ev.px) ? `now ${px(ev.px)}` : 'no price'),
      levels ? h('div', { class: 'pc-line fs-xs muted' }, levels) : null,
      closedView ? null : h('div', { class: 'row wrap', style: { gap: '6px' } }, ...flagChips(ev)),
      h('div', { class: 'pc-foot' },
        h('span', { class: 'fs-xs muted' }, closedView ? `Held ${held == null ? F.NA : F.days(held)} · closed ${F.date(t.closed)}` : `Opened ${F.date(t.opened)}`),
        h('div', { class: 'row', style: { gap: '4px' } },
          closedView ? null : h('button', { class: 'btn sm', type: 'button', onclick: () => openClosePosition(t, redraw) }, 'Close'),
          closedView ? null : h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Edit ${t.sym}`, onclick: () => openPositionForm({ trade: t, onDone: redraw }) }, icon('edit')),
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Delete ${t.sym}`, onclick: () => deletePosition(t, redraw) }, icon('trash')))));
  }

  function openTable(open) {
    if (!open.length) return h('div', { class: 'empty' }, icon('brief'), h('div', { class: 't' }, 'No open positions'), h('div', null, 'Add a trade you took, or press “Log this trade” on any idea.'));
    const acct = Z.acctCcy();
    const rows = open.slice().sort(by(t => t.opened || '', -1));
    return h('section', { class: 'card pos-section' },
      h('div', { class: 'card-h' }, h('div', { class: 'label' }, 'Open positions')),
      h('div', { class: 'pos-cards' }, ...rows.map(t => posCard(t, false))),
      h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl fixed-first' },
        h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', null, 'Side'), h('th', { class: 'r' }, 'Quantity'), h('th', { class: 'r' }, 'Entry'), h('th', { class: 'r' }, 'Now'), h('th', { class: 'r' }, `Result (${acct})`), h('th', { class: 'r' }, 'Stop'), h('th', null, 'Targets'), h('th', null, 'Opened'), h('th', null, ''))),
        h('tbody', null, ...rows.map(t => {
          const ev = J.evaluate(t);
          const px = n => F.px(n, t.market);
          return h('tr', { class: ['link', ev.flags.includes('stop') && 'alert'], onclick: e => { if (!e.target.closest('a,button')) go('/i/' + t.idea); } },
            h('td', null, h('div', { class: 'sym' }, h('a', { ...linkProps('/i/' + t.idea) }, t.sym)), h('div', { class: 'nm' }, t.name)),
            h('td', null, t.dir === 'short' ? chip('Short', 'down') : chip('Long', 'up')),
            h('td', { class: 'r mono' }, qtyText(t)),
            h('td', { class: 'r mono' }, px(t.entry)),
            h('td', { class: 'r mono' }, F.isNum(ev.px) ? px(ev.px) : F.NA),
            h('td', { class: 'r' }, F.isNum(ev.pl)
              ? h('div', null, h('b', { class: F.dirClass(ev.pl) }, signedMoney(ev.pl)), h('div', { class: 'fs-xs muted' }, `${F.pct(ev.pct, 1)}${F.isNum(ev.r) ? ' · ' + F.r(ev.r, 1) : ''}${ev.rateKnown ? '' : ' · rate missing'}`))
              : F.NA),
            h('td', { class: 'r mono' }, F.isNum(t.stop) ? px(t.stop) : h('span', { class: 'muted' }, 'none')),
            h('td', null, h('div', { class: 'stack', style: { gap: '4px' } },
              ...(F.isNum(t.t1) || F.isNum(t.t2) ? [F.isNum(t.t1) ? h('span', { class: 'mono fs-xs' }, 'T1 ' + px(t.t1)) : null, F.isNum(t.t2) ? h('span', { class: 'mono fs-xs' }, 'T2 ' + px(t.t2)) : null] : [h('span', { class: 'muted' }, 'none')]),
              ...flagChips(ev))),
            h('td', { class: 'muted' }, F.date(t.opened)),
            h('td', null, h('div', { class: 'row', style: { gap: '4px', justifyContent: 'flex-end' } },
              h('button', { class: 'btn sm', type: 'button', onclick: () => openClosePosition(t, redraw) }, 'Close'),
              h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Edit ${t.sym}`, title: 'Edit', onclick: () => openPositionForm({ trade: t, onDone: redraw }) }, icon('edit')),
              h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Delete ${t.sym}`, title: 'Delete', onclick: () => deletePosition(t, redraw) }, icon('trash')))));
        })))));
  }

  function closedTable(closed) {
    const acct = Z.acctCcy();
    const part = closed.slice(0, closedShown);
    return h('section', { class: 'card pos-section' },
      h('div', { class: 'card-h' }, h('div', { class: 'label' }, `Closed trades (${closed.length})`)),
      h('div', { class: 'pos-cards' }, ...part.map(t => posCard(t, true))),
      h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl fixed-first' },
        h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', null, 'Side'), h('th', { class: 'r' }, 'Quantity'), h('th', { class: 'r' }, 'Entry'), h('th', { class: 'r' }, 'Exit'), h('th', { class: 'r' }, `Result (${acct})`), h('th', { class: 'r' }, 'Held'), h('th', null, 'Closed'), h('th', null, ''))),
        h('tbody', null, ...part.map(t => {
          const ev = J.evaluate(t);
          const px = n => F.px(n, t.market);
          const held = J.heldDays(t);
          return h('tr', null,
            h('td', null, h('div', { class: 'sym' }, h('a', { ...linkProps('/i/' + t.idea) }, t.sym)), h('div', { class: 'nm' }, t.name)),
            h('td', null, t.dir === 'short' ? chip('Short', 'down') : chip('Long', 'up')),
            h('td', { class: 'r mono' }, qtyText(t)), h('td', { class: 'r mono' }, px(t.entry)), h('td', { class: 'r mono' }, px(t.exit)),
            h('td', { class: 'r' }, F.isNum(ev.pl) ? h('div', null, h('b', { class: F.dirClass(ev.pl) }, signedMoney(ev.pl)), h('div', { class: 'fs-xs muted' }, `${F.pct(ev.pct, 1)}${F.isNum(ev.r) ? ' · ' + F.r(ev.r, 1) : ''}`)) : F.NA),
            h('td', { class: 'r' }, held == null ? F.NA : F.days(held)),
            h('td', { class: 'muted' }, F.date(t.closed)),
            h('td', null, h('div', { class: 'row', style: { justifyContent: 'flex-end' } }, h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Delete ${t.sym}`, title: 'Delete', onclick: () => deletePosition(t, redraw) }, icon('trash')))));
        })))),
      closed.length > part.length ? h('div', { class: 'row', style: { justifyContent: 'center', padding: 'var(--s-3)' } }, h('button', { class: 'btn sm', type: 'button', onclick: () => { closedShown += 20; draw(); } }, `Show more (${closed.length - part.length} left)`)) : null);
  }

  function draw() {
    const all = J.list();
    const open = all.filter(t => t.status === 'open');
    const closed = all.filter(t => t.status === 'closed').sort(by(t => t.closed || t.opened || '', -1));
    clear(root);
    root.append(head(open));
    if (!all.length) {
      root.append(h('div', { class: 'empty' }, icon('brief'), h('div', { class: 't' }, 'No positions yet'),
        h('div', null, 'When you take a trade, press “Log this trade” in the idea’s ticket, or add it here. Acumen then follows it against your stop and targets and shows live results.'),
        h('div', { class: 'row', style: { justifyContent: 'center', marginTop: 'var(--s-3)', gap: '8px' } },
          h('a', { class: 'btn', ...linkProps('/ideas') }, 'Browse trade ideas'),
          h('button', { class: 'btn primary', type: 'button', onclick: () => openPositionForm({ onDone: redraw }) }, icon('plus'), 'Add a position'))));
      return;
    }
    const evs = open.map(t => J.evaluate(t));
    const hit = open.filter((t, i) => evs[i].flags.includes('stop'));
    if (hit.length) root.append(banner('warn', h('b', null, `${hit.length === 1 ? 'A stop level has' : hit.length + ' stop levels have'} been reached: `), hit.map(t => t.sym).join(', ') + '. Check your broker; the position may already have been closed or may be gapping beyond the stop.'));
    if (evs.some(e => !e.rateKnown)) root.append(banner('info', 'Some currency rates are missing from the data, so those positions are shown in the instrument’s own currency instead of ' + Z.acctCcy() + '.'));
    root.append(summary(open, closed), openTable(open));
    if (closed.length) root.append(closedTable(closed));
    root.append(h('p', { class: 'fineprint' }, 'Results use the latest downloaded prices, not your broker’s fills, and ignore commissions, taxes and financing. Compare with your broker statements.',
      explain('Acumen cannot see your brokerage account. Positions here are what you typed in. “Result” is price change × quantity, converted to your account currency with the latest rate in the data.')));
  }

  draw();
  return { el: root, redraw: draw };
}
