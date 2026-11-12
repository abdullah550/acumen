import test from 'node:test';
import assert from 'node:assert/strict';
import {
  STATE_ORDER, stateLabel, countByState, filterSectors, pressureBar, bySector, beneficiaries, pillars,
  verdictView, screenNote, tBar, tMark, resultOf, testName, splitTests, weekRows, liveSummary, backdropItems,
} from '../src/lib/flows.js';

const sec = (name, state, extra = {}) => ({ name, state, pressure7: 5, pressure30: 8, ...extra });
const rows = [sec('Banks', 'entering'), sec('Cement', 'turning_in'), sec('Textile', 'neutral'), sec('Fertilizer', 'fading'), sec('Power', 'leaving'), sec('Oil', 'leaving')];

test('sectors are counted and filtered by state, and every state is present even when empty', () => {
  const c = countByState(rows);
  assert.equal(c.all, 6);
  assert.equal(c.entering, 1);
  assert.equal(c.leaving, 2);
  assert.deepEqual(STATE_ORDER.map(s => c[s]).reduce((a, b) => a + b, 0), 6);
  assert.deepEqual(filterSectors(rows, 'leaving').map(r => r.name), ['Power', 'Oil']);
  assert.equal(filterSectors(rows, 'all').length, 6);
  assert.notEqual(filterSectors(rows, 'all'), rows, 'a copy, so sorting it cannot reorder the file');
  assert.deepEqual(countByState(null).entering, 0);
  assert.deepEqual(filterSectors(null, 'entering'), []);
});

test('the three questions pick the right sectors', () => {
  assert.deepEqual(bySector(rows, 'entering').map(r => r.name), ['Banks']);
  assert.deepEqual(bySector(rows, 'entering', 'turning_in').map(r => r.name), ['Banks', 'Cement']);
  assert.deepEqual(bySector(rows, 'leaving').map(r => r.name), ['Power', 'Oil']);
  assert.deepEqual(bySector(undefined, 'leaving'), []);
});

test('beneficiaries are shares in entering or turning-in sectors that pass at least two tests', () => {
  const cands = [
    { sym: 'A', sector_state: 'entering', pillars: 3 },
    { sym: 'B', sector_state: 'leaving', pillars: 3 },
    { sym: 'C', sector_state: 'turning_in', pillars: 2 },
    { sym: 'D', sector_state: 'neutral', pillars: 2 },
    { sym: 'E', sector_state: 'entering', pillars: 1 },
  ];
  assert.deepEqual(beneficiaries(cands).map(c => c.sym), ['A', 'C']);
  assert.deepEqual(beneficiaries(cands, 1).map(c => c.sym), ['A']);
  assert.deepEqual(beneficiaries(null), []);
});

test('the three tests of a candidate share keep their order and state', () => {
  const p = pillars({ accumulation: true, fundamentals: false, interest: true });
  assert.deepEqual(p.map(x => x.key), ['accumulation', 'fundamentals', 'interest']);
  assert.deepEqual(p.map(x => x.on), [true, false, true]);
  assert.ok(p.every(x => x.label && x.hint));
});

test('the pressure bar shows the side, caps at the full length and treats missing values as empty', () => {
  assert.deepEqual(pressureBar(30), { side: 'pos', width: 50 });
  assert.deepEqual(pressureBar(-15), { side: 'neg', width: 25 });
  assert.equal(pressureBar(200).width, 100);
  assert.equal(pressureBar(-200).width, 100);
  assert.deepEqual(pressureBar(0), { side: 'none', width: 0 });
  assert.deepEqual(pressureBar(null), { side: 'none', width: 0 });
  assert.deepEqual(pressureBar(NaN), { side: 'none', width: 0 });
});

test('state labels come from the file when it names them, and fall back to plain words', () => {
  assert.equal(stateLabel({ states: { entering: 'Inflow' } }, 'entering'), 'Inflow');
  assert.equal(stateLabel({}, 'turning_in'), 'Turning in');
  assert.equal(stateLabel(null, 'leaving'), 'Leaving');
  assert.equal(stateLabel(null, 'odd'), 'odd');
});

test('the verdict leads with the honest reading for every outcome, and never calls an untested result a signal', () => {
  for (const k of ['supported', 'partial', 'reversed', 'not_shown', 'too_short']) {
    const v = verdictView({ verdict: k });
    assert.equal(v.kind, k);
    assert.ok(v.title.length > 10, k);
  }
  const ns = verdictView({ verdict: 'not_shown', sector_line: 'S', stock_lines: ['a', 'b'] });
  assert.equal(ns.tone, 'warn');
  assert.match(ns.title, /did not predict/);
  assert.deepEqual(ns.lines, ['S', 'a', 'b']);
  assert.equal(verdictView({ verdict: 'supported' }).tone, 'info');
  assert.equal(verdictView({ verdict: 'reversed' }).tone, 'warn');
  const none = verdictView(null);
  assert.equal(none.kind, 'none');
  assert.deepEqual(none.lines, []);
});

test('the t-statistic strip runs from the middle to the value, on the right side and capped at the edge', () => {
  assert.deepEqual(tBar(3, 6), { side: 'pos', left: 50, width: 25 });
  assert.deepEqual(tBar(-3, 6), { side: 'neg', left: 25, width: 25 });
  assert.deepEqual(tBar(60, 6), { side: 'pos', left: 50, width: 50 });
  assert.deepEqual(tBar(-60, 6), { side: 'neg', left: 0, width: 50 });
  assert.deepEqual(tBar(null), { side: 'none', left: 50, width: 0 });
  assert.equal(tMark(0, 6), 50);
  assert.equal(tMark(6, 6), 100);
  assert.equal(tMark(-6, 6), 0);
  assert.ok(Math.abs(tMark(2.5, 6) - 70.8333) < 0.001);
  assert.equal(tMark(99, 6), 100, 'a mark beyond the edge stays on the strip');
});

test('a test result is read from its own verdict, with too little data kept apart from no relationship', () => {
  assert.equal(resultOf({ n: 90, t: 3.2, verdict: 'supported' }).label, 'Predicted');
  assert.equal(resultOf({ n: 90, t: -3.4, verdict: 'reversed' }).tone, 'warn');
  assert.equal(resultOf({ n: 90, t: 0.5, verdict: 'none' }).label, 'No relationship shown');
  assert.equal(resultOf({ n: 0, t: null, verdict: 'none' }).label, 'Too little data');
  assert.equal(resultOf(null).label, 'Too little data');
  assert.equal(resultOf({ n: 90, t: null, verdict: 'none' }).label, 'Too little data');
});

test('test names say the level, the window and the look-ahead', () => {
  assert.equal(testName({ level: 'sector', label: '30-day', horizon: 21 }), 'Sectors: 30-day flow, next 21 trading days');
  assert.equal(testName({ level: 'stock', label: '7-day', horizon: 5 }), 'Single shares: 7-day pressure, next 5 trading days');
});

test('tests are split into the two fixed in advance, the other sector tests and the single-share tests, losing none', () => {
  const tests = [
    { id: 'a', level: 'sector', primary: true }, { id: 'b', level: 'sector', primary: false }, { id: 'c', level: 'sector', primary: true },
    { id: 'd', level: 'stock', primary: false }, { id: 'e', level: 'stock', primary: false },
  ];
  const s = splitTests({ tests });
  assert.deepEqual(s.primary.map(t => t.id), ['a', 'c']);
  assert.deepEqual(s.sector.map(t => t.id), ['b']);
  assert.deepEqual(s.stock.map(t => t.id), ['d', 'e']);
  assert.equal(s.primary.length + s.sector.length + s.stock.length, tests.length);
  assert.deepEqual(splitTests(null), { primary: [], sector: [], stock: [] });
});

test('the weekly record shows each week with its scored result only when it has one', () => {
  const weeks = [{ week: '2026-W45', asof: '2026-11-06', entering: ['Banks'], leaving: ['Oil'] }, { week: '2026-W44', asof: '2026-10-30', entering: [], leaving: [] }];
  const live = { rows: [{ week: '2026-W44', spread5: 1.25, spread21: null }] };
  const r = weekRows(weeks, live);
  assert.equal(r.length, 2);
  assert.equal(r[0].spread5, null);
  assert.equal(r[1].spread5, 1.25);
  assert.equal(r[1].spread21, null);
  assert.deepEqual(r[0].entering, ['Banks']);
  assert.deepEqual(weekRows(undefined, undefined), []);
  assert.equal(weekRows(weeks, null)[1].spread5, null);
});

test('the live summary lists only horizons that have a scored week', () => {
  const live = { horizons: { 5: { n: 6, hit: 50, mean_spread: 0.4 }, 21: { n: 0, hit: null, mean_spread: null } } };
  assert.deepEqual(liveSummary(live), [{ days: 5, n: 6, hit: 50, mean: 0.4 }]);
  assert.deepEqual(liveSummary(null), []);
  assert.deepEqual(liveSummary({}), []);
});

test('the backdrop skips what is missing instead of showing blanks', () => {
  const items = backdropItems({ usd_pkr: 281.234, policy_rate_pk: 11, dxy: null, gold: 4012.6, brent: undefined, vix: 17.04 }, { label: 'Risk-on' });
  assert.deepEqual(items.map(i => i.k), ['Pakistan market', 'USD/PKR', 'Policy rate', 'Gold', 'VIX']);
  assert.equal(items.find(i => i.k === 'USD/PKR').v, '281.2');
  assert.equal(items.find(i => i.k === 'Policy rate').v, '11.0%');
  assert.equal(items.find(i => i.k === 'Gold').v, '4,013');
  assert.deepEqual(backdropItems(null, null), []);
  assert.deepEqual(backdropItems({}, {}), []);
});

test('the note under the candidates follows the verdict and never promises a gain', () => {
  for (const k of ['supported', 'partial', 'reversed', 'not_shown', 'too_short', undefined, null]) {
    const n = screenNote(k);
    assert.match(n, /^A screen that tells you where to look, not a recommendation\./, String(k));
    assert.doesNotMatch(n, /will (rise|gain|outperform)/i);
  }
  assert.match(screenNote('not_shown'), /no reliable link/);
  assert.match(screenNote('supported'), /not proof/);
  assert.match(screenNote('reversed'), /worse afterwards/);
  assert.equal(screenNote('nonsense'), screenNote('too_short'));
});
