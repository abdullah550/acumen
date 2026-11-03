// Temporary: opens the published site in a real browser on a GitHub server and reports what a visitor sees on the new Bargains page
// (home tile, menu link, cards against the published file, excluded list, instrument link, phone-sized window). Deleted once the answer is used.
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

// 1. wait until the site serves the build that was just pushed, with the new file in its data (the deploy is the last step of the refresh run)
let build = null;
let meta = null;
for (let i = 0; i < 72; i++) {
  try {
    const r = await fetch(SITE + 'sw.js?x=' + Date.now(), { cache: 'no-store' });
    const t = r.ok ? await r.text() : '';
    build = (t.match(/BUILD = '([^']+)'/) || [])[1] || 'HTTP ' + r.status;
    const m = await fetch(SITE + 'data/meta.json?x=' + Date.now(), { cache: 'no-store' });
    meta = m.ok ? await m.json() : null;
  } catch (e) { build = 'unreachable: ' + e.message; }
  if ((!WANT || build === WANT) && meta && meta.files && meta.files.bargains) break;
  if (i % 4 === 0) log(`waiting: the site serves build ${build} (expecting ${WANT || 'any'}), bargains in meta: ${!!(meta && meta.files && meta.files.bargains)}`);
  await sleep(15000);
}
note(!WANT || build === WANT, 'the site serves the new build', `serving ${build}`);
note(!!(meta && meta.files && meta.files.bargains), 'meta.json lists the bargains file', meta ? `generated ${meta.generated_at}, enc ${JSON.stringify(meta.enc)}` : 'no meta');

const browser = await chromium.launch();

async function open(viewport, mobile) {
  const ctx = await browser.newContext({ viewport, isMobile: !!mobile, hasTouch: !!mobile, deviceScaleFactor: mobile ? 2 : 1 });
  const page = await ctx.newPage();
  const seen = { errors: [] };
  page.on('pageerror', e => seen.errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') seen.errors.push('console: ' + m.text().slice(0, 200)); });
  return { ctx, page, seen };
}
const shot = async (page, name, opt = {}) => { try { await page.screenshot({ path: `shots/${name}.png`, ...opt }); log('   screenshot', name); } catch (e) { log('   screenshot failed', name, e.message); } };
const wait = (page, sel, name, ms = 45000) => page.waitForSelector(sel, { timeout: ms }).then(() => name, () => null);
const flat = s => (s || '').replace(/\n+/g, ' | ');

try {
  const dk = await open({ width: 1280, height: 900 });
  const page = dk.page;
  await page.goto(SITE + '#/', { waitUntil: 'domcontentloaded' });
  const first = await Promise.race([wait(page, '.pulse .pulse-tile', 'home'), wait(page, 'input[type=password]', 'locked')]);
  if (first === 'locked') {
    log('The site is encrypted: it asks for a passphrase, so nothing more can be checked from here.');
    await shot(page, 'locked');
  } else {
    note(first === 'home', 'the Command Center opens');

    // what was published
    const body = await page.evaluate(async () => { try { const r = await fetch('data/bargains.json', { cache: 'no-store' }); return r.ok ? await r.json() : { error: 'HTTP ' + r.status }; } catch (e) { return { error: String(e) }; } });
    const ov = await page.evaluate(async () => { try { const r = await fetch('data/overview.json', { cache: 'no-store' }); return r.ok ? await r.json() : null; } catch { return null; } });
    if (body.error) { note(false, 'bargains.json can be read', body.error); }
    else {
      log('   published:', JSON.stringify(body.counts), 'asof', body.asof, 'stale_days', body.stale_days);
      log('   tiers:', JSON.stringify((body.tiers || []).map(t => t.label)));
      for (const p of (body.picks || [])) log(`   pick ${p.sym}: price ${p.px}, fair ${p.fair}, ${p.cents} cents, tier ${p.tier}, yardsticks ${(p.models || []).map(m => m.value).join('/')}`);
      for (const p of (body.excluded || [])) log(`   excluded ${p.sym}: ${p.cents} cents — ${(p.reasons || []).length} reason(s): ${(p.reasons || [])[0] || ''}`.slice(0, 220));
      note(Array.isArray(body.picks) && Array.isArray(body.excluded) && body.counts && body.counts.valued > 0, 'the file has picks, excluded and counts', `valued ${body.counts && body.counts.valued} of ${body.counts && body.counts.screened}`);
      note(!!(ov && ov.bargains && ov.bargains.counts && ov.bargains.counts.picks === body.counts.picks), 'overview.json carries the same count for the home tile');

      // the home tile
      const tile = page.locator('.card', { hasText: 'Bargains' }).first();
      const hasTile = await tile.waitFor({ timeout: 15000 }).then(() => true, () => false);
      note(hasTile, 'the home screen has a Bargains tile');
      if (hasTile) {
        log('   home tile:', JSON.stringify(flat(await tile.innerText()).slice(0, 400)));
        note(await page.locator('.bg-mini').count() === Math.min(3, body.picks.length), 'the tile shows the nearest shares', `${await page.locator('.bg-mini').count()} of ${body.picks.length}`);
        await tile.scrollIntoViewIfNeeded();
        await shot(page, 'desktop-home-tile', { clip: undefined });
      }

      // menu link and the page
      const link = page.locator('nav.nav a[data-nav="bargains"]');
      note(await link.count() === 1, 'the menu has a Bargains link');
      await link.click();
      await page.waitForSelector('.bg-stats', { timeout: 20000 }).catch(() => {});
      await sleep(600);
      note(await page.locator('nav.nav a[data-nav="bargains"][aria-current="page"]').count() === 1, 'the menu marks Bargains as the current page');
      log('   banner:', JSON.stringify(flat(await page.locator('.banner').first().innerText().catch(() => ''))));
      log('   stats:', JSON.stringify(flat(await page.locator('.bg-stats').innerText().catch(() => ''))));
      log('   sections:', JSON.stringify(await page.locator('.sec-head h2').allInnerTexts()));
      const cards = await page.locator('.bg-card:not(.dim)').count();
      const dim = await page.locator('.bg-card.dim').count();
      note(cards === body.picks.length, 'one card for every listed share', `${cards} shown, ${body.picks.length} in the file`);
      note(dim === body.excluded.length, 'one card for every cheap share that failed a check', `${dim} shown, ${body.excluded.length} in the file`);
      await shot(page, 'desktop-bargains-head');
      if (cards) {
        const c1 = page.locator('.bg-card:not(.dim)').first();
        const txt = await c1.innerText();
        log('   first card:', JSON.stringify(flat(txt).slice(0, 700)));
        note(txt.includes(body.picks[0].sym), 'the deepest discount comes first', body.picks[0].sym);
        note(await c1.locator('.bg-gauge .bg-px').count() === 1 && await c1.locator('.bg-model').count() === 3, 'the gauge shows the price and three yardsticks');
        note(/At most 5% of your account/.test(txt), 'the position cap is stated');
        await c1.locator('summary').click();
        note(await c1.locator('.bg-model-row').count() === 3, 'opening "How this fair value was built" lists three yardsticks');
        await c1.scrollIntoViewIfNeeded();
        await shot(page, 'desktop-bargains-card');
      }
      if (dim) {
        await page.locator('h2', { hasText: 'Cheap on paper' }).scrollIntoViewIfNeeded();
        await shot(page, 'desktop-bargains-excluded');
      }
      await page.locator('.card', { hasText: 'How the fair value is worked out' }).scrollIntoViewIfNeeded().catch(() => {});
      await shot(page, 'desktop-bargains-method');
      if (cards) {
        const sym = page.locator('.bg-card:not(.dim)').first().locator('a.ic-sym');
        await sym.scrollIntoViewIfNeeded();
        await sym.click();
        const ok = await page.waitForSelector('.inst-head', { timeout: 20000 }).then(() => true, () => false);
        note(ok && (await page.locator('.inst-head').first().innerText()).includes(body.picks[0].sym), 'the symbol opens the instrument page', body.picks[0].sym);
      }
    }
  }
  log('   desktop page errors:', JSON.stringify(dk.seen.errors));
  note(dk.seen.errors.length === 0, 'no script errors on desktop');
  await dk.ctx.close();

  // a phone-sized window
  const ph = await open({ width: 390, height: 844 }, true);
  await ph.page.goto(SITE + '#/bargains', { waitUntil: 'domcontentloaded' });
  const got = await wait(ph.page, '.bg-stats', 'page', 45000);
  if (got) {
    await sleep(1200);
    const over = await ph.page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    note(over <= 0, 'the phone page does not scroll sideways', `${over}px`);
    await shot(ph.page, 'phone-bargains-top');
    const c = ph.page.locator('.bg-card:not(.dim)').first();
    if (await c.count()) { await c.scrollIntoViewIfNeeded(); await shot(ph.page, 'phone-bargains-card'); }
    await ph.page.locator('.tabbar button[data-nav="more"]').click().catch(() => {});
    await sleep(500);
    note(await ph.page.locator('.more-row', { hasText: 'Bargains' }).count() >= 1, 'the phone More sheet lists Bargains');
    await shot(ph.page, 'phone-more');
  } else log('   phone: the Bargains page did not appear (site locked or slow)');
  log('   phone page errors:', JSON.stringify(ph.seen.errors));
  note(ph.seen.errors.length === 0, 'no script errors on the phone');
  await ph.ctx.close();
} catch (e) {
  log('CHECK CRASHED:', e && e.stack ? e.stack.split('\n').slice(0, 4).join(' / ') : String(e));
  problems.push('crashed');
}
await browser.close();
log(problems.length ? `PROBLEMS: ${problems.join('; ')}` : 'ALL CHECKS PASSED');
