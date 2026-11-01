import { h } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { Live } from '../lib/live.js';
import { linkProps } from '../lib/router.js';
import { chgSpan } from './kit.js';

// The handful of prices people look at first. Each tile opens its instrument; a tile is left out when the data has no such instrument.
const TILES = [['PSX:KSE100', 'KSE-100'], ['FX:USDPKR', 'USD / PKR'], ['FX:XAUUSD', 'Gold'], ['FX:EURUSD', 'EUR / USD'], ['CRYPTO:BTC', 'Bitcoin'], ['CRYPTO:ETH', 'Ethereum']];

function tile(id, label, q) {
  const info = Live.quote(id);
  const [price, chg, ts] = q;
  const note = info.live ? (!info.open ? 'Market closed' : info.delayMin ? `${info.delayMin} min delayed` : 'Live') : ts ? `Last refresh ${F.ago(ts)}` : '';
  return h('a', { class: 'pulse-tile', ...linkProps('/i/' + id), title: info.live ? `${label}: price from the live feed${info.delayMin ? `, ${info.delayMin} minutes behind the exchange` : ''}` : `${label}: price from the last data refresh` },
    h('div', { class: 'pt-l' }, info.live && info.open ? h('span', { class: 'dot live' }) : null, label),
    h('div', { class: 'pt-v mono' }, F.px(price, id.split(':')[0].toLowerCase())),
    h('div', { class: 'pt-c' }, F.isNum(chg) ? chgSpan(chg, 2) : F.NA),
    h('div', { class: 'pt-s' }, note));
}

/** A row of tiles that redraws itself whenever live prices arrive. Returns `{ el, destroy }`. */
export function pulseStrip() {
  const root = h('section', { class: 'pulse', 'aria-label': 'Key prices' });
  const draw = () => {
    const quotes = (D.peek('prices') || {}).quotes || {};
    root.replaceChildren(...TILES.filter(([id]) => quotes[id] && F.isNum(quotes[id][0])).map(([id, label]) => tile(id, label, quotes[id])));
    root.hidden = !root.childElementCount;
  };
  window.addEventListener('acumen:live', draw);
  draw();
  return { el: root, redraw: draw, destroy: () => window.removeEventListener('acumen:live', draw) };
}
