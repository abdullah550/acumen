import { h } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { STALE_DAYS } from '../lib/util.js';
import { explain } from './kit.js';
import { stackBar } from './charts.js';

// How old the latest bar is, and whether that is more than this market should show (weekends and holidays allowed for).
export function freshness(m, asof) {
  const age = F.daysOld(asof);
  return { age, stale: age != null && age > (STALE_DAYS[m] ?? 4) };
}

// Advancers and decliners today, plus how many names sit above their 50-day and 200-day averages.
export function breadthBlock(br) {
  if (!br || !F.isNum(br.adv)) return null;
  return h('div', { class: 'stack tight' },
    h('div', { class: 'label' }, 'Breadth today', explain('How many instruments rose, fell or were unchanged, and how many trade above their 50-day and 200-day averages. Broad participation makes a trend more trustworthy.')),
    stackBar([{ label: 'Up', value: br.adv, color: 'var(--up)' }, { label: 'Flat', value: br.unch, color: 'var(--line-strong)' }, { label: 'Down', value: br.dec, color: 'var(--down)' }]),
    h('div', { class: 'mc-breadth' },
      h('span', null, F.num(br.pct_above_50, 0) + '% above 50-day'), h('span', null, F.num(br.pct_above_200, 0) + '% above 200-day'),
      br.new_highs != null ? h('span', null, `${br.new_highs} new highs · ${br.new_lows} new lows`) : null));
}
