import { S } from './store.js';
import { D } from './data.js';
import { uid } from './util.js';
import * as F from './fmt.js';
import { isNum } from './fmt.js';
import { ccyAcct, quoteCcy } from './sizing.js';

export function list() { return S.get('journal') || []; }

export function add(t) {
  const trade = { id: uid(), status: 'open', ...t };
  S.set('journal', [trade, ...list()]);
  return trade;
}
export function update(id, patch) { S.set('journal', list().map(t => (t.id === id ? { ...t, ...patch } : t))); }
export function remove(id) { S.set('journal', list().filter(t => t.id !== id)); }

export function lastPrice(id) {
  const p = D.peek('prices');
  const q = p && p.quotes && p.quotes[id];
  return q ? { px: q[0], chg: q[1], ts: q[2] } : null;
}

// Live and realised numbers for one journal row, in the account currency.
export function evaluate(t) {
  const sign = t.dir === 'short' ? -1 : 1;
  const known = ccyAcct(t.ccy || quoteCcy({ market: t.market, sym: t.sym }));
  const rate = known ?? 1;
  const hasStop = isNum(t.stop) && t.stop > 0;
  const risk = hasStop ? Math.abs(t.entry - t.stop) : 0;
  const live = lastPrice(t.idea);
  const px = t.status === 'closed' ? t.exit : live && live.px;
  const move = isNum(px) ? (px - t.entry) * sign : null;
  const pl = move == null ? null : move * t.qty * rate;
  const rMult = move == null || !risk ? null : move / risk;
  const flags = [];
  if (t.status === 'open' && isNum(px)) {
    if (hasStop && (sign > 0 ? px <= t.stop : px >= t.stop)) flags.push('stop');
    if (isNum(t.t1) && (sign > 0 ? px >= t.t1 : px <= t.t1)) flags.push('t1');
    if (isNum(t.t2) && (sign > 0 ? px >= t.t2 : px <= t.t2)) flags.push('t2');
  }
  return {
    px, pl, r: rMult, flags, rate, rateKnown: known != null, stale: live ? live.ts : null,
    pct: isNum(px) ? (move / t.entry) * 100 : null,
    cost: t.entry * t.qty * rate,
    value: isNum(px) ? px * t.qty * rate : null,
    riskAmt: hasStop ? risk * t.qty * rate : null,
  };
}

// Whole calendar days a closed (or still open) position was held.
export function heldDays(t) {
  const a = F.parseDate(t.opened);
  const b = t.status === 'closed' && t.closed ? F.parseDate(t.closed) : new Date();
  if (!a || !b) return null;
  return Math.max(0, Math.round((b - a) / 86400000));
}
