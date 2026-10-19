// Turns meta.json into a plain-language verdict on how fresh and complete the data is.
// Pure (no DOM), so it can be tested on its own: see web/test/health.test.mjs.
import * as F from './fmt.js';
import { MARKETS, MARKET_LABEL, STALE_DAYS } from './util.js';

export const REFRESH_WARN_HOURS = 36;
const SEVERITY = { bad: 3, warn: 2, info: 1 };

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// "5 hours", "3 days"
export function span(hours) {
  if (!F.isNum(hours)) return 'an unknown time';
  if (hours < 1.5) return 'about an hour';
  if (hours < 48) return `${Math.round(hours)} hours`;
  return `${Math.round(hours / 24)} days`;
}

function calendarDays(asof, now) {
  const t = F.parseDate(asof);
  if (!t) return null;
  const a = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((a - t) / 86400000);
}

// state: error (no data) > stale (old prices) > warming (history still filling) > ok
export function marketState(k, m, now = new Date()) {
  const age = calendarDays(m.asof, now);
  const limit = STALE_DAYS[k] ?? 4;
  const old = F.isNum(age) && age > limit;
  const state = m.status === 'error' ? 'error' : (m.status === 'stale' || old) ? 'stale' : m.status === 'warming' ? 'warming' : 'ok';
  return { age, limit, old, state };
}

export function assess(meta, { now = new Date() } = {}) {
  meta = meta || {};
  const gen = F.parseDate(meta.generated_at);
  const hours = gen ? (now.getTime() - gen.getTime()) / 36e5 : null;
  const issues = [];

  const markets = MARKETS.filter(k => meta.markets && meta.markets[k]).map(k => ({ k, name: MARKET_LABEL[k], ...meta.markets[k], ...marketState(k, meta.markets[k], now) }));
  const sources = Array.isArray(meta.sources) ? meta.sources : [];
  const failedSources = sources.filter(s => !s.ok);
  const hl = meta.health || {};

  if (!meta.demo && hours != null && hours > REFRESH_WARN_HOURS) {
    issues.push({ tone: 'warn', title: `Last refreshed ${span(hours)} ago`, text: 'The scheduled refresh normally runs several times a day. If it has stopped, everything you see here is getting older. Open the Actions tab of your repository to see why.' });
  }

  for (const m of markets) {
    if (m.state === 'error') {
      issues.push({ tone: 'bad', title: `${m.name} has no data`, text: m.note || 'Every source failed in the last refresh, so no instrument in this market could be updated or analysed.' });
    } else if (m.state === 'stale') {
      const lead = m.old ? `${m.name} prices are ${plural(m.age, 'day', 'days')} old` : `${m.name} is out of date`;
      issues.push({
        tone: 'warn', title: lead,
        text: `The latest price date is ${F.date(m.asof, { year: true })}. ${m.old ? `Weekends and holidays explain up to ${m.limit} days for this market, and this is older than that. ` : m.note ? m.note + '. ' : ''}Treat this market's ideas as out of date until a refresh succeeds.`,
      });
    } else if (m.state === 'warming') {
      issues.push({ tone: 'info', title: `${m.name} history is still loading (${F.num(m.history_pct ?? 0, 0)}%)`, text: 'Some instruments do not have enough price history yet for every indicator and test. This fills in over the next refreshes.' });
    }
  }

  if (failedSources.length) {
    const unhealthy = markets.some(m => m.state === 'error' || m.state === 'stale');
    const names = failedSources.slice(0, 4).map(s => s.message ? `${s.name} (${s.message})` : s.name).join('; ');
    issues.push({
      tone: unhealthy ? 'warn' : 'info',
      title: `${failedSources.length} of ${sources.length} data sources failed`,
      text: `${names}${failedSources.length > 4 ? ` and ${failedSources.length - 4} more` : ''}. ${unhealthy ? 'At least one market is missing or out of date, so a fallback source did not cover it.' : 'Each market tries several sources in turn, and every market still has current prices, so a fallback source did the job.'}`,
    });
  }

  if (F.isNum(hl.failed) && hl.failed > 0) {
    const share = hl.analysed ? hl.failed / (hl.analysed + hl.failed) : 0;
    const eg = (hl.failed_examples || []).slice(0, 3).join('; ');
    issues.push({
      tone: share > 0.05 ? 'warn' : 'info',
      title: `${plural(hl.failed, 'instrument', 'instruments')} could not be analysed`,
      text: `${eg ? eg + '. ' : ''}These are left out of the screener and ideas until the data for them is complete.`,
    });
  }

  issues.sort((a, b) => SEVERITY[b.tone] - SEVERITY[a.tone]);

  const worst = issues.reduce((w, i) => Math.max(w, SEVERITY[i.tone] === 1 ? 0 : SEVERITY[i.tone]), 0);
  let tone = worst >= 3 ? 'bad' : worst >= 2 ? 'warn' : 'ok';
  if (meta.demo && tone === 'ok') tone = 'warn'; // synthetic data is never "healthy"
  // the headline says demo data first, whatever else is wrong with it
  const okSources = sources.filter(s => s.ok).length;
  let title, text;
  if (meta.demo) {
    title = 'Demo data';
    text = 'Everything here is synthetic, so use it to explore the app, not to trade. Real data appears after your first successful refresh.';
  } else if (tone === 'bad') {
    title = 'Some data is missing';
    text = 'At least one market has no data right now. Its ideas cannot be updated until a refresh succeeds.';
  } else if (tone === 'warn') {
    title = 'Some data needs attention';
    text = 'The app works, but read the points below before acting on an idea.';
  } else {
    title = 'All data is current';
    text = `Every market has recent prices${sources.length ? ` and ${okSources} of ${sources.length} sources answered in the last refresh` : ''}.`;
  }

  const order = { false: 0, true: 1 };
  const sortedSources = [...sources].sort((a, b) =>
    (order[!!a.ok] - order[!!b.ok]) || (MARKETS.indexOf(a.market) - MARKETS.indexOf(b.market)) || String(a.name).localeCompare(String(b.name)));

  return {
    tone, title, text, issues, markets, hours,
    sources: sortedSources,
    counts: { sources: sources.length, sourcesOk: okSources, analysed: hl.analysed ?? null, failed: hl.failed ?? 0, seconds: hl.seconds ?? null },
  };
}
