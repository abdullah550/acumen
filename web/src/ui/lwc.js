import { createChart, LineSeries, AreaSeries, CandlestickSeries, HistogramSeries, createSeriesMarkers, CrosshairMode, LineStyle } from 'lightweight-charts';
import * as F from '../lib/fmt.js';
import { isDark } from '../lib/store.js';

export { LineSeries, AreaSeries, CandlestickSeries, HistogramSeries, createSeriesMarkers, LineStyle };

export const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

export function palette() {
  return {
    text: cssVar('--ink-3'), ink: cssVar('--ink'), grid: cssVar('--grid'), axis: cssVar('--axis'), cross: cssVar('--crosshair'),
    up: cssVar('--up'), down: cssVar('--down'), accent: cssVar('--accent'), surface: cssVar('--surface'), line: cssVar('--line-strong'),
    s2: cssVar('--surface-2'), warn: cssVar('--warning'), font: cssVar('--font-sans'),
    mk: { psx: cssVar('--mk-psx'), fx: cssVar('--mk-fx'), crypto: cssVar('--mk-crypto') },
    // categorical series colours, fixed order (validated palette): blue, orange, aqua, yellow, magenta, violet
    cat: isDark() ? ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9'] : ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#4a3aa7'],
  };
}

export function chartOptions(c = palette()) {
  return {
    autoSize: true,
    layout: {
      background: { type: 'solid', color: 'rgba(0,0,0,0)' },
      textColor: c.text, fontFamily: c.font, fontSize: 11,
      attributionLogo: true,
      panes: { separatorColor: c.axis, separatorHoverColor: c.grid, enableResize: true },
    },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderColor: c.axis, scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: c.axis, timeVisible: false, rightOffset: 4, fixLeftEdge: false },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: c.cross, width: 1, style: LineStyle.Dashed, labelBackgroundColor: c.ink },
      horzLine: { color: c.cross, width: 1, style: LineStyle.Dashed, labelBackgroundColor: c.ink },
    },
    localization: { dateFormat: 'dd MMM yy' },
  };
}

// Re-colours every registered chart when the theme or palette changes.
const live = new Set();
function recolor() {
  const c = palette();
  for (const h of live) { try { h.recolor(c); } catch { /* chart already disposed */ } }
}
window.addEventListener('acumen:appearance', () => requestAnimationFrame(recolor));

export function makeChart(el, extra = {}) {
  const c = palette();
  const chart = createChart(el, { ...chartOptions(c), ...extra });
  const handle = { chart, recolor: () => {}, destroy() { live.delete(handle); try { chart.remove(); } catch { /* ignore */ } } };
  live.add(handle);
  return handle;
}

// A single-series line/area chart from [[date, value], ...]
export function lineChart(el, points, { area = true, color, precision = 2, height = 220, fixed = true } = {}) {
  el.style.height = height + 'px';
  const handle = makeChart(el);
  const c0 = palette();
  const data = points.filter(p => F.isNum(p[1])).map(p => ({ time: p[0], value: p[1] }));
  const colorFor = c => color ? (typeof color === 'function' ? color(c) : color) : c.accent;
  const opts = c => (area
    ? { lineColor: colorFor(c), topColor: colorFor(c) + '40', bottomColor: colorFor(c) + '00', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: 'price', precision, minMove: 1 / 10 ** precision } }
    : { color: colorFor(c), lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: 'price', precision, minMove: 1 / 10 ** precision } });
  const series = handle.chart.addSeries(area ? AreaSeries : LineSeries, opts(c0));
  series.setData(data);
  handle.chart.timeScale().fitContent();
  if (fixed) handle.chart.applyOptions({ handleScroll: false, handleScale: false });
  handle.recolor = c => { handle.chart.applyOptions(chartOptions(c)); series.applyOptions(opts(c)); };
  handle.series = series;
  return handle;
}
