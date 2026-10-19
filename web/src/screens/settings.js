import { h, replace, append } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S, Secret, applyAppearance } from '../lib/store.js';
import { replaceQuery, MEMORY } from '../lib/router.js';
import { MARKET_LABEL } from '../lib/util.js';
import * as J from '../lib/journal.js';
import { assess } from '../lib/health.js';
import { card, chip, mkChip, stat, tabs, seg, chgSpan, toast, ask, banner } from '../ui/kit.js';
import { accountControls } from '../ui/account.js';
import { exportData, importData } from '../ui/positions.js';
import { icon } from '../lib/icons.js';
import { pageHead, sectionTitle } from './common.js';

/* Data & Settings: is the data fresh, how the app looks, what is stored on this device, and what the app is built from. */

const TABS = [
  { value: 'health', label: 'Data health' },
  { value: 'prefs', label: 'Preferences' },
  { value: 'device', label: 'This device' },
  { value: 'about', label: 'About' },
];

const STATE = {
  ok: { label: 'Current', tone: 'up', icon: 'check' },
  stale: { label: 'Out of date', tone: 'warn', icon: 'alert' },
  warming: { label: 'Loading history', tone: 'outline', icon: 'clock' },
  error: { label: 'No data', tone: 'bad', icon: 'alert' },
};
const stateChip = st => { const u = STATE[st] || STATE.ok; return chip(u.label, u.tone, { icon: u.icon }); };

const ago = iso => (iso ? F.ago(iso) : F.NA);
const when = iso => (iso ? F.dateTime(iso) : '');
// "Pakistan Stock Exchange" next to the PSX chip; nothing extra when the full name is just "Forex" again.
const longName = m => (m.label && m.label !== MARKET_LABEL[m.k] ? m.label : null);
const ageText = n => (!F.isNum(n) ? '' : n <= 0 ? 'today' : n === 1 ? 'yesterday' : `${n} days ago`);

function bytes(n) {
  if (!F.isNum(n)) return F.NA;
  if (n < 1024 * 1024) return `${F.num(n / 1024, 0)} KB`;
  if (n < 1024 ** 3) return `${F.num(n / 1024 ** 2, 1)} MB`;
  return `${F.num(n / 1024 ** 3, 1)} GB`;
}

// The build id the page was loaded with (app.js?v=<hash>); the single-file preview has none.
function buildId() {
  const el = document.querySelector('script[src*="app.js"]');
  const m = el && /[?&]v=([^&]+)/.exec(el.getAttribute('src') || '');
  return m ? m[1] : null;
}

// A labelled setting: what it is on the left, the control on the right (stacked on a phone).
function row(title, text, control) {
  return h('div', { class: 'st-row' },
    h('div', { class: 'st-row-t' }, h('b', null, title), text ? h('span', null, text) : null),
    control ? h('div', { class: 'st-row-c' }, control) : null);
}
const rows = (...kids) => h('div', { class: 'st-rows' }, ...kids);
const isStandalone = () => { try { return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true; } catch { return false; } };

export default async function render(route) {
  const ctx = { dead: false, tab: null, cleanups: [] };
  let tab = TABS.some(t => t.value === route.query.t) ? route.query.t : 'health';
  const host = h('div', { class: 'stack loose', role: 'tabpanel' });
  const runCleanups = () => { for (const fn of ctx.cleanups.splice(0)) { try { fn(); } catch { /* already gone */ } } };

  const show = t => {
    runCleanups();
    ctx.tab = t;
    const build = { health: healthTab, prefs: prefsTab, device: deviceTab, about: aboutTab }[t];
    replace(host, build(ctx, () => show(t)));
  };

  const bar = tabs(TABS, tab, v => { tab = v; replaceQuery({ t: v === 'health' ? null : v }); show(v); });
  const root = h('div', { class: 'stack loose' },
    pageHead('Data & Settings', 'Whether the data is fresh, how the app looks and behaves, and what is kept on this device.'),
    h('div', { class: 'card-tabs st-tabs' }, bar), host);
  show(tab);
  return { el: root, destroy() { ctx.dead = true; runCleanups(); } };
}

/* ======================================================================= data health */
function healthTab() {
  const meta = D.meta || {};
  const a = assess(meta);
  return append(h('div', { class: 'stack loose' }), [headCard(a, meta), tilesCard(a, meta), marketsSection(a), sourcesSection(a, meta), helpCard()]);
}

function checkControl() {
  const out = h('span', { class: 'st-msg-inline muted fs-sm', role: 'status' });
  const reload = h('button', { class: 'btn primary', type: 'button', hidden: true, onclick: () => location.reload() }, icon('refresh'), 'Reload with the new data');
  const btn = h('button', { class: 'btn', type: 'button' }, icon('refresh'), 'Check for new data');
  btn.addEventListener('click', async () => {
    if (navigator.onLine === false) { out.textContent = 'You are offline, so there is nothing to check. The last downloaded data is shown.'; return; }
    btn.disabled = true; out.textContent = 'Checking…'; reload.hidden = true;
    try {
      const m = await D.fetchRaw('meta.json');
      if (m && m.generated_at && m.generated_at !== (D.meta || {}).generated_at) { out.textContent = `Newer data is available (refreshed ${ago(m.generated_at)}).`; reload.hidden = false; }
      else out.textContent = `You already have the newest data (refreshed ${ago((D.meta || {}).generated_at)}).`;
    } catch { out.textContent = 'Could not reach the server. The last downloaded data is shown.'; }
    btn.disabled = false;
  });
  return h('div', { class: 'row wrap st-check' }, btn, reload, out);
}

function headCard(a) {
  const ic = a.tone === 'ok' ? 'check' : 'alert';
  const issues = a.issues.length
    ? h('ul', { class: 'st-issues' }, ...a.issues.map(i => h('li', { class: i.tone },
      h('span', { class: 'st-ic' }, icon(i.tone === 'info' ? 'info' : 'alert')),
      h('div', null, h('div', { class: 'st-it' }, i.title), h('p', { class: 'st-ip' }, i.text)))))
    : null;
  return card({
    cls: a.tone === 'ok' ? 'accent' : null,
    body: h('div', { class: 'stack' },
      h('div', { class: 'st-head ' + a.tone },
        h('div', { class: 'st-badge' }, icon(ic)),
        h('div', { class: 'grow' }, h('h2', { class: 'st-title' }, a.title), h('p', { class: 'st-sub' }, a.text))),
      checkControl(), issues),
  });
}

function tilesCard(a, meta) {
  const c = a.counts;
  const secs = c.seconds;
  return card({
    body: h('div', { class: 'stats' },
      stat('Last refresh', ago(meta.generated_at), when(meta.generated_at), { info: 'When the refresh job last produced a complete set of results. It runs on a schedule in your repository; this app only reads what it published.' }),
      stat('Instruments analysed', F.isNum(c.analysed) ? F.int(c.analysed) : F.NA, c.failed ? `${F.int(c.failed)} could not be analysed` : 'None failed'),
      stat('Sources working', c.sources ? `${c.sourcesOk} of ${c.sources}` : F.NA, c.sources && c.sourcesOk < c.sources ? `${c.sources - c.sourcesOk} failed last time` : 'Answered last refresh'),
      stat('Analysis time', F.isNum(secs) ? (secs >= 90 ? `${F.num(secs / 60, 1)} min` : `${F.num(secs, 0)} s`) : F.NA, 'On the refresh job')),
  });
}

// A small card for phones, where a wide table would scroll sideways.
function crow({ title, chips, sub, right, lines = [] }) {
  return h('div', { class: 'ev-crow' },
    h('div', { class: 'ev-crow-top' },
      h('div', { class: 'ev-crow-id' }, h('div', { class: 'row' }, title, chips), sub ? h('div', { class: 'ev-crow-sub' }, sub) : null),
      h('div', { class: 'ev-crow-r' }, right)),
    ...lines.map(l => h('div', { class: 'ev-crow-line' }, ...l)));
}
const kv = (k, v) => h('span', null, h('span', { class: 'muted' }, k + ' '), v);

function marketsSection(a) {
  if (!a.markets.length) return h('div');
  const table = h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl' },
    h('thead', null, h('tr', null,
      h('th', { scope: 'col' }, 'Market'), h('th', { scope: 'col' }, 'Status'), h('th', { scope: 'col' }, 'Latest price date'),
      h('th', { class: 'r', scope: 'col' }, 'Instruments'), h('th', { class: 'r', scope: 'col' }, 'History loaded'), h('th', { class: 'r', scope: 'col' }, 'Last good fetch'))),
    h('tbody', null, ...a.markets.map(m => h('tr', { class: m.state === 'error' ? 'alert' : null },
      h('td', null, h('div', { class: 'row' }, mkChip(m.k), longName(m) ? h('span', { class: 'ink-2' }, longName(m)) : null)),
      h('td', null, stateChip(m.state)),
      h('td', null, F.date(m.asof, { year: true }), h('span', { class: 'muted' }, m.age != null ? ` · ${ageText(m.age)}` : '')),
      h('td', { class: 'r' }, F.int(m.instruments)),
      h('td', { class: 'r' }, F.isNum(m.history_pct) ? F.num(m.history_pct, 0) + '%' : F.NA),
      h('td', { class: 'r', title: when(m.last_success) }, ago(m.last_success)))))));
  const cards = h('div', { class: 'ev-cards' }, ...a.markets.map(m => crow({
    title: h('b', null, m.label || m.name), chips: longName(m) ? mkChip(m.k) : null, right: stateChip(m.state),
    lines: [
      [kv('Latest price', F.date(m.asof, { year: true })), m.age != null ? h('span', { class: 'muted' }, ageText(m.age)) : null],
      [kv('Instruments', F.int(m.instruments)), kv('History', F.isNum(m.history_pct) ? F.num(m.history_pct, 0) + '%' : F.NA)],
      [kv('Last good fetch', ago(m.last_success))],
    ],
  })));
  return h('section', { class: 'stack' },
    sectionTitle('Markets', null, 'What the last refresh produced for each market. “Out of date” means the latest price is older than weekends and holidays explain.'),
    card({ flush: true, body: [table, cards] }));
}

function sourcesSection(a) {
  if (!a.sources.length) return h('div');
  const failed = a.sources.filter(s => !s.ok).length;
  const res = s => (s.ok ? chip('Worked', 'up', { icon: 'check' }) : chip('Failed', 'bad', { icon: 'alert' }));
  const table = h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl st-src' },
    h('thead', null, h('tr', null,
      h('th', { scope: 'col' }, 'Source'), h('th', { scope: 'col' }, 'Market'), h('th', { scope: 'col' }, 'Result'),
      h('th', { class: 'r', scope: 'col' }, 'Items'), h('th', { class: 'r', scope: 'col' }, 'Time'), h('th', { class: 'r', scope: 'col' }, 'Fetched'), h('th', { scope: 'col' }, 'Message'))),
    h('tbody', null, ...a.sources.map(s => h('tr', { class: s.ok ? null : 'alert' },
      h('td', null, h('span', { class: 'sym' }, s.name)),
      h('td', null, MARKET_LABEL[s.market] ? mkChip(s.market) : s.market || ''),
      h('td', null, res(s)),
      h('td', { class: 'r' }, F.isNum(s.items) ? F.int(s.items) : F.NA),
      h('td', { class: 'r' }, F.isNum(s.duration_s) ? F.num(s.duration_s, 1) + ' s' : F.NA),
      h('td', { class: 'r', title: when(s.at) }, ago(s.at)),
      h('td', { class: 'wrap st-msg' }, s.message || ''))))));
  const cards = h('div', { class: 'ev-cards' }, ...a.sources.map(s => crow({
    title: h('span', { class: 'sym' }, s.name), chips: MARKET_LABEL[s.market] ? mkChip(s.market) : null, right: res(s),
    lines: [
      [kv('Items', F.isNum(s.items) ? F.int(s.items) : F.NA), kv('Time', F.isNum(s.duration_s) ? F.num(s.duration_s, 1) + ' s' : F.NA), kv('Fetched', ago(s.at))],
      s.message ? [h('span', { class: 'st-msg-line' }, s.message)] : [],
    ].filter(l => l.length),
  })));
  return h('section', { class: 'stack' },
    sectionTitle('Sources', failed ? chip(`${failed} failed`, 'bad', { icon: 'alert' }) : chip('All answered', 'up', { icon: 'check' }),
      'Where the last refresh got its prices. Each market has several sources in a fallback chain, so one failure often only means the next source was used.'),
    card({ flush: true, body: [table, cards] }));
}

function helpCard() {
  const steps = [
    ['Look at the last run.', 'Open your repository on GitHub, choose the Actions tab and open the latest “Refresh data” run. A red cross means a step failed; open it to read the reason.'],
    ['Run it again.', 'Press “Run workflow” on that page to refresh right now. It takes a few minutes. Then press “Check for new data” here.'],
    ['Find the broken source.', 'A failed source is usually harmless because the next one in the chain takes over. If a market stays empty for more than a day, a source has probably changed its format. Run “python -m acumen doctor” on any computer with Python to test every source and see which one broke.'],
    ['Check the schedule.', 'GitHub pauses scheduled runs in a repository that has had no activity for 60 days. If the data stops getting newer, open the Actions tab and enable the workflow again.'],
  ];
  return h('details', { class: 'card ev-gloss' },
    h('summary', null, h('div', { class: 'label' }, 'If something looks wrong')),
    h('ol', { class: 'st-steps' }, ...steps.map(([t, x]) => h('li', null, h('b', null, t + ' '), x))));
}

/* ======================================================================= preferences */
function prefsTab() {
  const root = h('div', { class: 'stack loose' });
  let acct = null;

  const grpEx = h('span', { class: 'mono st-ex' });
  const showEx = () => { grpEx.textContent = `${F.int(1234567)} and ${F.compact(25000000)}`; };
  showEx();

  const appearance = card({
    title: 'Appearance',
    body: rows(
      row('Theme', 'Light, dark, or follow your device.',
        seg([{ value: 'system', label: 'System' }, { value: 'light', label: 'Light', icon: 'sun' }, { value: 'dark', label: 'Dark', icon: 'moon' }], S.get('theme'),
          v => { S.set('theme', v); applyAppearance(); }, { label: 'Theme' })),
      row('Up and down colours', 'Green and red, or blue and orange for colour-blind readers. Direction is also shown with arrows and signs, never colour alone.', h('div', { class: 'row wrap' },
        seg([{ value: 'rg', label: 'Green and red' }, { value: 'cb', label: 'Blue and orange' }], S.get('palette'), v => { S.set('palette', v); applyAppearance(); }, { label: 'Up and down colours' }),
        h('span', { class: 'row st-prev', 'aria-hidden': 'true' }, chgSpan(1.24), chgSpan(-0.87)))),
      row('Number style', 'Group digits in thousands (1,234,567) or the South Asian way with lakh and crore (12,34,567).', h('div', { class: 'row wrap' },
        seg([{ value: 'intl', label: '1,234,567' }, { value: 'sa', label: '12,34,567' }], S.get('grouping'), v => { S.set('grouping', v); showEx(); if (acct) acct.refresh(); }, { label: 'Number style' }),
        h('span', { class: 'muted fs-sm' }, 'e.g. ', grpEx))),
      row('Density', 'Compact fits more rows on the screen.',
        seg([{ value: 'comfortable', label: 'Comfortable' }, { value: 'compact', label: 'Compact' }], S.get('density'), v => { S.set('density', v); applyAppearance(); }, { label: 'Density' }))),
  });

  acct = accountControls();
  const account = card({
    title: 'Account and risk',
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted st-lead' }, 'These size every idea: how many shares, lots or coins to buy so that hitting the stop costs about the risk you choose. They are saved on this device only and used the same way on the Portfolio screen.'),
      acct),
  });

  append(root, [appearance, account]);
  return root;
}

/* ======================================================================= this device */
function deviceTab(ctx, redraw) {
  return append(h('div', { class: 'stack loose' }), [installCard(ctx), storageCard(ctx), securityCard(ctx, redraw), backupCard(redraw), resetCard(redraw)]);
}

function installHelp() {
  const ua = navigator.userAgent || '';
  const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const android = /Android/.test(ua);
  if (ios) return 'In Safari, tap the Share button, then “Add to Home Screen”. Open Acumen from that icon so it runs without browser bars.';
  if (android) return 'Open the browser menu (the three dots) and choose “Install app” or “Add to Home screen”.';
  return 'In Chrome or Edge, use the install icon at the right end of the address bar, or open the menu and choose “Install Acumen”. In Safari on a Mac, choose File, then “Add to Dock”.';
}

// How much of the top-level data the service worker has kept for offline use.
async function savedData() {
  if (!('caches' in window)) return null;
  const want = ['meta.json', ...D.expectedFiles()];
  const have = new Set();
  try {
    const cache = await caches.open('acumen-data-v1');
    for (const req of await cache.keys()) {
      const path = new URL(req.url).pathname;
      for (const f of want) if (path.endsWith('/data/' + f)) have.add(f);
    }
  } catch { return null; }
  return { have: have.size, want: want.length };
}

// { chip, pending }: pending means the state is still changing (data is being saved), so ask again shortly.
async function offlineState() {
  if (MEMORY) return { chip: chip('Not in this preview', 'outline') };
  if (!('serviceWorker' in navigator)) return { chip: chip('Not supported here', 'warn', { icon: 'alert' }) };
  const secure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  if (!secure) return { chip: chip('Needs HTTPS', 'warn', { icon: 'alert' }) };
  let reg = null;
  try { reg = await navigator.serviceWorker.getRegistration(); } catch { /* ignore */ }
  if (!reg) return { chip: chip('Not set up yet', 'outline', { icon: 'clock' }), pending: true };
  if (!navigator.serviceWorker.controller) return { chip: chip('Reload once to finish', 'outline', { icon: 'clock' }) };
  const d = await savedData();
  if (d && d.have < d.want) return { chip: chip(`Saving data (${d.have} of ${d.want} files)`, 'outline', { icon: 'clock' }), pending: true };
  return { chip: chip('Ready', 'up', { icon: 'check' }) };
}

function installCard(ctx) {
  const installBody = h('div', { class: 'st-rows' });
  const offlineSlot = h('span', null, '…');
  const draw = () => {
    let r;
    if (MEMORY) r = row('Install on this device', 'The installable app belongs to the deployed site. This preview cannot be installed.', chip('Preview only', 'outline'));
    else if (isStandalone()) r = row('Installed', 'You are using Acumen as an app. It opens in its own window and keeps working offline with the last downloaded data.', chip('Installed', 'up', { icon: 'check' }));
    else if (window.__installPrompt) {
      const p = window.__installPrompt;
      r = row('Install Acumen', 'Adds an icon to your home screen or desktop, opens without browser bars and works offline.',
        h('button', { class: 'btn primary', type: 'button', onclick: async () => {
          try { p.prompt(); await p.userChoice; } catch { /* dismissed */ }
          window.__installPrompt = null; draw();
        } }, icon('download'), 'Install'));
    } else r = row('Install Acumen', installHelp());
    replace(installBody, r, row('Works offline', 'After the first visit the app and its main data files are saved on this device, so it opens without a connection. Instrument pages you have not opened yet need a connection the first time. Prices and ideas always show their real age.', offlineSlot));
  };
  draw();
  // The state can still be changing right after the first visit (the worker is saving the data): look again for a while.
  let stopped = false, tries = 0, timer = null;
  const poll = async () => {
    const st = await offlineState();
    if (stopped || ctx.dead) return;
    replace(offlineSlot, st.chip);
    if (st.pending && ++tries < 12) timer = setTimeout(poll, 1500);
  };
  poll();
  ctx.cleanups.push(() => { stopped = true; clearTimeout(timer); });
  const onInstallable = () => draw();
  const onInstalled = () => { window.__installPrompt = null; draw(); toast('Acumen is installed'); };
  window.addEventListener('acumen:installable', onInstallable);
  window.addEventListener('appinstalled', onInstalled);
  ctx.cleanups.push(() => { window.removeEventListener('acumen:installable', onInstallable); window.removeEventListener('appinstalled', onInstalled); });
  return card({ title: 'Install and offline', body: installBody });
}

async function clearAppCaches({ data = false, shell = false } = {}) {
  if (!('caches' in window)) return;
  for (const k of await caches.keys()) {
    if ((data && k.startsWith('acumen-data')) || (shell && k.startsWith('acumen-shell'))) await caches.delete(k);
  }
}
async function dropServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  try { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); } catch { /* ignore */ }
}

function storageCard(ctx) {
  const body = h('div', { class: 'st-rows' });
  const draw = async () => {
    let est = null, persisted = null, files = null;
    try { est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null; } catch { /* ignore */ }
    try { persisted = navigator.storage && navigator.storage.persisted ? await navigator.storage.persisted() : null; } catch { /* ignore */ }
    try {
      if ('caches' in window) { files = 0; for (const k of await caches.keys()) if (k.startsWith('acumen-data')) files += (await (await caches.open(k)).keys()).length; }
    } catch { files = null; }
    if (ctx.dead) return;
    const use = est && F.isNum(est.usage) ? `This site uses ${bytes(est.usage)}${F.isNum(est.quota) ? ` of the ${bytes(est.quota)} the browser allows` : ''}.` : '';
    const build = buildId();
    const rowsList = [
      row('Downloaded data', `${files == null ? 'Market data is cached by the browser for offline use.' : files ? `${F.int(files)} data files are kept for offline use.` : 'No data files are kept yet.'} ${use}`.trim(),
        h('button', { class: 'btn', type: 'button', onclick: async () => {
          const ok = await ask({ title: 'Clear downloaded data?', text: navigator.onLine === false ? 'You are offline. Without the saved copy Acumen cannot show any data until you reconnect.' : 'The saved copy of the market data is removed and downloaded again now. Your positions, watchlist and settings are not touched.', confirm: 'Clear and reload', danger: true });
          if (!ok) return;
          await clearAppCaches({ data: true }); toast('Cleared. Reloading…'); setTimeout(() => location.reload(), 350);
        } }, icon('trash'), 'Clear')),
      row('App files', build ? `Version ${build}. If the app looks outdated or seems stuck, this fetches a fresh copy.` : 'If the app looks outdated or seems stuck, this fetches a fresh copy.',
        h('button', { class: 'btn', type: 'button', onclick: async () => {
          await dropServiceWorker(); await clearAppCaches({ shell: true }); toast('Fetching fresh app files…'); setTimeout(() => location.reload(), 350);
        } }, icon('refresh'), 'Refresh app files')),
    ];
    if (persisted !== null && navigator.storage && navigator.storage.persist) {
      rowsList.push(row('Protect my data from automatic cleanup', 'Your positions and watchlist live only in this browser. Browsers may clear a site’s storage when the device runs low on space unless the site asks to be kept.',
        persisted ? chip('Protected', 'up', { icon: 'check' }) : h('button', { class: 'btn', type: 'button', onclick: async () => {
          let ok = false; try { ok = await navigator.storage.persist(); } catch { /* ignore */ }
          toast(ok ? 'Your data is now protected from automatic cleanup' : 'The browser did not agree. Installing the app, or exporting a backup, is the safer route.'); draw();
        } }, icon('shield'), 'Ask the browser to protect it')));
    }
    replace(body, ...rowsList);
  };
  draw();
  return card({ title: 'Storage', body });
}

function securityCard(ctx, redraw) {
  if (!D.encrypted) {
    return card({
      title: 'Privacy of the data',
      body: h('div', { class: 'stack' },
        banner('info', h('b', null, 'This site’s data is not encrypted. '), 'Anyone who finds the address can read the market data and ideas, which come from public sources. To keep them private, add a passphrase to your repository (the README shows how) and the next refresh encrypts everything.'),
        h('p', { class: 'muted st-lead' }, 'Your own positions, watchlist and settings never leave this browser, encrypted or not.')),
    });
  }
  const saved = !!Secret.get();
  return card({
    title: 'Privacy of the data',
    body: rows(
      row('Encrypted', 'The market data and ideas are encrypted with AES-256 and opened inside this browser with your passphrase, which is never sent anywhere.', chip('On', 'up', { icon: 'lock' })),
      row('Remembered on this device', saved ? 'You will not be asked for the passphrase here. What is kept is a key derived from it, not the passphrase itself, but anyone who can use this browser profile can open the data while it is kept. Forget it on a shared device.' : 'You are asked for the passphrase each time you open Acumen.',
        saved ? h('button', { class: 'btn', type: 'button', onclick: () => { Secret.clear(); toast('Forgotten. You will be asked next time.'); redraw(); } }, icon('unlock'), 'Forget on this device') : chip('Not remembered', 'outline')),
      row('Lock now', 'Closes the data on this device and asks for the passphrase again. To change the passphrase, change the repository secret and refresh the data; then enter the new one here.',
        h('button', { class: 'btn', type: 'button', onclick: () => { D.lock(); location.reload(); } }, icon('lock'), 'Lock now'))),
  });
}

function counts() {
  const list = J.list();
  const open = list.filter(t => t.status !== 'closed').length;
  return { open, closed: list.length - open, watch: (S.get('watchlist') || []).length, total: list.length };
}

function backupCard(redraw) {
  const c = counts();
  const mine = c.total || c.watch
    ? `${F.int(c.total)} ${c.total === 1 ? 'position' : 'positions'} (${F.int(c.open)} open, ${F.int(c.closed)} closed) and ${F.int(c.watch)} watchlist ${c.watch === 1 ? 'item' : 'items'} are stored here.`
    : 'No positions or watchlist items are stored here yet.';
  return card({
    title: 'Your positions and watchlist',
    body: rows(
      row('Stored only in this browser', `${mine} They are not sent to any server and not shared with your other devices. Clearing this site’s data in the browser deletes them, so export a backup now and then.`),
      row('Back up or move to another device', 'Export saves a file. Import adds the positions and watchlist from such a file and skips any you already have.',
        h('div', { class: 'row wrap' },
          h('button', { class: 'btn', type: 'button', onclick: exportData }, icon('download'), 'Export'),
          h('button', { class: 'btn', type: 'button', onclick: () => importData(redraw) }, icon('upload'), 'Import')))),
  });
}

function resetCard(redraw) {
  const c = counts();
  return card({
    title: 'Reset', cls: 'st-danger',
    body: rows(
      row('Reset preferences', 'Puts the theme, colours, number style, density, filters, account size and risk back to the defaults. Your positions and watchlist stay.',
        h('button', { class: 'btn', type: 'button', onclick: async () => {
          const ok = await ask({ title: 'Reset preferences?', text: 'Appearance, filters, account size and risk go back to the defaults. Your positions and watchlist are kept.', confirm: 'Reset preferences', danger: true });
          if (!ok) return;
          S.resetPrefs(); applyAppearance(); toast('Preferences reset'); redraw();
        } }, icon('refresh'), 'Reset preferences')),
      row('Erase everything on this device', 'Removes your positions, watchlist, preferences, the remembered passphrase and the saved copy of the data from this browser. Nothing on other devices or in your repository is affected.',
        h('button', { class: 'btn danger', type: 'button', onclick: async () => {
          const ok = await ask({ title: 'Erase everything on this device?', text: `This deletes ${F.int(c.total)} ${c.total === 1 ? 'position' : 'positions'}, ${F.int(c.watch)} watchlist ${c.watch === 1 ? 'item' : 'items'}, your preferences and the saved data copy from this browser. It cannot be undone. Export a backup first if you want to keep your positions.`, confirm: 'Erase everything', danger: true });
          if (!ok) return;
          S.reset(); Secret.clear(); applyAppearance();
          await clearAppCaches({ data: true, shell: true }); await dropServiceWorker();
          toast('Erased. Reloading…'); setTimeout(() => location.reload(), 400);
        } }, icon('trash'), 'Erase everything'))),
  });
}

/* ======================================================================= about */
const LIBS = [
  ['TradingView Lightweight Charts™', 'Price charts. © TradingView, Inc., Apache License 2.0.', 'https://www.tradingview.com/'],
  ['IBM Plex Sans and Plex Mono', 'The typefaces. © IBM Corp., SIL Open Font License 1.1.', 'https://github.com/IBM/plex'],
];

function aboutTab() {
  const meta = D.meta || {};
  const build = buildId();
  const sources = Array.isArray(meta.sources) ? meta.sources : [];
  const bySource = new Map();
  for (const s of sources) { const k = s.market; if (!bySource.has(k)) bySource.set(k, []); bySource.get(k).push(s.name); }
  const kvRows = [
    ['App version', meta.version || F.NA],
    build ? ['App build', build] : null,
    ['Data format', meta.schema != null ? `v${meta.schema}` : F.NA],
    ['Data refreshed', meta.generated_at ? `${when(meta.generated_at)} (${ago(meta.generated_at)})` : F.NA],
    ['Kind of data', meta.demo ? 'Demo (synthetic)' : 'Live'],
    ['Encryption', D.encrypted ? 'On' : 'Off'],
  ].filter(Boolean);

  const how = card({
    title: 'How Acumen works',
    body: h('ol', { class: 'st-steps' },
      h('li', null, h('b', null, 'A scheduled job gathers the data. '), 'Several times a day a job in your GitHub repository downloads prices and other figures from public sources, trying a backup source when one fails.'),
      h('li', null, h('b', null, 'It does the analysis ahead of time. '), 'For every stock, currency pair and coin it computes indicators, ratios and scores, builds a trade plan with entry, stop and targets, sizes it to your risk, and tests how the same method would have done on past data.'),
      h('li', null, h('b', null, 'This app only reads the results. '), 'The job publishes plain files next to the app. There is no server to run or pay for, and the last downloaded copy keeps working offline.')),
  });

  const src = bySource.size ? card({
    title: 'Sources in the last refresh',
    body: h('div', { class: 'stack' },
      ...[...bySource.entries()].map(([m, names]) => h('div', { class: 'row wrap st-srcrow' }, MARKET_LABEL[m] ? mkChip(m) : m, ...names.map(n => h('span', { class: 'chip outline mono' }, n)))),
      h('p', { class: 'muted fs-sm st-lead' }, 'The data belongs to its providers. Acumen only reads what each one publishes openly, for personal research. Check a provider’s terms before passing its data on.')),
  }) : null;

  const credits = card({
    title: 'Credits',
    body: h('ul', { class: 'st-credits' }, ...LIBS.map(([n, t, u]) => h('li', null, h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, n), h('span', { class: 'muted' }, ' · ' + t)))),
  });

  const keys = card({
    title: 'Shortcuts',
    body: h('div', { class: 'st-keys' },
      h('span', null, h('kbd', null, '/'), ' or ', h('kbd', null, 'Ctrl'), ' + ', h('kbd', null, 'K'), h('span', { class: 'muted' }, '  Search any stock, pair or coin')),
      h('span', null, h('kbd', null, 'Esc'), h('span', { class: 'muted' }, '  Close a dialog or the search'))),
  });

  return append(h('div', { class: 'stack loose' }), [
    card({ title: 'Acumen', body: h('dl', { class: 'kv st-kv' }, ...kvRows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)])) }),
    how, src, h('div', { class: 'grid c2 top st-two' }, credits, keys),
    meta.disclaimer ? banner('info', h('b', null, 'Please read. '), meta.disclaimer) : null,
  ]);
}
