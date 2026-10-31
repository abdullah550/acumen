// Run with: node --test web/test   (no browser needed: lib/livecore.js has no DOM or network code)
import test from 'node:test';
import assert from 'node:assert/strict';
import * as C from '../src/lib/livecore.js';

const row = (id, m = id.split(':')[0].toLowerCase()) => ({ id, m, sym: id.split(':')[1] });
const answer = items => ({ totalCount: items.length, data: items.map(([s, close, change]) => ({ s, d: [s.split(':')[1], close, change] })) });

test('an answer becomes prices by ticker; rows without a positive price are dropped', () => {
  const m = C.parseScan(answer([['PSX:HBL', 287.66, 1.07], ['PSX:BAD', null, 1], ['PSX:ZERO', 0, 0], ['PSX:OGDC', 303.68, null]]));
  assert.deepEqual([...m.keys()], ['PSX:HBL', 'PSX:OGDC']);
  assert.deepEqual(m.get('PSX:HBL'), { px: 287.66, chg: 1.07 });
  assert.equal(m.get('PSX:OGDC').chg, null);
  assert.equal(C.parseScan(null).size, 0);
  assert.equal(C.parseScan({ data: 'x' }).size, 0);
});

test('the request body asks for every ticker and lifts the default row cap', () => {
  const b = C.scanBody(['A:B']);
  assert.deepEqual(b.symbols.tickers, ['A:B']);
  assert.deepEqual(b.columns, ['name', 'close', 'change']);
  assert.deepEqual(b.range, [0, 2000]);
});

test('PSX: one ticker per instrument, matched back by id', () => {
  const rows = [row('PSX:HBL'), row('PSX:KSE100'), row('FX:EURUSD'), row('PSX:ACIETF')];
  const req = C.psxRequest(rows);
  assert.equal(req.path, 'pakistan');
  assert.deepEqual(req.body.symbols.tickers, ['PSX:HBL', 'PSX:KSE100', 'PSX:ACIETF']);
  const q = C.psxQuotes(rows, C.parseScan(answer([['PSX:HBL', 287.66, 1.07], ['PSX:KSE100', 166409.25, -0.47]])));
  assert.deepEqual([...q.keys()], ['PSX:HBL', 'PSX:KSE100'], 'an instrument TradingView does not list is simply absent');
  assert.equal(C.psxRequest([row('FX:EURUSD')]), null);
});

test('forex: the first venue that lists a pair wins; silver comes from TVC', () => {
  const rows = [row('FX:EURUSD'), row('FX:XAGUSD'), row('FX:USDJPY')];
  const req = C.fxRequest(rows);
  assert.equal(req.path, 'global');
  assert.ok(req.body.symbols.tickers.includes('FX_IDC:EURUSD') && req.body.symbols.tickers.includes('OANDA:EURUSD') && req.body.symbols.tickers.includes('TVC:SILVER'));
  const q = C.fxQuotes(rows, C.parseScan(answer([['OANDA:EURUSD', 1.1146, -0.06], ['FX_IDC:EURUSD', 1.1147, -0.05], ['TVC:SILVER', 49.1, 0.3]])));
  assert.equal(q.get('FX:EURUSD').px, 1.1147, 'FX_IDC is ahead of OANDA');
  assert.equal(q.get('FX:XAGUSD').px, 49.1);
  assert.equal(q.has('FX:USDJPY'), false);
});

test('rupee crosses are the dollar rate times the other currency\'s dollar rate, in both directions, and pegs are constants', () => {
  const rows = [row('FX:USDPKR'), row('FX:EURPKR'), row('FX:JPYPKR'), row('FX:AEDPKR'), row('FX:SARPKR'), row('FX:CHFPKR')];
  const req = C.fxRequest(rows);
  const t = req.body.symbols.tickers;
  assert.ok(t.includes('FX_IDC:USDPKR') && t.includes('FX_IDC:EURUSD') && t.includes('FX_IDC:USDEUR') && t.includes('FX_IDC:USDJPY') && t.includes('FX_IDC:JPYUSD'));
  assert.ok(!t.some(x => /AED|SAR/.test(x.split(':')[1].replace('PKR', ''))), 'pegged currencies need no leg');
  const scan = C.parseScan(answer([['FX_IDC:USDPKR', 280, 0.1], ['FX_IDC:EURUSD', 1.1, -0.2], ['FX_IDC:USDJPY', 150, 0.5]]));
  const q = C.fxQuotes(rows, scan);
  assert.equal(q.get('FX:USDPKR').px, 280);
  assert.ok(Math.abs(q.get('FX:EURPKR').px - 308) < 1e-9);
  assert.ok(Math.abs(q.get('FX:EURPKR').chg - ((1.001 * 0.998 - 1) * 100)) < 1e-9);
  assert.ok(Math.abs(q.get('FX:JPYPKR').px - 280 / 150) < 1e-9, 'yen is quoted per dollar: divide');
  assert.ok(Math.abs(q.get('FX:JPYPKR').chg - ((1.001 / 1.005 - 1) * 100)) < 1e-9);
  assert.ok(Math.abs(q.get('FX:AEDPKR').px - 280 / 3.6725) < 1e-9);
  assert.equal(q.get('FX:AEDPKR').chg, 0.1);
  assert.ok(Math.abs(q.get('FX:SARPKR').px - 280 / 3.75) < 1e-9);
  assert.equal(q.has('FX:CHFPKR'), false, 'no leg, no price');
  assert.equal(C.fxQuotes(rows, C.parseScan(answer([['FX_IDC:EURUSD', 1.1, 0]]))).has('FX:EURPKR'), false, 'no dollar-rupee rate, no cross');
});

test('crypto: discovery asks every venue, then only the one that answered', () => {
  const rows = [row('CRYPTO:BTC'), row('CRYPTO:XMR'), row('CRYPTO:BAD TICKER')];
  const winners = new Map();
  const first = C.cryptoRequest(rows, winners);
  assert.equal(first.path, 'crypto');
  assert.equal(first.body.symbols.tickers.length, 2 * C.CRYPTO_VENUES.length, 'a ticker that cannot be one is not asked for');
  const q1 = C.cryptoQuotes(rows, C.parseScan(answer([['COINBASE:BTCUSD', 79700, -1.9], ['BINANCE:BTCUSDT', 79820, -1.88], ['KRAKEN:XMRUSD', 180, 2]])), winners);
  assert.equal(q1.get('CRYPTO:BTC').px, 79820, 'the better venue wins when two answer');
  assert.equal(winners.get('BTC'), 'BINANCE:BTCUSDT');
  assert.equal(winners.get('XMR'), 'KRAKEN:XMRUSD');
  const second = C.cryptoRequest(rows, winners);
  assert.deepEqual(second.body.symbols.tickers, ['BINANCE:BTCUSDT', 'KRAKEN:XMRUSD']);
  const q2 = C.cryptoQuotes(rows, C.parseScan(answer([['BINANCE:BTCUSDT', 79900, -1.7]])), winners);
  assert.equal(q2.get('CRYPTO:BTC').px, 79900);
  assert.equal(winners.has('XMR'), false, 'a venue that stopped answering is forgotten, so the next request asks every venue again');
});

test('live prices replace the published ones, keeping the time they refer to', () => {
  const prices = { quotes: { 'PSX:HBL': [280, 0.5, '2026-10-30T10:00:00Z'], 'FX:EURUSD': [1.1, 0, 't0'] } };
  const base = new Map();
  const ids = C.applyQuotes(prices, new Map([['PSX:HBL', { px: 287.66, chg: 1.07 }], ['PSX:NEW', { px: 5, chg: 0 }]]), { market: 'psx', ts: 'T1', base });
  assert.deepEqual(ids, ['PSX:HBL'], 'an instrument this build does not know is ignored');
  assert.deepEqual(prices.quotes['PSX:HBL'], [287.66, 1.07, 'T1']);
  assert.deepEqual(prices.quotes['FX:EURUSD'], [1.1, 0, 't0'], 'other markets are untouched');
  C.applyQuotes(prices, new Map([['PSX:HBL', { px: 288, chg: null }]]), { market: 'psx', ts: 'T2', base });
  assert.deepEqual(prices.quotes['PSX:HBL'], [288, 0.5, 'T2'], 'with no change figure the published one stays');
  assert.deepEqual(base.get('PSX:HBL'), [280, 0.5, '2026-10-30T10:00:00Z'], 'the comparison is always with what the refresh published');
  C.applyQuotes(prices, new Map([['FX:EURUSD', { px: 1.101, chg: 0.1 }]]), { market: 'fx', ts: null, base });
  assert.deepEqual(prices.quotes['FX:EURUSD'], [1.101, 0.1, 't0'], 'a closed market has no newer time: the published one stays');
});

test('an answer that is another instrument is not believed', () => {
  const prices = { quotes: { 'CRYPTO:AI': [1.0, 0, 't'], 'CRYPTO:SOL': [150, 0, 't'], 'FX:USDTRY': [49, 0, 't'], 'PSX:X': [100, 0, 't'] } };
  const base = new Map();
  assert.deepEqual(C.applyQuotes(prices, new Map([['CRYPTO:AI', { px: 0.275, chg: 1 }], ['CRYPTO:SOL', { px: 140, chg: -6 }]]), { market: 'crypto', ts: 'T', base }), ['CRYPTO:SOL']);
  assert.equal(prices.quotes['CRYPTO:AI'][0], 1.0);
  assert.deepEqual(C.applyQuotes(prices, new Map([['FX:USDTRY', { px: 4.9, chg: 0 }]]), { market: 'fx', ts: 'T', base }), []);
  assert.deepEqual(C.applyQuotes(prices, new Map([['PSX:X', { px: 260, chg: 1 }]]), { market: 'psx', ts: 'T', base }), []);
  assert.deepEqual(C.applyQuotes(prices, new Map([['PSX:X', { px: 0, chg: 1 }], ['PSX:Y', { px: 1, chg: 1 }]]), { market: 'psx', ts: 'T', base }), []);
  assert.deepEqual(C.applyQuotes(null, new Map(), { market: 'psx', ts: 'T', base }), []);
});

test('market hours: PSX in Pakistan time, forex around the weekend, crypto always', () => {
  const at = s => new Date(s);
  assert.equal(C.sessionState('psx', at('2026-10-29T06:00:00Z')).open, true, 'Thursday 11:00 in Lahore');
  assert.equal(C.sessionState('psx', at('2026-10-29T12:00:00Z')).open, false, 'Thursday 17:00 in Lahore');
  assert.equal(C.sessionState('psx', at('2026-10-31T06:00:00Z')).open, false, 'Saturday');
  assert.equal(C.sessionState('psx', at('2026-10-26T04:30:00Z')).open, true, 'Monday 09:30 in Lahore');
  assert.equal(C.sessionState('psx', at('2026-10-25T20:00:00Z')).open, false, 'Sunday evening UTC is Monday 01:00 in Lahore, before the open');
  assert.equal(C.sessionState('fx', at('2026-10-30T21:00:00Z')).open, true);
  assert.equal(C.sessionState('fx', at('2026-10-30T22:30:00Z')).open, false);
  assert.equal(C.sessionState('fx', at('2026-10-31T12:00:00Z')).open, false);
  assert.equal(C.sessionState('fx', at('2026-11-01T22:00:00Z')).open, true);
  assert.equal(C.sessionState('crypto', at('2026-10-31T12:00:00Z')).open, true);
});

test('how often to ask: every minute while open, rarely while closed, and slower after failures', () => {
  const open = new Date('2026-10-29T06:00:00Z'), shut = new Date('2026-10-31T06:00:00Z');
  assert.equal(C.nextDelay('psx', open), 60_000);
  assert.equal(C.nextDelay('psx', shut), 15 * 60_000);
  assert.equal(C.nextDelay('crypto', shut), 60_000);
  assert.deepEqual([1, 2, 3, 4, 5, 9].map(n => C.nextDelay('crypto', open, n)), [30_000, 60_000, 120_000, 300_000, 600_000, 600_000]);
});

test('the label says Live while something answered recently, and why not otherwise', () => {
  const now = 1_000_000_000;
  assert.deepEqual(C.summarise({ psx: { at: null, err: null }, fx: { at: now - 30_000 } }, now), { tone: 'live', text: 'Live' });
  assert.deepEqual(C.summarise({ psx: { at: now - 10 * 60_000 }, fx: { at: null } }, now), { tone: 'stale', text: 'Live paused' });
  assert.deepEqual(C.summarise({ psx: { at: null, err: 'HTTP 500' } }, now), { tone: 'error', text: 'Live offline' });
  assert.deepEqual(C.summarise({ psx: { at: null, err: null } }, now), { tone: 'wait', text: 'Live…' });
});
