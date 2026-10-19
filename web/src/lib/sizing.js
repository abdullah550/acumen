import { S } from './store.js';
import { D } from './data.js';
import { isNum } from './fmt.js';

export const GRADE_RISK_MULT = { A: 1.0, B: 0.75, C: 0.5, D: 0.25 };

export function cfg() { return (D.meta && D.meta.config) || {}; }
export function usdPkr() { const v = cfg().usd_pkr; return isNum(v) && v > 0 ? v : null; }
export function acctCcy() { return S.get('accountCcy') === 'USD' ? 'USD' : 'PKR'; }

export function profileCfg(name) {
  const p = (cfg().profiles || {})[name || S.get('profile')] || (cfg().profiles || {}).balanced || {};
  return { risk_per_trade_pct: 1, max_heat_pct: 6, max_positions: 8, min_grade: 'B', ...p };
}

export function defaultEquity() {
  const base = cfg().default_equity || 1000000;
  const fx = usdPkr();
  if (acctCcy() === 'USD') return fx ? Math.round(base / fx / 100) * 100 : 5000;
  return base;
}
export function equity() { const e = S.get('equity'); return isNum(e) && e > 0 ? e : defaultEquity(); }
export function baseRiskPct() { const r = S.get('riskPct'); return isNum(r) && r > 0 ? r : profileCfg().risk_per_trade_pct; }

// Risk to take on this idea (percent of equity), mirroring the engine's rule.
export function ideaRiskPct(idea) {
  const g = GRADE_RISK_MULT[idea.grade] ?? 0.25;
  return Math.round(baseRiskPct() * g * (idea.calibrated ? 1 : 0.5) * 100) / 100;
}

export function quoteCcy(idea) {
  if (idea.market === 'psx') return 'PKR';
  if (idea.market === 'crypto') return 'USD';
  const sym = idea.sym || '';
  return sym.length >= 6 ? sym.slice(-3) : 'USD';
}

// USD value of one unit of `ccy`, or null when unknown
export function ccyUsd(ccy) {
  if (ccy === 'USD') return 1;
  if (ccy === 'PKR') { const f = usdPkr(); return f ? 1 / f : null; }
  const prices = D.peek('prices');
  const q = prices && prices.quotes;
  if (!q) return null;
  const direct = q['FX:' + ccy + 'USD'];
  if (direct && isNum(direct[0])) return direct[0];
  const inv = q['FX:USD' + ccy];
  if (inv && isNum(inv[0]) && inv[0] !== 0) return 1 / inv[0];
  return null;
}

// Account-currency value of one unit of `ccy`
export function ccyAcct(ccy) {
  const u = ccyUsd(ccy);
  if (u == null) return null;
  if (acctCcy() === 'USD') return u;
  const f = usdPkr();
  return f ? u * f : null;
}

export function unitStep(idea) {
  if (idea.market === 'psx') return 1;
  if (idea.market === 'fx') return 1000;
  return 0;
}

// Position size for an idea. Returns amounts in the account currency.
export function size(idea, { eq = equity(), riskPct = ideaRiskPct(idea), entry } = {}) {
  const qc = quoteCcy(idea);
  const fx = ccyAcct(qc);
  const ent = isNum(entry) ? entry : (idea.entry && idea.entry.px) || idea.price;
  const rpu = Math.abs(ent - idea.stop);
  const out = { ok: false, quoteCcy: qc, acct: acctCcy(), eq, riskPct, entry: ent, notes: [] };
  if (!isNum(ent) || !isNum(rpu) || rpu <= 0) { out.notes.push('No valid stop distance.'); return out; }
  if (fx == null) {
    out.notes.push(`No ${qc}/${acctCcy()} rate in the data, so amounts are shown in ${qc}.`);
  }
  const rate = fx ?? 1;
  out.rate = rate;
  const riskAmt = eq * (riskPct / 100);
  let units = riskAmt / (rpu * rate);
  let binding = 'risk';
  const maxPosPct = cfg().max_position_pct || 20;
  if (idea.market !== 'fx') {
    const cap = (eq * maxPosPct / 100) / (ent * rate);
    if (units > cap) { units = cap; binding = 'position cap'; }
  }
  const adv = idea.liquidity && idea.liquidity.max_units_by_adv;
  if (isNum(adv) && adv > 0 && units > adv) { units = adv; binding = 'liquidity'; }
  const step = unitStep(idea);
  const rawUnits = units;
  if (step > 0) units = Math.floor(units / step) * step;
  else units = Number(units.toPrecision(4));
  out.units = units;
  out.binding = binding;
  out.notional = units * ent * rate;
  out.maxLoss = units * rpu * rate;
  out.effRiskPct = eq > 0 ? (out.maxLoss / eq) * 100 : null;
  const sign = idea.dir === 'short' ? -1 : 1;
  const t = idea.targets || [];
  const leg = (i, frac) => (t[i] ? units * frac * (t[i].px - ent) * sign * rate : null);
  const f1 = t[0] && isNum(t[0].scale_out) ? t[0].scale_out : 0.5;
  out.gainT1 = leg(0, f1);
  out.gainBoth = t[1] ? (leg(0, f1) ?? 0) + (leg(1, 1 - f1) ?? 0) : null;
  out.gainAllT1 = leg(0, 1);
  out.lots = idea.market === 'fx' ? units / 100000 : null;
  out.ok = units > 0;
  if (!out.ok) {
    out.notes.push(rawUnits > 0 && step > 1
      ? `Your risk budget buys about ${Math.floor(rawUnits).toLocaleString('en-US')} units at this stop distance, below the smallest order size most brokers accept (${step.toLocaleString('en-US')}). Raise the risk percentage or the account size, or pass on this idea.`
      : 'The risk budget is too small for even one unit at this stop distance. Raise the risk percentage or the account size, or pass on this idea.');
  }
  if (idea.market === 'fx') out.notes.push('Forex notional is leveraged: check the margin your broker requires.');
  return out;
}

export function planText(idea, sz) {
  const t = idea.targets || [];
  const side = idea.dir === 'short' ? 'SELL' : 'BUY';
  const lines = [
    `${side} ${idea.sym} (${idea.name}) — ${idea.horizon} horizon, grade ${idea.grade}, conviction ${idea.conviction}`,
    `Entry: ${idea.entry && idea.entry.type === 'limit' ? 'limit ' : 'market '}${idea.entry && idea.entry.px}` + (idea.entry && idea.entry.low != null ? ` (zone ${idea.entry.low}–${idea.entry.high})` : ''),
    `Stop: ${idea.stop} (${idea.stop_pct}%)`,
    ...t.map((x, i) => `Target ${i + 1}: ${x.px} (${x.pct > 0 ? '+' : ''}${x.pct}%, ${x.r}R, ~${Math.round((x.prob || 0) * 100)}% chance, ${x.eta_days ? x.eta_days.join('–') + ' days' : ''})`),
    `Modelled win chance ${Math.round((idea.p_win || 0) * 100)}%, expected ${idea.exp_r}R, reward/risk ${idea.rr}`,
  ];
  if (sz && sz.ok) lines.push(`Size at ${sz.eq.toLocaleString()} ${sz.acct} and ${sz.riskPct}% risk: ${sz.units} units`);
  lines.push('Model estimate, not advice.');
  return lines.join('\n');
}
