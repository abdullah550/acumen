import { h } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { chgSpan, sparkline } from './kit.js';

// Order and grouping of the cross-asset series on the Markets screen. Series the build does not provide are skipped, and any
// series not listed here is shown under "Other" so nothing the data contains is hidden.
export const MACRO_GROUPS = [
  { title: 'Dollar and the rupee', keys: ['USDPKR', 'DXY'] },
  { title: 'Risk appetite', keys: ['SPX', 'MSCI_EM', 'VIX'] },
  { title: 'Commodities', keys: ['GOLD', 'BRENT'] },
  { title: 'US interest rates', keys: ['FEDFUNDS', 'DGS2', 'US10Y', 'T10Y2Y'] },
];

// Yields and spreads are quoted in percent, so their changes are shown in basis points (1 bp = 0.01 percentage point).
const RATE_SERIES = new Set(['US10Y', 'DGS2', 'FEDFUNDS', 'T10Y2Y']);

export function macroValue(k, x) {
  if (!x || !F.isNum(x.last)) return F.NA;
  const dec = x.unit === 'points' || k === 'USDPKR' ? 2 : x.last > 1000 ? 0 : 2;
  return F.num(x.last, dec) + (RATE_SERIES.has(k) ? '%' : '');
}

// A change over 1D / 1W / 1M. Series flagged unit "points" hold differences, not percentages, so they get their own units.
export function macroChg(k, x, key) {
  const v = x ? x[key] : null;
  if (x && x.unit === 'points' && F.isNum(v)) {
    const text = RATE_SERIES.has(k) ? `${F.num(v * 100, 0, { sign: true })} bp` : `${F.num(v, 2, { sign: true })} pts`;
    return chgSpan(v, 2, { text });
  }
  return chgSpan(v, 2);
}

export function macroTile(k, x, { full = false } = {}) {
  if (!full) {
    return h('div', { class: 'mtile' },
      h('div', { class: 'label' }, x.label),
      h('div', { class: 'mt-v' }, macroValue(k, x)),
      h('div', { class: 'mt-c' }, macroChg(k, x, 'chg_1d'), h('span', { class: 'muted' }, '1D')),
      sparkline(x.spark, { hgt: 26 }));
  }
  return h('div', { class: 'mtile full' },
    h('div', { class: 'label' }, x.label),
    h('div', { class: 'mt-top' }, h('div', { class: 'mt-v' }, macroValue(k, x)), h('div', { class: 'mt-spark' }, sparkline(x.spark, { hgt: 34, color: 'var(--accent)' }))),
    h('div', { class: 'mt-chgs' }, [['1D', 'chg_1d'], ['1W', 'chg_1w'], ['1M', 'chg_1m']].map(([lab, key]) => h('div', null, h('span', null, lab), macroChg(k, x, key)))),
    h('div', { class: 'mt-asof' }, 'Last 30 days · as of ' + F.date(x.asof, { year: false })));
}
