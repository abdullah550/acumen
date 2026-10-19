import { h, s } from '../lib/dom.js';
import * as F from '../lib/fmt.js';

export const arrowGlyph = up => s('svg', { viewBox: '0 0 10 10', width: 9, height: 9, 'aria-hidden': 'true' }, s('path', { d: up ? 'M5 1.2 9 8H1z' : 'M5 8.8 1 2h8z', fill: 'currentColor' }));

// One horizon's signal for an instrument row. A solid badge is an actionable idea, an outlined one is reference only,
// a faded one is a weak signal with no plan. Direction is always the arrow, never colour alone.
export function signalBadge(row, hz, ideas) {
  const idea = ideas && ideas.get(row.id + '|' + hz);
  const sg = row.sig && row.sig[hz];
  if (idea) {
    const up = idea.dir === 'long';
    return h('span', { class: ['sg', up ? 'up' : 'down', idea.actionable ? 'act' : 'ref'], title: `${idea.action} · grade ${idea.grade} · conviction ${idea.conviction}${idea.actionable ? '' : ' · reference only'}` }, arrowGlyph(up), idea.grade, ' ', idea.conviction);
  }
  if (sg && sg.dir) return h('span', { class: ['sg', 'weak', sg.dir === 'long' ? 'up' : 'down'], title: `Leans ${sg.dir}, conviction ${sg.conv}: too weak for a trade plan` }, arrowGlyph(sg.dir === 'long'), ' ', sg.conv);
  return h('span', { class: 'muted' }, F.NA);
}
