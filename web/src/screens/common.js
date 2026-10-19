import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { skeleton, banner } from '../ui/kit.js';
import { GRADE_ORDER } from '../lib/util.js';

export function pageHead(title, sub, actions) {
  return h('div', { class: 'page-head' },
    h('div', null, h('h1', { class: 'h-page' }, title), sub ? h('p', { class: 'sub' }, sub) : null),
    actions ? h('div', { class: 'row wrap' }, actions) : null);
}

export function loadingBlock(rows = 3) {
  return h('div', { class: 'stack' }, ...Array.from({ length: rows }, () => skeleton('100%', 84)));
}

export function errorBlock(err, retry) {
  return h('div', { class: 'stack' },
    banner('bad', h('b', null, 'Could not load this section. '), String((err && err.message) || err)),
    retry ? h('div', null, h('button', { class: 'btn', type: 'button', onclick: retry }, icon('refresh'), 'Try again')) : null);
}

export function sectionTitle(title, right, sub) {
  return h('div', { class: 'sec-head' },
    h('div', null, h('h2', { class: 'h-sec' }, title), sub ? h('p', { class: 'sub' }, sub) : null),
    right ? h('div', { class: 'row' }, right) : null);
}

// Ranks ideas for "best right now": actionable first, validated pools first, then grade, conviction, expected result.
export function rankIdeas(list) {
  return [...list].sort((a, b) =>
    (b.actionable - a.actionable) ||
    ((b.calibrated ? 1 : 0) - (a.calibrated ? 1 : 0)) ||
    ((GRADE_ORDER[b.grade] || 0) - (GRADE_ORDER[a.grade] || 0)) ||
    (b.conviction - a.conviction) ||
    ((b.exp_r ?? -9) - (a.exp_r ?? -9)));
}

export function ideaKey(i) { return i.id + '|' + i.horizon; }

export async function ideasIndex(D) {
  if (D._ideaIdx) return D._ideaIdx;
  const f = await D.get('ideas');
  const m = new Map();
  for (const i of f.ideas) m.set(ideaKey(i), i);
  D._ideaIdx = m;
  return m;
}
