import { h } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { S } from '../lib/store.js';
import * as Z from '../lib/sizing.js';
import { seg, field, explain } from './kit.js';

export const PROFILES = ['conservative', 'balanced', 'aggressive'];
export const PROFILE_LABEL = { conservative: 'Conservative', balanced: 'Balanced', aggressive: 'Aggressive' };

export function profileText(name) {
  const p = Z.profileCfg(name);
  return `Risks ${F.num(p.risk_per_trade_pct, 2)}% of the account on a typical idea, keeps total money at risk under ${F.num(p.max_heat_pct, 1)}%, holds at most ${p.max_positions} positions and only takes grade ${p.min_grade} or better.`;
}

const parseNum = v => Number(String(v).replace(/[,\s_]/g, ''));

// Currency, account size, risk profile and risk per trade. Used by Portfolio and Settings; every change is saved at once.
export function accountControls({ onChange = () => {} } = {}) {
  const eqIn = h('input', { class: 'input mono', inputmode: 'decimal', autocomplete: 'off' });
  const rkIn = h('input', { class: 'input mono', inputmode: 'decimal', autocomplete: 'off' });
  const eqField = field('Account size', eqIn);
  const rkNote = h('div', { class: 'muted fs-xs' });
  const rkField = field(h('span', null, 'Risk per trade (%)', explain('The share of your account you would lose if one typical idea hit its stop. Acumen scales it by grade (A full, B three quarters, C half) and halves it again when the odds for that kind of setup have not been validated. Leave blank to use the profile default.')), rkIn);
  rkField.append(rkNote);
  const desc = h('p', { class: 'muted acct-desc' });
  const warn = h('div', { class: 'err', role: 'status' });

  function fill() {
    const c = Z.acctCcy();
    const e = S.get('equity');
    eqIn.value = F.isNum(e) && e > 0 ? F.num(e, 0) : '';
    eqIn.placeholder = F.num(Z.defaultEquity(), 0);
    const lab = eqField.querySelector('label');
    if (lab) lab.textContent = `Account size (${c})`;
    const r = S.get('riskPct');
    const def = Z.profileCfg().risk_per_trade_pct;
    rkIn.value = F.isNum(r) && r > 0 ? String(r) : '';
    rkIn.placeholder = String(def);
    rkNote.textContent = F.isNum(r) && r > 0 ? `Profile default is ${def}%. Clear the box to go back to it.` : `Using the ${PROFILE_LABEL[S.get('profile')] || 'Balanced'} default.`;
    desc.textContent = profileText(S.get('profile'));
    warn.textContent = '';
  }

  eqIn.addEventListener('input', () => {
    const raw = eqIn.value.trim();
    if (!raw) { S.set('equity', null, { silent: true }); onChange(); return; }
    const v = parseNum(raw);
    if (F.isNum(v) && v > 0) { S.set('equity', v, { silent: true }); S.set('equityMemo', { ...(S.get('equityMemo') || {}), [Z.acctCcy()]: v }, { silent: true }); onChange(); }
  });
  eqIn.addEventListener('blur', () => { const e = S.get('equity'); if (F.isNum(e) && e > 0) eqIn.value = F.num(e, 0); });

  rkIn.addEventListener('input', () => {
    const raw = rkIn.value.trim();
    warn.textContent = '';
    if (!raw) { S.set('riskPct', null, { silent: true }); fill(); rkIn.focus(); onChange(); return; }
    const v = parseNum(raw);
    if (!F.isNum(v) || v <= 0) return;
    if (v > 5) { warn.textContent = 'Use 5% or less: even professionals rarely risk more than 2% on one idea.'; return; }
    S.set('riskPct', v, { silent: true });
    rkNote.textContent = `Profile default is ${Z.profileCfg().risk_per_trade_pct}%.`;
    if (v > 2) warn.textContent = 'That is a high risk per idea. A string of losses at this level can hurt quickly.';
    onChange();
  });

  const ccySeg = seg([{ value: 'PKR', label: 'PKR' }, { value: 'USD', label: 'USD' }], Z.acctCcy(), v => {
    if (v === Z.acctCcy()) return;
    const fx = Z.usdPkr();
    const e = S.get('equity');
    const memo = { ...(S.get('equityMemo') || {}) };
    S.set('accountCcy', v);
    // keep the same purchasing power when the account currency changes
    if (F.isNum(e) && e > 0) {
      memo[v === 'USD' ? 'PKR' : 'USD'] = e;
      const conv = fx ? Math.round(v === 'USD' ? e / fx : e * fx) : null;
      const back = memo[v];
      // returning to a size the person typed in that currency restores it exactly instead of a rounded round trip
      S.set('equity', conv != null && F.isNum(back) && Math.abs(back - conv) / conv < 0.01 ? back : conv);
      S.set('equityMemo', memo, { silent: true });
    }
    fill(); onChange();
  }, { label: 'Account currency' });

  const profSeg = seg(PROFILES.map(p => ({ value: p, label: PROFILE_LABEL[p] })), S.get('profile'), v => {
    S.set('profile', v);
    S.set('riskPct', null);
    fill(); onChange();
  }, { label: 'Risk profile' });

  fill();
  const root = h('div', { class: 'acct stack' },
    h('div', { class: 'acct-grid' },
      h('div', { class: 'field' }, h('div', { class: 'flabel' }, 'Account currency'), ccySeg),
      eqField,
      h('div', { class: 'field' }, h('div', { class: 'flabel' }, 'Risk profile'), profSeg),
      rkField),
    desc, warn);
  root.refresh = fill;
  return root;
}
