import { s } from './dom.js';

// Hand-drawn 24x24 stroke icons. Paths are separated by '|'.
const P = {
  home: 'M3.5 11 12 4l8.5 7|M5.5 9.8V20h13V9.8|M10 20v-5.5h4V20',
  bolt: 'M13.2 2.8 5 13.6h6l-1 7.6 8.2-10.8h-6z',
  table: 'M4 5h16v14H4z|M4 10h16|M10 10v9',
  brief: 'M4 8.5h16V19H4z|M9 8.5V5.5h6v3|M4 13.5h16',
  globe: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M3 12h18|M12 3c3 3 3 15 0 18|M12 3c-3 3-3 15 0 18',
  shield: 'M12 3 5 6v5.5c0 4.4 3 7.6 7 9.5 4-1.9 7-5.1 7-9.5V6z|M9 12l2.2 2.2L15.5 10',
  sliders: 'M4 7h8|M16 7h4|M4 17h4|M12 17h8|M14 4.5v5|M10 14.5v5',
  search: 'M10.5 4a6.5 6.5 0 100 13 6.5 6.5 0 000-13z|M15.5 15.5 20 20',
  star: 'M12 3.6l2.5 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.1 2.8 1-5.8-4.2-4.1 5.8-.8z',
  up: 'M12 19V5|M6 11l6-6 6 6',
  down: 'M12 5v14|M6 13l6 6 6-6',
  caretUp: 'M6 15l6-7 6 7z',
  caretDown: 'M6 9l6 7 6-7z',
  right: 'M9 5l7 7-7 7',
  left: 'M15 5l-7 7 7 7',
  chevD: 'M5 9l7 7 7-7',
  chevU: 'M5 15l7-7 7 7',
  x: 'M6 6l12 12|M18 6 6 18',
  filter: 'M4 5.5h16l-6 7V19l-4-2v-4.5z',
  download: 'M12 4v11|M7 11l5 5 5-5|M5 20h14',
  upload: 'M12 16V5|M7 9l5-5 5 5|M5 20h14',
  lock: 'M6 11h12v9H6z|M8.5 11V8a3.5 3.5 0 017 0v3',
  unlock: 'M6 11h12v9H6z|M8.5 11V8a3.5 3.5 0 016.9-.9',
  refresh: 'M20 11a8 8 0 00-14.5-4.2L4 9|M4 4v5h5|M4 13a8 8 0 0014.5 4.2L20 15|M20 20v-5h-5',
  info: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M12 11v5.5|M12 7.6v.01',
  alert: 'M12 3.5 2.8 19.5h18.4z|M12 10v4.5|M12 17.2v.01',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  clock: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M12 7v5l3 2',
  target: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M12 7.5a4.5 4.5 0 100 9 4.5 4.5 0 000-9z|M12 11.6v.8',
  flag: 'M5 21V4|M5 5h11l-2 3.5 2 3.5H5',
  sun: 'M12 8a4 4 0 100 8 4 4 0 000-8z|M12 2.5v2|M12 19.5v2|M2.5 12h2|M19.5 12h2|M5.3 5.3l1.4 1.4|M17.3 17.3l1.4 1.4|M5.3 18.7l1.4-1.4|M17.3 6.7l1.4-1.4',
  moon: 'M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z',
  copy: 'M9 9h10.5v11H9z|M5 15V4h10.5',
  ext: 'M14 4h6v6|M20 4l-9 9|M18 14v6H4V6h6',
  plus: 'M12 5v14|M5 12h14',
  minus: 'M5 12h14',
  trash: 'M5 7h14|M9 7V4.5h6V7|M7 7l1 13h8l1-13',
  pulse: 'M3 12h4l3-8 4 16 3-8h4',
  trendUp: 'M3 17l6-6 4 4 8-8|M15 7h6v6',
  trendDown: 'M3 7l6 6 4-4 8 8|M15 17h6v-6',
  more: 'M5 12h.01|M12 12h.01|M19 12h.01',
  menu: 'M4 7h16|M4 12h16|M4 17h16',
  bell: 'M6 16v-5a6 6 0 0112 0v5l1.5 2h-15z|M10 20.5a2 2 0 004 0',
  calc: 'M6 3h12v18H6z|M9 7h6|M9 12h.01|M12 12h.01|M15 12h.01|M9 16h.01|M12 16h.01|M15 16h.01',
  book: 'M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z|M5 17a3 3 0 013-3h11',
  pie: 'M12 3a9 9 0 109 9h-9z|M15 3.5A9 9 0 0120.5 9H15z',
  layers: 'M12 3 3 8l9 5 9-5z|M3 12.5l9 5 9-5|M3 16.5l9 5 9-5',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z|M12 9.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z',
  wifiOff: 'M3 3l18 18|M8.5 8.8A11 11 0 003 12|M5.5 15a8 8 0 015-2.6|M12 19v.01|M16 10.5A11 11 0 0121 12|M16 14.4a8 8 0 00-1.2-.9',
  smartphone: 'M7 3h10v18H7z|M11 18h2',
  list: 'M9 6h11|M9 12h11|M9 18h11|M4 6h.01|M4 12h.01|M4 18h.01',
  grid: 'M4 4h7v7H4z|M13 4h7v7h-7z|M4 13h7v7H4z|M13 13h7v7h-7z',
  sort: 'M8 5v14|M4 15l4 4 4-4|M16 19V5|M12 9l4-4 4 4',
  swap: 'M4 8h13|M13 4l4 4-4 4|M20 16H7|M11 12l-4 4 4 4',
  zap: 'M13 2 4 14h7l-1 8 9-12h-7z',
  edit: 'M4 20h4L19 9l-4-4L4 16z|M13.5 6.5l4 4',
  key: 'M14.5 9.5a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z|M12 12.5 20 20|M17 17l2-2',
  checkCircle: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M8 12.3l2.8 2.8L16 9.5',
  xCircle: 'M12 3a9 9 0 100 18 9 9 0 000-18z|M9 9l6 6|M15 9l-6 6',
};

export function icon(name, cls) {
  const d = P[name] || P.info;
  const svg = s('svg', {
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: cls || null,
    focusable: 'false',
  }, ...d.split('|').map(p => s('path', { d: p })));
  if (name === 'star') svg.dataset.icon = 'star';
  return svg;
}

// Filled variants
export function iconFill(name, cls) {
  const svg = icon(name, cls);
  svg.setAttribute('fill', 'currentColor');
  return svg;
}

let logoSeq = 0;
// Every logo gets its own gradient id: when two share one and the first sits in a display:none subtree
// (the side rail on phones), Chrome fails to resolve the paint server and the tile renders empty.
export function logo(size = 30) {
  const gid = 'acLogoG' + (++logoSeq);
  return s('svg', { viewBox: '0 0 48 48', width: size, height: size, 'aria-hidden': 'true', class: 'brand-mark' },
    s('defs', null,
      s('linearGradient', { id: gid, x1: '0', y1: '0', x2: '1', y2: '1' },
        s('stop', { offset: '0', 'stop-color': '#7b5cf0' }),
        s('stop', { offset: '1', 'stop-color': '#4326b9' }))),
    s('rect', { width: '48', height: '48', rx: '12', fill: `url(#${gid})` }),
    s('path', { d: 'M13 35 24 11l11 24', fill: 'none', stroke: '#fff', 'stroke-width': '4', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
    s('path', { d: 'M17.5 28.5 23 24.5l3.2 2.6 5.3-5', fill: 'none', stroke: '#c9bcff', 'stroke-width': '2.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
}
