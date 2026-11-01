// Temporary: opens the published site in a real browser on a GitHub server and reports what a visitor sees (live chip, key prices,
// instrument page, settings card, a second price round, a phone-sized window). Deleted once the answer is used.
import { createRequire } from 'node:module';
import fs from 'node:fs';

const require = createRequire((process.env.RUNNER_TEMP || '/tmp') + '/pw/');
const { chromium } = require('playwright');

const SITE = process.env.SITE_URL || 'https://abdullah550.github.io/acumen/';
const WANT = process.env.WANT_BUILD || '';
const lines = [];
const log = (...a) => { const s = a.join(' '); lines.push(s); console.log(s); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const problems = [];
const note = (ok, what, extra = '') => { log(`${ok ? 'PASS' : 'FAIL'}  ${what}${extra ? ' — ' + extra : ''}`); if (!ok) problems.push(what); };
fs.mkdirSync('shots', { recursive: true });

log('check at', new Date().toISOString());

// 1. wait until the site serves the build that was just pushed (the deploy comes at the end of the refresh run)
let build = null;
for (let i = 0; i < 64; i++) {
  try {
    const r = await fetch(SITE + 'sw.js?x=' + Date.now(), { cache: 'no-store' });
    const t = r.ok ? await r.text() : '';
    build = (t.match(/BUILD = '([^']+)'/) || [])[1] || 'HTTP ' + r.status;
  } catch (e) { build = 'unreachable: ' + e.message; }
  if (!WANT || build === WANT) break;
  if (i % 4 === 0) log(`waiting: the site serves build ${build}, expecting ${WANT}`);
  await sleep(15000);
}
note(!WANT || build === WANT, 'the site serves the new build', `serving ${build}`);

const browser = await chromium.launch();

async function open(viewport, mobile) {
  const ctx = await browser.newContext({ viewport, isMobile: !!mobile, hasTouch: !!mobile, deviceScaleFactor: mobile ? 2 : 1 });
  const page = await ctx.newPage();
  const seen = { tv: [], errors: [] };
  page.on('pageerror', e => seen.errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') seen.errors.push('console: ' + m.text().slice(0, 200)); });
  page.on('requestfailed', r => { if (/tradingview/.test(r.url())) seen.errors.push('failed: ' + r.url() + ' ' + ((r.failure() || {}).errorText || '')); });
  page.on('response', r => { if (/scanner\.tradingview\.com/.test(r.url())) seen.tv.push({ path: new URL(r.url()).pathname, status: r.status(), at: Date.now() }); });
  return { ctx, page, seen };
}
const shot = async (page, name) => { try { await page.screenshot({ path: `shots/${name}.png` }); log('   screenshot', name); } catch (e) { log('   screenshot failed', name, e.message); } };
const wait = (page, sel, name, ms = 45000) => page.waitForSelector(sel, { timeout: ms }).then(() => name, () => null);

try {
  // 2. desktop
  const dk = await open({ width: 1280, height: 900 });
  const page = dk.page;
  await page.goto(SITE + '#/', { waitUntil: 'domcontentloaded' });
  const first = await Promise.race([wait(page, '.live-chip:not([hidden])', 'chip'), wait(page, 'input[type=password]', 'locked')]);
  if (first === 'locked') {
    log('The site is encrypted: it asks for a passphrase, so nothing more can be checked from here.');
    await shot(page, 'locked');
  } else {
    note(first === 'chip', 'the Live chip appears');
    const green = await page.waitForSelector('.live-chip.up', { timeout: 45000 }).then(() => true, () => false);
    const chipText = await page.locator('.live-chip').first().innerText().catch(() => '');
    note(green, 'the chip turns green', JSON.stringify(chipText));
    log('   chip title:', JSON.stringify(await page.locator('.live-chip').first().getAttribute('title').catch(() => null)));

    await page.waitForSelector('.pulse .pulse-tile', { timeout: 15000 }).catch(() => {});
    const tiles = await page.locator('.pulse-tile').allInnerTexts();
    tiles.forEach(t => log('   tile:', t.replace(/\n+/g, ' | ')));
    note(tiles.length >= 4, 'the key-price strip shows tiles', String(tiles.length));
    await shot(page, 'desktop-home');

    // what was published, against what is shown
    const pub = await page.evaluate(async () => { try { const r = await fetch('data/prices.json', { cache: 'no-store' }); return r.ok ? await r.json() : null; } catch { return null; } });
    if (pub && pub.quotes) {
      log('   published table generated', pub.generated_at);
      const ids = { 'KSE-100': 'PSX:KSE100', 'USD / PKR': 'FX:USDPKR', GOLD: 'FX:XAUUSD', 'EUR / USD': 'FX:EURUSD', BITCOIN: 'CRYPTO:BTC', ETHEREUM: 'CRYPTO:ETH' };
      for (const t of tiles) {
        const parts = t.split('\n').map(s => s.trim()).filter(Boolean);
        const id = ids[parts[0].toUpperCase()];
        const shown = Number((parts[1] || '').replace(/,/g, ''));
        const q = id && pub.quotes[id];
        if (q && shown) log(`   ${parts[0]}: shown ${shown}, published ${q[0]} (${q[2]}), ratio ${(shown / q[0]).toFixed(4)}`);
      }
    } else log('   could not read the published prices');

    for (const id of ['PSX:HBL', 'CRYPTO:BTC', 'FX:EURUSD']) {
      await page.goto(SITE + '#/i/' + id);
      const ok = await page.waitForSelector('.inst-head .ih-px', { timeout: 20000 }).then(() => true, () => false);
      const head = ok ? (await page.locator('.inst-head').first().innerText()).replace(/\n+/g, ' | ').slice(0, 220) : '';
      note(ok, `instrument page ${id}`, head);
      if (id === 'CRYPTO:BTC') await shot(page, 'desktop-btc');
    }

    await page.goto(SITE + '#/settings');
    await page.waitForSelector('text=Live prices', { timeout: 15000 }).catch(() => {});
    const card = await page.locator('.card', { hasText: 'Live prices' }).first().innerText().catch(() => '');
    log('   settings card:', JSON.stringify(card.replace(/\n+/g, ' | ').slice(0, 700)));
    await shot(page, 'desktop-settings');

    // a second round: crypto is open all the time, so it must have asked again after a minute
    await page.goto(SITE + '#/');
    await sleep(75000);
    const byPath = {};
    for (const r of dk.seen.tv) (byPath[r.path] ||= []).push(r.status);
    log('   TradingView answers so far:', JSON.stringify(byPath));
    const crypto = (byPath['/crypto/scan'] || []).length;
    note(crypto >= 2, 'crypto asked again after a minute', `${crypto} requests`);
    note(Object.values(byPath).flat().every(s => s === 200), 'every TradingView answer was HTTP 200');
    await shot(page, 'desktop-home-later');
  }
  log('   desktop page errors:', JSON.stringify(dk.seen.errors));
  note(dk.seen.errors.length === 0, 'no script errors on desktop');
  await dk.ctx.close();

  // 3. a phone-sized window
  const ph = await open({ width: 390, height: 844 }, true);
  await ph.page.goto(SITE + '#/', { waitUntil: 'domcontentloaded' });
  const got = await wait(ph.page, '.live-chip:not([hidden])', 'chip');
  if (got) {
    await ph.page.waitForSelector('.pulse .pulse-tile', { timeout: 20000 }).catch(() => {});
    await sleep(2500);
    const box = await ph.page.locator('.live-chip').first().boundingBox();
    note(!!box && box.x >= 0 && box.x + box.width <= 390, 'the chip fits on a 390 px phone', box ? `x=${Math.round(box.x)} w=${Math.round(box.width)}` : 'no box');
    const over = await ph.page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    note(over <= 0, 'the phone page does not scroll sideways', `${over}px`);
    await shot(ph.page, 'phone-home');
  } else log('   phone: no chip (site locked or slow)');
  log('   phone page errors:', JSON.stringify(ph.seen.errors));
  await ph.ctx.close();
} catch (e) {
  log('CHECK CRASHED:', e && e.stack ? e.stack.split('\n').slice(0, 4).join(' / ') : String(e));
  problems.push('crashed');
}
await browser.close();
log(problems.length ? `PROBLEMS: ${problems.join('; ')}` : 'ALL CHECKS PASSED');
