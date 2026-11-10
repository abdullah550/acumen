import { h, append, replace } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import * as Z from '../lib/sizing.js';
import { linkProps } from '../lib/router.js';
import { icon } from '../lib/icons.js';
import { card, banner, chip, stat, empty, chgSpan } from '../ui/kit.js';
import { pageHead, sectionTitle, errorBlock } from './common.js';
import * as B from '../lib/bargains.js';

/* Bargains answers one question: which Pakistani shares cost far less than a deliberately conservative estimate of what they are worth?
   Fair value is the middle of three low yardsticks, a share is listed only when it also clears the checks that keep value traps out,
   and what looked cheap but failed a check is shown with the reason. Nothing here is back-tested, and the page says so. */

const SUB = 'Pakistani shares priced far below a deliberately conservative estimate of what they are worth. “A dollar for fifty cents” means a price of half fair value or less.';
const pkr = x => F.px(x, 'psx');
const plural = (n, one, many) => `${F.int(n)} ${n === 1 ? one : many}`;

// "50¢ or less", "51–65¢": a tier heading in a few characters, from the file's own tier list
function tierShort(tiers, i) {
  return i === 0 ? `${Math.round(tiers[0].max_cents)}¢ or less` : `${Math.round(tiers[i - 1].max_cents) + 1}–${Math.round(tiers[i].max_cents)}¢`;
}

export default async function render() {
  if (!(D.meta && D.meta.files && D.meta.files.bargains)) {
    return { el: h('div', { class: 'stack loose' }, pageHead('Bargains', SUB), empty('Not in this data yet', 'The next data refresh adds the Bargains list. Reload the app once it has run.', 'info')) };
  }
  let body;
  try { body = await D.get('bargains'); } catch (e) { return { el: errorBlock(e) }; }
  const root = h('div', { class: 'stack loose' });
  append(root, [
    pageHead('Bargains', SUB, asofChip(body)),
    h('div', { class: 'stack' }, verdict(body), summaryStats(body), h('p', { class: 'muted fs-sm bg-note' }, 'A screen that tells you where to look, not a recommendation: it is not back-tested, and a low price can stay low. Read the company’s latest accounts and announcements before acting.')),
    ...tierSections(body),
    recordSection(body),
    excludedSection(body),
    methodSection(body),
  ]);
  return { el: root };
}

function asofChip(body) {
  if (!body.asof) return null;
  const old = F.isNum(body.stale_days) && body.stale_days > 3;
  return chip(old ? `Prices are ${body.stale_days} days old` : `Prices of ${F.date(body.asof, { year: false })}`, old ? 'warn' : 'outline', { icon: 'clock', title: 'Every discount on this page is measured against this price date' });
}

/* ======================================================================= what the page says first */
function verdict(body) {
  const v = B.headline(body);
  const picks = body.picks || [];
  const topCents = v.top ? Math.round(v.top.max_cents) : 50;
  const nearest = picks.length ? (body.tiers || []).find(t => t.min_mos === picks[0].tier) : null;
  const more = v.excluded ? ` ${plural(v.excluded, 'share looks', 'shares look')} cheap on paper but ${v.excluded === 1 ? 'fails' : 'fail'} a check: ${v.excluded === 1 ? 'it is' : 'they are'} listed further down with the reason.` : '';
  if (v.kind === 'none') {
    return banner('info', h('b', null, 'Nothing passes every check right now. '), 'A share is listed only when it is priced well below a conservative fair value and also clears the checks that keep value traps out.' + more);
  }
  if (v.kind === 'short') {
    return banner('info', h('b', null, `Nothing is priced at ${topCents} cents on the dollar or less today. `), `The closest ${picks.length === 1 ? 'is' : 'are'} listed below, at ${nearest ? Math.round(nearest.max_cents) : '…'} cents or less.` + more);
  }
  return banner('info', h('b', null, `${plural(v.reached, 'share is', 'shares are')} priced at ${topCents} cents on the dollar or less. `), (picks.length > v.reached ? `${plural(picks.length - v.reached, 'more is', 'more are')} listed at a smaller discount.` : '') + more);
}

function summaryStats(body) {
  const c = body.counts || {};
  const tiers = body.tiers || [];
  return h('div', { class: 'stats bg-stats' },
    stat('Shares valued', F.int(c.valued), `of ${F.int(c.screened)} screened`, { small: true }),
    ...tiers.map((t, i) => stat(tierShort(tiers, i), F.int((c.by_tier || {})[String(Math.round(t.min_mos))] || 0), t.name, { small: true })),
    stat('Cheap, not listed', F.int(c.excluded), 'fail a check', { small: true }));
}

/* ======================================================================= the list */
function tierSections(body) {
  const method = body.method || {};
  return B.groupByTier(body.picks, body.tiers).map(({ tier, rows }) => h('section', { class: 'stack' },
    sectionTitle(tier.name, chip(rows.length ? plural(rows.length, 'share', 'shares') : 'None today', rows.length ? 'up' : 'outline'), tier.label),
    rows.length ? h('div', { class: 'grid auto ideas-grid' }, ...rows.map(r => bargainCard(r, method))) : h('p', { class: 'muted fs-sm' }, 'No share reaches this level and passes every check today.')));
}

const idBlock = r => h('div', { class: 'ic-id' }, h('a', { class: 'ic-sym', ...linkProps('/i/' + r.id) }, r.sym), h('div', { class: 'ic-name' }, r.name));
const cell = (label, value, sub) => h('div', { class: 'bg-cell' }, h('div', { class: 'ic-l' }, label), h('div', { class: 'ic-v' }, value), sub ? h('div', { class: 'ic-s' }, sub) : null);
const metric = (label, value) => h('div', { class: 'ic-metric' }, label, h('b', null, value));
const centsBadge = (r, cls, unit = 'on the dollar') => h('div', { class: ['bg-cents', cls], title: `${B.centsText(r.cents)}: the price is ${F.num(r.mos_pct, 0)}% below fair value` }, h('span', { class: 'n' }, `${r.cents}¢`), h('span', { class: 'u' }, unit));

function bargainCard(p, method) {
  const wait = Array.isArray(p.horizon_months) ? `${p.horizon_months[0]}–${p.horizon_months[1]} months` : p.horizon;
  return h('article', { class: 'bg-card' },
    h('div', { class: 'ic-top' }, idBlock(p), centsBadge(p)),
    h('div', { class: 'ic-tags' }, p.sector ? chip(p.sector, 'outline') : null, F.isNum(p.mcap) ? chip(`PKR ${F.compact(p.mcap)} market value`, 'outline') : null),
    h('div', { class: 'bg-prices' },
      cell('Price', pkr(p.px), 'PKR'),
      cell('Fair value', pkr(p.fair), 'PKR'),
      cell('To fair value', chgSpan(p.upside_pct, 0), 'gain if reached')),
    gaugeEl(p),
    h('div', { class: 'ic-grid' },
      cell('Target 1', pkr(p.target1), 'halfway'),
      cell('Target 2', pkr(p.target2), 'fair value'),
      cell('Re-read below', pkr(p.review_below), `${F.num(method.review_drop_pct, 0)}% under the price`),
      cell('Typical wait', wait, 'not a forecast')),
    h('p', { class: 'bg-why' }, p.why),
    p.risks && p.risks.length ? h('ul', { class: 'bg-risks' }, ...p.risks.map(r => h('li', null, r))) : null,
    h('div', { class: 'ic-foot' },
      metric('P/E', F.isNum(p.pe) ? F.num(p.pe, 1) + (F.isNum(p.pe_peer) ? ` (peers ${F.num(p.pe_peer, 1)})` : '') : F.NA),
      metric('P/B', F.num(p.pb, 2)),
      metric(p.roe_derived ? 'Profit / book' : 'ROE', F.isNum(p.roe) ? `${F.num(p.roe, 0)}%` : F.NA),
      metric('Yield', F.isNum(p.dy) && p.dy > 0 ? `${F.num(p.dy, 1)}%` : 'none'),
      metric('Trades a day', `PKR ${F.compact(p.adv)}`),
      metric('3 months', chgSpan(p.chg3m, 0))),
    h('p', { class: 'muted fs-xs' }, capText(p, method)),
    howBuilt(p),
    h('div', { class: 'row bg-actions' }, h('a', { class: 'btn sm', ...linkProps('/i/' + p.id) }, 'Open instrument', icon('right'))));
}

function capText(p, method) {
  const pct = method.max_position_pct;
  const acct = Z.ccyAcct('PKR');
  const cap = B.positionCap(acct ? Z.equity() / acct : null, pct, p.px);
  const base = `At most ${F.num(pct, 0)}% of your account in any one of these, because the model can be wrong`;
  return cap ? `${base}: PKR ${F.int(cap.amount)}${cap.shares ? `, about ${F.int(cap.shares)} shares` : ''}.` : `${base}.`;
}

// price, the three yardsticks and fair value on one line; the shaded stretch is the gap the price has to close
function gaugeEl(p) {
  const g = B.gauge(p);
  if (!g) return null;
  return h('div', { class: 'bg-gauge', role: 'img', 'aria-label': `Price ${pkr(p.px)} against a fair value of ${pkr(p.fair)}` },
    h('div', { class: 'bg-track' },
      h('div', { class: 'bg-gap', style: { left: g.from + '%', width: (g.to - g.from) + '%' } }),
      ...g.models.map(m => h('span', { class: 'bg-model', style: { left: m.at + '%' }, title: `${m.name}: PKR ${pkr(m.value)}` })),
      h('span', { class: 'bg-fair', style: { left: g.fair + '%' }, title: `Fair value: PKR ${pkr(p.fair)}` }),
      h('span', { class: 'bg-px', style: { left: g.px + '%' }, title: `Price: PKR ${pkr(p.px)}` })),
    h('div', { class: 'bg-legend' }, h('span', { class: 'k px' }, 'Price'), h('span', { class: 'k model' }, 'Three yardsticks'), h('span', { class: 'k fair' }, 'Fair value')));
}

function howBuilt(p) {
  return h('details', { class: 'bg-how' },
    h('summary', null, 'How this fair value was built'),
    h('div', { class: 'stack tight' },
      ...(p.models || []).map(m => h('div', { class: 'bg-model-row' }, h('div', null, h('b', null, m.name), h('div', { class: 'muted fs-xs' }, m.note)), h('div', { class: 'mono' }, `PKR ${pkr(m.value)}`))),
      h('p', { class: 'muted fs-xs' }, 'Fair value is the middle of the three, never their average, so one generous method cannot lift it.')));
}

/* ======================================================================= the live record */
const TH = (text, cls) => h('th', { class: cls, scope: 'col' }, text);
const dash = () => h('span', { class: 'muted' }, F.NA);
const retCell = (x, dp = 1) => (F.isNum(x) ? chgSpan(x, dp) : dash());
const kv = (k, v) => h('span', null, h('span', { class: 'muted' }, k + ' '), v);
const sinceText = r => (r.days ? plural(r.days, 'day', 'days') : 'Just listed');

function recordSection(body) {
  const rec = body.record;
  if (!rec) return null;
  const v = B.recordHeadline(rec);
  const state = v.kind === 'none' ? chip('Not started', 'outline')
    : v.kind === 'early' ? chip('Too early to read', 'warn', { icon: 'clock' })
      : chip(`Read at ${B.ageLabel(v.best.days)}`, 'accent');
  return h('section', { class: 'stack bg-rec' },
    sectionTitle('Live record', state, 'Every share is logged the day it first passes every check and followed from then on, against the KSE-100 and the typical share. The screen cannot be back-tested, so this record is the evidence of whether cheap shares close their gap.'),
    recordBanner(v, rec),
    v.kind === 'none' ? null : horizonCard(rec),
    v.kind === 'none' ? null : followedCard(rec),
    h('details', { class: 'bg-how' },
      h('summary', null, 'How to read this record'),
      h('ul', { class: 'bg-list' }, ...(rec.caveats || []).map(c => h('li', null, c)))));
}

function recordBanner(v, rec) {
  if (v.kind === 'none') return banner('info', h('b', null, 'Nothing is being followed yet. '), 'The record starts the day the first share passes every check.');
  const lead = v.oldest > 0
    ? `${plural(v.tracked, 'share is', 'shares are')} followed since ${F.date(v.since)}, the oldest for ${plural(v.oldest, 'day', 'days')}. `
    : `${plural(v.tracked, 'share was', 'shares were')} logged on ${F.date(v.since)}; none has had time to show anything yet. `;
  if (v.kind === 'early') {
    return banner('info', h('b', null, 'Too early to say whether cheap shares close their gap. '),
      lead + `A result is read only once ${F.int(v.min)} shares have been followed for the age in question (${(rec.horizons || []).map(x => B.ageLabel(x.days)).join(', ')}), so nothing below is a result yet.`);
  }
  const b = v.best;
  return banner('info', h('b', null, `After ${B.ageLabel(b.days)}, ${plural(b.n, 'share', 'shares')} returned ${F.chgText(b.ret_mean, 1)} on average. `),
    `Over the same days the typical share returned ${F.chgText(b.typical_mean, 1)} and the KSE-100 ${F.chgText(b.index_mean, 1)}; ${B.ofText(b.ahead_typical, b.ahead_typical_of) || 'none'} beat the typical share. Shares listed in the same weeks share the same market moves, so read this loosely.`);
}

// what the shares that are old enough did in their first 3 months, 6 months and year, against the two yardsticks
function horizonCard(rec) {
  const hz = (rec.horizons || []).filter(Boolean);
  const tag = x => (!x.n ? chip('None old enough yet', 'outline') : !x.ready ? chip(`Too few: ${x.n} of ${F.int(rec.min_shares)}`, 'outline', { title: `An age is read only once ${F.int(rec.min_shares)} shares have been followed that long` }) : null);
  const of = (k, n) => B.ofText(k, n) || F.NA;
  const reached = x => (x.n ? `${x.reached_t2} of ${x.n}` : F.NA);
  return card({
    title: 'Results at fixed ages', flush: true,
    info: 'Each share is read when it is 3 months, 6 months and a year past its listing day, so shares listed at different times can be compared. “Typical share” is the median return of every liquid Pakistani share over the same days; the KSE-100 leaves dividends out, which flatters the shares.',
    body: h('div', null,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl' },
        h('thead', null, h('tr', null, TH('After'), TH('Shares', 'r'), TH('Cheap shares', 'r'), TH('Typical share', 'r'), TH('KSE-100', 'r'), TH('Ahead of typical share', 'r'), TH('Ahead of KSE-100', 'r'), TH('Reached fair value', 'r'))),
        h('tbody', null, ...hz.map(x => h('tr', { class: x.ready ? null : 'bg-rec-dim' },
          h('td', null, h('div', { class: 'row' }, h('b', null, B.ageLabel(x.days)), tag(x))),
          h('td', { class: 'r' }, F.int(x.n)), h('td', { class: 'r' }, retCell(x.ret_mean)), h('td', { class: 'r' }, retCell(x.typical_mean)), h('td', { class: 'r' }, retCell(x.index_mean)),
          h('td', { class: 'r' }, of(x.ahead_typical, x.ahead_typical_of)), h('td', { class: 'r' }, of(x.ahead_index, x.ahead_index_of)), h('td', { class: 'r' }, reached(x))))))),
      h('div', { class: 'ev-cards' }, ...hz.map(x => h('div', { class: ['ev-crow', !x.ready && 'bg-rec-dim'] },
        h('div', { class: 'ev-crow-top' }, h('div', { class: 'ev-crow-id' }, h('b', null, `After ${B.ageLabel(x.days)}`), tag(x)), h('div', { class: 'ev-crow-r' }, plural(x.n, 'share', 'shares'))),
        h('div', { class: 'ev-crow-line' }, kv('Cheap shares', retCell(x.ret_mean)), kv('Typical share', retCell(x.typical_mean)), kv('KSE-100', retCell(x.index_mean))),
        h('div', { class: 'ev-crow-line' }, kv('Ahead of typical share', of(x.ahead_typical, x.ahead_typical_of)), kv('Ahead of KSE-100', of(x.ahead_index, x.ahead_index_of))),
        h('div', { class: 'ev-crow-line' }, kv('Reached fair value', reached(x))))))),
  });
}

// every share ever logged, newest first: where it was listed, where it is, and what the two yardsticks did meanwhile
function followedCard(rec) {
  const rows = rec.rows || [];
  const LIMIT = 15;
  const st = { all: false };
  const host = h('div');
  const status = r => { const s = B.recordStatus(r); return chip(s.label, s.tone, s.key === 'review' ? { icon: 'alert' } : undefined); };
  const way = r => (F.isNum(r.gap_closed_pct) ? `${F.num(r.gap_closed_pct, 0)}%` : F.NA);
  const move = r => (F.isNum(r.px) && F.isNum(r.last) ? `${pkr(r.px)} → ${pkr(r.last)}` : F.NA);
  function draw() {
    const part = st.all ? rows : rows.slice(0, LIMIT);
    replace(host,
      h('div', { class: 'tbl-wrap ev-wide' }, h('table', { class: 'tbl fixed-first' },
        h('thead', null, h('tr', null, TH('Share'), TH('Listed'), TH('Price then → now', 'r'), TH('Return', 'r'), TH('Typical share', 'r'), TH('KSE-100', 'r'), TH('Way to fair value', 'r'), TH('Status'))),
        h('tbody', null, ...part.map(r => h('tr', null,
          h('td', null, h('div', { class: 'scr-id' }, h('div', { class: 'scr-id-t' }, h('a', { class: 'sym', ...linkProps('/i/' + r.id) }, r.sym)), h('div', { class: 'nm', title: r.name }, r.name))),
          h('td', null, F.date(r.listed), h('div', { class: 'muted fs-xs' }, sinceText(r))),
          h('td', { class: 'r' }, move(r)), h('td', { class: 'r' }, retCell(r.ret_pct)), h('td', { class: 'r' }, retCell(r.typical_pct)), h('td', { class: 'r' }, retCell(r.index_pct)),
          h('td', { class: 'r' }, way(r)), h('td', null, status(r))))))),
      h('div', { class: 'ev-cards' }, ...part.map(r => h('div', { class: 'ev-crow' },
        h('div', { class: 'ev-crow-top' },
          h('div', { class: 'ev-crow-id' }, h('a', { class: 'sym', ...linkProps('/i/' + r.id) }, r.sym), h('div', { class: 'ev-crow-sub', title: r.name }, r.name)),
          h('div', { class: 'ev-crow-r' }, h('div', { class: 'stack tight ev-end' }, retCell(r.ret_pct), status(r)))),
        h('div', { class: 'ev-crow-line' }, kv('Listed', F.date(r.listed)), kv('', sinceText(r))),
        h('div', { class: 'ev-crow-line' }, kv('Price', move(r)), kv('Way to fair value', way(r))),
        h('div', { class: 'ev-crow-line' }, kv('Typical share', retCell(r.typical_pct)), kv('KSE-100', retCell(r.index_pct)))))),
      rows.length > LIMIT ? h('div', { class: 'ev-more' }, h('button', { class: 'btn sm', type: 'button', onclick: () => { st.all = !st.all; draw(); } }, st.all ? 'Show fewer' : `Show all ${F.int(rows.length)}`)) : null);
  }
  draw();
  return card({
    title: 'Shares followed', flush: true,
    info: '“Return” is the change in price since the listing day, adjusted for bonus issues, splits and dividends. “Way to fair value” is how much of the gap between the listing price and fair value has closed: 100% means fair value was reached. The two yardsticks cover the same days.',
    body: host,
  });
}

/* ======================================================================= cheap on paper, not listed */
function excludedSection(body) {
  const rows = body.excluded || [];
  if (!rows.length) return null;
  const floor = body.method && body.method.watch_min_mos_pct;
  return h('section', { class: 'stack' },
    sectionTitle('Cheap on paper, but not listed', chip(plural(rows.length, 'share', 'shares'), 'warn'),
      `${F.isNum(floor) ? `At least ${F.num(floor, 0)}% below fair value by the numbers, ` : 'Cheap by the numbers, '}but each fails a check that keeps value traps out. The ones failing a single check come first. Read these as warnings, not leads.`),
    h('div', { class: 'grid auto ideas-grid' }, ...B.nearMisses(rows).map(excludedCard)));
}

function excludedCard(e) {
  return h('article', { class: 'bg-card dim' },
    h('div', { class: 'ic-top' }, idBlock(e), centsBadge(e, 'muted', 'on paper')),
    h('div', { class: 'ic-tags' }, e.sector ? chip(e.sector, 'outline') : null),
    h('div', { class: 'bg-prices two' }, cell('Price', pkr(e.px), 'PKR'), cell('Fair value on paper', pkr(e.fair), 'PKR')),
    h('div', { class: 'stack tight' },
      h('div', { class: 'ic-l' }, 'Why it is not listed'),
      h('ul', { class: 'bg-risks' }, ...(e.reasons || []).map(r => h('li', null, r)))));
}

/* ======================================================================= how it works */
function methodSection(body) {
  const m = body.method || {};
  const ch = m.checks || {};
  const bn = x => `PKR ${F.compact(x)}`;
  const checks = [
    `Market value at least ${bn(ch.min_mcap)}, trading at least ${bn(ch.min_adv)} a day, and at least ${F.num(ch.min_free_float_pct, 0)}% of the shares free to trade`,
    `A P/E of at least ${F.num(ch.min_pe, 1)}, a return on equity under ${F.num(ch.roe_outlier_pct, 0)}% and profit up by less than ${F.num(ch.max_eps_jump_pct, 0)}% in a year: anything more extreme is usually a one-off gain or a data error`,
    `The price has not fallen more than ${F.num(ch.max_drop_1m_pct, 0)}% in a month or ${F.num(ch.max_drop_3m_pct, 0)}% in three months: the reported profit may not show the reason yet`,
    `Debt at most ${F.num(ch.max_debt_to_equity, 1)} times equity (not applied to banks and insurers)`,
    'The price agrees with the reported ratios and with the last stored close, and the share traded in the last week; otherwise it is a split, a bonus issue or stale figures',
    'A profit history exists, so there is a way to tell whether this year is typical',
    'All three yardsticks put the value above the price',
  ];
  const scope = body.scope || {};
  return card({
    title: 'How the fair value is worked out',
    body: h('div', { class: 'stack loose' },
      h('div', { class: 'grid c2 top' },
        h('div', { class: 'stack' },
          h('div', { class: 'ic-l' }, 'Three low yardsticks'),
          h('ul', { class: 'bg-list' }, ...(m.models || []).map(x => h('li', null, h('b', null, x.name), ' ' + x.text))),
          h('p', { class: 'muted fs-sm' }, `Required return ${F.num(m.required_return_pct, 1)}% (the risk-free rate of ${F.num(m.risk_free_pct, 1)}% plus the full equity premium of ${F.num(m.equity_premium_pct, 1)}%, with a beta of at least one). Growth is assumed to be at most ${F.num(m.growth_pct, 1)}%, below inflation. Profit is normalised: after a rise the average of this year and last is used, after a rebound of 100% or more only half of it.`)),
        h('div', { class: 'stack' },
          h('div', { class: 'ic-l' }, 'Checks that keep value traps out'),
          h('ul', { class: 'bg-list' }, ...checks.map(c => h('li', null, c))))),
      h('div', { class: 'stack' },
        h('div', { class: 'ic-l' }, 'What this is not'),
        h('ul', { class: 'bg-list' }, ...(body.caveats || []).map(c => h('li', null, c)), ...(body.notes || []).map(c => h('li', null, c)))),
      scope.fx || scope.crypto ? h('div', { class: 'stack' },
        h('div', { class: 'ic-l' }, 'Forex and crypto'),
        h('ul', { class: 'bg-list' }, scope.fx ? h('li', null, scope.fx) : null, scope.crypto ? h('li', null, scope.crypto) : null)) : null),
  });
}
