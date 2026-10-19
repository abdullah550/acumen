// Tiny DOM helpers. h() builds HTML, s() builds SVG, both accept (tag, props, ...children).
const SVGNS = 'http://www.w3.org/2000/svg';

function apply(el, props) {
  for (const k in props) {
    const v = props[k];
    if (v == null || v === false) continue;
    if (k === 'class' || k === 'className') {
      el.setAttribute('class', Array.isArray(v) ? v.filter(Boolean).join(' ') : v);
    } else if (k === 'style' && typeof v === 'object') {
      for (const sk in v) {
        const sv = v[sk];
        if (sv == null) continue;
        if (sk.startsWith('--')) el.style.setProperty(sk, sv);
        else el.style[sk] = sv;
      }
    } else if (k.startsWith('on') && typeof v === 'function') {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'html') {
      el.innerHTML = v;
    } else if (k === 'dataset') {
      Object.assign(el.dataset, v);
    } else if (k === 'value' && 'value' in el) {
      el.value = v;
    } else if (k === 'checked' && 'checked' in el) {
      el.checked = !!v;
    } else if (v === true) {
      el.setAttribute(k, '');
    } else {
      el.setAttribute(k, v);
    }
  }
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false || k === true) continue;
    el.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) apply(el, props);
  return append(el, kids);
}

export function s(tag, props, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  if (props) apply(el, props);
  return append(el, kids);
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replace(el, ...kids) {
  clear(el);
  return append(el, kids);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function debounce(fn, ms = 150) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function raf(fn) { return requestAnimationFrame(fn); }

export function download(name, text, type = 'text/plain') {
  try {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return true;
  } catch { return false; }
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  try {
    const ta = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = text; document.body.append(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove(); return ok;
  } catch { return false; }
}
