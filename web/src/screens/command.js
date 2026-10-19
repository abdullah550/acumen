import { h } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import { go, linkProps } from '../lib/router.js';
import { HORIZONS, HZ_LABEL, MARKETS, MARKET_LABEL, MARKET_LONG } from '../lib/util.js';
import { card, banner, seg, chip, mkChip, chgSpan, sparkline, stat, explain, empty, skeleton } from '../ui/kit.js';
import { ideaCard } from '../ui/ticket.js';
import { gauge, stackBar, heatTiles, hbars, treemap, divColor, tipRows } from '../ui/charts.js';
import { macroTile } from '../ui/macro.js';
import { breadthBlock } from '../ui/market.js';
import { pageHead, sectionTitle, rankIdeas, errorBlock } from './common.js';

export default async function render() {
  const root = h('div', { class: 'stack loose' });
  const cleanups = [];
  let ov;
  try { ov = await D.get('overview'); } catch (e) { return { el: errorBlock(e) }; }
  D.get('prices').catch(() => null);

  const meta = D.meta;
  root.append(pageHead('Command Center', 'What looks best across Pakistan, forex and crypto right now, with entries, stops, targets and timelines.',
    h('a', { class: 'btn', ...linkProps('/ideas') }, icon('bolt'), 'All trade ideas')));

  // alerts
  const alerts = (ov.alerts || []);
  if (alerts.length) {
    root.append(h('div', { class: 'stack tight' }, ...alerts.slice(0, 4).map(a => banner(a.level === 'warn' ? 'warn' : 'info', a.text))));
  }

  // best ideas
  const ideasHost = h('div', { class: 'grid auto ideas-grid' }, ...Array.from({ length: 3 }, () => skeleton('100%', 230)));
  let hz = 'all';
  const hzSeg = seg([{ value: 'all', label: 'All horizons' }, ...HORIZONS.map(x => ({ value: x, label: HZ_LABEL[x] }))], hz, v => { hz = v; fill(); }, { sm: true });
  const more = h('a', { class: 'btn ghost sm', ...linkProps('/ideas') }, 'See all', icon('right'));
  root.append(h('section', { class: 'stack' }, sectionTitle('Best ideas right now', [hzSeg, more], 'Ranked by evidence quality, grade, conviction and expected result after costs. Tap a card for the full plan.'), ideasHost));
  let allIdeas = null;
  const fill = () => {
    if (!allIdeas) return;
    const pool = allIdeas.filter(i => i.actionable && (hz === 'all' || i.horizon === hz));
    const picked = [];
    const perPool = {};
    for (const i of rankIdeas(pool)) {
      const k = i.market + i.horizon;
      perPool[k] = (perPool[k] || 0) + 1;
      if (perPool[k] > (hz === 'all' ? 2 : 3)) continue;
      picked.push(i);
      if (picked.length >= 6) break;
    }
    const none = !allIdeas.some(i => i.actionable);
    ideasHost.replaceChildren(...(picked.length ? picked.map(ideaCard) : [none
      ? h('div', { class: 'empty' }, icon('shield'), h('div', { class: 't' }, 'Nothing is marked actionable right now'),
        h('div', null, 'No market and horizon has yet shown that its signals beat random entries with the same stops, targets and costs, so Acumen is not recommending trades. Staying out is a legitimate position.'),
        h('div', { class: 'row', style: { justifyContent: 'center', gap: '8px', marginTop: '10px' } },
          h('a', { class: 'btn', ...linkProps('/ideas') }, icon('bolt'), 'Research candidates'), h('a', { class: 'btn ghost', ...linkProps('/evidence') }, icon('shield'), 'See the tests')))
      : empty('No actionable ideas in this view', 'Acumen only shows ideas that pass its checks. Try another horizon.', 'bolt')]));
  };
  D.get('ideas').then(f => { allIdeas = f.ideas; fill(); }).catch(e => ideasHost.replaceChildren(errorBlock(e)));

  // market cards
  root.append(h('section', { class: 'stack' }, sectionTitle('Market regime', null, 'Is the backdrop helping or hurting? Regime feeds the conviction score for every idea in that market.'),
    h('div', { class: 'grid c3' }, ...MARKETS.filter(m => ov.markets[m]).map(m => marketCard(m, ov.markets[m], cleanups)))));

  // movers + pulse
  root.append(h('div', { class: 'grid c2 top' }, moversCard(ov), pulseCard(ov)));

  // macro strip
  const macroHost = h('div', { class: 'macro-strip' }, ...Array.from({ length: 6 }, () => skeleton('100%', 76)));
  root.append(h('section', { class: 'stack' }, sectionTitle('Cross-asset backdrop'), macroHost));
  D.get('macro').then(m => macroHost.replaceChildren(...macroTiles(m, ov))).catch(() => macroHost.replaceChildren(h('div', { class: 'muted' }, 'Macro data unavailable.')));

  // portfolio + track record
  root.append(h('div', { class: 'grid c2 top' }, portfolioCard(ov), trackCard(ov)));

  return { el: root, destroy: () => cleanups.forEach(fn => fn()) };
}

/* ---------- market cards ---------- */
function marketCard(m, d, cleanups) {
  const rg = d.regime || {};
  const head = h('div', { class: 'mc-head' }, h('div', null, h('div', { class: 'label' }, MARKET_LONG[m]), h('div', { class: 'mc-regime' }, rg.label || '—')), h('div', { class: 'mc-asof' }, chip('as of ' + F.date(d.asof, { year: false }), 'outline')));
  const body = [head, gauge(rg.score), rg.note ? h('p', { class: 'mc-note' }, rg.note) : null];
  if (m === 'psx' && d.index) {
    const ix = d.index;
    const recent = (ix.series || []).slice(-160);
    const pts = recent.map(p => p[1]);
    const months = recent.length > 1 ? Math.max(1, Math.round((F.parseDate(recent[recent.length - 1][0]) - F.parseDate(recent[0][0])) / 864e5 / 30.4)) : null;
    body.push(h('div', { class: 'mc-index' },
      h('div', null, h('div', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, ix.name), h('div', { class: 'mc-big' }, F.num(ix.close, 0))),
      h('div', { class: 'mc-chgs' }, [['1D', ix.chg_1d], ['1W', ix.chg_1w], ['1M', ix.chg_1m], ['YTD', ix.chg_ytd]].map(([k, v]) => h('div', null, h('span', null, k), chgSpan(v, 1))))));
    body.push(h('div', { class: 'mc-spark' }, sparkline(pts, { hgt: 72, w: 300 }), h('div', { class: 'mc-spark-cap' }, months ? `Last ${months} months` : '')));
  }
  if (m === 'fx' && rg.dxy) {
    body.push(h('div', { class: 'mc-index' }, h('div', null, h('div', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, 'US dollar index'), h('div', { class: 'mc-big' }, F.num(rg.dxy.close, 2))), h('div', { class: 'mc-chgs' }, h('div', null, h('span', null, '1M'), chgSpan(rg.dxy.chg_1m, 1)))));
    const st = d.strength || [];
    if (st.length) body.push(h('div', { class: 'stack tight' }, h('div', { class: 'label' }, 'Currency strength, 1 week', explain('Average move of each currency against the other seven majors over the past week.')),
      hbars(st.map(x => ({ label: x.ccy, value: x.chg_1w, text: F.pct(x.chg_1w, 2) })), { signed: true, labelW: 40 })));
  }
  if (m === 'crypto' && d.global) {
    const g = d.global;
    body.push(h('div', { class: 'stats' },
      stat('Total market cap', F.money(g.total_mcap, '$', { compactFrom: 1e3 }), F.isNum(g.mcap_chg_24h) ? F.pct(g.mcap_chg_24h, 1) + ' in 24h' : null, { small: true }),
      stat('BTC dominance', F.isNum(g.btc_dominance) ? F.num(g.btc_dominance, 1) + '%' : F.NA, F.isNum(g.eth_dominance) ? 'ETH ' + F.num(g.eth_dominance, 1) + '%' : null, { small: true }),
      F.isNum(g.fear_greed) ? stat('Fear & greed', F.num(g.fear_greed, 0), 'out of 100', { small: true }) : null));
  }
  if (m === 'crypto' && (d.heat || []).length) {
    body.push(h('div', { class: 'stack tight' }, h('div', { class: 'label' }, 'Largest coins'),
      h('table', { class: 'tbl mini' }, h('tbody', null, ...d.heat.slice(0, 5).map(x => h('tr', { class: 'link', onclick: () => go('/i/' + x.id) },
        h('td', null, h('span', { class: 'sym' }, x.sym)), h('td', { class: 'r' }, chgSpan(x.chg_1d, 1), h('span', { class: 'muted' }, ' 24h')), h('td', { class: 'r' }, chgSpan(x.chg_7d, 1), h('span', { class: 'muted' }, ' 7d'))))))));
  }
  const bb = breadthBlock(rg.breadth);
  if (bb) body.push(bb);
  const cnt = d.counts || {};
  body.push(h('div', { class: 'mc-foot' }, h('span', { class: 'muted' }, `${F.int(cnt.analysed)} analysed · ${F.int(cnt.ideas)} ideas`),
    h('div', { class: 'row' }, h('a', { class: 'btn ghost sm', ...linkProps('/markets', { m: m === 'psx' ? null : m }) }, 'Market view'), h('a', { class: 'btn ghost sm', ...linkProps('/ideas', { m }) }, `${MARKET_LABEL[m]} ideas`, icon('right')))));
  return h('section', { class: 'card pad market-card' }, ...body);
}

/* ---------- movers ---------- */
function moversCard(ov) {
  let m = MARKETS.find(x => ov.markets[x] && ov.markets[x].movers) || 'psx';
  let kind = 'gainers';
  const host = h('div', { class: 'tbl-wrap' });
  const draw = () => {
    const list = ((ov.markets[m] || {}).movers || {})[kind] || [];
    host.replaceChildren(list.length ? h('table', { class: 'tbl' },
      h('tbody', null, ...list.map(r => h('tr', { class: 'link', onclick: () => go('/i/' + r.id) },
        h('td', null, h('div', { class: 'sym' }, r.sym), h('div', { class: 'nm' }, r.name)),
        h('td', { class: 'r' }, kind === 'active' ? h('span', { class: 'muted' }, F.compact(r.value)) : F.px(r.px, m)),
        h('td', { class: 'r' }, kind === 'active' ? F.px(r.px, m) : chgSpan(r.chg_1d, 2)))))) : empty('Nothing to show', 'No data for this view.', 'table'));
  };
  const mSeg = seg(MARKETS.filter(x => ov.markets[x]).map(x => ({ value: x, label: MARKET_LABEL[x] })), m, v => { m = v; if (m === 'fx' && kind === 'active') { kind = 'gainers'; kSeg.set(kind); } draw(); }, { sm: true });
  const kSeg = seg([{ value: 'gainers', label: 'Gainers' }, { value: 'losers', label: 'Losers' }, { value: 'active', label: 'Most active' }], kind, v => { kind = v; draw(); }, { sm: true });
  draw();
  return card({ title: 'Movers today', actions: [mSeg], body: h('div', { class: 'stack' }, kSeg, host) });
}

/* ---------- market pulse (sector heat / fx strength / crypto map) ---------- */
function pulseCard(ov) {
  let m = MARKETS.find(x => ov.markets[x]) || 'psx';
  let span = 'chg_1d';
  const host = h('div', { class: 'stack' });
  const draw = () => {
    const d = ov.markets[m] || {};
    host.replaceChildren();
    if (m === 'psx') {
      const secs = (d.sectors || []).slice().sort((a, b) => (b[span] ?? -99) - (a[span] ?? -99));
      const mx = Math.max(...secs.map(x => Math.abs(x[span] ?? 0)), 1);
      host.append(heatTiles(secs.map(x => ({ label: x.name, value: x[span], sub: `${x.n} names · ${F.num(x.pct_above_50, 0)}% >50d`, tip: tipRows(x.name, [['1 day', F.pct(x.chg_1d)], ['1 week', F.pct(x.chg_1w)], ['1 month', F.pct(x.chg_1m)], ['3 months', F.pct(x.chg_3m)], ['Strength vs index', F.pct(x.rs)], ['Names', String(x.n)]]) })), { max: mx, minW: 132, onClick: it => go('/screener', { m: 'psx', sec: it.label }) }));
    } else if (m === 'fx') {
      const st = d.strength || [];
      host.append(hbars(st.map(x => ({ label: x.ccy, value: x[span === 'chg_1d' ? 'chg_1d' : span === 'chg_1w' ? 'chg_1w' : 'chg_1m'] ?? x.chg_1d, text: F.pct(x[span === 'chg_1w' ? 'chg_1w' : span === 'chg_1m' ? 'chg_1m' : 'chg_1d'], 2) })), { signed: true, labelW: 44 }));
      host.append(h('p', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, 'Average move of each currency against the other seven majors over the chosen period.'));
    } else {
      const heat = (d.heat || []).slice(0, 30);
      const key = span === 'chg_1d' ? 'chg_1d' : span === 'chg_1w' ? 'chg_7d' : 'chg_30d';
      const mx = Math.max(...heat.map(x => Math.abs(x[key] ?? 0)), 1);
      host.append(treemap(heat.map(x => ({ label: x.sym, area: Math.sqrt(x.mcap || 1), text: F.pct(x[key], 1), color: divColor(x[key], mx), tip: tipRows(x.name, [['Market cap', F.money(x.mcap, '$')], ['24h', F.pct(x.chg_1d)], ['7d', F.pct(x.chg_7d)], ['30d', F.pct(x.chg_30d)]]), href: () => go('/i/' + x.id) })), { w: 640, hgt: 300 }));
      host.append(h('p', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, 'Tile area is proportional to the square root of market cap so smaller coins stay readable.'));
    }
  };
  const mSeg = seg(MARKETS.filter(x => ov.markets[x]).map(x => ({ value: x, label: MARKET_LABEL[x] })), m, v => { m = v; draw(); }, { sm: true });
  const sSeg = seg([{ value: 'chg_1d', label: '1D' }, { value: 'chg_1w', label: '1W' }, { value: 'chg_1m', label: '1M' }], span, v => { span = v; draw(); }, { sm: true });
  draw();
  return card({ title: 'Market pulse', actions: [mSeg], body: h('div', { class: 'stack' }, sSeg, host) });
}

/* ---------- macro ---------- */
function macroTiles(mac, ov) {
  const order = ['USDPKR', 'DXY', 'GOLD', 'BRENT', 'SPX', 'VIX', 'US10Y'];
  const tiles = order.filter(k => mac.series && mac.series[k]).map(k => macroTile(k, mac.series[k]));
  const pk = mac.pakistan;
  if (pk) tiles.push(h('div', { class: 'mtile' }, h('div', { class: 'label' }, 'Pakistan policy rate'), h('div', { class: 'mt-v' }, F.num(pk.policy_rate, 2) + '%'), h('div', { class: 'mt-c muted' }, F.isNum(pk.cpi_yoy) ? `CPI ${F.num(pk.cpi_yoy, 1)}% · real ${F.num(pk.real_rate, 1)}%` : ''), h('div', { class: 'muted', style: { fontSize: 'var(--fs-2xs)' } }, 'as of ' + F.date(pk.asof, { year: true }))));
  void ov;
  return tiles;
}

/* ---------- portfolio + track ---------- */
function portfolioCard(ov) {
  const p = ov.portfolio;
  if (!p) return card({ title: 'Model portfolio', body: empty('No model portfolio', 'Not enough qualified ideas.', 'brief') });
  const colors = { psx: 'var(--mk-psx)', fx: 'var(--mk-fx)', crypto: 'var(--mk-crypto)' };
  return card({
    title: 'Model portfolio snapshot', actions: [h('a', { class: 'btn ghost sm', ...linkProps('/portfolio') }, 'Open', icon('right'))],
    body: h('div', { class: 'stack' },
      h('div', { class: 'stats' },
        stat('Positions', String(p.n), `${p.profile} profile`, { small: true }),
        stat('Money at risk', F.pct(p.heat_pct, 1, { sign: false }), 'if every stop is hit', { small: true }),
        stat('Expected result', F.r(p.exp_r_total, 1), 'sum over positions', { small: true })),
      h('div', { class: 'label' }, 'Weight by market'),
      stackBar(Object.entries(p.by_market || {}).map(([k, v]) => ({ label: MARKET_LABEL[k] || k, value: v, color: colors[k], text: F.num(v, 0) + '%' }))),
      h('p', { class: 'muted', style: { fontSize: 'var(--fs-xs)' } }, 'Built from the best qualified ideas with limits on risk per trade, total risk, position size and correlation.')),
  });
}

function trackCard(ov) {
  const t = ov.track_record || {}, c = ov.calibration || {};
  return card({
    title: 'Honest scorecard', info: 'Acumen records every idea it issues and what happens to it. This is the live record, which counts for more than any backtest. The pooled backtest tells you how rules like these behaved historically.',
    actions: [h('a', { class: 'btn ghost sm', ...linkProps('/evidence') }, 'Evidence', icon('right'))],
    body: h('div', { class: 'stack' },
      h('div', { class: 'stats' },
        stat('Ideas open', F.int(t.open), `since ${F.date(t.since, { year: true })}`, { small: true }),
        stat('Closed', F.int(t.closed), null, { small: true }),
        stat('Win rate', F.isNum(t.win) ? F.frac(t.win) : F.NA, F.isNum(t.avg_r) ? 'avg ' + F.r(t.avg_r) : 'not enough closed yet', { small: true })),
      t.closed ? null : h('p', { class: 'ink-2', style: { fontSize: 'var(--fs-sm)' } }, 'No ideas have resolved yet, so there is no live track record to show. Treat every number in the app as a model estimate until this fills in.'),
      c.note ? banner(c.calibrated ? 'info' : 'warn', c.note) : null),
  });
}
