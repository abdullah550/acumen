import { h, s, clear, append } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { MARKET_LABEL, HZ_LABEL, HORIZONS } from '../lib/util.js';

/* ---------- small atoms ---------- */
export function chip(text, tone, extra) {
  return h('span', { class: ['chip', tone, extra && extra.cls], title: extra && extra.title }, extra && extra.icon ? icon(extra.icon) : null, text);
}
export const mkChip = m => h('span', { class: `chip mk mk-${m}` }, MARKET_LABEL[m] || m);

export function hzGlyph(hz) {
  const n = Math.max(0, HORIZONS.indexOf(hz)) + 1;
  return s('svg', { viewBox: '0 0 14 12', width: 14, height: 12, 'aria-hidden': 'true' },
    ...[0, 1, 2].map(i => s('rect', { x: i * 5, y: 12 - (4 + i * 3), width: 4, height: 4 + i * 3, rx: 1, fill: 'currentColor', opacity: i < n ? 1 : 0.28 })));
}
export const hzChip = hz => h('span', { class: 'chip outline', title: `${HZ_LABEL[hz]} horizon` }, hzGlyph(hz), HZ_LABEL[hz] || hz);

// `research` marks an idea that is not actionable: its direction and grade are drawn in neutral colours, so that a plan which
// has not been shown to beat random entries cannot be mistaken for a recommendation. Pass `isResearch(idea)`.
export const isResearch = x => !!x && x.actionable === false && (x.action === 'BUY' || x.action === 'SELL');

export function gradeBadge(g, title, research = false) {
  return h('span', { class: ['grade', g || 'none', research && 'research'], title: title || (g ? `Grade ${g}${research ? ' (research only: not actionable)' : ''}` : 'No grade') }, g || '–');
}

export function actionChip(action, research = false) {
  if (action === 'BUY' || action === 'SELL') {
    const buy = action === 'BUY';
    return h('span', { class: ['chip', research ? 'outline' : buy ? 'up' : 'down'], title: research ? 'Direction of the signal only: not actionable' : null }, icon(buy ? 'up' : 'down'), action);
  }
  return h('span', { class: 'chip warn' }, icon('alert'), action || 'AVOID');
}

export const researchChip = () => h('span', { class: 'chip outline research', title: 'Not actionable: a plan worth studying, not a recommendation' }, icon('info'), 'Research only');

export function chgSpan(x, d = 1, opt = {}) {
  const cls = F.dirClass(x);
  const arrow = cls === 'up' ? s('svg', { viewBox: '0 0 10 10', width: 9, height: 9, 'aria-hidden': 'true' }, s('path', { d: 'M5 1.2 9 8H1z', fill: 'currentColor' }))
    : cls === 'down' ? s('svg', { viewBox: '0 0 10 10', width: 9, height: 9, 'aria-hidden': 'true' }, s('path', { d: 'M5 8.8 1 2h8z', fill: 'currentColor' })) : null;
  return h('span', { class: ['chg', cls, opt.cls] }, opt.noArrow ? null : arrow, opt.text != null ? opt.text : F.pct(x, d, { sign: opt.sign !== false }));
}

export function convMeter(conv, { calibrated = true, grade = null, compact = false } = {}) {
  const v = F.isNum(conv) ? F.clip(conv, 0, 100) : 0;
  const el = h('div', { class: ['cmeter', !calibrated && 'hatch'], title: calibrated ? `Conviction ${v} of 100` : `Conviction ${v} of 100 (not validated out of sample: probabilities use pooled base rates)` },
    h('div', { class: 'track' },
      h('div', { class: 'fill', style: { width: v + '%' } }),
      ...[45, 60, 75].map(t => h('div', { class: 'tick', style: { left: t + '%' } }))),
    compact ? null : h('span', { class: 'val' }, F.isNum(conv) ? Math.round(conv) : '–'));
  return el;
}

export function stat(label, value, sub, opt = {}) {
  return h('div', { class: 'stat' },
    h('div', { class: 'label' }, label, opt.info ? explain(opt.info) : null),
    h('div', { class: ['v', opt.small && 'sm', opt.cls] }, value),
    sub ? h('div', { class: 's' }, sub) : null);
}

export function card({ title, actions, body, cls, flush, info } = {}) {
  return h('section', { class: ['card', cls] },
    title || actions ? h('div', { class: 'card-h' }, h('div', { class: 'label' }, title, info ? explain(info) : null), actions ? h('div', { class: 'actions' }, actions) : null) : null,
    h('div', { class: ['card-b', flush && 'flush'] }, body));
}

export function banner(kind, ...kids) {
  const ic = kind === 'warn' ? 'alert' : kind === 'bad' ? 'alert' : 'info';
  return h('div', { class: ['banner', kind], role: kind === 'bad' ? 'alert' : null }, icon(ic), h('div', { class: 'grow' }, ...kids));
}

export function empty(title, text, ic = 'search') {
  return h('div', { class: 'empty' }, icon(ic), h('div', { class: 't' }, title), text ? h('div', null, text) : null);
}

export function skeleton(w = '100%', hgt = 16) { return h('div', { class: 'skel', style: { width: typeof w === 'number' ? w + 'px' : w, height: hgt + 'px' } }); }

/* ---------- controls ---------- */
export function seg(options, value, onChange, { sm = false, label } = {}) {
  const wrap = h('div', { class: ['seg', sm && 'sm'], role: 'group', 'aria-label': label || null });
  const render = v => {
    clear(wrap);
    for (const o of options) {
      const opt = typeof o === 'string' ? { value: o, label: o } : o;
      wrap.append(h('button', {
        type: 'button', 'aria-pressed': String(opt.value === v), title: opt.title || null,
        onclick: () => { render(opt.value); onChange(opt.value); },
      }, opt.icon ? icon(opt.icon) : null, opt.label));
    }
  };
  render(value);
  wrap.set = render;
  return wrap;
}

export function tabs(items, value, onChange) {
  const wrap = h('div', { class: 'tabs', role: 'tablist' });
  const render = v => {
    clear(wrap);
    for (const it of items) {
      wrap.append(h('button', {
        role: 'tab', type: 'button', 'aria-selected': String(it.value === v),
        onclick: () => { render(it.value); onChange(it.value); },
      }, it.label));
    }
  };
  render(value);
  return wrap;
}

export function toggle(label, checked, onChange) {
  const input = h('input', { type: 'checkbox', checked, onchange: e => onChange(e.target.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'tr' }), label);
}

export function pillToggle(label, pressed, onChange, swatch) {
  const b = h('button', { class: 'pill-toggle', type: 'button', 'aria-pressed': String(!!pressed) },
    swatch ? h('span', { class: 'sw', style: { background: swatch } }) : null, label);
  b.addEventListener('click', () => { const v = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(v)); onChange(v); });
  return b;
}

export function select(options, value, onChange, attrs = {}) {
  const el = h('select', { class: 'select', ...attrs, onchange: e => onChange(e.target.value) },
    ...options.map(o => { const op = typeof o === 'string' ? { value: o, label: o } : o; return h('option', { value: op.value, selected: op.value === value }, op.label); }));
  el.value = value;
  return el;
}

let fieldSeq = 0;
// A labelled control. The visible label is tied to the control (for/id) when the control is a single form element.
export function field(label, control, hint) {
  const single = control && /^(INPUT|SELECT|TEXTAREA)$/.test(control.tagName);
  let lab;
  if (single) {
    if (!control.id) control.id = 'fld' + (++fieldSeq);
    lab = h('label', { for: control.id }, label);
  } else lab = h('div', { class: 'flabel' }, label);
  return h('div', { class: 'field' }, lab, control, hint ? h('div', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, hint) : null);
}

/* ---------- popover ("explain") ---------- */
let popEl = null;
let popOwner = null;
export function showPop(anchorRect, content, { follow = false } = {}) {
  if (!popEl) { popEl = h('div', { class: 'pop', role: 'tooltip' }); document.body.append(popEl); }
  clear(popEl);
  append(popEl, [content]);
  popEl.style.display = 'block';
  positionPop(anchorRect, follow);
}
function positionPop(r, follow) {
  const pad = 10;
  const w = popEl.offsetWidth, hh = popEl.offsetHeight;
  let x, y;
  if (follow) { x = r.x + 14; y = r.y + 14; if (x + w > innerWidth - pad) x = r.x - w - 14; if (y + hh > innerHeight - pad) y = r.y - hh - 14; }
  else { x = r.left + r.width / 2 - w / 2; y = r.bottom + 8; if (y + hh > innerHeight - pad) y = r.top - hh - 8; }
  x = Math.max(pad, Math.min(innerWidth - w - pad, x)); y = Math.max(pad, y);
  popEl.style.left = x + 'px'; popEl.style.top = y + 'px';
}
export function hidePop() { if (popEl) popEl.style.display = 'none'; popOwner = null; }
document.addEventListener('pointerdown', e => { if (popOwner && !popOwner.contains(e.target)) hidePop(); }, true);
window.addEventListener('scroll', hidePop, true);

export function explain(content) {
  const btn = h('button', { class: 'info-btn', type: 'button', 'aria-label': 'What is this?' }, icon('info'));
  const node = typeof content === 'string' ? h('div', null, content) : content;
  const show = () => { popOwner = btn; showPop(btn.getBoundingClientRect(), node); };
  btn.addEventListener('mouseenter', show);
  btn.addEventListener('focus', show);
  btn.addEventListener('mouseleave', () => { if (document.activeElement !== btn) hidePop(); });
  btn.addEventListener('blur', hidePop);
  btn.addEventListener('click', e => { e.stopPropagation(); if (popEl && popEl.style.display === 'block' && popOwner === btn) hidePop(); else show(); });
  return btn;
}

/* ---------- toast ---------- */
let toastBox = null;
export function toast(msg, ms = 2600) {
  if (!toastBox) { toastBox = h('div', { class: 'toasts', 'aria-live': 'polite' }); document.body.append(toastBox); }
  const t = h('div', { class: 'toast' }, msg);
  toastBox.append(t);
  setTimeout(() => t.remove(), ms);
}

/* ---------- sheet ---------- */
let sheetCount = 0;
const openSheets = []; // topmost last: only that one answers Escape and Tab
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
export function sheet({ title, body, center = false, onClose, width } = {}) {
  const prevFocus = document.activeElement;
  const scrim = h('div', { class: 'scrim' });
  const closeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close' }, icon('x'));
  const content = h('div', { class: 'sheet-b' }, body);
  const el = h('div', { class: ['sheet', center && 'center'], role: 'dialog', 'aria-modal': 'true', 'aria-label': typeof title === 'string' ? title : 'Details', style: width ? { width } : null },
    h('div', { class: 'sheet-h' }, h('div', { class: 't' }, title), closeBtn), content);
  const api = {
    el, content,
    close() {
      document.removeEventListener('keydown', onKey, true);
      const at = openSheets.indexOf(api);
      if (at >= 0) openSheets.splice(at, 1);
      scrim.remove(); el.remove();
      if (--sheetCount === 0) document.documentElement.style.overflow = '';
      if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch { /* ignore */ } }
      if (onClose) onClose();
    },
    setBody(...kids) { clear(content); append(content, kids); },
  };
  // Keep Tab inside the dialog: wrap from the last control to the first and back.
  const trap = e => {
    const nodes = [...el.querySelectorAll(FOCUSABLE)].filter(n => n.getClientRects().length > 0);
    if (!nodes.length) { e.preventDefault(); closeBtn.focus(); return; }
    const first = nodes[0], last = nodes[nodes.length - 1], at = document.activeElement;
    if (!el.contains(at)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && at === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
  };
  const onKey = e => {
    if (openSheets[openSheets.length - 1] !== api) return;
    if (e.key === 'Escape') { e.stopPropagation(); api.close(); }
    else if (e.key === 'Tab') trap(e);
  };
  closeBtn.addEventListener('click', () => api.close());
  scrim.addEventListener('click', () => api.close());
  document.addEventListener('keydown', onKey, true);
  document.body.append(scrim, el);
  openSheets.push(api);
  sheetCount++; document.documentElement.style.overflow = 'hidden';
  closeBtn.focus();
  return api;
}

export function ask({ title, text, confirm = 'Confirm', danger = false }) {
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); } };
    const sh = sheet({
      title, center: true, onClose: () => finish(false),
      body: h('div', { class: 'stack' },
        h('p', { class: 'ink-2' }, text),
        h('div', { class: 'row', style: { justifyContent: 'flex-end' } },
          h('button', { class: 'btn', type: 'button', onclick: () => sh.close() }, 'Cancel'),
          h('button', { class: ['btn', danger ? 'danger' : 'primary'], type: 'button', onclick: () => { finish(true); sh.close(); } }, confirm))),
    });
  });
}

/* ---------- sparkline ---------- */
export function sparkline(values, { w = 100, hgt = 28, dir, area = true, cls, color } = {}) {
  const pts = (values || []).filter(F.isNum);
  if (pts.length < 2) return h('span', { class: 'muted' }, '');
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const span = hi - lo || 1;
  const pad = 2;
  const X = i => (i / (pts.length - 1)) * w;
  const Y = v => pad + (1 - (v - lo) / span) * (hgt - pad * 2);
  const d = pts.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1)).join('');
  const up = dir != null ? dir >= 0 : pts[pts.length - 1] >= pts[0];
  const col = color || (up ? 'var(--up)' : 'var(--down)');
  return s('svg', { viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: 'none', class: ['spark', cls], 'aria-hidden': 'true', style: { width: '100%', height: hgt + 'px', overflow: 'visible' } },
    area ? s('path', { d: d + `L${w} ${hgt}L0 ${hgt}Z`, fill: col, opacity: 0.1 }) : null,
    s('path', { d, fill: 'none', stroke: col, 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
}

export { h, s, icon, F };
