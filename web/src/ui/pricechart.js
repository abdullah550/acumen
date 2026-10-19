import { h } from '../lib/dom.js';
import * as F from '../lib/fmt.js';
import { makeChart, palette, chartOptions, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers, LineStyle } from './lwc.js';

// Interactive price chart for one instrument, built on TradingView Lightweight Charts.
export const OVERLAYS = [
  { id: 'sma20', label: 'SMA 20', ci: 0, keys: ['sma20'] },
  { id: 'sma50', label: 'SMA 50', ci: 1, keys: ['sma50'] },
  { id: 'sma200', label: 'SMA 200', ci: 5, keys: ['sma200'] },
  { id: 'ema20', label: 'EMA 20', ci: 2, keys: ['ema20'] },
  { id: 'bb', label: 'Bollinger', ci: -1, keys: ['bb_upper', 'bb_lower'] },
  { id: 'donchian', label: 'Donchian', ci: 3, keys: ['donchian_high', 'donchian_low'] },
  { id: 'supertrend', label: 'SuperTrend', ci: -2, keys: ['supertrend'] },
  { id: 'ichimoku', label: 'Ichimoku cloud', ci: 4, keys: ['ichimoku_a', 'ichimoku_b'] },
];
export const PANES = [
  { id: 'volume', label: 'Volume' },
  { id: 'rsi', label: 'RSI' },
  { id: 'macd', label: 'MACD' },
  { id: 'stoch', label: 'Stochastic' },
  { id: 'adx', label: 'ADX' },
  { id: 'atr', label: 'ATR' },
  { id: 'obv', label: 'OBV' },
  { id: 'cmf', label: 'CMF' },
  { id: 'mfi', label: 'MFI' },
  { id: 'none', label: 'None' },
];

export function overlaySwatch(o, c = palette()) {
  if (o.ci === -1) return c.text;
  if (o.ci === -2) return c.up;
  return c.cat[o.ci];
}

export function createPriceChart(host, detail, init) {
  const st = { tf: 'D', range: '6M', overlays: new Set(['sma50', 'sma200']), pane: 'volume', plan: true, levels: true, markers: true, ...init };
  const market = detail.market;
  const ch = detail.chart;
  const dates = ch.dates;
  const n = dates.length;
  const legend = h('div', { class: 'chart-legend' });
  const box = h('div', { class: 'chartbox' });
  host.replaceChildren(box, legend);

  let handle = null;
  let candle = null;
  let vol = null;
  let planLines = [];
  let levelLines = [];
  let markerApi = null;
  let overlaySeries = {};
  let paneSeries = [];
  let paneLines = [];
  const dec = F.decimals(ch.close[n - 1], market);
  const priceFmt = { type: 'price', precision: dec, minMove: 1 / 10 ** dec };

  const lineData = (arr, ds = dates) => { const out = []; for (let i = 0; i < ds.length; i++) if (arr[i] != null) out.push({ time: ds[i], value: arr[i] }); return out; };

  function legendFor(i) {
    const o = ch.open[i], hi = ch.high[i], lo = ch.low[i], cl = ch.close[i], pv = i > 0 ? ch.close[i - 1] : null;
    const chg = pv ? (cl / pv - 1) * 100 : null;
    const cls = F.dirClass(chg);
    legend.replaceChildren(h('div', { class: 'ohlc' },
      h('span', null, F.date(dates[i], { year: true })), h('span', null, 'O ', h('b', null, F.px(o, market))), h('span', null, 'H ', h('b', null, F.px(hi, market))),
      h('span', null, 'L ', h('b', null, F.px(lo, market))), h('span', null, 'C ', h('b', null, F.px(cl, market))),
      F.isNum(chg) ? h('span', { class: ['chg', cls] }, F.pct(chg, 2)) : null,
      F.isNum(ch.volume[i]) ? h('span', null, 'Vol ', h('b', null, F.compact(ch.volume[i]))) : null));
  }

  // Lets the vertical scale stretch so the plan's stop and targets are on screen, not just the candles.
  function planRange() {
    const idea = st.planIdea;
    if (!st.plan || !idea || st.tf === 'W') return null;
    const v = [idea.stop, idea.entry && idea.entry.px, ...(idea.targets || []).map(t => t.px)].filter(F.isNum);
    return v.length ? { lo: Math.min(...v), hi: Math.max(...v) } : null;
  }
  function scaleProvider() {
    return base => {
      const res = base();
      const x = planRange();
      if (!x) return res;
      if (res === null) return { priceRange: { minValue: x.lo, maxValue: x.hi } };
      return { ...res, priceRange: { minValue: Math.min(res.priceRange.minValue, x.lo), maxValue: Math.max(res.priceRange.maxValue, x.hi) } };
    };
  }

  function build() {
    if (handle) handle.destroy();
    const c = palette();
    const weekly = st.tf === 'W';
    handle = makeChart(box, { ...chartOptions(c), handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true }, kineticScroll: { touch: true, mouse: false } });
    const chart = handle.chart;
    planLines = []; levelLines = []; overlaySeries = {}; paneSeries = []; paneLines = []; markerApi = null;

    if (weekly) {
      const w = ch.weekly || { dates: [], close: [] };
      const l = chart.addSeries(LineSeries, { color: c.ink, lineWidth: 2, priceFormat: priceFmt, priceLineVisible: false }, 0);
      l.setData(lineData(w.close, w.dates));
      candle = l; vol = null;
      const s10 = chart.addSeries(LineSeries, { color: c.cat[1], lineWidth: 1, priceFormat: priceFmt, priceLineVisible: false, lastValueVisible: false }, 0);
      s10.setData(lineData(w.sma10 || [], w.dates));
      const s40 = chart.addSeries(LineSeries, { color: c.cat[5], lineWidth: 1, priceFormat: priceFmt, priceLineVisible: false, lastValueVisible: false }, 0);
      s40.setData(lineData(w.sma40 || [], w.dates));
      legend.replaceChildren(h('div', { class: 'ohlc' }, h('span', null, 'Weekly close'), h('span', null, h('b', { style: { color: c.cat[1] } }, '━'), ' SMA 10'), h('span', null, h('b', { style: { color: c.cat[5] } }, '━'), ' SMA 40')));
      chart.timeScale().fitContent();
    } else {
      candle = chart.addSeries(CandlestickSeries, { upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false, priceFormat: priceFmt, priceLineVisible: true, priceLineColor: c.cross, priceLineStyle: LineStyle.Dotted, autoscaleInfoProvider: scaleProvider() }, 0);
      candle.setData(dates.map((t, i) => ({ time: t, open: ch.open[i], high: ch.high[i], low: ch.low[i], close: ch.close[i] })));
      vol = null;
      for (const o of OVERLAYS) addOverlay(chart, o, c);
      applyOverlayVisibility();
      applyPane(chart, c);
      applyLines(c);
      setRange(st.range, false);
      legendFor(n - 1);
      chart.subscribeCrosshairMove(p => {
        if (!p || !p.time) { legendFor(n - 1); return; }
        const i = dates.indexOf(p.time);
        legendFor(i >= 0 ? i : n - 1);
      });
    }
    handle.recolor = cc => {
      chart.applyOptions(chartOptions(cc));
      if (!weekly) {
        candle.applyOptions({ upColor: cc.up, downColor: cc.down, wickUpColor: cc.up, wickDownColor: cc.down, priceLineColor: cc.cross });
        build();
      }
    };
  }

  function addOverlay(chart, o, c) {
    const col = o.ci >= 0 ? c.cat[o.ci] : c.text;
    const common = { priceFormat: priceFmt, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false };
    const group = [];
    if (o.id === 'supertrend') {
      const s = chart.addSeries(LineSeries, { ...common, lineWidth: 2 }, 0);
      const data = [];
      const arr = ch.supertrend || ch.overlays.supertrend;
      for (let i = 0; i < n; i++) { const v = ch.overlays.supertrend[i]; if (v != null) data.push({ time: dates[i], value: v, color: ch.close[i] >= v ? c.up : c.down }); }
      void arr;
      s.setData(data); group.push(s);
    } else if (o.id === 'bb') {
      for (const k of o.keys) { const s = chart.addSeries(LineSeries, { ...common, color: col, lineWidth: 1, lineStyle: LineStyle.Dashed }, 0); s.setData(lineData(ch.overlays[k])); group.push(s); }
    } else if (o.id === 'ichimoku') {
      o.keys.forEach((k, j) => { const s = chart.addSeries(LineSeries, { ...common, color: j === 0 ? c.cat[2] : c.cat[4], lineWidth: 1 }, 0); s.setData(lineData(ch.overlays[k])); group.push(s); });
    } else {
      for (const k of o.keys) { const s = chart.addSeries(LineSeries, { ...common, color: col, lineWidth: o.id === 'sma200' ? 2 : 1 }, 0); s.setData(lineData(ch.overlays[k])); group.push(s); }
    }
    overlaySeries[o.id] = group;
  }

  function applyOverlayVisibility() {
    for (const id in overlaySeries) for (const s of overlaySeries[id]) s.applyOptions({ visible: st.overlays.has(id) });
  }

  function clearPane(chart) {
    for (const s of paneSeries) { try { chart.removeSeries(s); } catch { /* ignore */ } }
    paneSeries = []; paneLines = [];
    vol = null;
  }

  function applyPane(chart, c) {
    clearPane(chart);
    const P = ch.panes || {};
    const id = st.pane;
    if (id === 'none') return;
    if (id === 'volume') {
      vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false, color: c.line }, 1);
      vol.setData(dates.map((t, i) => ({ time: t, value: ch.volume[i] || 0, color: (ch.close[i] >= ch.open[i] ? c.up : c.down) + '99' })));
      paneSeries.push(vol);
    } else {
      const add = (def, opt, data) => { const s = chart.addSeries(def, { priceLineVisible: false, lastValueVisible: true, ...opt }, 1); s.setData(data); paneSeries.push(s); return s; };
      const ref = (s, price, title) => paneLines.push(s.createPriceLine({ price, color: c.cross, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: title || '' }));
      if (id === 'rsi') { const s = add(LineSeries, { color: c.cat[5], lineWidth: 2 }, lineData(P.rsi)); ref(s, 70); ref(s, 30); }
      if (id === 'mfi') { const s = add(LineSeries, { color: c.cat[5], lineWidth: 2 }, lineData(P.mfi)); ref(s, 80); ref(s, 20); }
      if (id === 'macd') {
        add(HistogramSeries, { lastValueVisible: false }, dates.map((t, i) => (P.macd_hist[i] == null ? null : { time: t, value: P.macd_hist[i], color: (P.macd_hist[i] >= 0 ? c.up : c.down) + '99' })).filter(Boolean));
        add(LineSeries, { color: c.cat[0], lineWidth: 2 }, lineData(P.macd));
        add(LineSeries, { color: c.cat[1], lineWidth: 1 }, lineData(P.macd_signal));
      }
      if (id === 'stoch') { const s = add(LineSeries, { color: c.cat[0], lineWidth: 2 }, lineData(P.stoch_k)); add(LineSeries, { color: c.cat[1], lineWidth: 1 }, lineData(P.stoch_d)); ref(s, 80); ref(s, 20); }
      if (id === 'adx') { const s = add(LineSeries, { color: c.ink, lineWidth: 2 }, lineData(P.adx)); add(LineSeries, { color: c.up, lineWidth: 1 }, lineData(P.plus_di)); add(LineSeries, { color: c.down, lineWidth: 1 }, lineData(P.minus_di)); ref(s, 25); }
      if (id === 'atr') add(LineSeries, { color: c.cat[3], lineWidth: 2 }, lineData(P.atr));
      if (id === 'obv') add(LineSeries, { color: c.cat[0], lineWidth: 2 }, lineData(P.obv));
      if (id === 'cmf') { const s = add(LineSeries, { color: c.cat[2], lineWidth: 2 }, lineData(P.cmf)); ref(s, 0); }
    }
    try {
      const panes = chart.panes();
      if (panes[1]) { panes[0].setStretchFactor(3.2); panes[1].setStretchFactor(1); }
      if (panes[1]) chart.priceScale('right', 1).applyOptions({ scaleMargins: { top: 0.2, bottom: 0.04 } });
    } catch { /* older API */ }
  }

  function applyLines(c) {
    for (const l of planLines) { try { candle.removePriceLine(l); } catch { /* ignore */ } }
    for (const l of levelLines) { try { candle.removePriceLine(l); } catch { /* ignore */ } }
    planLines = []; levelLines = [];
    if (st.tf === 'W') return;
    const last = ch.close[n - 1];
    if (st.levels) {
      const lv = (ch.levels || []).slice().sort((a, b) => b.strength - a.strength).slice(0, 6);
      const below = lv.filter(l => l.px < last).sort((a, b) => b.px - a.px)[0];
      const above = lv.filter(l => l.px > last).sort((a, b) => a.px - b.px)[0];
      for (const l of lv) {
        const sup = l.px < last;
        levelLines.push(candle.createPriceLine({ price: l.px, color: (sup ? c.up : c.down) + 'aa', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: l === below || l === above, title: sup ? 'S' : 'R' }));
      }
    }
    const idea = st.planIdea;
    if (st.plan && idea) {
      const e = idea.entry || {};
      const add = (price, color, title, w = 2, style = LineStyle.Solid) => planLines.push(candle.createPriceLine({ price, color, lineWidth: w, lineStyle: style, axisLabelVisible: true, title }));
      if (F.isNum(e.px)) add(e.px, c.ink, 'Entry', 2, LineStyle.LargeDashed);
      add(idea.stop, c.down, 'Stop');
      (idea.targets || []).forEach((t, i) => add(t.px, c.up, 'T' + (i + 1)));
    }
    // the scale may need to grow (or shrink) to fit the plan
    try { candle.applyOptions({ autoscaleInfoProvider: scaleProvider() }); } catch { /* ignore */ }
    if (markerApi) { try { markerApi.setMarkers([]); } catch { /* ignore */ } }
    if (st.markers && (ch.markers || []).length) {
      const mk = (ch.markers || []).filter(m => dates.includes(m.date)).sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-10)
        .map(m => ({ time: m.date, position: m.dir === 'long' ? 'belowBar' : 'aboveBar', color: m.dir === 'long' ? c.up : c.down, shape: m.dir === 'long' ? 'arrowUp' : 'arrowDown', text: '' }));
      if (markerApi) markerApi.setMarkers(mk); else markerApi = createSeriesMarkers(candle, mk);
    }
  }

  function setRange(r, apply = true) {
    st.range = r;
    if (!handle || st.tf === 'W') return;
    const chart = handle.chart;
    const bars = { '1M': 22, '3M': 63, '6M': 126, '1Y': 252, All: n }[r] || 126;
    const from = Math.max(0, n - bars);
    try { chart.timeScale().setVisibleLogicalRange({ from: from - 1, to: n + 4 }); } catch { chart.timeScale().fitContent(); }
    void apply;
  }

  build();
  return {
    state: st,
    setRange,
    setTimeframe(tf) { st.tf = tf; build(); },
    setOverlay(id, on) { if (on) st.overlays.add(id); else st.overlays.delete(id); applyOverlayVisibility(); },
    setPane(id) { st.pane = id; if (handle && st.tf === 'D') { applyPane(handle.chart, palette()); } },
    setPlan(idea) { st.planIdea = idea; if (handle) applyLines(palette()); },
    toggle(key, on) { st[key] = on; if (handle) applyLines(palette()); },
    height(px) { box.style.height = px + 'px'; },
    destroy() { if (handle) handle.destroy(); },
  };
}
