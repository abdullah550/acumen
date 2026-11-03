import test from 'node:test';
import assert from 'node:assert/strict';
import { groupByTier, gauge, positionCap, centsText, headline, nearMisses } from '../src/lib/bargains.js';

const tiers = [
  { min_mos: 50, max_cents: 50, label: '50 cents on the dollar or less', name: 'A dollar for fifty cents' },
  { min_mos: 35, max_cents: 65, label: '51 to 65 cents on the dollar', name: '35% or more below fair value' },
  { min_mos: 20, max_cents: 80, label: '66 to 80 cents on the dollar', name: '20% or more below fair value' },
];
const pick = (sym, tier, extra = {}) => ({ sym, tier, px: 40, fair: 72, mos_pct: 44, cents: 56, models: [{ name: 'a', value: 70 }, { name: 'b', value: 72 }, { name: 'c', value: 87 }], ...extra });

test('picks are grouped under their tier, an empty tier is kept, and nothing is lost', () => {
  const picks = [pick('A', 35), pick('B', 50), pick('C', 35)];
  const groups = groupByTier(picks, tiers);
  assert.deepEqual(groups.map(g => g.rows.map(r => r.sym)), [['B'], ['A', 'C'], []]);
  assert.equal(groups.reduce((n, g) => n + g.rows.length, 0), picks.length);
  assert.deepEqual(groupByTier(null, null), []);
});

test('the gauge puts every mark on the line and the gap between price and fair value inside it', () => {
  const g = gauge(pick('A', 35));
  const top = 87 * 1.06;
  assert.ok(Math.abs(g.px - (40 / top) * 100) < 0.06);
  assert.ok(Math.abs(g.fair - (72 / top) * 100) < 0.06);
  assert.equal(g.from, g.px);
  assert.equal(g.to, g.fair);
  assert.deepEqual(g.models.map(m => m.name), ['a', 'b', 'c']);
  for (const v of [g.px, g.fair, g.from, g.to, ...g.models.map(m => m.at)]) assert.ok(v >= 0 && v <= 100);
  // a price above fair value still has a gap with from < to
  const up = gauge(pick('Z', 20, { px: 90, fair: 72 }));
  assert.ok(up.from < up.to && up.to === up.px);
});

test('the gauge refuses figures it cannot draw', () => {
  assert.equal(gauge(null), null);
  assert.equal(gauge({ px: 0, fair: 10 }), null);
  assert.equal(gauge({ px: 10, fair: NaN }), null);
  assert.ok(gauge({ px: 10, fair: 20 }), 'yardsticks are optional');
  assert.equal(gauge({ px: 10, fair: 20, models: [{ name: 'x', value: -3 }, null] }).models.length, 0);
});

test('the position cap is a share of the account and a whole number of shares', () => {
  assert.deepEqual(positionCap(1_000_000, 5, 14.88), { amount: 50000, shares: 3360 });
  assert.equal(positionCap(null, 5, 14.88), null);
  assert.equal(positionCap(1_000_000, 0, 14.88), null);
  assert.equal(positionCap(1_000_000, 5, 0), null);
  assert.equal(positionCap(-5, 5, 10), null);
});

test('cents on the dollar are worded, and a price at fair value is not called a discount', () => {
  assert.equal(centsText(55.4), '55 cents on the dollar');
  assert.equal(centsText(100), 'Not below fair value');
  assert.equal(centsText(null), '');
});

test('the headline tells nothing, the nearest, or the deepest tier apart', () => {
  const some = headline({ tiers, picks: [pick('B', 50), pick('A', 35)], counts: { excluded: 4 } });
  assert.equal(some.kind, 'some');
  assert.equal(some.reached, 1);
  assert.equal(some.excluded, 4);
  const short = headline({ tiers, picks: [pick('A', 35)], counts: { excluded: 0 } });
  assert.equal(short.kind, 'short');
  assert.equal(short.top.max_cents, 50);
  assert.equal(headline({ tiers, picks: [], counts: { excluded: 2 } }).kind, 'none');
  assert.equal(headline(null).kind, 'none');
});

test('near misses come first: fewest failed checks, then the deepest discount, without touching the input', () => {
  const rows = [
    { sym: 'ERR', mos_pct: 92, reasons: ['a', 'b', 'c'] },
    { sym: 'ONE', mos_pct: 38, reasons: ['a'] },
    { sym: 'ONE2', mos_pct: 62, reasons: ['a'] },
    { sym: 'TWO', mos_pct: 50, reasons: ['a', 'b'] },
  ];
  assert.deepEqual(nearMisses(rows).map(r => r.sym), ['ONE2', 'ONE', 'TWO', 'ERR']);
  assert.equal(rows[0].sym, 'ERR');
  assert.deepEqual(nearMisses(null), []);
});
