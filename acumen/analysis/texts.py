"""Plain-language descriptions generated from computed numbers.

Every sentence is produced by a rule that checks the number it quotes, so the text can never claim
something the data does not show. ``L`` is a dict with the last-bar feature values (floats, NaN allowed).
"""

from __future__ import annotations

import math

import numpy as np


def ok(x) -> bool:
    return x is not None and isinstance(x, (int, float, np.floating, np.integer)) and math.isfinite(float(x))


def px_str(x: float) -> str:
    """Price with sensible precision for PKR equities, FX quotes and low-priced coins."""
    if not ok(x):
        return "n/a"
    a = abs(x)
    if a >= 1000:
        return f"{x:,.0f}"
    if a >= 100:
        return f"{x:,.1f}"
    if a >= 10:
        return f"{x:.2f}"
    if a >= 1:
        return f"{x:.3f}"
    return f"{x:.5g}"


def pct_str(x: float, nd: int = 1, sign: bool = True) -> str:
    if not ok(x):
        return "n/a"
    return f"{x:+.{nd}f}%" if sign else f"{x:.{nd}f}%"


def trend_label(score: float | None) -> str:
    if not ok(score):
        return "Unknown"
    return "Strong uptrend" if score >= 0.6 else "Uptrend" if score >= 0.25 else "Sideways" if score > -0.25 else "Downtrend" if score > -0.6 else "Strong downtrend"


def momentum_label(score: float | None) -> str:
    if not ok(score):
        return "Unknown"
    return "Strong" if score >= 0.6 else "Positive" if score >= 0.25 else "Neutral" if score > -0.25 else "Negative" if score > -0.6 else "Weak"


def volume_label(score: float | None) -> str:
    if not ok(score):
        return "Unknown"
    return "Accumulation" if score >= 0.35 else "Mild accumulation" if score >= 0.12 else "Neutral" if score > -0.12 else "Mild distribution" if score > -0.35 else "Distribution"


def vol_label(pct: float | None) -> str:
    if not ok(pct):
        return "Unknown"
    return "Low" if pct < 25 else "Normal" if pct < 70 else "High" if pct < 90 else "Extreme"


def adx_word(adx: float | None) -> str:
    if not ok(adx):
        return ""
    return "trending" if adx >= 25 else "building" if adx >= 20 else "weak trend"


def _sig(v: bool | None) -> str:
    return "neutral" if v is None else ("bullish" if v else "bearish")


def indicator_table(L: dict) -> list[dict]:
    """Rows of the indicator panel: name, value, signal (bullish / bearish / neutral / overbought / oversold)."""
    rows: list[dict] = []

    def add(name: str, value, signal: str, nd: int = 2):
        if ok(value):
            rows.append({"name": name, "value": round(float(value), nd), "signal": signal})

    rsi = L.get("rsi14")
    if ok(rsi):
        add("RSI(14)", rsi, "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "bullish" if rsi >= 55 else "bearish" if rsi <= 45 else "neutral", 1)
    if ok(L.get("macd_hist")):
        m, s, h = L.get("macd"), L.get("macd_sig"), L["macd_hist"]
        add("MACD(12,26,9) histogram", h, "bullish" if (ok(m) and ok(s) and m > s and h > 0) else "bearish" if (ok(m) and ok(s) and m < s and h < 0) else "neutral", 4)
    k, d = L.get("stoch_k"), L.get("stoch_d")
    if ok(k):
        add("Stochastic %K(14,3,3)", k, "overbought" if k >= 80 else "oversold" if k <= 20 else _sig(k > d) if ok(d) else "neutral", 1)
    if ok(L.get("adx")):
        pdi, mdi, a = L.get("pdi"), L.get("mdi"), L["adx"]
        add("ADX(14)", a, "neutral" if a < 20 or not (ok(pdi) and ok(mdi)) else _sig(pdi > mdi), 1)
    if ok(L.get("cci20")):
        c = L["cci20"]
        add("CCI(20)", c, "overbought" if c >= 150 else "oversold" if c <= -150 else "bullish" if c > 50 else "bearish" if c < -50 else "neutral", 1)
    if ok(L.get("mfi14")):
        m = L["mfi14"]
        add("MFI(14)", m, "overbought" if m >= 80 else "oversold" if m <= 20 else "bullish" if m > 55 else "bearish" if m < 45 else "neutral", 1)
    if ok(L.get("bb_pctb")):
        b = L["bb_pctb"]
        add("Bollinger %B(20,2)", b, "overbought" if b > 1 else "oversold" if b < 0 else "bullish" if b > 0.6 else "bearish" if b < 0.4 else "neutral", 2)
    if ok(L.get("st_dir")):
        add("SuperTrend(10,3)", L["st_dir"], _sig(L["st_dir"] > 0), 0)
    close = L.get("close")
    if ok(close) and ok(L.get("cloud_top")) and ok(L.get("cloud_bot")):
        pos = 1.0 if close > L["cloud_top"] else -1.0 if close < L["cloud_bot"] else 0.0
        add("Ichimoku cloud (price vs cloud)", pos, "bullish" if pos > 0 else "bearish" if pos < 0 else "neutral", 0)
    for n in (20, 50, 200):
        s = L.get(f"sma{n}")
        if ok(close) and ok(s) and s > 0:
            add(f"Price vs SMA{n} (%)", (close / s - 1) * 100, _sig(close > s), 1)
    if ok(L.get("ema20")) and ok(L.get("ema50")):
        add("EMA20 vs EMA50 (%)", (L["ema20"] / L["ema50"] - 1) * 100, _sig(L["ema20"] > L["ema50"]), 2)
    for n in (20, 60):
        v = L.get(f"roc{n}")
        if ok(v):
            add(f"Return {n} bars (%)", v, _sig(v > 0), 1)
    if ok(L.get("cmf20")):
        c = L["cmf20"]
        add("Chaikin money flow(20)", c, "bullish" if c > 0.05 else "bearish" if c < -0.05 else "neutral", 2)
    if ok(L.get("relvol")):
        add("Relative volume (x 20-day avg)", L["relvol"], "neutral", 2)
    if ok(L.get("atrp")):
        add("ATR(14) as % of price", L["atrp"], "neutral", 2)
    if ok(L.get("prox52")):
        add("Close vs 52-week high (%)", (L["prox52"] - 1) * 100, "bullish" if L["prox52"] >= 0.9 else "bearish" if L["prox52"] <= 0.6 else "neutral", 1)
    return rows


def technical_block(L: dict, scores: dict, levels: dict, patterns: list[dict]) -> dict:
    """The ``technical`` section of the instrument detail."""
    close = L.get("close")
    tr_pts, mo_pts, vo_pts, vl_pts = [], [], [], []
    # trend
    for n in (50, 200):
        s = L.get(f"sma{n}")
        if ok(close) and ok(s):
            tr_pts.append(f"Close {'above' if close > s else 'below'} the {n}-day average ({pct_str((close / s - 1) * 100)})")
    if ok(L.get("sma50")) and ok(L.get("sma200")):
        tr_pts.append("50-day average is " + ("above" if L["sma50"] > L["sma200"] else "below") + " the 200-day average")
    if ok(L.get("adx")):
        tr_pts.append(f"ADX {L['adx']:.0f} ({adx_word(L['adx'])})")
    if ok(L.get("st_dir")):
        tr_pts.append("SuperTrend is " + ("bullish" if L["st_dir"] > 0 else "bearish"))
    if ok(L.get("wk_trend")):
        tr_pts.append("Weekly trend is " + ("up" if L["wk_trend"] > 0 else "down" if L["wk_trend"] < 0 else "mixed"))
    # momentum
    if ok(L.get("rsi14")):
        r = L["rsi14"]
        mo_pts.append(f"RSI {r:.0f}" + (" (overbought)" if r >= 70 else " (oversold)" if r <= 30 else ""))
    if ok(L.get("macd_hist")):
        mo_pts.append("MACD histogram " + ("positive" if L["macd_hist"] > 0 else "negative") + (" and rising" if ok(L.get("macd_hist_chg")) and L["macd_hist_chg"] > 0 else " and falling" if ok(L.get("macd_hist_chg")) and L["macd_hist_chg"] < 0 else ""))
    for n, lab in ((20, "1-month"), (60, "3-month"), (120, "6-month")):
        if ok(L.get(f"roc{n}")):
            mo_pts.append(f"{lab} return {pct_str(L[f'roc{n}'])}")
    # volatility
    if ok(L.get("atrp")):
        vo_pts.append(f"ATR is {L['atrp']:.1f}% of price")
    if ok(L.get("atrp_pct")):
        vo_pts.append(f"Volatility is at the {L['atrp_pct']:.0f}th percentile of the past year")
    if ok(L.get("bbw_pct")):
        vo_pts.append(f"Bollinger width is at the {L['bbw_pct']:.0f}th percentile")
    if L.get("squeeze"):
        vo_pts.append("Volatility squeeze in progress (bands inside the Keltner channel)")
    # volume
    if ok(L.get("cmf20")):
        vl_pts.append(f"Chaikin money flow {L['cmf20']:+.2f}")
    if ok(L.get("updown_vol")):
        vl_pts.append(f"{L['updown_vol'] * 100:.0f}% of the last 10 sessions' volume traded on up days")
    if ok(L.get("relvol")):
        vl_pts.append(f"Today's volume is {L['relvol']:.1f}x the 20-day average")

    t_score, m_score, v_score = scores.get("trend"), scores.get("momentum"), scores.get("volume")
    vol_pct = L.get("atrp_pct")
    summary = []
    summary.append(f"{trend_label(t_score)}" + (f" (ADX {L['adx']:.0f}, {adx_word(L['adx'])})" if ok(L.get("adx")) else ""))
    summary.append(f"{momentum_label(m_score).lower()} momentum")
    if ok(L.get("sma50")) and ok(close):
        summary.append(f"price {pct_str((close / L['sma50'] - 1) * 100)} vs the 50-day average")
    text = "; ".join(summary) + "."
    text = text[0].upper() + text[1:]
    if ok(vol_pct):
        text += f" Volatility is {vol_label(vol_pct).lower()}."
    sup, res = levels.get("support"), levels.get("resistance")
    if sup is not None and res is not None:
        text += f" Nearest support {px_str(sup)}, resistance {px_str(res)}."
    elif sup is not None:
        text += f" Nearest support {px_str(sup)}."
    elif res is not None:
        text += f" Nearest resistance {px_str(res)}."
    return {
        "summary": text,
        "trend": {"label": trend_label(t_score), "score": None if not ok(t_score) else round(float(t_score), 2), "points": tr_pts},
        "momentum": {"label": momentum_label(m_score), "score": None if not ok(m_score) else round(float(m_score), 2), "points": mo_pts},
        "volatility": {"label": vol_label(vol_pct), "score": None if not ok(vol_pct) else round((float(vol_pct) - 50.0) / 50.0, 2), "points": vo_pts},
        "volume": {"label": volume_label(v_score), "score": None if not ok(v_score) else round(float(v_score), 2), "points": vl_pts},
        "indicators": indicator_table(L),
        "patterns": patterns,
    }


def reasons(L: dict, fac: dict, sign: int, setup: str, horizon: str, bench_name: str | None, support: float | None, resistance: float | None, pattern: str | None, limit: int = 5) -> list[str]:
    """Why the signal exists, in order of importance. Each line quotes a number that was actually computed."""
    out: list[str] = []
    up = sign > 0
    close = L.get("close")
    w_up, w_dn = ("above", "below") if up else ("below", "above")

    def add(s: str):
        if s and s not in out:
            out.append(s)

    # 1. the trigger
    if setup in ("Breakout", "Breakdown"):
        n = 55 if (horizon != "short") and (ok(L.get("ref_hi55")) and ((up and close > L["ref_hi55"]) or (not up and close < L["ref_lo55"]))) else 20
        add(f"Closed {w_up} its {n}-day {'high' if up else 'low'}" + (f" on {L['relvol']:.1f}x average volume" if ok(L.get("relvol")) else ""))
    elif setup == "Squeeze release":
        add(f"Volatility squeeze released {'upward' if up else 'downward'}: price pushed outside the Bollinger band after a tight range")
    elif setup in ("Trend pullback", "Trend rally fade"):
        add(f"{'Pullback inside an uptrend' if up else 'Bounce inside a downtrend'}: RSI {L.get('rsi14', float('nan')):.0f}, price within one ATR of the 20-day average" if ok(L.get("rsi14")) else "Pullback toward the 20-day average inside the prevailing trend")
    elif setup in ("Oversold bounce", "Overbought fade"):
        if ok(L.get("rsi2")):
            add(f"Short-term {'oversold' if up else 'overbought'}: 2-day RSI {L['rsi2']:.0f}" + (f", %B {L['bb_pctb']:.2f}" if ok(L.get("bb_pctb")) else ""))
    elif setup == "Momentum continuation":
        if ok(L.get("roc60")):
            add(f"Momentum is {'rising' if up else 'falling'}: {pct_str(L['roc60'])} over 3 months with the trend intact")
    elif setup == "Reversal candle at extreme":
        add((pattern or "Reversal candle") + f" with RSI {L.get('rsi14', float('nan')):.0f}" if ok(L.get("rsi14")) else (pattern or "Reversal candle"))
    # 2. trend alignment
    if ok(close) and ok(L.get("sma50")) and ok(L.get("sma200")):
        c50, c200 = close > L["sma50"], close > L["sma200"]
        if up and c50 and c200:
            add("Price is above both the 50-day and 200-day averages" + (" and the 50-day is above the 200-day" if L["sma50"] > L["sma200"] else ""))
        elif not up and (not c50) and (not c200):
            add("Price is below both the 50-day and 200-day averages" + (" and the 50-day is below the 200-day" if L["sma50"] < L["sma200"] else ""))
    if ok(L.get("wk_trend")) and L["wk_trend"] * sign > 0:
        add(f"Weekly trend agrees ({'up' if up else 'down'})")
    # 3. momentum and relative strength
    if horizon == "long" and ok(L.get("mom_12_1")) and L["mom_12_1"] * sign > 10:
        add(f"12-month momentum (skipping the last month) {pct_str(L['mom_12_1'], 0)}")
    elif ok(L.get("roc60")) and L["roc60"] * sign > 5:
        add(f"3-month return {pct_str(L['roc60'], 0)}")
    if bench_name and ok(L.get("rs_roc60")) and L["rs_roc60"] * sign > 0.03:
        add(f"{'Outperforming' if up else 'Underperforming'} {bench_name} over 3 months ({pct_str(L['rs_roc60'] * 100, 0)} relative)")
    # 4. volume
    if ok(L.get("cmf20")) and L["cmf20"] * sign > 0.08:
        add(f"{'Accumulation' if up else 'Distribution'}: Chaikin money flow {L['cmf20']:+.2f}")
    # 5. structure
    if up and ok(L.get("prox52")) and L["prox52"] >= 0.95:
        add(f"Within {(1 - L['prox52']) * 100:.0f}% of the 52-week high")
    if not up and ok(L.get("prox52")) and L["prox52"] <= 0.6:
        add(f"{(1 - L['prox52']) * 100:.0f}% below the 52-week high")
    if up and support is not None and ok(close) and 0 < (close - support) <= 2.0 * L.get("atr", float("inf")):
        add(f"Sitting just above support at {px_str(support)}")
    if (not up) and resistance is not None and ok(close) and 0 < (resistance - close) <= 2.0 * L.get("atr", float("inf")):
        add(f"Sitting just below resistance at {px_str(resistance)}")
    return out[:limit]


def factor_details(L: dict, horizon: str, bench_name: str | None) -> dict[str, str]:
    """One short line per factor describing the numbers behind it (empty string when nothing to say)."""
    out: dict[str, str] = {}
    close = L.get("close")
    t = []
    for n in (20, 50, 200):
        s = L.get(f"sma{n}")
        if ok(close) and ok(s):
            t.append(f"{'above' if close > s else 'below'} SMA{n}")
    if ok(L.get("adx")):
        t.append(f"ADX {L['adx']:.0f}")
    out["trend"] = ", ".join(t).capitalize() if t else ""
    m = []
    if ok(L.get("rsi14")):
        m.append(f"RSI {L['rsi14']:.0f}")
    if ok(L.get("macd_hist")):
        m.append("MACD hist " + ("+" if L["macd_hist"] > 0 else "-"))
    n_roc = {"short": 10, "medium": 60, "long": 120}[horizon]
    if ok(L.get(f"roc{n_roc}")):
        m.append(f"{n_roc}-bar return {pct_str(L[f'roc{n_roc}'])}")
    out["momentum"] = ", ".join(m)
    r = []
    if ok(L.get("rsi2")):
        r.append(f"RSI(2) {L['rsi2']:.0f}")
    if ok(L.get("bb_pctb")):
        r.append(f"%B {L['bb_pctb']:.2f}")
    if ok(L.get("z20")):
        r.append(f"z-score {L['z20']:+.1f}")
    out["reversion"] = ", ".join(r) if horizon == "short" else "Not used at this horizon"
    s = []
    key = {"short": "pos20", "medium": "pos55"}.get(horizon)
    if key and ok(L.get(key)):
        s.append(f"{L[key] * 100:.0f}% of the {20 if key == 'pos20' else 55}-bar range")
    if ok(L.get("prox52")):
        s.append(f"{(1 - L['prox52']) * 100:.0f}% below the 52-week high")
    out["structure"] = ", ".join(s)
    v = []
    if ok(L.get("cmf20")):
        v.append(f"CMF {L['cmf20']:+.2f}")
    if ok(L.get("updown_vol")):
        v.append(f"{L['updown_vol'] * 100:.0f}% up-day volume")
    if ok(L.get("mfi14")):
        v.append(f"MFI {L['mfi14']:.0f}")
    out["volume"] = ", ".join(v)
    out["pattern"] = ("Candle score %+.2f" % L["candle"]) if (horizon == "short" and ok(L.get("candle"))) else "Not used at this horizon"
    if bench_name and ok(L.get("rs_roc60")):
        out["rs"] = f"{pct_str(L['rs_roc60'] * 100, 1)} vs {bench_name} over 3 months"
    else:
        out["rs"] = "No benchmark"
    return out
