// Temporary: finds out what the NCCPL foreign/local investor flow data looks like from a GitHub server (pages, endpoints, formats).
// Deleted once the answer is used.
import { createRequire } from 'node:module';
import fs from 'node:fs';

const require = createRequire((process.env.RUNNER_TEMP || '/tmp') + '/pw/');
const { chromium } = require('playwright');
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
fs.mkdirSync('probe', { recursive: true });
const out = [];
const log = (...a) => { const s = a.join(' '); out.push(s); console.log(s); };

async function plain(url, opt = {}) {
  try {
    const r = await fetch(url, { method: opt.method || 'GET', body: opt.body, redirect: 'follow', signal: AbortSignal.timeout(25000),
      headers: { 'user-agent': UA, accept: 'text/html,application/json,*/*', ...(opt.headers || {}) } });
    const t = await r.text();
    return { status: r.status, type: r.headers.get('content-type'), len: t.length, text: t, url: r.url };
  } catch (e) { return { error: String(e && e.cause ? e.cause : e).slice(0, 200) }; }
}

// 1. the NCCPL site itself: which pages exist, and which links mention the investor flow data
log('== NCCPL home and guesses');
const base = 'https://www.nccpl.com.pk';
const home = await plain(base + '/');
log('home:', home.error || `${home.status} ${home.type} ${home.len} bytes -> ${home.url}`);
if (home.text) {
  fs.writeFileSync('probe/nccpl_home.html', home.text.slice(0, 200000));
  const links = [...home.text.matchAll(/<a[^>]+href=["']([^"'#]+)["'][^>]*>([\s\S]*?)<\/a>/gi)].map(m => [m[1], m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()]);
  log('links on home:', links.length);
  for (const [href, text] of links) if (/fipi|lipi|portfolio|market|information|statistic|download|report/i.test(href + ' ' + text)) log('  link:', href, '|', text.slice(0, 80));
}
for (const p of ['/market-information/fipi-lipi', '/en/market-information/fipi-lipi', '/market-information/fipi', '/market-information/lipi', '/Home/FIPI', '/FIPI', '/fipi-lipi', '/market-information', '/en/market-information']) {
  const r = await plain(base + p);
  const title = r.text ? (r.text.match(/<title>([^<]*)<\/title>/i) || [])[1] : '';
  log('guess', p, '->', r.error || `${r.status} ${r.type} ${r.len} ${JSON.stringify((title || '').trim().slice(0, 80))} ${r.url}`);
}

// 2. the Capital Stake API without a key, and its docs
log('== Capital Stake API');
for (const u of ['https://csapis.com/3.0/portfolio-investments?period=mtd&type=local', 'https://csapis.com/', 'https://capitalstake.com/docs/rest/api']) {
  const r = await plain(u);
  log(u, '->', r.error || `${r.status} ${r.type} ${r.len}`, JSON.stringify((r.text || '').slice(0, 300)));
}

// 3. pages that show the data in a browser: what they call to fill their tables
const browser = await chromium.launch();
async function capture(name, url, act) {
  log('== browser', name, url);
  const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1280, height: 1000 } });
  const page = await ctx.newPage();
  const calls = [];
  page.on('response', async r => {
    const req = r.request();
    if (!['xhr', 'fetch', 'document'].includes(req.resourceType())) return;
    let body = ''; try { body = (await r.text()).slice(0, 8000); } catch { /* binary or gone */ }
    calls.push({ method: req.method(), url: r.url(), status: r.status(), type: r.headers()['content-type'], post: (req.postData() || '').slice(0, 2000), body });
  });
  try { await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }); } catch (e) { log(name, 'goto:', String(e).slice(0, 160)); }
  await page.waitForTimeout(3000);
  const describe = async tag => {
    const info = await page.evaluate(() => {
      const el = e => ({ tag: e.tagName.toLowerCase(), id: e.id, name: e.getAttribute('name'), type: e.getAttribute('type'), value: (e.value || '').slice(0, 40), text: (e.innerText || '').slice(0, 40) });
      return {
        title: document.title, url: location.href,
        controls: [...document.querySelectorAll('input,select,button,a.btn')].slice(0, 60).map(el),
        tables: [...document.querySelectorAll('table')].slice(0, 8).map(t => ({ id: t.id, cls: t.className, rows: t.rows.length, head: t.rows[0] ? t.rows[0].innerText.replace(/\s+/g, ' ').slice(0, 300) : '', first: t.rows[1] ? t.rows[1].innerText.replace(/\s+/g, ' ').slice(0, 300) : '' })),
        text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 1500),
      };
    }).catch(e => ({ error: String(e) }));
    log(`${name} [${tag}]`, JSON.stringify(info).slice(0, 4500));
  };
  await describe('loaded');
  if (act) { try { await act(page); } catch (e) { log(name, 'action failed:', String(e).slice(0, 200)); } await page.waitForTimeout(4000); await describe('after action'); }
  try { await page.screenshot({ path: `probe/${name}.png`, fullPage: false }); } catch { /* skip */ }
  fs.writeFileSync(`probe/${name}_calls.json`, JSON.stringify(calls, null, 1));
  log(name, 'calls:', calls.length, JSON.stringify(calls.map(c => `${c.method} ${c.status} ${c.url.slice(0, 120)}`).slice(0, 40)));
  await ctx.close();
}
const clickGo = async page => {
  const b = page.locator('input[type=submit], input[type=button], button').filter({ hasText: /search|submit|go|show|view|get/i });
  if (await b.count()) { log('clicking', await b.first().evaluate(e => e.outerHTML.slice(0, 200))); await b.first().click(); }
  else {
    const any = page.locator('input[type=submit], input[type=button]');
    if (await any.count()) { log('clicking first submit', await any.first().evaluate(e => e.outerHTML.slice(0, 200))); await any.first().click(); }
  }
};
await capture('scs_fipilipi', 'https://pakistan.scstrade.com/FIPILIPI.aspx', clickGo);
await capture('finhisaab', 'https://finhisaab.com/market-updates/fipi-lipi', null);
await capture('nccpl_home', base + '/', null);
await browser.close();
fs.writeFileSync('probe.txt', out.join('\n'));
log('done');
