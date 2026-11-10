// Temporary: reads the public FIPI/LIPI month pages the way a visitor's browser does, to learn their exact shape (rows, columns, history depth).
// Deleted once the answer is used.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import zlib from 'node:zlib';

const require = createRequire((process.env.RUNNER_TEMP || '/tmp') + '/pw/');
const { chromium } = require('playwright');
fs.mkdirSync('probe', { recursive: true });
const out = [];
const log = (...a) => { const s = a.join(' '); out.push(s); console.log(s); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const clean = s => s.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&nbsp;/g, ' ').replace(/&#039;|&#39;/g, "'").replace(/\s+/g, ' ').trim();
const parseRows = html => {
  const body = (html.match(/<tbody[^>]*>([\s\S]*?)<\/tbody>/i) || [])[1] || '';
  return [...body.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map(m => [...m[1].matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(c => clean(c[1])));
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await ctx.newPage();

async function look(kind, ym, keep) {
  const url = `https://www.brecorder.com/markets/${kind}-sector-wise${ym ? '/' + ym : ''}`;
  const t0 = Date.now();
  let resp = null;
  try { resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }); } catch (e) { log(kind, ym || 'default', 'goto failed:', String(e).slice(0, 160)); return; }
  await page.waitForSelector('#financial-table', { timeout: 20000 }).catch(() => {});
  let raw = ''; try { raw = await resp.text(); } catch { /* gone */ }
  const rawRows = parseRows(raw);
  const dom = await page.evaluate(() => {
    const t = document.querySelector('#financial-table');
    const meta = [...document.querySelectorAll('p,div,span')].map(e => e.innerText || '').find(x => /entries for/i.test(x) && x.length < 200) || '';
    let api = null;
    try { const jq = window.jQuery || window.$; if (jq && jq.fn && jq.fn.dataTable) { const d = jq('#financial-table').DataTable(); api = { total: d.rows().count(), len: d.page.len() }; } } catch (e) { api = { error: String(e).slice(0, 100) }; }
    return { title: document.title, domRows: t ? t.tBodies[0]?.rows.length ?? 0 : -1, meta: meta.replace(/\s+/g, ' ').trim(), api };
  }).catch(e => ({ error: String(e) }));
  const rows = rawRows;
  const col = i => [...new Set(rows.map(r => r[i]))];
  const dates = col(1).map(d => Date.parse(d)).filter(Number.isFinite).sort((a, b) => a - b);
  log(`${kind} ${ym || 'default'}: http ${resp.status()} ${Date.now() - t0}ms | title=${JSON.stringify(dom.title)} | meta=${JSON.stringify(dom.meta)} | rawRows=${rawRows.length} domRows=${dom.domRows} api=${JSON.stringify(dom.api)}`);
  if (rows.length) {
    log('   columns example:', JSON.stringify(rows[0]));
    log('   last row       :', JSON.stringify(rows[rows.length - 1]));
    log('   clients        :', JSON.stringify(col(0)));
    log('   market types   :', JSON.stringify(col(3)));
    log('   sectors (' + col(2).length + ')   :', JSON.stringify(col(2)));
    log('   dates          :', dates.length ? `${new Date(dates[0]).toISOString().slice(0, 10)} .. ${new Date(dates[dates.length - 1]).toISOString().slice(0, 10)} (${col(1).length} days)` : 'none');
  }
  if (keep) {
    fs.writeFileSync(`probe/${kind}_${ym}.json`, JSON.stringify(rows));
    fs.writeFileSync(`probe/${kind}_${ym}.html.gz`, zlib.gzipSync(raw.slice(0, 3_000_000)));
  }
  await sleep(1500);
}

for (const kind of ['fipi', 'lipi']) {
  for (const [ym, keep] of [[null, false], ['2026-10', true], ['2026-09', false], ['2025-02', kind === 'fipi'], ['2023-01', false], ['2022-01', false], ['2021-06', false]]) await look(kind, ym, keep);
}
try { await page.screenshot({ path: 'probe/last.png' }); } catch { /* skip */ }
await browser.close();
fs.writeFileSync('probe.txt', out.join('\n'));
log('done');
