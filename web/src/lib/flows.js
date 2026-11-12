// Pure helpers for the Money Flows page (no DOM), kept apart so node --test can check them.

export const STATE_ORDER = ['entering', 'turning_in', 'neutral', 'fading', 'leaving'];
export const STATE_TONE = { entering: 'up', turning_in: 'accent', neutral: 'outline', fading: 'warn', leaving: 'down' };
export const STATE_HINT = {
  entering: 'Buying pressure above the rest of the market over both the last 7 and the last 30 days.',
  turning_in: 'The last 7 days are strong, but the 30-day picture is not yet.',
  neutral: 'Close to the rest of the market.',
  fading: 'The last 7 days are weak after earlier support.',
  leaving: 'Buying pressure below the rest of the market over both the last 7 and the last 30 days.',
};

export const isNum = x => typeof x === 'number' && Number.isFinite(x);

export function stateLabel(body, s) {
  return (body && body.states && body.states[s]) || ({ entering: 'Entering', turning_in: 'Turning in', neutral: 'Neutral', fading: 'Fading', leaving: 'Leaving' })[s] || s;
}

/* ---------------------------------------------------------------- the sector table */
export function countByState(rows) {
  const c = { all: (rows || []).length };
  for (const s of STATE_ORDER) c[s] = 0;
  for (const r of rows || []) if (c[r.state] !== undefined) c[r.state] += 1;
  return c;
}

export function filterSectors(rows, state) {
  return state && state !== 'all' ? (rows || []).filter(r => r.state === state) : [...(rows || [])];
}

// a bar for a buying-pressure percentage: which side of the middle and how long (full length at +-`full` percent)
export function pressureBar(x, full = 60) {
  if (!isNum(x)) return { side: 'none', width: 0 };
  return { side: x > 0 ? 'pos' : x < 0 ? 'neg' : 'none', width: Math.round(Math.min(Math.abs(x) / full, 1) * 100) };
}

/* ---------------------------------------------------------------- the three questions */
export function bySector(rows, ...states) {
  return (rows || []).filter(r => states.includes(r.state));
}

export function beneficiaries(cands, limit = 6) {
  return (cands || []).filter(c => (c.sector_state === 'entering' || c.sector_state === 'turning_in') && c.pillars >= 2).slice(0, limit);
}

export function pillars(c) {
  return [
    { key: 'accumulation', label: 'Accumulating', on: !!c.accumulation, hint: '30-day buying pressure in the top third of liquid shares, not negative over 7 days, and not already up a lot in a month' },
    { key: 'fundamentals', label: 'Earnings up', on: !!c.fundamentals, hint: 'Profitable, with profit per share up on a year earlier by at least the threshold' },
    { key: 'interest', label: 'Busy trading', on: !!c.interest, hint: 'Turnover over the last 7 days well above its 90-day norm (the stand-in for institutional interest)' },
  ];
}

/* ---------------------------------------------------------------- the backtest */
export function verdictView(bt) {
  const v = bt && bt.verdict;
  const lines = [];
  if (bt && bt.sector_line) lines.push(bt.sector_line);
  for (const l of (bt && bt.stock_lines) || []) lines.push(l);
  const table = {
    supported: ['info', 'Both fixed tests passed: the measures ranked later sector performance.', 'Treat that as a lead worth following rather than proof; the cautions below still apply, and the weekly record on this page tests it out of sample.'],
    partial: ['warn', 'One of the two fixed tests passed and the other did not.', 'That is not enough to call the measure a signal.'],
    reversed: ['warn', 'The measure pointed the wrong way.', 'Sectors with the strongest recent buying pressure did worse afterwards, not better.'],
    not_shown: ['warn', 'These flows describe where money has been going. They did not predict which sectors did better afterwards.', 'Use this page as a map of what has happened, not as a trading signal.'],
    too_short: ['info', 'There is too little history to test the measures yet.', 'A verdict needs more weekly checks than the stored history allows so far.'],
  };
  const [tone, title, text] = table[v] || ['info', 'Not tested yet.', ''];
  return { kind: v || 'none', tone, title, text, lines };
}

// the sentence under the candidate table: what the test above says about acting on a screen like this
export function screenNote(kind) {
  const lead = 'A screen that tells you where to look, not a recommendation. ';
  const table = {
    supported: 'The test below found that recent buying pressure ranked later sector returns, but that is a lead to follow up and not proof, so read about a share before acting on it.',
    partial: 'The test below found only part of a link between recent buying pressure and later returns, so a share passing here is a reason to read about it, not a reason to buy it.',
    reversed: 'The test below found the sectors with the strongest recent buying pressure doing worse afterwards, so a share passing here is a reason to read about it, not a reason to buy it.',
    not_shown: 'The test below found no reliable link between recent buying pressure and later returns, so a share passing here is a reason to read about it, not a reason to buy it.',
    too_short: 'The test below has too little history to say whether recent buying pressure predicts anything, so a share passing here is only a reason to read about it.',
  };
  return lead + (table[kind] || table.too_short);
}

// where a t-statistic sits on a strip from -scale to +scale: the bar runs from the middle to the value
export function tBar(t, scale = 6) {
  if (!isNum(t)) return { side: 'none', left: 50, width: 0 };
  const w = Math.min(Math.abs(t) / scale, 1) * 50;
  return t >= 0 ? { side: 'pos', left: 50, width: w } : { side: 'neg', left: 50 - w, width: w };
}

export function tMark(t, scale = 6) { return 50 + Math.min(t / scale, 1) * 50; }

export function resultOf(test) {
  if (!test || !test.n || test.t == null) return { label: 'Too little data', tone: 'outline' };
  if (test.verdict === 'supported') return { label: 'Predicted', tone: 'up' };
  if (test.verdict === 'reversed') return { label: 'Pointed the wrong way', tone: 'warn' };
  return { label: 'No relationship shown', tone: 'outline' };
}

export function testName(t) {
  const ahead = `next ${t.horizon} trading days`;
  if (t.level === 'stock') return `Single shares: ${t.label} pressure, ${ahead}`;
  return `Sectors: ${t.label} flow, ${ahead}`;
}

export function splitTests(bt) {
  const tests = (bt && bt.tests) || [];
  return { primary: tests.filter(t => t.primary), sector: tests.filter(t => t.level === 'sector' && !t.primary), stock: tests.filter(t => t.level === 'stock') };
}

/* ---------------------------------------------------------------- the weekly record */
// the weekly archive with each week's scored result (if it has had time to be scored) alongside
export function weekRows(weeks, live) {
  const by = new Map(((live && live.rows) || []).map(r => [r.week, r]));
  return (weeks || []).map(w => ({ ...w, spread5: by.has(w.week) ? by.get(w.week).spread5 ?? null : null, spread21: by.has(w.week) ? by.get(w.week).spread21 ?? null : null }));
}

export function liveSummary(live) {
  const hz = (live && live.horizons) || {};
  const parts = [];
  for (const k of Object.keys(hz)) {
    const x = hz[k];
    if (x && x.n) parts.push({ days: Number(k), n: x.n, hit: x.hit, mean: x.mean_spread });
  }
  return parts;
}

export function backdropItems(macro, regime) {
  const out = [];
  if (regime && regime.label) out.push({ k: 'Pakistan market', v: regime.label });
  const m = macro || {};
  const add = (k, key, fmt) => { if (isNum(m[key])) out.push({ k, v: fmt(m[key]) }); };
  add('USD/PKR', 'usd_pkr', x => x.toFixed(1));
  add('Policy rate', 'policy_rate_pk', x => `${x.toFixed(1)}%`);
  add('Dollar index', 'dxy', x => x.toFixed(1));
  add('Gold', 'gold', x => Math.round(x).toLocaleString('en-US'));
  add('Brent', 'brent', x => x.toFixed(1));
  add('VIX', 'vix', x => x.toFixed(1));
  return out;
}
