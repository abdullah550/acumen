"""Everything computed for one instrument from its own bars (pass 1 of the engine).

The result is a ``Snapshot``: the latest signal per horizon with its plan, the instrument's own backtest
per horizon, the screener row, and the static parts of the detail page. Pass 2 (``ideas.py``) adds what
needs other instruments: pooled calibration, fundamentals, peers.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Config
from ..model import Instrument
from . import indicators as ind
from . import regime as reg
from . import stats as st
from . import texts as tx
from .features import compute_features
from .htf import resample_ohlcv
from .levels import nearest_levels, support_resistance
from .patterns import detect_all, recent_patterns
from .signals import horizon_signals
from .simulate import PlanLevels, equity_curve, plan_levels, run_strategy, trade_stats, trades_frame

HORIZONS = ("short", "medium", "long")
CAL_DAYS = {"psx": 7.0 / 5.0, "fx": 7.0 / 5.0, "crypto": 1.0}  # calendar days per daily bar
PERIODS = {"psx": 252, "fx": 252, "crypto": 365}


@dataclass
class HorizonResult:
    horizon: str
    comp: float | None = None
    conv: float | None = None
    dir: int = 0  # tradable direction on the last bar (+1 / -1 / 0)
    raw_dir: int = 0  # direction before market rules (PSX shorts)
    setup: str | None = None
    factors: dict = field(default_factory=dict)
    plan: PlanLevels | None = None
    stats: dict = field(default_factory=dict)
    equity: list = field(default_factory=list)
    baseline: pd.DataFrame | None = None  # random-entry trades with the same exits (columns entry_date, r, r_gross) for the edge-vs-random check
    trades: pd.DataFrame | None = None
    recent_trades: list = field(default_factory=list)


@dataclass
class Snapshot:
    id: str
    market: str
    sym: str
    name: str
    sector: str | None
    kind: str
    tradable: bool
    asof: str
    last: float
    n_bars: int
    enough_history: bool
    atr: float | None
    row: dict
    horizons: dict
    last_feat: dict
    detail: dict
    flags: list
    liquidity: dict
    returns: pd.Series | None
    levels: dict
    pattern_name: str | None = None
    approx_share: float = 0.0


def cost_fraction(inst: Instrument, cfg: Config) -> float:
    c = cfg.get_path("costs", {})
    if inst.market == "psx":
        return float(c.get("psx", 0.006))
    if inst.market == "crypto":
        return float(c.get("crypto", 0.0025))
    group = (inst.meta or {}).get("group", "minor")
    return float(c.get("fx", {}).get(group, 0.0003))


def liquidity_tier(market: str, adv_value: float | None, group: str | None = None) -> str:
    if market == "fx":
        return {"major": "A", "metal": "A", "minor": "B", "exotic": "C", "pkr": "D"}.get(group or "minor", "B")
    if adv_value is None or not math.isfinite(adv_value):
        return "D"
    a, b, c = (1e8, 2.5e7, 5e6) if market == "psx" else (1e8, 2e7, 2e6)
    return "A" if adv_value >= a else "B" if adv_value >= b else "C" if adv_value >= c else "D"


def _dec(ref_price: float) -> int:
    if not (ref_price and math.isfinite(ref_price) and ref_price > 0):
        return 4
    return int(max(2, min(10, 5 - math.floor(math.log10(ref_price)))))


def arr(x, dec: int) -> list:
    """Series/array to a JSON-safe list (NaN -> None)."""
    a = np.asarray(x, dtype=float)
    out = np.round(a, dec).tolist()
    return [None if (v is None or (isinstance(v, float) and not math.isfinite(v))) else v for v in out]


def _f(x, nd: int = 2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _pct_days(close: pd.Series, days: int) -> float | None:
    """Percent change from the last bar on or before ``last_date - days`` to the last bar."""
    if len(close) < 2:
        return None
    last_date = close.index[-1]
    cut = last_date - pd.Timedelta(days=days)
    if close.index[0] > cut:
        return None
    base = close.loc[:cut]
    if len(base) == 0:
        return None
    b = float(base.iloc[-1])
    return (float(close.iloc[-1]) / b - 1.0) * 100.0 if b > 0 else None


def _ytd(close: pd.Series) -> float | None:
    last = close.index[-1]
    prev = close.loc[: pd.Timestamp(year=last.year - 1, month=12, day=31)]
    if len(prev) == 0:
        return None
    return (float(close.iloc[-1]) / float(prev.iloc[-1]) - 1.0) * 100.0


def analyse_instrument(
    inst: Instrument,
    bars: pd.DataFrame,
    bench: pd.Series | None,
    tilt: pd.Series | None,
    cfg: Config,
    *,
    bench_name: str | None = None,
    usd_pkr: float = 283.0,
) -> Snapshot | None:
    """Pass 1 for one instrument. Returns ``None`` when there is too little data to say anything."""
    scfg = cfg["signals"]
    out_cfg = cfg["output"]
    max_hist = int(cfg.get_path("backtest.max_history_bars", 1500))
    bars = bars.iloc[-max_hist:]
    n = len(bars)
    if n < 30:
        return None
    df = bars[["open", "high", "low", "close", "volume"]].astype(float)
    approx = bars["approx_hl"].to_numpy(dtype=bool) if "approx_hl" in bars.columns else np.zeros(n, dtype=bool)
    close = df["close"]
    market = inst.market
    bench_c = bench if (bench is not None and bench_name) else None
    f = compute_features(df, bench_c)
    enough = n >= int(scfg["min_bars"])
    tilt_a = (tilt.reindex(df.index, method="ffill").fillna(0.0) if tilt is not None else pd.Series(0.0, index=df.index))
    allow_short = not (market == "psx" and not scfg.get("psx_allow_short", False))
    cost = cost_fraction(inst, cfg)
    cal_days = CAL_DAYS[market]
    lane_count = max(1, int(cfg.get_path("backtest.baseline_lanes", 4)))

    # extra last-value indicators
    cci20 = ind.cci(df["high"], df["low"], close, 20)
    macd_hist_chg = f["macd_hist"].diff()
    atrp_pct = ind.percentile_rank(f["atrp"], 252) if n >= 120 else pd.Series(np.nan, index=df.index)
    bbw_pct = ind.percentile_rank(f["bb_bw"], 252) if n >= 120 else pd.Series(np.nan, index=df.index)
    L = {k: (float(v) if pd.notna(v) else float("nan")) for k, v in f.iloc[-1].items() if not isinstance(v, (str, bool)) or True}
    L["cci20"] = float(cci20.iloc[-1]) if pd.notna(cci20.iloc[-1]) else float("nan")
    L["macd_hist_chg"] = float(macd_hist_chg.iloc[-1]) if pd.notna(macd_hist_chg.iloc[-1]) else float("nan")
    L["atrp_pct"] = float(atrp_pct.iloc[-1]) if pd.notna(atrp_pct.iloc[-1]) else float("nan")
    L["bbw_pct"] = float(bbw_pct.iloc[-1]) if pd.notna(bbw_pct.iloc[-1]) else float("nan")
    L["squeeze"] = bool(f["squeeze"].iloc[-1]) if pd.notna(f["squeeze"].iloc[-1]) else False
    last_px = float(close.iloc[-1])
    atr_last = float(f["atr"].iloc[-1]) if pd.notna(f["atr"].iloc[-1]) else None

    # levels and patterns
    lv = support_resistance(df) if n >= 60 else []
    below, above = nearest_levels(lv, last_px, 1)
    sup = below[0].px if below else None
    res = above[0].px if above else None
    pats = recent_patterns(df, 60) if n >= 30 else []
    last_pat = next((p for p in pats if p["dir"] is not None and p["date"] >= (df.index[-1] - pd.Timedelta(days=5)).strftime("%Y-%m-%d")), None)

    # horizons
    horizons: dict[str, HorizonResult] = {}
    sigs = {}
    if enough:
        for h in HORIZONS:
            hs = horizon_signals(f, tilt_a, h, scfg, allow_short=allow_short)
            sigs[h] = hs
            comp_v = hs.comp.iloc[-1]
            hr = HorizonResult(h)
            if pd.notna(comp_v):
                hr.comp = float(comp_v)
                hr.conv = float(hs.conv.iloc[-1])
                hr.dir = int(hs.dir[-1])
                hr.raw_dir = int(hs.raw_dir[-1])
                hr.setup = str(hs.setup[-1])
                hr.factors = {k: (None if pd.isna(v) else float(v)) for k, v in hs.fac.iloc[-1].items()}
                d_plan = hr.dir if hr.dir != 0 else hr.raw_dir
                if d_plan != 0 and atr_last:
                    sw = f["sw_lo"].iloc[-1] if d_plan > 0 else f["sw_hi"].iloc[-1]
                    hr.plan = plan_levels(last_px, atr_last, float(sw) if pd.notna(sw) else None, d_plan, hr.setup, cfg["plan"][h])
            # own backtest
            valid = np.isfinite(hs.comp.to_numpy()) & np.isfinite(f["atr"].to_numpy())
            start = int(np.argmax(valid)) if valid.any() else n
            tr = run_strategy(
                df.index, df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), close.to_numpy(), approx,
                hs.dir, hs.setup, f["atr"].to_numpy(), f["sw_lo"].to_numpy(), f["sw_hi"].to_numpy(),
                hs.conv.to_numpy(), hs.comp.to_numpy(), cfg["plan"][h],
                time_stop=int(cfg["horizons"][h]["time_stop_bars"]), cooldown=int(scfg["cooldown_bars"][h]),
                cost_frac=cost, start=start,
            )
            tdf = trades_frame(tr)
            hr.trades = tdf
            # same exits, random entries: how much of the result is just the market drifting? Several independent streams of
            # random entries ("lanes"), so that a plan trade has enough of them around its day to be compared with
            lanes = []
            for lane in range(lane_count):
                rng = np.random.default_rng(zlib.crc32(f"{inst.id}|{h}|{lane}".encode()))
                pick = rng.random(n) < 0.10
                rdir = np.ones(n, dtype=int) if not allow_short else rng.choice([-1, 1], n)
                btr = run_strategy(
                    df.index, df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), close.to_numpy(), approx,
                    np.where(pick, rdir, 0), np.full(n, "Trend following", dtype=object), f["atr"].to_numpy(), f["sw_lo"].to_numpy(), f["sw_hi"].to_numpy(),
                    np.zeros(n), np.zeros(n), cfg["plan"][h], time_stop=int(cfg["horizons"][h]["time_stop_bars"]), cooldown=int(scfg["cooldown_bars"][h]),
                    cost_frac=cost, start=start,
                )
                bdf = trades_frame(btr)
                if len(bdf):
                    lanes.append(bdf[["entry_date", "sign", "r", "r_gross"]])
            hr.baseline = pd.concat(lanes, ignore_index=True) if lanes else None
            hr.stats = trade_stats(tdf, float(cfg.get_path("backtest.oos_fraction", 0.4)), int(cfg.get_path("backtest.min_trades_reliable", 20)))
            hr.equity = equity_curve(tdf)
            dec = _dec(last_px)
            hr.recent_trades = [
                {"entry_date": pd.Timestamp(r.entry_date).strftime("%Y-%m-%d"), "exit_date": pd.Timestamp(r.exit_date).strftime("%Y-%m-%d"),
                 "dir": "long" if r.sign > 0 else "short", "entry": round(float(r.fill), dec), "exit": round(float(r.exit), dec),
                 "r": round(float(r.r), 2), "bars": int(r.bars), "reason": str(r.reason), "setup": str(r.setup)}
                for r in tdf.iloc[-30:].itertuples()
            ]
            horizons[h] = hr
    else:
        for h in HORIZONS:
            horizons[h] = HorizonResult(h, trades=trades_frame([]), stats=trade_stats(None))

    # screener row
    ret = close.pct_change().replace([np.inf, -np.inf], np.nan)
    rets1y = ret.iloc[-PERIODS[market]:]
    adv_val = None
    if market != "fx":
        val = (close * df["volume"]).iloc[-20:]
        if val.notna().sum() >= 5 and (df["volume"].iloc[-20:] > 0).any():
            adv_val = float(val.median())
    group = (inst.meta or {}).get("group")
    tier = liquidity_tier(market, adv_val, group)
    bench_ret = bench_c.pct_change() if bench_c is not None else None
    beta = st.beta(rets1y, bench_ret.reindex(rets1y.index)) if bench_ret is not None and n >= 100 else float("nan")
    corr = float(rets1y.corr(bench_ret.reindex(rets1y.index))) if bench_ret is not None and n >= 100 else float("nan")
    close1y = close.iloc[-PERIODS[market]:]
    mdd, _, _ = st.max_drawdown(close1y) if len(close1y) > 20 else (float("nan"), None, None)
    rf = float(cfg.get_path("valuation.risk_free_pkr", 0.11)) if market == "psx" else 0.0
    sharpe1y = st.sharpe(rets1y, rf, PERIODS[market]) if len(rets1y) > 60 else float("nan")
    sortino1y = st.sortino(rets1y, rf, PERIODS[market]) if len(rets1y) > 60 else float("nan")
    gap_thr = {"psx": 2.0, "crypto": 3.0, "fx": 0.4}[market]
    gapf = st.gap_frequency(df["open"].iloc[-PERIODS[market]:], close1y, gap_thr) if len(close1y) > 30 else float("nan")
    var95 = st.var_hist(rets1y, 0.95) if len(rets1y) > 60 else float("nan")
    cvar95 = st.cvar_hist(rets1y, 0.95) if len(rets1y) > 60 else float("nan")
    vol_ann = st.annualised_vol(rets1y, PERIODS[market]) if len(rets1y) > 30 else float("nan")
    dd_dev = st.downside_deviation(rets1y, PERIODS[market]) if len(rets1y) > 30 else float("nan")
    approx_share = float(approx[-120:].mean()) if n else 0.0
    flags: list[str] = []
    if approx_share > 0.5:
        flags.append("approx_hl")
    if not enough:
        flags.append("new")
    if tier in ("C", "D") and market != "fx":
        flags.append("low_liq")
    if not inst.tradable or inst.kind == "index":
        flags.append("not_tradable")
    hi252, lo252 = L.get("hi252"), L.get("lo252")
    spark_src = close.iloc[-30:]
    spark = [round(float(v / spark_src.iloc[0] - 1.0), 4) for v in spark_src] if len(spark_src) >= 2 else []
    last_vol = float(df["volume"].iloc[-1])
    row = {
        "id": inst.id, "m": market, "sym": inst.symbol, "name": inst.name, "sec": inst.sector, "kind": inst.kind,
        "tradable": bool(inst.tradable and inst.kind != "index"),
        "px": _f(last_px, _dec(last_px)), "asof": df.index[-1].strftime("%Y-%m-%d"),
        "chg1d": _f((close.iloc[-1] / close.iloc[-2] - 1) * 100) if n >= 2 else None,
        "chg1w": _f(_pct_days(close, 7)), "chg1m": _f(_pct_days(close, 30)), "chg3m": _f(_pct_days(close, 91)),
        "chg6m": _f(_pct_days(close, 182)), "chg1y": _f(_pct_days(close, 365)), "ytd": _f(_ytd(close)),
        "vol": _f(last_vol, 0) if market != "fx" else None, "adv": _f(adv_val, 0), "liq": tier,
        "rsi": _f(L.get("rsi14"), 1), "adx": _f(L.get("adx"), 1), "atrp": _f(L.get("atrp")),
        "bbw": _f(L.get("bb_bw") * 100 if math.isfinite(L.get("bb_bw", float("nan"))) else None),
        "d50": _f((last_px / L["sma50"] - 1) * 100) if math.isfinite(L.get("sma50", float("nan"))) else None,
        "d200": _f((last_px / L["sma200"] - 1) * 100) if math.isfinite(L.get("sma200", float("nan"))) else None,
        "above50": bool(last_px > L["sma50"]) if math.isfinite(L.get("sma50", float("nan"))) else None,
        "above200": bool(last_px > L["sma200"]) if math.isfinite(L.get("sma200", float("nan"))) else None,
        "hi52d": _f((last_px / hi252 - 1) * 100) if hi252 and math.isfinite(hi252) else None,
        "lo52d": _f((last_px / lo252 - 1) * 100) if lo252 and math.isfinite(lo252) else None,
        "rvol": _f(L.get("relvol")), "vol30": _f(ind.hist_vol(close, 30, PERIODS[market]).iloc[-1]) if n >= 35 else None,
        "mdd1y": _f(mdd), "sharpe1y": _f(sharpe1y), "beta": _f(beta),
        "pe": None, "pb": None, "dy": None, "roe": None, "eps": None, "mcap": None, "fv": None, "upside": None,
        "sig": {h: {"dir": None, "conv": None, "grade": None, "score": None} for h in HORIZONS},
        "best": None, "spark": spark, "bars": int(n), "flags": flags,
    }

    # detail (static part)
    dec = _dec(last_px)
    tail = int(out_cfg.get("bars_in_detail", 320))
    sl = slice(-tail, None)
    ft = f.iloc[sl]
    obv = ind.obv(close, df["volume"]).iloc[sl] if (df["volume"] > 0).any() else pd.Series(np.nan, index=ft.index)
    has_vol = bool((df["volume"] > 0).any())
    don_hi = df["high"].rolling(20, min_periods=20).max().iloc[sl]
    don_lo = df["low"].rolling(20, min_periods=20).min().iloc[sl]
    st_line = ft["st_line"]
    wk = resample_ohlcv(df, "W-FRI", closed_only=False)
    wk = wk.iloc[-104:]
    wk_close = resample_ohlcv(df, "W-FRI", closed_only=False)["close"]
    w10, w40 = ind.sma(wk_close, 10).iloc[-104:], ind.sma(wk_close, 40).iloc[-104:]
    chart = {
        "dates": [d.strftime("%Y-%m-%d") for d in ft.index],
        "open": arr(ft["open"], dec), "high": arr(ft["high"], dec), "low": arr(ft["low"], dec), "close": arr(ft["close"], dec),
        "volume": arr(ft["volume"], 0) if has_vol else [None] * len(ft),
        "overlays": {
            "sma20": arr(ft["sma20"], dec), "sma50": arr(ft["sma50"], dec), "sma200": arr(ft["sma200"], dec), "ema20": arr(ft["ema20"], dec),
            "bb_upper": arr(ft["bb_up"], dec), "bb_lower": arr(ft["bb_lo"], dec), "supertrend": arr(st_line, dec),
            "donchian_high": arr(don_hi, dec), "donchian_low": arr(don_lo, dec),
            "ichimoku_a": arr(ft["cloud_top"], dec), "ichimoku_b": arr(ft["cloud_bot"], dec),
        },
        "panes": {
            "rsi": arr(ft["rsi14"], 1), "macd": arr(ft["macd"], dec + 1), "macd_signal": arr(ft["macd_sig"], dec + 1), "macd_hist": arr(ft["macd_hist"], dec + 1),
            "stoch_k": arr(ft["stoch_k"], 1), "stoch_d": arr(ft["stoch_d"], 1), "adx": arr(ft["adx"], 1), "plus_di": arr(ft["pdi"], 1), "minus_di": arr(ft["mdi"], 1),
            "atr": arr(ft["atr"], dec + 1), "obv": arr(obv, 0), "cmf": arr(ft["cmf20"], 3) if has_vol else [None] * len(ft), "mfi": arr(ft["mfi14"], 1) if has_vol else [None] * len(ft),
        },
        "levels": [lvl.to_dict() for lvl in sorted([x for x in lv if x.strength >= 0.25], key=lambda x: abs(x.px - last_px))[:8]],
        "markers": [{"date": p["date"], "kind": "pattern", "label": p["name"], "dir": p["dir"]} for p in pats if p["dir"] is not None and p["date"] >= ft.index[0].strftime("%Y-%m-%d")][:14],
        "weekly": {"dates": [d.strftime("%Y-%m-%d") for d in wk.index], "close": arr(wk["close"], dec), "sma10": arr(w10, dec), "sma40": arr(w40, dec)},
    }
    fac_med = horizons["medium"].factors if horizons["medium"].factors else {}
    tech = tx.technical_block(
        L, {"trend": fac_med.get("trend"), "momentum": fac_med.get("momentum"), "volume": fac_med.get("volume")},
        {"support": sup, "resistance": res}, [{"date": p["date"], "name": p["name"], "dir": p["dir"]} for p in pats[:8]],
    )
    risk = {
        "vol_ann": _f(vol_ann), "beta": _f(beta), "beta_vs": bench_name, "var95_1d": _f(var95), "cvar95_1d": _f(cvar95),
        "max_dd_1y": _f(mdd), "downside_dev": _f(dd_dev), "sharpe_1y": _f(sharpe1y), "sortino_1y": _f(sortino1y),
        "gap_freq": _f(gapf), "corr_benchmark": _f(corr),
        "liquidity": {"adv_value": _f(adv_val, 0), "tier": tier, "days_to_exit_1pct_equity": None},
    }
    detail = {
        "chart": chart, "technical": tech, "risk": risk,
        "backtest": {h: {"stats": horizons[h].stats, "oos": horizons[h].stats.get("oos"), "equity": horizons[h].equity, "trades": horizons[h].recent_trades} for h in HORIZONS},
        "notes": ([f"High and low prices are approximated from open and close for {approx_share * 100:.0f}% of the last 120 sessions; ATR and stops may be understated."] if approx_share > 0.3 else [])
        + (["Less than %d sessions of history: no trade ideas are produced." % int(scfg["min_bars"])] if not enough else []),
    }
    return Snapshot(
        id=inst.id, market=market, sym=inst.symbol, name=inst.name, sector=inst.sector, kind=inst.kind,
        tradable=bool(inst.tradable and inst.kind != "index"), asof=df.index[-1].strftime("%Y-%m-%d"), last=last_px,
        n_bars=n, enough_history=enough, atr=atr_last, row=row, horizons=horizons, last_feat=L, detail=detail, flags=flags,
        liquidity={"adv_value": adv_val, "tier": tier}, returns=ret.iloc[-520:].astype("float32"),
        levels={"support": sup, "resistance": res, "all": lv}, pattern_name=(last_pat or {}).get("name"), approx_share=approx_share,
    )
