// A table wider (or taller) than the space it sits in scrolls inside its own box. A scrolling box has to be
// reachable from the keyboard, or anyone without a mouse can never get to the columns that are cut off.
// This watches every .tbl-wrap and, only while it really scrolls, makes it focusable (the arrow keys then
// scroll it) and gives it a name taken from the heading above it. Wraps that fit are left alone, so the
// tab order does not fill up with stops that do nothing.
const SEL = '.tbl-wrap';
const MARK = 'data-scroll-region';
const HEADING = '.label, h1, h2, h3, h4, .card-h';

let ro = null;

function headingText(node) {
  const src = node.matches('.card-h') ? (node.querySelector('.label') || node) : node;
  const c = src.cloneNode(true);
  c.querySelectorAll('button, svg, [aria-hidden="true"]').forEach(n => n.remove());
  return c.textContent.replace(/\s+/g, ' ').trim();
}

// The closest heading above the table: a caption or label of its own, else the last heading that comes before it
// among its own siblings, else among its parent's siblings, and so on up the page.
export function nameOf(wrap) {
  const table = wrap.querySelector('table');
  const cap = table && table.querySelector('caption');
  const own = (cap && cap.textContent.trim()) || (table && table.getAttribute('aria-label')) || wrap.getAttribute('data-label');
  let t = own || '';
  for (let branch = wrap, p = wrap.parentElement; !t && p && p !== document.body; branch = p, p = p.parentElement) {
    for (const c of p.children) {
      if (c === branch) break;
      if (c.matches(HEADING)) t = headingText(c) || t;
    }
  }
  if (!t) return 'Data table';
  return /table$/i.test(t) ? t : `${t} table`;
}

function sync(wrap) {
  if (!wrap.isConnected) { if (ro) ro.unobserve(wrap); return; }
  const scrolls = wrap.scrollWidth > wrap.clientWidth + 1 || wrap.scrollHeight > wrap.clientHeight + 1;
  if (scrolls) {
    wrap.setAttribute(MARK, '');
    wrap.setAttribute('tabindex', '0');
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', nameOf(wrap));
  } else if (wrap.hasAttribute(MARK)) {
    for (const a of [MARK, 'tabindex', 'role', 'aria-label']) wrap.removeAttribute(a);
  }
}

function watch(wrap) {
  ro.observe(wrap);
  const table = wrap.querySelector('table'); // the table grows or shrinks without the wrap changing size
  if (table) ro.observe(table);
}

export function watchScrollRegions(root = document.body) {
  if (ro || typeof ResizeObserver !== 'function' || typeof MutationObserver !== 'function') return;
  ro = new ResizeObserver(entries => {
    const seen = new Set();
    for (const e of entries) {
      const wrap = e.target.matches(SEL) ? e.target : e.target.closest(SEL);
      if (!wrap || seen.has(wrap)) continue;
      seen.add(wrap);
      watch(wrap);
      sync(wrap);
    }
  });
  const found = n => {
    if (n.nodeType !== 1) return;
    const up = n.closest(SEL);
    if (up) watch(up);
    n.querySelectorAll(SEL).forEach(watch);
  };
  new MutationObserver(list => { for (const m of list) m.addedNodes.forEach(found); }).observe(root, { childList: true, subtree: true });
  found(root);
}
