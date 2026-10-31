// One-off probe (to be deleted): the web app's own request code (web/src/lib/livecore.js) against the real TradingView scanner,
// from a GitHub runner. Checks that the request body is accepted (including the row-cap lift), that answers parse, and that the
// derived prices (rupee crosses, silver) come out sensible.
import * as C from '../web/src/lib/livecore.js';

const say = (...a) => console.log(...a);
const row = (id) => ({ id, m: id.split(':')[0].toLowerCase(), sym: id.split(':')[1] });
const PSX = ['KSE100', 'KSE30', 'HBL', 'OGDC', 'MCB', 'UBL', 'LUCK', 'ENGRO', 'FFC', 'PPL', 'PSO', 'HUBC', 'MEBL', 'SYS', 'TRG', 'EFERT', 'NBP', 'DGKC', 'MARI', 'BAHL', 'ACIETF', 'MIIETF'];
const FXP = ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCAD', 'USDCHF', 'USDJPY', 'EURJPY', 'GBPJPY', 'EURGBP', 'USDTRY', 'USDZAR', 'USDCNH', 'USDINR', 'USDPKR', 'XAUUSD', 'XAGUSD',
  'EURPKR', 'GBPPKR', 'AEDPKR', 'SARPKR', 'CNHPKR', 'JPYPKR', 'CHFPKR', 'AUDPKR', 'CADPKR'];
const COINS = ['BTC', 'ETH', 'BNB', 'XRP', 'SOL', 'TRX', 'DOGE', 'ADA', 'LINK', 'XMR', 'AVAX', 'LTC', 'ONE', 'AI', 'EDGE', 'MNT', 'KAS', 'PEPE', 'SUI', 'TON'];
const rows = [...PSX.map(s => row('PSX:' + s)), ...FXP.map(s => row('FX:' + s)), ...COINS.map(s => row('CRYPTO:' + s))];

async function ask(req) {
  const t = Date.now();
  const res = await fetch(C.SCANNER + req.path + '/scan', { method: 'POST', body: JSON.stringify(req.body), headers: { 'Content-Type': 'text/plain;charset=UTF-8', Origin: 'https://abdullah550.github.io' } });
  const text = await res.text();
  say(`  ${req.path}/scan: HTTP ${res.status}, ${text.length} bytes, ${Date.now() - t} ms, ${req.body.symbols.tickers.length} tickers asked${res.ok ? '' : ', body ' + text.slice(0, 200)}`);
  return res.ok ? JSON.parse(text) : null;
}
const show = (m, ids) => ids.forEach(id => say(`    ${id}: ${m.has(id) ? JSON.stringify(m.get(id)) : '— no price'}`));

say(`probe at ${new Date().toISOString()}`);
say('PSX');
{
  const p = await ask(C.psxRequest(rows));
  const q = C.psxQuotes(rows, C.parseScan(p));
  say(`  ${q.size} of ${PSX.length} priced`); show(q, ['PSX:KSE100', 'PSX:HBL', 'PSX:ACIETF', 'PSX:MIIETF']);
}
say('FX');
{
  const req = C.fxRequest(rows);
  const p = await ask(req);
  const q = C.fxQuotes(rows, C.parseScan(p));
  say(`  ${q.size} of ${FXP.length} priced; missing: ${FXP.filter(s => !q.has('FX:' + s)).join(', ') || 'none'}`);
  show(q, ['FX:EURUSD', 'FX:USDPKR', 'FX:XAUUSD', 'FX:XAGUSD', 'FX:EURPKR', 'FX:JPYPKR', 'FX:AEDPKR']);
}
say('Crypto, first request (every venue for every coin)');
const winners = new Map();
{
  const p = await ask(C.cryptoRequest(rows, winners));
  const q = C.cryptoQuotes(rows, C.parseScan(p), winners);
  say(`  ${q.size} of ${COINS.length} priced; no price: ${COINS.filter(s => !q.has('CRYPTO:' + s)).join(', ') || 'none'}`);
  say('  venues:', JSON.stringify(Object.fromEntries([...winners].slice(0, 8))));
  show(q, ['CRYPTO:BTC', 'CRYPTO:ETH', 'CRYPTO:XMR', 'CRYPTO:ONE', 'CRYPTO:AI']);
}
say('Crypto, second request (the venue that answered, per coin)');
{
  const p = await ask(C.cryptoRequest(rows, winners));
  const q = C.cryptoQuotes(rows, C.parseScan(p), winners);
  say(`  ${q.size} of ${COINS.length} priced`);
}
