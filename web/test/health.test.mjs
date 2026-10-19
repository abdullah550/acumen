// Run with: node --test web/test   (no browser needed: lib/health.js has no DOM code)
import test from 'node:test';
import assert from 'node:assert/strict';
import { assess, span, marketState } from '../src/lib/health.js';

const NOW = new Date(2026, 9, 1, 12, 0, 0); // Thursday 1 Oct 2026, local time
const hoursAgo = h => new Date(NOW.getTime() - h * 36e5).toISOString();
const day = n => { const d = new Date(2026, 9, 1 - n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const mk = (asof, status = 'ok', extra = {}) => ({ label: 'x', currency: 'PKR', asof, instruments: 10, status, history_pct: 100, last_success: hoursAgo(2), note: null, ...extra });
const src = (name, market, ok = true, message = 'ok') => ({ id: name, name, market, ok, message, at: hoursAgo(2), items: 5, duration_s: 1.2 });
const healthy = (over = {}) => ({
  generated_at: hoursAgo(3), demo: false,
  markets: { psx: mk(day(1)), fx: mk(day(1)), crypto: mk(day(0)) },
  sources: [src('a', 'psx'), src('b', 'fx'), src('c', 'crypto')],
  health: { analysed: 100, failed: 0, failed_examples: [], seconds: 40 },
  ...over,
});

test('fresh, complete data is reported as current with nothing to flag', () => {
  const a = assess(healthy(), { now: NOW });
  assert.equal(a.tone, 'ok');
  assert.equal(a.title, 'All data is current');
  assert.deepEqual(a.issues, []);
  assert.equal(a.markets.length, 3);
  assert.ok(a.markets.every(m => m.state === 'ok'));
});

test('demo data is labelled as such and never reads as healthy', () => {
  const a = assess(healthy({ demo: true }), { now: NOW });
  assert.equal(a.tone, 'warn');
  assert.equal(a.title, 'Demo data');
  assert.match(a.text, /synthetic/);
  assert.deepEqual(a.issues, [], 'the headline already says it, so it is not repeated as an issue');
});

test('a refresh that stopped days ago is called out with its age', () => {
  const a = assess(healthy({ generated_at: hoursAgo(5 * 24) }), { now: NOW });
  assert.equal(a.tone, 'warn');
  assert.ok(a.issues.some(i => i.title === 'Last refreshed 5 days ago'));
});

test('weekends and holidays are allowed for before prices are called old', () => {
  // PSX: Friday close seen on Monday is 3 days, the limit is 4
  assert.equal(marketState('psx', mk(day(3)), NOW).state, 'ok');
  assert.equal(marketState('psx', mk(day(4)), NOW).state, 'ok');
  assert.equal(marketState('psx', mk(day(5)), NOW).state, 'stale');
  // crypto trades every day, so 3 days is already old
  assert.equal(marketState('crypto', mk(day(2)), NOW).state, 'ok');
  assert.equal(marketState('crypto', mk(day(3)), NOW).state, 'stale');
});

test('old prices raise a warning that names the market and the age', () => {
  const m = healthy(); m.markets.psx = mk(day(6));
  const a = assess(m, { now: NOW });
  assert.equal(a.tone, 'warn');
  const i = a.issues.find(x => /PSX prices are 6 days old/.test(x.title));
  assert.ok(i, 'expected a PSX staleness issue');
  assert.match(i.text, /ideas as out of date/);
  assert.match(i.text, /up to 4 days/);
});

test('a market with no data is a bad state and carries the reason', () => {
  const m = healthy(); m.markets.fx = mk(day(1), 'error', { note: 'yahoo: HTTP 429; stooq: timeout' });
  const a = assess(m, { now: NOW });
  assert.equal(a.tone, 'bad');
  assert.equal(a.title, 'Some data is missing');
  assert.equal(a.issues[0].tone, 'bad');
  assert.match(a.issues[0].text, /HTTP 429/);
});

test('a failed source is only information when every market is still current', () => {
  const m = healthy({ sources: [src('a', 'psx'), src('b', 'fx', false, 'HTTP 403'), src('c', 'crypto')] });
  const a = assess(m, { now: NOW });
  assert.equal(a.tone, 'ok', 'a fallback covered it');
  const i = a.issues.find(x => /1 of 3 data sources failed/.test(x.title));
  assert.ok(i); assert.equal(i.tone, 'info'); assert.match(i.text, /HTTP 403/);
  assert.equal(a.sources[0].name, 'b', 'failed sources are listed first');
});

test('a failed source next to an out-of-date market is a warning', () => {
  const m = healthy({ sources: [src('a', 'psx', false, 'timeout'), src('b', 'fx'), src('c', 'crypto')] });
  m.markets.psx = mk(day(9));
  const a = assess(m, { now: NOW });
  assert.equal(a.tone, 'warn');
  assert.equal(a.issues.find(x => /data sources failed/.test(x.title)).tone, 'warn');
});

test('history that is still loading is information, not an alarm', () => {
  const m = healthy(); m.markets.crypto = mk(day(0), 'warming', { history_pct: 62 });
  const a = assess(m, { now: NOW });
  assert.equal(a.tone, 'ok');
  assert.ok(a.issues.some(i => /history is still loading \(62%\)/.test(i.title)));
});

test('instruments that could not be analysed are counted, and many of them is a warning', () => {
  const few = assess(healthy({ health: { analysed: 200, failed: 2, failed_examples: ['PSX:AAA: no prices'], seconds: 30 } }), { now: NOW });
  assert.equal(few.tone, 'ok');
  assert.match(few.issues[0].title, /2 instruments could not be analysed/);
  const many = assess(healthy({ health: { analysed: 80, failed: 20, failed_examples: [], seconds: 30 } }), { now: NOW });
  assert.equal(many.tone, 'warn');
});

test('the worst issue comes first', () => {
  const m = healthy({ sources: [src('a', 'psx', false, 'x'), src('b', 'fx'), src('c', 'crypto')] });
  m.markets.fx = mk(day(1), 'error');
  const a = assess(m, { now: NOW });
  const order = a.issues.map(i => i.tone);
  assert.deepEqual(order, [...order].sort((x, y) => ({ bad: 3, warn: 2, info: 1 }[y] - { bad: 3, warn: 2, info: 1 }[x])));
});

test('missing or partial meta never throws', () => {
  for (const meta of [null, undefined, {}, { markets: {} }, { sources: null, health: null }, { markets: { psx: { asof: null } } }]) {
    const a = assess(meta, { now: NOW });
    assert.ok(['ok', 'warn', 'bad'].includes(a.tone));
    assert.ok(Array.isArray(a.issues) && Array.isArray(a.markets) && Array.isArray(a.sources));
  }
});

test('durations read naturally', () => {
  assert.equal(span(0.4), 'about an hour');
  assert.equal(span(5), '5 hours');
  assert.equal(span(47), '47 hours');
  assert.equal(span(72), '3 days');
  assert.equal(span(null), 'an unknown time');
});
