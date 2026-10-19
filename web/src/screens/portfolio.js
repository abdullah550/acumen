import { h, clear, replace } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import * as F from '../lib/fmt.js';
import { D } from '../lib/data.js';
import { S } from '../lib/store.js';
import * as Z from '../lib/sizing.js';
import * as J from '../lib/journal.js';
import { go, linkProps, replaceQuery } from '../lib/router.js';
import { HZ_LABEL, MARKET_LABEL } from '../lib/util.js';
import { tabs, banner, gradeBadge, stat, empty, toast, explain, chgSpan } from '../ui/kit.js';
import { hbars, stackBar, heatMatrix } from '../ui/charts.js';
import { accountControls, PROFILE_LABEL } from '../ui/account.js';
import { journalView } from '../ui/positions.js';
import { openLogTrade } from '../ui/ticket.js';
import { signalBadge } from '../ui/signals.js';
import { pageHead, errorBlock } from './common.js';

const unitStep = m => (m === 'psx' ? 1 : m === 'fx' ? 1000 : 0);
const MK_COLOR = { psx: 'var(--mk-psx)', fx: 'var(--mk-fx)', crypto: 'var(--mk-crypto)' };
const HZ_COLOR = { short: 'color-mix(in srgb, var(--accent) 38%, var(--surface))', medium: 'color-mix(in srgb, var(--accent) 68%, var(--surface))', long: 'var(--accent)' };

// Positive correlation (the positions move together, so less diversification) is amber, negative is accent.
const corrColor = v => (F.isNum(v) ? `color-mix(in srgb, ${v >= 0 ? 'var(--warning)' : 'var(--accent)'} ${Math.round(6 + Math.min(1, Math.abs(v)) * 52)}%, var(--mid))` : 'var(--surface-2)');

/* ---------- the model portfolio, rescaled to this person's account ---------- */
function scaleModel(prof, eq, k) {
  const acct = Z.acctCcy();
  const positions = (prof.positions || []).map(p => {
    const qc = Z.quoteCcy({ market: p.market, sym: p.sym });
    const known = Z.ccyAcct(qc);
    const rate = known ?? 1;
    const riskPct = p.risk_pct * k;
    const weightPct = p.weight_pct * k;
    const step = unitStep(p.market);
    let units = (eq * weightPct / 100) / (p.entry * rate);
    units = step > 0 ? Math.floor(units / step) * step : Number(units.toPrecision(4));
    const value = units * p.entry * rate;
    const maxLoss = units * Math.abs(p.entry - p.stop) * rate;
    return { ...p, qc, rate, rateKnown: known != null, riskPct, weightPct, units, value, maxLoss, effRisk: eq > 0 ? maxLoss / eq * 100 : null, tooSmall: units <= 0 };
  });
  const st = prof.stats || {};
  const sc = v => (F.isNum(v) ? v * k : null);
  return {
    acct, positions,
    stats: { ...st, heat_pct: sc(st.heat_pct), expected_return_pct: sc(st.expected_return_pct), var95_pct: sc(st.var95_pct), cvar95_pct: sc(st.cvar95_pct), worst_case_pct: sc(st.worst_case_pct) },
    stress: (prof.stress || []).map(x => ({ ...x, pnl_pct: sc(x.pnl_pct) })),
  };
}

function heatBar(used, cap) {
  const top = Math.max(cap * 1.25, used * 1.05, 0.0001);
  const over = used > cap + 1e-9;
  return h('div', { class: 'heatbar', role: 'img', 'aria-label': `${F.num(used, 2)} percent of the account at risk, budget ${F.num(cap, 1)} percent` },
    h('div', { class: 'hb2-track' },
      h('div', { class: ['hb2-fill', over && 'over'], style: { width: Math.min(100, used / top * 100) + '%' } }),
      h('div', { class: 'hb2-cap', style: { left: cap / top * 100 + '%' } }, h('span', null, `Budget ${F.num(cap, 1)}%`))));
}

export default async function render(route) {
  let pf;
  try { pf = await D.get('portfolio'); } catch (e) { return { el: errorBlock(e) }; }
  const ideasFile = await D.get('ideas').catch(() => null);
  const ins = await D.get('instruments').catch(() => null);
  await D.get('prices').catch(() => null);
  const ideaMap = new Map(((ideasFile && ideasFile.ideas) || []).map(i => [i.id + '|' + i.horizon, i]));
  const rowsById = new Map(((ins && ins.rows) || []).map(r => [r.id, r]));
  const q = route.query || {};

  const openCount = () => J.list().filter(t => t.status === 'open').length;
  const watchCount = () => (S.get('watchlist') || []).length;
  let cur = ['model', 'mine', 'watch'].includes(q.t) ? q.t : (openCount() ? 'mine' : 'model');
  const defaultTab = () => (openCount() ? 'mine' : 'model');

  const root = h('div', { class: 'stack' });
  const host = h('div');
  const tabsHost = h('div');

  const sumText = h('span', { class: 'acct-sum' });
  const drawSum = () => {
    const custom = F.isNum(S.get('equity')) && S.get('equity') > 0;
    replace(sumText, h('b', null, F.money(Z.equity(), Z.acctCcy(), { compactFrom: 1e9, d: 0 })), custom ? null : h('span', { class: 'muted' }, ' (example size)'), h('span', { class: 'muted' }, ` · ${PROFILE_LABEL[S.get('profile')] || 'Balanced'} · ${+Z.baseRiskPct().toFixed(2)}% risk`));
  };
  const account = accountControls({ onChange: () => { drawSum(); if (cur !== 'watch') drawTab(); } });
  drawSum();
  // Open by default until the person has typed their own account size; then it folds into one line.
  const acctBox = h('details', { class: 'card acct-details' },
    h('summary', null, h('span', { class: 'label' }, 'Your account'), sumText, h('span', { class: 'acct-edit' }, 'Change')),
    h('div', { class: 'acct-body stack' }, h('p', { class: 'muted fs-sm st-lead' }, 'These settings stay on this device. They only change how much Acumen suggests you buy; the ideas themselves are the same for everyone.'), account));
  if (!(F.isNum(S.get('equity')) && S.get('equity') > 0)) acctBox.open = true;
  root.append(
    pageHead('Portfolio', 'A model portfolio sized for your account, your own positions with live results, and your watchlist.'),
    acctBox,
    tabsHost, host,
    h('p', { class: 'fineprint' }, 'Model estimates for research, not investment advice. Sizes assume every stop is hit at its level; gaps can be worse. Nothing here is connected to a brokerage account.'));

  /* ----- model portfolio ----- */
  function modelView() {
    const key = pf.profiles[S.get('profile')] ? S.get('profile') : 'balanced';
    const prof = pf.profiles[key];
    const eq = Z.equity();
    const acct = Z.acctCcy();
    const ccy = acct;
    const base = Z.baseRiskPct();
    const k = prof.risk_per_trade_pct > 0 ? base / prof.risk_per_trade_pct : 1;
    const m = scaleModel(prof, eq, k);
    const money = v => F.money(v, ccy, { compactFrom: 1e7, d: 0 });
    const pcfg = Z.profileCfg(key);
    const wrap = h('div', { class: 'stack' });

    wrap.append(banner('info', h('b', null, `${PROFILE_LABEL[key]} model portfolio for ${money(eq)}. `),
      `The best actionable ideas that fit the ${PROFILE_LABEL[key]} rules, sized to your account. It is a worked example of disciplined sizing, not a recommendation to hold exactly these positions.`));

    if (!m.positions.length) {
      wrap.append(empty('Nothing fits the rules today', `No actionable idea passes the ${PROFILE_LABEL[key]} filters right now. Staying in cash is a legitimate position; the portfolio refreshes with each data update.`, 'shield'));
      return wrap;
    }

    const st = m.stats;
    const heat = st.heat_pct || 0;
    wrap.append(h('div', { class: 'stats pf-stats' },
      stat('Positions', String(m.positions.length), `of up to ${pcfg.max_positions}`),
      stat('At risk if every stop hits', F.pct(-heat, 2, { sign: false }), `${money(-(eq * heat / 100))}`, { cls: 'down', info: 'The most the model portfolio loses if every stop is hit at its level. Gaps and slippage can make it worse.' }),
      stat('Modelled expected result', F.pct(st.expected_return_pct, 2, { sign: true }), F.isNum(st.exp_r_total) ? `${F.num(st.exp_r_total, 2, { sign: true })}R across positions` : null, { cls: F.dirClass(st.expected_return_pct), info: 'Sum of each idea’s expected R times the share of the account it risks. It is an average over many repeats of such a portfolio, never a forecast of one outcome, and it comes from simulated history.' }),
      stat('Bad day, 1 in 20', F.isNum(st.var95_pct) ? F.pct(st.var95_pct, 2, { sign: true }) : F.NA, F.isNum(st.cvar95_pct) ? `${F.pct(st.cvar95_pct, 2, { sign: true })} on average across the worst days` : null, { cls: 'down', info: 'From the last year of daily returns: on the worst 5% of days the whole portfolio fell by at least this much. It describes ordinary bad days, not a crash.' }),
      stat('Diversification', F.isNum(st.diversification_ratio) ? F.num(st.diversification_ratio, 2) + '×' : F.NA, 'above 1× means positions offset each other', { info: 'The combined volatility of the positions on their own divided by the volatility of the portfolio. 1× would mean they all move together.' })));

    wrap.append(h('section', { class: 'card pad' },
      h('div', { class: 'row between wrap' }, h('div', { class: 'label' }, 'Money at risk vs your budget', explain('Heat is the total you would lose if every position hit its stop. Each profile caps it so no single bad week can do lasting damage.')),
        h('div', { class: ['mono', heat > pcfg.max_heat_pct + 1e-9 ? 'down' : ''] }, `${F.num(heat, 2)}% of ${F.num(pcfg.max_heat_pct, 1)}%`)),
      heatBar(heat, pcfg.max_heat_pct)));

    /* positions */
    const tbl = h('section', { class: 'card' },
      h('div', { class: 'card-h' }, h('div', { class: 'label' }, 'Positions'), h('div', { class: 'actions muted fs-xs' }, `Amounts in ${ccy}; units rounded down to what a broker accepts`)),
      h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl fixed-first' },
        h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', null, 'Horizon'), h('th', null, 'Grade'), h('th', { class: 'r' }, 'Entry'), h('th', { class: 'r' }, 'Stop'), h('th', { class: 'r' }, 'Target 1'), h('th', { class: 'r' }, 'Units'), h('th', { class: 'r' }, 'Value'), h('th', { class: 'r' }, 'At risk'), h('th', { class: 'r' }, 'Expected'), h('th', null, h('span', { class: 'sr-only' }, 'Actions')))),
        h('tbody', null, ...m.positions.map(p => {
          const px = n => F.px(n, p.market);
          const idea = ideaMap.get(p.id + '|' + p.horizon);
          const unitsText = p.market === 'fx' ? `${F.num(p.units, 0)}` : F.num(p.units, p.market === 'crypto' ? Math.max(0, 4 - Math.floor(Math.log10(Math.max(p.units, 1e-9)))) : 0);
          return h('tr', { class: ['link', p.tooSmall && 'dim'], onclick: e => { if (!e.target.closest('a,button')) go('/i/' + p.id, { h: p.horizon }); } },
            h('td', null, h('div', { class: 'sym' }, h('a', { ...linkProps('/i/' + p.id, { h: p.horizon }) }, p.sym)), h('div', { class: 'nm' }, p.name + (p.sector && p.market === 'psx' ? ' · ' + p.sector : ''))),
            h('td', null, h('span', { class: 'chip outline' }, HZ_LABEL[p.horizon])),
            h('td', null, gradeBadge(p.grade)),
            h('td', { class: 'r mono' }, px(p.entry)), h('td', { class: 'r mono' }, px(p.stop)), h('td', { class: 'r mono' }, px(p.target1)),
            h('td', { class: 'r mono' }, p.tooSmall ? h('span', { title: 'Smaller than the smallest order size a broker accepts, at this account size and risk setting' }, '0') : unitsText, p.market === 'fx' && !p.tooSmall ? h('div', { class: 'fs-xs muted' }, `${F.num(p.units / 100000, 2)} lots`) : null),
            h('td', { class: 'r' }, p.tooSmall ? F.NA : h('div', null, money(p.value), h('div', { class: 'fs-xs muted' }, F.pct(p.value / eq * 100, 1, { sign: false }) + ' of account'))),
            h('td', { class: 'r' }, p.tooSmall ? F.NA : h('div', null, money(-p.maxLoss), h('div', { class: 'fs-xs muted' }, F.pct(-p.effRisk, 2, { sign: false })))),
            h('td', { class: ['r', F.dirClass(p.exp_r)] }, F.r(p.exp_r, 2)),
            h('td', null, idea && !p.tooSmall ? h('button', { class: 'btn sm', type: 'button', title: 'Record this trade in My positions', onclick: () => openLogTrade(idea, Z.size(idea, { eq, riskPct: p.riskPct })) }, 'Log') : null));
        })))));
    wrap.append(tbl);
    if (m.positions.some(p => !p.rateKnown)) wrap.append(banner('info', 'Some currency rates are missing from the data, so those rows are shown in the instrument’s own currency.'));
    if (m.positions.some(p => p.tooSmall)) wrap.append(banner('warn', 'At this account size and risk setting one or more positions are smaller than the smallest order a broker accepts (one share, or 1,000 units for forex). Raise the account size or the risk per trade, or skip those ideas.'));

    /* allocation */
    const shareParts = (obj, order, labelOf, colorOf) => order.filter(x => obj[x]).map(x => ({ label: labelOf(x), value: obj[x], text: F.num(obj[x], 0) + '%', color: colorOf(x) }));
    const bySector = Object.entries(st.by_sector || {}).sort((a, b) => b[1] - a[1]);
    wrap.append(h('section', { class: 'card pad' },
      h('div', { class: 'label', style: { marginBottom: 'var(--s-4)' } }, 'Where the money sits', explain('Share of total position value, not of your whole account.')),
      h('div', { class: 'pf-alloc' },
        h('div', { class: 'stack' }, h('div', { class: 'fs-xs muted' }, 'By market'), stackBar(shareParts(st.by_market || {}, ['psx', 'fx', 'crypto'], x => MARKET_LABEL[x], x => MK_COLOR[x]), { height: 12 })),
        h('div', { class: 'stack' }, h('div', { class: 'fs-xs muted' }, 'By horizon'), stackBar(shareParts(st.by_horizon || {}, ['short', 'medium', 'long'], x => HZ_LABEL[x], x => HZ_COLOR[x]), { height: 12 })),
        h('div', { class: 'stack' }, h('div', { class: 'fs-xs muted' }, 'By sector'),
          bySector.length ? hbars(bySector.map(([name, v]) => ({ label: name, value: v, text: F.num(v, 0) + '%' })), { max: 100, labelW: 150 }) : h('div', { class: 'muted' }, 'No sector data')))));

    /* stress */
    const stress = m.stress;
    if (stress.length) {
      const mx = Math.max(...stress.map(x => Math.abs(x.pnl_pct || 0)), 0.5);
      const stressRow = x => {
        const v = x.pnl_pct;
        const w = F.isNum(v) ? Math.min(100, Math.abs(v) / mx * 50) : 0;
        const pos = !F.isNum(v) || v >= 0;
        return h('div', { class: 'sr' },
          h('div', { class: 'sr-top' }, h('b', null, x.name), h('span', { class: ['mono', F.dirClass(v)] }, F.pct(v, 2), F.isNum(v) ? h('span', { class: 'muted' }, `  (${v > 0 ? '+' : ''}${money(eq * v / 100)})`) : null)),
          h('div', { class: 'signed' }, h('div', { class: 'hb-track' }, h('div', { class: 'hb-zero' }), w > 0 ? h('div', { class: ['hb-fill', pos ? 'pos' : 'neg'], style: { width: w + '%', left: pos ? '50%' : (50 - w) + '%' } }) : null)),
          h('div', { class: 'fs-xs muted' }, x.detail));
      };
      wrap.append(h('section', { class: 'card pad' },
        h('div', { class: 'label', style: { marginBottom: 'var(--s-4)' } }, 'What if the market turns against it?', explain('Simple what-if tests. They scale each position by its beta, or add up the stops. They are not forecasts.')),
        h('div', { class: 'stack' }, ...stress.map(stressRow))));
    }

    /* correlation */
    const corr = prof.correlation || {};
    if (corr.ids && corr.ids.length >= 2 && corr.matrix && corr.matrix.length) {
      const labels = corr.ids.map(id => (rowsById.get(id) || { sym: id.split(':').pop() }).sym);
      wrap.append(h('section', { class: 'card pad' },
        h('div', { class: 'label', style: { marginBottom: 'var(--s-3)' } }, 'How the positions move together', explain('Correlation of daily returns over the last year. Near +1 the two positions tend to rise and fall together, so owning both adds little diversification. Near 0 they are independent; below 0 they offset.')),
        heatMatrix(labels, labels, corr.matrix, { max: 1, fmt: v => F.num(v, 2), color: corrColor }),
        h('div', { class: 'row wrap fs-xs muted', style: { gap: '16px', marginTop: 'var(--s-3)' } },
          h('span', { class: 'sb-key' }, h('i', { style: { background: corrColor(0.8) } }), 'Move together (amber, positive)'),
          h('span', { class: 'sb-key' }, h('i', { style: { background: corrColor(-0.6) } }), 'Move opposite (violet, negative)'),
          h('span', null, 'Numbers are shown in every cell.'))));
    }

    const notes = (pf.notes || []);
    wrap.append(h('ul', { class: 'pts fs-xs muted' },
      ...notes.map(n => h('li', null, n)),
      h('li', null, F.isNum(k) && Math.abs(k - 1) > 0.005 ? `Risk numbers are scaled by ${F.num(k, 2)}× because your risk per trade (${F.num(base, 2)}%) differs from the ${PROFILE_LABEL[key]} default (${F.num(prof.risk_per_trade_pct, 2)}%).` : `Built with the ${PROFILE_LABEL[key]} default of ${F.num(prof.risk_per_trade_pct, 2)}% risk per trade.`),
      h('li', null, `Portfolio generated ${F.dateTime(pf.generated_at)} for a reference account of ${F.compact(pf.equity_reference)} and rescaled here.`)));
    return wrap;
  }

  /* ----- watchlist ----- */
  function watchView() {
    const ids = S.get('watchlist') || [];
    if (!ids.length) return empty('Your watchlist is empty', 'Tap the star on any instrument page or trade idea to follow it here. It is saved on this device.', 'star');
    const rows = ids.map(id => rowsById.get(id)).filter(Boolean);
    const gone = ids.filter(id => !rowsById.has(id));
    const remove = id => { S.set('watchlist', (S.get('watchlist') || []).filter(x => x !== id)); toast('Removed from watchlist'); drawTabs(); drawTab(); };
    const ctx = { ideas: ideaMap };
    const wrap = h('div', { class: 'stack' });
    wrap.append(h('section', { class: 'card' },
      h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl fixed-first' },
        h('thead', null, h('tr', null, h('th', null, 'Instrument'), h('th', { class: 'r' }, 'Price'), h('th', { class: 'r' }, '1D'), h('th', { class: 'r' }, '1M'), ...['short', 'medium', 'long'].map(hz => h('th', null, HZ_LABEL[hz])), h('th', null, h('span', { class: 'sr-only' }, 'Actions')))),
        h('tbody', null, ...rows.map(r => h('tr', { class: 'link', onclick: e => { if (!e.target.closest('a,button')) go('/i/' + r.id); } },
          h('td', null, h('div', { class: 'sym' }, h('a', { ...linkProps('/i/' + r.id) }, r.sym)), h('div', { class: 'nm' }, r.name)),
          h('td', { class: 'r mono' }, F.px(r.px, r.m)),
          h('td', { class: 'r' }, F.isNum(r.chg1d) ? chgSpan(r.chg1d, 1) : F.NA),
          h('td', { class: 'r' }, F.isNum(r.chg1m) ? chgSpan(r.chg1m, 1) : F.NA),
          ...['short', 'medium', 'long'].map(hz => h('td', null, signalBadge(r, hz, ctx.ideas))),
          h('td', null, h('div', { class: 'row', style: { justifyContent: 'flex-end' } }, h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Remove ${r.sym} from watchlist`, title: 'Remove', onclick: () => remove(r.id) }, icon('x'))))))))),
      h('div', { class: 'card-b fs-xs muted' }, 'A solid badge is an actionable idea (grade and conviction), an outlined one is reference only, a faded one is a weak signal with no plan.')));
    if (gone.length) wrap.append(banner('info', `${gone.length} saved ${gone.length === 1 ? 'item is' : 'items are'} no longer in the tracked universe: ${gone.join(', ')}. `, h('button', { class: 'btn sm', type: 'button', onclick: () => { for (const id of gone) S.set('watchlist', (S.get('watchlist') || []).filter(x => x !== id)); drawTabs(); drawTab(); } }, 'Clear them')));
    return wrap;
  }

  /* ----- tabs ----- */
  let mine = null;
  function drawTab() {
    clear(host);
    mine = null;
    let el;
    try {
      if (cur === 'model') el = modelView();
      else if (cur === 'mine') { mine = journalView({ onChange: drawTabs }); el = mine.el; }
      else el = watchView();
    } catch (e) { el = errorBlock(e); }
    host.append(el);
  }
  function drawTabs() {
    const n = openCount(), w = watchCount();
    replace(tabsHost, tabs([
      { value: 'model', label: 'Model portfolio' },
      { value: 'mine', label: n ? `My positions (${n})` : 'My positions' },
      { value: 'watch', label: w ? `Watchlist (${w})` : 'Watchlist' },
    ], cur, v => { cur = v; replaceQuery({ t: v === defaultTab() ? null : v }); drawTabs(); drawTab(); }));
  }
  drawTabs();
  drawTab();
  return { el: root };
}
