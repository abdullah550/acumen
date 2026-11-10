// Temporary: finds out whether the foreign/local investor flow tables (FIPI/LIPI) can be read from a GitHub server, and how (pages, endpoints, formats).
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
const title = t => ((t || '').match(/<title>([^<]*)<\/title>/i) || [])[1]?.trim().slice(0, 80) || '';

// 1. which of the candidate sites answer a plain request from here
log('== plain requests');
const pages = [
  'https://acuity.scstrade.com/FIPILIPI.aspx',
  'https://pakistan.scstrade.com/FIPILIPI.aspx',
  'https://www.scstrade.com/',
  'https://www.brecorder.com/markets/fipi-sector-wise/2025-02',
  'https://www.brecorder.com/markets/lipi-sector-wise/2026-03',
  'https://www.secp.gov.pk/document/daily-stock-market-report-dated-october-30-2026/?wpdmdl=68421',
  'https://sarmaaya.pk/',
  'https://dps.psx.com.pk/',
];
const got = {};
for (const u of pages) {
  const r = await plain(u);
  got[u] = r;
  log(u, '->', r.error || `${r.status} ${r.type} ${r.len} ${JSON.stringify(title(r.text))} ${r.url !== u ? '=> ' + r.url : ''}`);
}

// 2. the SCS Acuity page: what scripts it loads and what they call for the FIPI/LIPI table
const acu = got['https://acuity.scstrade.com/FIPILIPI.aspx'];
if (acu && acu.text) {
  fs.writeFileSync('probe/acuity_page.html', acu.text.slice(0, 300000));
  const srcs = [...acu.text.matchAll(/<script[^>]+src=["']([^"']+)["']/gi)].map(m => new URL(m[1], acu.url).href);
  log('scripts on the page:', srcs.length);
  for (const s of srcs) {
    if (!/scstrade/i.test(s)) continue;
    const r = await plain(s);
    const lines = (r.text || '').split(/\r?\n/);
    const hits = [];
    lines.forEach((l, i) => { if (/fipi|lipi|\.aspx\/|\$\.ajax|url\s*:/i.test(l)) hits.push(`${i + 1}: ${l.trim().slice(0, 220)}`); });
    log('script', s, '->', r.error || `${r.status} ${r.len} bytes`, 'hits:', hits.length);
    for (const h of hits.slice(0, 30)) log('   ', h);
    if (r.text) fs.writeFileSync('probe/' + s.split('/').pop().split('?')[0].slice(0, 60), r.text.slice(0, 400000));
  }
  const inline = [...acu.text.matchAll(/<script(?![^>]+src)[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
  for (const code of inline) for (const l of code.split(/\r?\n/)) if (/fipi|lipi|\.aspx\/|\$\.ajax|url\s*:|WebMethod/i.test(l)) log('   inline:', l.trim().slice(0, 220));
}

// 3. the same page in a real browser: what it calls when the table fills
const browser = await chromium.launch();
async function capture(name, url, act) {
  log('== browser', name, url);
  const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1280, height: 1000 } });
  const page = await ctx.newPage();
  const calls = [];
  page.on('response', async r => {
    const req = r.request();
    if (!['xhr', 'fetch', 'document'].includes(req.resourceType())) return;
    let body = ''; try { body = (await r.text()).slice(0, 6000); } catch { /* binary or gone */ }
    calls.push({ method: req.method(), url: r.url(), status: r.status(), type: r.headers()['content-type'], post: (req.postData() || '').slice(0, 2000), body });
  });
  try { await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }); } catch (e) { log(name, 'goto:', String(e).slice(0, 160)); }
  await page.waitForTimeout(3000);
  const describe = async tag => {
    const info = await page.evaluate(() => {
      const el = e => ({ tag: e.tagName.toLowerCase(), id: e.id, name: e.getAttribute('name'), type: e.getAttribute('type'), value: (e.value || '').slice(0, 40), text: (e.innerText || '').slice(0, 40) });
      return {
        title: document.title, url: location.href,
        controls: [...document.querySelectorAll('input,select,button')].slice(0, 60).map(el),
        tables: [...document.querySelectorAll('table')].slice(0, 8).map(t => ({ id: t.id, cls: t.className, rows: t.rows.length, head: t.rows[0] ? t.rows[0].innerText.replace(/\s+/g, ' ').slice(0, 300) : '', first: t.rows[1] ? t.rows[1].innerText.replace(/\s+/g, ' ').slice(0, 300) : '' })),
        text: document.body.innerText.replace(/\s+/g, ' ').slice(0, 1500),
      };
    }).catch(e => ({ error: String(e) }));
    log(`${name} [${tag}]`, JSON.stringify(info).slice(0, 5000));
  };
  await describe('loaded');
  if (act) { try { await act(page); } catch (e) { log(name, 'action failed:', String(e).slice(0, 200)); } await page.waitForTimeout(5000); await describe('after action'); }
  try { await page.screenshot({ path: `probe/${name}.png`, fullPage: false }); } catch { /* skip */ }
  fs.writeFileSync(`probe/${name}_calls.json`, JSON.stringify(calls, null, 1));
  log(name, 'calls:', calls.length, JSON.stringify(calls.map(c => `${c.method} ${c.status} ${c.url.slice(0, 140)}`).slice(0, 40)));
  await ctx.close();
}
// set a roughly one-month window in whichever date fields the page has, then press the search button
const fillAndGo = async page => {
  const d = n => { const x = new Date(Date.now() - n * 864e5); return { iso: x.toISOString().slice(0, 10), dmy: `${String(x.getUTCDate()).padStart(2, '0')}/${String(x.getUTCMonth() + 1).padStart(2, '0')}/${x.getUTCFullYear()}` }; };
  const from = d(35), to = d(3);
  const inputs = page.locator('input[type=text], input[type=date], input:not([type])');
  const n = await inputs.count();
  for (let i = 0; i < n; i++) {
    const info = await inputs.nth(i).evaluate(e => ({ id: e.id, name: e.name || '', ph: e.placeholder || '', v: e.value || '' }));
    const key = `${info.id} ${info.name} ${info.ph}`;
    const dmy = /\d\d\/\d\d\/\d{4}/.test(info.v) || /dd|mm/i.test(info.ph);
    if (/from|start/i.test(key)) { log('from field', JSON.stringify(info)); await inputs.nth(i).fill(dmy ? from.dmy : from.iso).catch(() => {}); }
    if (/\bto\b|_to|to_|end|till/i.test(key) && !/from/i.test(key)) { log('to field', JSON.stringify(info)); await inputs.nth(i).fill(dmy ? to.dmy : to.iso).catch(() => {}); }
  }
  const b = page.locator('input[type=submit], input[type=button], button, a.btn').filter({ hasText: /search|submit|go|show|view|get|find|filter/i });
  const bv = page.locator('input[type=submit][value], input[type=button][value]');
  if (await b.count()) { log('clicking', await b.first().evaluate(e => e.outerHTML.slice(0, 200))); await b.first().click(); }
  else if (await bv.count()) { log('clicking first submit', await bv.first().evaluate(e => e.outerHTML.slice(0, 200))); await bv.first().click(); }
};
await capture('acuity_fipilipi', 'https://acuity.scstrade.com/FIPILIPI.aspx', fillAndGo);
await capture('brecorder_fipi', 'https://www.brecorder.com/markets/fipi-sector-wise/2025-02', null);
await browser.close();
fs.writeFileSync('probe.txt', out.join('\n'));
log('done');
