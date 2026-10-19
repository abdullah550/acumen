import { h, clear, debounce } from '../lib/dom.js';
import { icon, logo } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { S, applyAppearance, isDark } from '../lib/store.js';
import { D } from '../lib/data.js';
import { go, linkProps, onRoute, current } from '../lib/router.js';
import { MARKET_LABEL } from '../lib/util.js';
import { mkChip, gradeBadge, toast } from './kit.js';

export const NAV = [
  { id: 'home', path: '/', label: 'Command Center', short: 'Home', icon: 'home' },
  { id: 'ideas', path: '/ideas', label: 'Trade Ideas', short: 'Ideas', icon: 'bolt' },
  { id: 'screener', path: '/screener', label: 'Screener', short: 'Screener', icon: 'table' },
  { id: 'portfolio', path: '/portfolio', label: 'Portfolio', short: 'Portfolio', icon: 'brief' },
  { id: 'markets', path: '/markets', label: 'Markets', short: 'Markets', icon: 'globe' },
  { id: 'evidence', path: '/evidence', label: 'Evidence', short: 'Evidence', icon: 'shield' },
  { id: 'settings', path: '/settings', label: 'Data & Settings', short: 'Settings', icon: 'sliders' },
];
const MOBILE_MAIN = ['home', 'ideas', 'screener', 'portfolio'];

export function sectionOf(segs) {
  const a = segs[0] || '';
  if (a === 'i') return 'screener';
  return NAV.find(n => n.path === '/' + a)?.id || (a === '' ? 'home' : 'home');
}

function marketStatusRows() {
  const m = (D.meta && D.meta.markets) || {};
  return Object.entries(m).map(([k, v]) => ({ k, ...v }));
}

export function freshness() {
  const rows = marketStatusRows();
  const bad = rows.filter(r => r.status === 'error');
  const stale = rows.filter(r => r.status === 'stale' || r.status === 'warming');
  const gen = D.meta && D.meta.generated_at;
  const hrs = gen ? (Date.now() - new Date(gen).getTime()) / 36e5 : null;
  if (D.meta && D.meta.demo) return { tone: 'warn', text: 'Demo data', detail: 'Synthetic data for previewing the app' };
  if (bad.length) return { tone: 'bad', text: `${bad.map(r => MARKET_LABEL[r.k]).join(', ')}: no data`, detail: 'See Data & Settings' };
  if (hrs != null && hrs > 36) return { tone: 'warn', text: `Updated ${F.ago(gen)}`, detail: 'The refresh job has not run recently' };
  if (stale.length) return { tone: 'warn', text: `${stale.map(r => MARKET_LABEL[r.k]).join(', ')} ${stale[0].status === 'warming' ? 'warming up' : 'stale'}`, detail: 'See Data & Settings' };
  return { tone: 'ok', text: gen ? `Updated ${F.ago(gen)}` : 'Up to date', detail: gen ? F.dateTime(gen) : '' };
}

export function buildShell() {
  const outlet = h('main', { class: 'page', id: 'main' });
  const title = h('div', { class: 'tb-title' }, 'Acumen');
  const fresh = h('a', { class: 'chip fresh', ...linkProps('/settings'), title: 'Data status' });
  const themeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Change theme', title: 'Theme' });
  const lockBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Lock this device', title: 'Lock this device' }, icon('lock'));
  const searchBtn = h('button', { class: 'search-btn', type: 'button', 'aria-label': 'Search instruments' }, icon('search'), h('span', { class: 'sb-long' }, 'Search any stock, pair or coin'), h('span', { class: 'sb-short' }, 'Search'), h('kbd', null, '/'));
  searchBtn.firstChild.style.cssText = 'width:16px;height:16px;flex:none';

  const navLinks = NAV.map(n => h('a', { ...linkProps(n.path), 'data-nav': n.id }, icon(n.icon), h('span', { class: 't' }, n.label)));
  const rail = h('aside', { class: 'rail' },
    h('a', { class: 'brand', ...linkProps('/') }, logo(30), h('div', null, h('div', { class: 'brand-name' }, 'Acumen'), h('div', { class: 'brand-sub' }, 'MARKET INTELLIGENCE'))),
    h('nav', { class: 'nav', 'aria-label': 'Main' }, ...navLinks),
    h('div', { class: 'rail-foot' }, h('div', { class: 'mini-status', id: 'mini-status' })));

  const tabItems = [...MOBILE_MAIN.map(id => NAV.find(n => n.id === id)), { id: 'more', label: 'More', short: 'More', icon: 'menu' }];
  const tabLinks = tabItems.map(n => n.id === 'more'
    ? h('button', { type: 'button', 'data-nav': 'more', onclick: () => openMore() }, icon(n.icon), n.short)
    : h('a', { ...linkProps(n.path), 'data-nav': n.id }, icon(n.icon), n.short));
  const tabbar = h('nav', { class: 'tabbar', 'aria-label': 'Main' }, ...tabLinks);

  const top = h('header', { class: 'topbar' },
    h('a', { class: 'tb-brand', ...linkProps('/') }, logo(26), 'Acumen'),
    title, h('div', { class: 'grow' }), searchBtn, fresh, themeBtn, lockBtn);

  const root = h('div', { class: 'app' }, rail, h('div', { class: 'main' }, top, outlet), tabbar);

  const setTheme = () => {
    const t = S.get('theme');
    themeBtn.replaceChildren(icon(t === 'system' ? (isDark() ? 'moon' : 'sun') : t === 'dark' ? 'moon' : 'sun'));
    themeBtn.title = `Theme: ${t}`;
  };
  themeBtn.addEventListener('click', () => {
    const order = ['system', 'light', 'dark'];
    const next = order[(order.indexOf(S.get('theme')) + 1) % 3];
    S.set('theme', next); applyAppearance(); setTheme(); toast('Theme: ' + next);
  });
  setTheme();
  // Settings can change the theme too, and the system theme can flip while the app is open.
  window.addEventListener('acumen:appearance', setTheme);

  lockBtn.hidden = !D.encrypted;
  lockBtn.addEventListener('click', () => { D.lock(); location.reload(); });
  searchBtn.addEventListener('click', () => openSearch());
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || '') && !e.metaKey && !e.ctrlKey) { e.preventDefault(); openSearch(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); }
  });

  const refreshStatus = () => {
    const f = freshness();
    clear(fresh);
    fresh.className = 'chip fresh ' + (f.tone === 'ok' ? 'up' : f.tone === 'bad' ? 'bad' : 'warn');
    fresh.append(h('span', { class: 'dot ' + (f.tone === 'ok' ? 'ok' : f.tone === 'bad' ? 'error' : 'stale') }), f.text);
    fresh.title = f.detail;
    const mini = root.querySelector('#mini-status');
    if (mini) {
      clear(mini);
      for (const r of marketStatusRows()) {
        mini.append(h('div', { class: 'ms-row' }, h('span', { class: 'dot ' + (r.status || 'ok') }), h('b', null, MARKET_LABEL[r.k] || r.k), h('span', { class: 'ms-time' }, F.date(r.asof, { year: false }))));
      }
    }
  };

  const mark = r => {
    const sec = sectionOf(r.segs);
    root.querySelectorAll('[data-nav]').forEach(a => {
      if (a.dataset.nav === sec || (a.dataset.nav === 'more' && ['markets', 'evidence', 'settings'].includes(sec))) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    const n = NAV.find(x => x.id === sec);
    title.textContent = r.segs[0] === 'i' ? 'Instrument' : n ? n.label : 'Acumen';
  };
  onRoute(mark);
  mark(current());
  refreshStatus();

  return { root, outlet, setTitle: t => { title.textContent = t; }, refreshStatus };
}

/* ---------- More sheet (mobile) ---------- */
import { sheet } from './kit.js';
function openMore() {
  const items = NAV.filter(n => !MOBILE_MAIN.includes(n.id));
  const sh = sheet({
    title: 'More', center: false,
    body: h('div', { class: 'stack tight' }, ...items.map(n => h('a', { class: 'more-row', ...linkProps(n.path), onclick: () => setTimeout(() => sh.close(), 0) }, icon(n.icon), h('span', null, n.label), icon('right')))),
  });
}

/* ---------- Search palette ---------- */
// A combobox: focus stays in the field, the arrow keys move a highlighted option, and screen readers
// are told which one it is (aria-activedescendant) and how many matched.
let palOpen = false;
export async function openSearch() {
  if (palOpen) return;
  palOpen = true;
  const opener = document.activeElement;
  const input = h('input', {
    type: 'search', role: 'combobox', placeholder: 'Search symbol or name…', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Search',
    'aria-autocomplete': 'list', 'aria-expanded': 'true', 'aria-controls': 'pal-list',
  });
  // tabindex -1: the list is scrolled with the arrow keys from the field, so it is not a tab stop of its own
  const list = h('div', { class: 'pl', role: 'listbox', id: 'pal-list', 'aria-label': 'Matching instruments', tabindex: '-1' });
  const status = h('div', { class: 'sr-only', role: 'status', 'aria-live': 'polite' });
  const foot = h('div', { class: 'pf' }, h('span', null, '↑↓ to move'), h('span', null, 'Enter to open'), h('span', null, 'Esc to close'));
  const box = h('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Search instruments' }, h('div', { class: 'pi' }, icon('search'), input), list, status, foot);
  const scrim = h('div', { class: 'scrim', style: { zIndex: 95 } });
  document.body.append(scrim, box);
  input.focus();
  let rows = [];
  let sel = 0;
  let items = [];
  try { const idx = await D.index(); rows = Array.from(idx.values()); } catch { /* data not ready */ }
  const close = (restore = true) => {
    palOpen = false; box.remove(); scrim.remove(); document.removeEventListener('keydown', onKey, true);
    if (restore && opener && opener !== document.body && opener.isConnected && opener.focus) opener.focus();
  };
  const pick = it => { if (!it) return; close(false); const rec = S.get('recent') || []; S.set('recent', [it.id, ...rec.filter(x => x !== it.id)].slice(0, 8), { silent: true }); go('/i/' + it.id); };
  const render = () => {
    clear(list);
    if (!items.length) {
      // a disabled option, so the listbox never holds anything but options
      list.append(h('div', { class: 'empty', role: 'option', 'aria-disabled': 'true', 'aria-selected': 'false' }, h('div', { class: 't' }, 'No matches'), h('div', null, 'Try a ticker like HUBC, a pair like EURUSD or a coin like BTC')));
      input.removeAttribute('aria-activedescendant');
      status.textContent = 'No matches';
      return;
    }
    items.forEach((it, i) => {
      const best = it.best;
      list.append(h('div', { class: 'po', role: 'option', id: 'pal-o-' + i, 'aria-selected': String(i === sel), onclick: () => pick(it), onmousemove: () => { if (sel !== i) { sel = i; mark(); } } },
        h('span', { class: 's' }, it.sym), h('span', { class: 'n' }, it.name), mkChip(it.m), best ? gradeBadge(best.grade) : null,
        h('span', { class: 'mono muted', style: { minWidth: '72px', textAlign: 'right' } }, F.px(it.px, it.m))));
    });
    status.textContent = `${items.length} ${items.length === 1 ? 'match' : 'matches'}`;
    mark();
  };
  const mark = () => {
    if (!items.length) return;
    [...list.children].forEach((c, i) => c.setAttribute('aria-selected', String(i === sel)));
    const el = list.children[sel];
    if (el) { input.setAttribute('aria-activedescendant', el.id); if (el.scrollIntoView) el.scrollIntoView({ block: 'nearest' }); }
  };
  const query = () => {
    const q = input.value.trim().toLowerCase();
    if (!q) {
      const ids = [...(S.get('recent') || []), ...(S.get('watchlist') || [])];
      const idx = new Map(rows.map(r => [r.id, r]));
      const seen = new Set();
      items = ids.map(i => idx.get(i)).filter(r => r && !seen.has(r.id) && seen.add(r.id)).slice(0, 12);
      if (!items.length) items = rows.filter(r => r.best && r.best.grade === 'A').slice(0, 8);
    } else {
      const sc = r => { const s = r.sym.toLowerCase(), n = (r.name || '').toLowerCase(); return s === q ? 0 : s.startsWith(q) ? 1 : n.startsWith(q) ? 2 : s.includes(q) ? 3 : n.includes(q) ? 4 : 9; };
      items = rows.map(r => [sc(r), r]).filter(x => x[0] < 9).sort((a, b) => a[0] - b[0] || a[1].sym.length - b[1].sym.length).slice(0, 30).map(x => x[1]);
    }
    sel = 0; render();
  };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Tab') { e.preventDefault(); input.focus(); } // the field is the only control, so keep focus in it
    else if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) { sel = Math.min(items.length - 1, sel + 1); mark(); } }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) { sel = Math.max(0, sel - 1); mark(); } }
    else if (e.key === 'Enter') { e.preventDefault(); pick(items[sel]); }
  };
  document.addEventListener('keydown', onKey, true);
  scrim.addEventListener('click', () => close());
  input.addEventListener('input', debounce(query, 60));
  query();
}
