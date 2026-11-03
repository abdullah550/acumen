// Bargains, the pure part: how the page groups shares under their "cents on the dollar" headings, where a price sits against its
// yardsticks, what the position cap allows. No DOM and no network, so node --test can exercise it.

const isNum = x => typeof x === 'number' && Number.isFinite(x);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const round1 = v => Math.round(v * 10) / 10;

/** Picks under the file's tier headings, best tier first. A tier nobody reaches is kept, so the page can say "none today". */
export function groupByTier(picks, tiers) {
  const list = Array.isArray(picks) ? picks : [];
  return (Array.isArray(tiers) ? tiers : []).map(tier => ({ tier, rows: list.filter(p => p.tier === tier.min_mos) }));
}

/**
 * Where the price, the three yardsticks and fair value sit on one line, as shares of its width (0 to 100). The line runs from zero to a
 * little past the highest figure, so every mark is on it. `from`/`to` bound the gap between price and fair value.
 */
export function gauge(row) {
  if (!row || !isNum(row.px) || !isNum(row.fair) || row.px <= 0 || row.fair <= 0) return null;
  const models = (row.models || []).filter(m => m && isNum(m.value) && m.value > 0);
  const top = Math.max(row.px, row.fair, ...models.map(m => m.value)) * 1.06;
  const at = v => round1(clamp((v / top) * 100, 0, 100));
  const px = at(row.px);
  const fair = at(row.fair);
  return { px, fair, from: Math.min(px, fair), to: Math.max(px, fair), models: models.map(m => ({ name: m.name, value: m.value, at: at(m.value) })) };
}

/** The largest position the page suggests, in rupees, and the shares it buys at the price. `null` while any figure is unknown. */
export function positionCap(equityPkr, capPct, px) {
  if (![equityPkr, capPct, px].every(isNum) || equityPkr <= 0 || capPct <= 0 || px <= 0) return null;
  const amount = (equityPkr * capPct) / 100;
  return { amount, shares: Math.floor(amount / px) };
}

/** "55 cents on the dollar"; a price at or above fair value is not a discount. */
export function centsText(cents) {
  if (!isNum(cents)) return '';
  return cents >= 100 ? 'Not below fair value' : `${Math.round(cents)} cents on the dollar`;
}

/** What the page says first. `none`: nothing passes every check; `short`: nothing reaches the deepest tier, so the nearest are shown; `some`: it does. */
export function headline(body) {
  const picks = body && Array.isArray(body.picks) ? body.picks : [];
  const tiers = body && Array.isArray(body.tiers) ? body.tiers : [];
  const top = tiers[0] || null;
  const reached = top ? picks.filter(p => p.tier === top.min_mos).length : 0;
  const excluded = body && body.counts ? body.counts.excluded || 0 : 0;
  if (!picks.length) return { kind: 'none', top, reached, excluded };
  if (!reached) return { kind: 'short', top, reached, excluded };
  return { kind: 'some', top, reached, excluded };
}

/** Cheap-on-paper shares with the fewest failed checks first (a near miss says more than a data error), then the deepest discount. */
export function nearMisses(rows) {
  const n = r => (r && Array.isArray(r.reasons) ? r.reasons.length : 99);
  return (Array.isArray(rows) ? rows : []).slice().sort((a, b) => n(a) - n(b) || (b.mos_pct || 0) - (a.mos_pct || 0));
}
