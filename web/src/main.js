import './styles/main.css';
import { h } from './lib/dom.js';
import { applyAppearance } from './lib/store.js';
import { D } from './lib/data.js';
import { start as startRouter, onRoute, MEMORY } from './lib/router.js';
import { logo, icon } from './lib/icons.js';
import { buildShell } from './ui/shell.js';
import { toast } from './ui/kit.js';
import { match } from './screens/index.js';
import { loadingBlock, errorBlock } from './screens/common.js';
import { watchScrollRegions } from './lib/scrollregions.js';

applyAppearance();
const app = document.getElementById('app');

window.__installPrompt = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); window.__installPrompt = e; window.dispatchEvent(new Event('acumen:installable')); });
window.addEventListener('offline', () => toast('You are offline. Showing the last downloaded data.'));
window.addEventListener('online', () => toast('Back online.'));

const splash = msg => h('main', { class: 'boot', 'aria-busy': 'true' }, h('div', { class: 'boot-in', role: 'status' }, logo(44), h('div', null, msg || 'Loading Acumen…')));

function fatal(err) {
  app.replaceChildren(h('main', { class: 'lock' }, h('div', { class: 'lock-card' },
    logo(40), h('h1', null, 'Acumen could not load its data'),
    h('p', null, String((err && err.message) || err)),
    h('p', { class: 'muted' }, 'If this is a new deployment, the first data refresh may still be running. If you were using the app before, check your connection: the last downloaded data is kept for offline use.'),
    h('button', { class: 'btn primary', type: 'button', onclick: () => location.reload() }, icon('refresh'), 'Try again'))));
}

function lockScreen(message) {
  const pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', placeholder: 'Passphrase', 'aria-label': 'Passphrase' });
  const rem = h('input', { type: 'checkbox', checked: true });
  const err = h('div', { class: 'err', role: 'alert' }, message || '');
  const btn = h('button', { class: 'btn primary block', type: 'submit' }, icon('unlock'), 'Unlock');
  const form = h('form', { class: 'stack', onsubmit: async ev => {
    ev.preventDefault();
    if (!pass.value) return;
    btn.disabled = true; btn.lastChild.textContent = 'Checking…'; err.textContent = '';
    await new Promise(r => setTimeout(r, 30));
    let ok = false;
    try { ok = await D.unlock(pass.value, rem.checked); } catch (e) { err.textContent = 'This browser cannot decrypt the data (' + e.message + ').'; }
    if (ok) { begin(); return; }
    btn.disabled = false; btn.lastChild.textContent = 'Unlock';
    if (!err.textContent) err.textContent = 'That passphrase does not open this data.';
    pass.select();
  } },
    pass, h('label', { class: 'check' }, rem, 'Remember on this device'), err, btn);
  app.replaceChildren(h('main', { class: 'lock' }, h('div', { class: 'lock-card' },
    logo(40), h('h1', null, 'Unlock Acumen'),
    h('p', null, 'The market data and ideas on this site are encrypted. Enter the passphrase you chose when you set it up. It is checked inside this browser and never sent anywhere.'),
    form)));
  setTimeout(() => pass.focus(), 50);
}

let token = 0;
let current = null;
let lastPath = null;

async function begin() {
  app.replaceChildren(splash('Opening your dashboard…'));
  try {
    await D.get('overview');
  } catch (e) {
    if (e.code === 'DECRYPT') { D.lock(); return lockScreen('The saved passphrase no longer opens the data. Enter the current one.'); }
    return fatal(e);
  }
  const shell = buildShell();
  app.replaceChildren(shell.root);
  watchScrollRegions(document.body);
  const outlet = shell.outlet;
  D.get('prices').then(() => shell.refreshStatus()).catch(() => {});
  onRoute(async r => {
    const my = ++token;
    const route = match(r.segs);
    document.title = (route.id === 'instrument' && r.segs[1] ? r.segs[1].split(':').pop() + ' · ' : route.id === 'home' ? '' : route.title + ' · ') + 'Acumen';
    const samePath = lastPath === r.path;
    lastPath = r.path;
    if (current && current.destroy) { try { current.destroy(); } catch { /* ignore */ } }
    current = null;
    if (!samePath) outlet.replaceChildren(loadingBlock(3));
    try {
      const res = await route.render(r);
      const out = res && res.nodeType ? { el: res } : res;
      if (my !== token) { if (out && out.destroy) out.destroy(); return; }
      outlet.replaceChildren(out.el);
      current = out;
      if (!samePath) window.scrollTo(0, 0);
    } catch (e) {
      if (my !== token) return;
      if (e && e.code === 'DECRYPT') { D.lock(); location.reload(); return; }
      outlet.replaceChildren(errorBlock(e, () => onRouteRerender()));
    }
  });
  const onRouteRerender = () => { lastPath = null; startRouter(); };
  startRouter();
  registerSW();
}

function registerSW() {
  if (MEMORY || !('serviceWorker' in navigator)) return;
  if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') return;
  navigator.serviceWorker.register('sw.js').then(warmWhenControlled).catch(() => { /* optional */ });
}

// Files fetched before the service worker takes control are not kept, so wait for it, then fetch them all once.
async function warmWhenControlled() {
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) await new Promise(res => navigator.serviceWorker.addEventListener('controllerchange', res, { once: true }));
  await D.warm();
  window.dispatchEvent(new Event('acumen:warmed'));
}

(async function boot() {
  app.replaceChildren(splash());
  try {
    await D.loadMeta();
  } catch (e) { return fatal(e); }
  if (D.encrypted && !(await D.tryStoredKey())) return lockScreen();
  begin();
})();
