import test from 'node:test';
import assert from 'node:assert/strict';
import { groupByTier, gauge, positionCap, centsText, headline, nearMisses, ageLabel, ofText, recordHeadline, recordStatus } from '../src/lib/bargains.js';

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

test('an age is worded in months or years, and a count says how many of how many', () => {
  assert.deepEqual([90, 180, 365, 730, 30, 45, 0, null].map(ageLabel), ['3 months', '6 months', '1 year', '2 years', '30 days', '45 days', '', '']);
  assert.equal(ofText(2, 3), '2 of 3');
  assert.equal(ofText(0, 3), '0 of 3');
  assert.equal(ofText(0, 0), '');
  assert.equal(ofText(null, 3), '');
});

test('the record headline tells an empty record, a young one and one that can be read apart', () => {
  const hz = (days, n, ready) => ({ days, n, ready });
  const rows = [{ sym: 'A' }, { sym: 'B' }];
  assert.equal(recordHeadline(null).kind, 'none');
  assert.equal(recordHeadline({ rows: [], horizons: [hz(90, 0, false)] }).kind, 'none');
  const early = recordHeadline({ rows, horizons: [hz(90, 2, false), hz(180, 0, false)], counts: { tracked: 2, open: 2 }, since: '2026-11-02', oldest_days: 95, min_shares: 20 });
  assert.equal(early.kind, 'early');
  assert.equal(early.best, null);
  assert.deepEqual([early.tracked, early.open, early.since, early.oldest, early.min], [2, 2, '2026-11-02', 95, 20]);
  const ok = recordHeadline({ rows, horizons: [hz(90, 30, true), hz(180, 22, true), hz(365, 4, false)], counts: { tracked: 30 } });
  assert.equal(ok.kind, 'readable');
  assert.equal(ok.best.days, 180);  // the longest age that has enough shares
});

test('a followed share says how it ended, or the most telling thing that is true of it now', () => {
  const open = { status: 'open', last: 20, review_below: 11, listed_now: true, t1_date: null };
  assert.equal(recordStatus(open).key, 'following');
  assert.equal(recordStatus({ ...open, listed_now: false }).key, 'dropped');
  assert.equal(recordStatus({ ...open, t1_date: '2026-12-01' }).key, 'first');
  assert.equal(recordStatus({ ...open, t1_date: '2026-12-01', last: 10 }).key, 'review');  // below the re-read price outranks an old milestone
  assert.deepEqual(recordStatus({ status: 'closed', reason: 'fair_value' }), { key: 'fair', label: 'Reached fair value', tone: 'up' });
  assert.equal(recordStatus({ status: 'closed', reason: 'time' }).key, 'time');
  assert.equal(recordStatus(null).key, 'none');
});
