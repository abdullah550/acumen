"""Synthetic markets for tests and for the preview. NOTHING here is real market data.

* PSX issuers are fictional ("Demo ... Ltd", symbols DEMOA, DEMOB, ...); the index is "Demo 100".
* Crypto coins are fictional ("Demo Coin 01", symbols DMC01 ...).
* Forex pairs use real currency codes because the pair names are just combinations of them, but every price
  path is generated here and the levels are only roughly realistic.
* Prices contain slowly varying drift on purpose so the engine has structure to find in stocks and forex. The coins are
  plain noise around a shared market factor on purpose: the app then has a market where nothing beats random entries, and shows
  what it does about that. Backtest numbers, probabilities and "edges" produced from this data say nothing about real markets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .model import Instrument, MarketData, SourceStatus, make_id

SECTORS = [
    ("Commercial Banks", "Bank", 6.0, 0.9), ("Cement", "Cement", 8.0, 1.1), ("Oil & Gas Exploration Companies", "Petroleum", 5.5, 1.0),
    ("Power Generation & Distribution", "Power", 4.5, 0.8), ("Fertilizer", "Fertilizer", 6.5, 0.9), ("Technology & Communication", "Systems", 14.0, 1.3),
    ("Textile Composite", "Textile", 7.0, 1.1), ("Pharmaceuticals", "Pharma", 11.0, 0.7), ("Automobile Assemblers", "Motors", 10.0, 1.2),
    ("Food & Personal Care Products", "Foods", 12.0, 0.8),
]
GREEK = ["Alpha", "Beta", "Gamma", "Delta", "Sigma", "Omega", "Zeta"]


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _ar_drift(rng, n: int, phi: float, sd: float) -> np.ndarray:
    mu = np.zeros(n)
    z = rng.normal(0, sd * np.sqrt(1 - phi**2), n)
    for i in range(1, n):
        mu[i] = phi * mu[i - 1] + z[i]
    return mu


def _no_drift(rng, n: int, phi: float, sd: float) -> np.ndarray:
    """What ``_ar_drift`` is replaced by in a world without structure."""
    return np.zeros(n)


BRIDGE_STEPS = 16


def _bars(rng, rets: np.ndarray, idx: pd.DatetimeIndex, base: float, vol_level: float, vol_base: float | None, jitter: float = 0.7, gap: float = 0.2) -> pd.DataFrame:
    """Daily bars whose close follows ``rets`` and whose open, high and low come from a price path that really ends at the close.

    Part of each day's move happens as an opening gap (``0.3 * ret`` plus a little noise); from the open the price runs to the
    close along a straight line with a Brownian bridge around it, and the high and low are that path's extremes. Highs and
    lows that were independent noise on top of the close would make wicks hit stops that the price never travelled to, which
    reads as a tiny negative edge for any strategy: the experiments in ``tests`` would measure the generator, not the engine.
    """
    n = len(rets)
    close = base * np.exp(np.cumsum(rets))
    prev = np.concatenate([[base], close[:-1]])
    open_ = prev * np.exp(0.3 * rets + rng.normal(0, vol_level * gap, n))
    k = BRIDGE_STEPS
    t = np.arange(1, k + 1) / k
    w = np.cumsum(rng.normal(0.0, 1.0, (n, k)), axis=1) * (vol_level * jitter / np.sqrt(k))
    path = np.log(open_)[:, None] + np.log(close / open_)[:, None] * t + (w - w[:, -1:] * t)
    high = np.maximum(np.exp(path.max(axis=1)), np.maximum(open_, close))
    low = np.minimum(np.exp(path.min(axis=1)), np.minimum(open_, close))
    volume = np.zeros(n) if vol_base is None else np.exp(rng.normal(np.log(vol_base), 0.45, n)) * (1 + 6 * np.abs(rets) / max(vol_level, 1e-6))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _market_factor(rng, n: int, vol: float, structure: bool = True) -> np.ndarray:
    if not structure:
        return rng.normal(0, vol, n)
    # bull, correction, recovery
    drift = np.concatenate([np.full(int(n * 0.55), 0.0007), np.full(int(n * 0.2), -0.0009), np.full(n - int(n * 0.55) - int(n * 0.2), 0.0006)])
    return drift + _ar_drift(rng, n, 0.97, 0.0006) + rng.normal(0, vol, n)


def make_psx(rng, idx, n_stocks: int = 64, structure: bool = True) -> MarketData:
    n = len(idx)
    md = MarketData("psx")
    drift = _ar_drift if structure else _no_drift
    mkt = _market_factor(rng, n, 0.009, structure)
    sec_f = {s[0]: drift(rng, n, 0.95, 0.0009) + rng.normal(0, 0.004, n) for s in SECTORS}
    ix = _bars(rng, mkt, idx, 150000.0, 0.009, vol_base=3e8, gap=0.1)
    md.instruments["PSX:DEMO100"] = Instrument("PSX:DEMO100", "psx", "DEMO100", "Demo 100 Index", None, "index", False)
    md.bars["PSX:DEMO100"] = ix
    for k in range(n_stocks):
        sec, short, pe_med, beta_med = SECTORS[k % len(SECTORS)]
        sym = f"DEMO{_letters(k)}"
        name = f"Demo {short} {GREEK[(k // len(SECTORS)) % len(GREEK)]} Ltd"
        beta = float(np.clip(rng.normal(beta_med, 0.25), 0.3, 1.8))
        idio = rng.uniform(0.010, 0.022)
        trend = drift(rng, n, 0.985, 0.0016) if rng.random() < 0.8 else np.zeros(n)
        rets = beta * mkt + sec_f[sec] + trend + rng.normal(0, idio, n)
        # PSX style daily limit of 7.5%
        rets = np.clip(rets, -0.075, 0.075)
        base = float(np.exp(rng.uniform(np.log(18), np.log(900))))
        shares = float(np.exp(rng.uniform(np.log(5e7), np.log(2e9))))
        bars = _bars(rng, rets, idx, base, idio, vol_base=float(np.exp(rng.uniform(np.log(1.5e4), np.log(2.5e6)))))
        if k % 9 == 3:  # a few names keep the "approximated high/low" flag for early history to exercise that path
            bars["approx_hl"] = False
            bars.loc[bars.index[: int(n * 0.6)], "approx_hl"] = True
            m = bars["approx_hl"]
            bars.loc[m, "high"] = bars.loc[m, ["open", "close"]].max(axis=1)
            bars.loc[m, "low"] = bars.loc[m, ["open", "close"]].min(axis=1)
        iid = make_id("psx", sym)
        md.instruments[iid] = Instrument(iid, "psx", sym, name, sec, "equity", True)
        md.bars[iid] = bars
        px = float(bars["close"].iloc[-1])
        pe = float(np.clip(rng.lognormal(np.log(pe_med), 0.35), 2.0, 40.0))
        eps = px / pe if rng.random() > 0.06 else -abs(px) * 0.02
        pb = float(np.clip(rng.lognormal(np.log(1.2), 0.45), 0.3, 6.0))
        roe = float(np.clip(pb / pe * 100 * rng.uniform(0.8, 1.2), -5, 45)) if eps > 0 else -3.0
        dy = float(np.clip(rng.normal(5.0, 3.0), 0.0, 14.0)) if eps > 0 else 0.0
        years = [int(idx[-1].year) - j for j in range(5)]
        md.fundamentals[iid] = {
            "pe": pe if eps > 0 else None, "pb": pb, "eps": eps, "dy": dy, "roe": roe, "bvps": px / pb, "mcap": px * shares, "shares": shares,
            "free_float_pct": float(rng.uniform(15, 60)), "payout_pct": float(np.clip(dy / 100 * px / max(eps, 1e-6) * 100, 0, 120)) if eps > 0 else 0.0,
            "eps_growth_pct": float(rng.normal(10, 15)), "net_margin_pct": float(np.clip(rng.normal(14, 8), -5, 40)),
            "dividends": [{"date": f"{y}-06-15", "amount": round(px * dy / 100, 2)} for y in years[::-1]] if dy > 0.5 else [],
            "eps_hist": [{"period": str(y), "value": round(eps * (0.9 ** j), 2)} for j, y in enumerate(years[:4])][::-1],
        }
        md.quotes[iid] = {"price": px, "prev_close": float(bars["close"].iloc[-2]), "chg_pct": float((bars["close"].iloc[-1] / bars["close"].iloc[-2] - 1) * 100), "volume": float(bars["volume"].iloc[-1]), "ts": idx[-1].strftime("%Y-%m-%dT11:30:00Z")}
    md.extras["benchmark"] = {"symbol": "DEMO100", "name": "Demo 100 Index"}
    md.extras["history_pct"] = 100
    md.status = [SourceStatus("demo_psx", True, "Synthetic data", len(md.instruments), 0.0, idx[-1].strftime("%Y-%m-%dT11:50:00Z"))]
    return md


def make_crypto(rng, idx, n_coins: int = 30, structure: bool = True) -> MarketData:
    n = len(idx)
    md = MarketData("crypto")
    mkt = _market_factor(rng, n, 0.022, structure)
    for k in range(n_coins):
        sym = f"DMC{k + 1:02d}"
        beta = 1.0 if k == 0 else float(np.clip(rng.normal(1.3, 0.35), 0.5, 2.2))
        idio = 0.0 if k == 0 else rng.uniform(0.015, 0.035)
        rets = beta * mkt + (rng.normal(0, idio, n) if k else 0.0)  # no coin-level trend: see the module docstring
        base = float(np.exp(rng.uniform(np.log(0.05), np.log(60000)))) if k else 52000.0
        vol_lvl = 0.03 + idio
        bars = _bars(rng, rets, idx, base, vol_lvl, vol_base=float(np.exp(rng.uniform(np.log(5e3), np.log(5e6)))) / max(base, 1e-3) * 1e3)
        iid = make_id("crypto", sym)
        md.instruments[iid] = Instrument(iid, "crypto", sym, f"Demo Coin {k + 1:02d}", None, "crypto", True)
        md.bars[iid] = bars
        px = float(bars["close"].iloc[-1])
        mcap = float(1.2e12 * (k + 1) ** -1.6)
        md.fundamentals[iid] = {"mcap": mcap, "rank": k + 1, "volume_24h": mcap * float(rng.uniform(0.02, 0.15)), "circ_supply": mcap / px, "max_supply": (mcap / px) * float(rng.choice([1.0, 1.3, 2.0])),
                                "ath": float(bars["close"].max() * 1.15), "ath_date": idx[int(np.argmax(bars["close"].to_numpy()))].strftime("%Y-%m-%d"), "cg_id": f"demo-coin-{k + 1:02d}"}
        md.quotes[iid] = {"price": px, "prev_close": float(bars["close"].iloc[-2]), "chg_pct": float((bars["close"].iloc[-1] / bars["close"].iloc[-2] - 1) * 100), "volume": float(bars["volume"].iloc[-1]), "ts": idx[-1].strftime("%Y-%m-%dT00:05:00Z")}
    md.extras["benchmark"] = {"symbol": "DMC01", "name": "Demo Coin 01"}
    md.extras["global"] = {"total_mcap": 2.9e12, "btc_dominance": 58.1, "eth_dominance": 12.4, "mcap_chg_24h": -0.6, "fear_greed": None}
    md.extras["history_pct"] = 100
    md.status = [SourceStatus("demo_crypto", True, "Synthetic data", len(md.instruments), 0.0, idx[-1].strftime("%Y-%m-%dT00:10:00Z"))]
    return md


FX_CCY0 = {"EUR": 1.08, "GBP": 1.27, "JPY": 1 / 150.0, "CHF": 1 / 0.88, "AUD": 0.66, "NZD": 0.60, "CAD": 1 / 1.36}
FX_VOL = {"EUR": 0.0045, "GBP": 0.0050, "JPY": 0.0055, "CHF": 0.0045, "AUD": 0.0060, "NZD": 0.0062, "CAD": 0.0042}
FX_PAIRS = [
    ("EUR", "USD", "major"), ("GBP", "USD", "major"), ("USD", "JPY", "major"), ("USD", "CHF", "major"), ("AUD", "USD", "major"), ("NZD", "USD", "major"), ("USD", "CAD", "major"),
    ("EUR", "GBP", "minor"), ("EUR", "JPY", "minor"), ("GBP", "JPY", "minor"), ("AUD", "JPY", "minor"), ("EUR", "CHF", "minor"), ("EUR", "AUD", "minor"),
    ("GBP", "CHF", "minor"), ("CAD", "JPY", "minor"), ("NZD", "JPY", "minor"), ("AUD", "NZD", "minor"), ("EUR", "CAD", "minor"), ("GBP", "AUD", "minor"),
]
EXOTIC = [("USD", "TRY", 33.0, 0.004, 0.0009), ("USD", "ZAR", 18.0, 0.007, 0.0001), ("USD", "MXN", 17.2, 0.006, 0.0), ("USD", "SGD", 1.33, 0.0025, 0.0),
          ("USD", "NOK", 10.6, 0.006, 0.0), ("USD", "SEK", 10.4, 0.006, 0.0)]


def make_fx(rng, idx, macro: dict, structure: bool = True) -> MarketData:
    n = len(idx)
    md = MarketData("fx")
    drift = _ar_drift if structure else _no_drift
    keep = 1.0 if structure else 0.0  # the fixed drifts of the exotic pairs, PKR and the metals
    usd_drift = drift(rng, n, 0.985, 0.0004)
    legs = {}
    for c, v0 in FX_CCY0.items():
        r = -usd_drift * rng.uniform(0.4, 1.0) * 0.5 + drift(rng, n, 0.985, 0.0003) + rng.normal(0, FX_VOL[c], n)
        legs[c] = v0 * np.exp(np.cumsum(r))
    legs["USD"] = np.ones(n)

    def add(base, quote, series, group, vol):
        sym = f"{base}{quote}"
        iid = make_id("fx", sym)
        rets = np.diff(np.log(series), prepend=np.log(series[0]))
        df = _bars(rng, rets, idx, float(series[0]), vol, None, gap=0.05)
        df["close"] = series
        md.instruments[iid] = Instrument(iid, "fx", sym, f"{base}/{quote}", None, "fx", group != "pkr", {"group": group, "base": base, "quote": quote})
        md.bars[iid] = df
        md.quotes[iid] = {"price": float(series[-1]), "prev_close": float(series[-2]), "chg_pct": float((series[-1] / series[-2] - 1) * 100), "volume": 0.0, "ts": idx[-1].strftime("%Y-%m-%dT21:00:00Z")}

    for b, q, g in FX_PAIRS:
        add(b, q, legs[b] / legs[q], g, 0.005)
    for b, q, lvl, vol, dr in EXOTIC:
        series = lvl * np.exp(np.cumsum(rng.normal(dr * keep, vol, n) + drift(rng, n, 0.985, 0.0004)))
        add(b, q, series, "exotic", vol)
    pkr = 278.0 * np.exp(np.cumsum(rng.normal(0.00005 * keep, 0.0007, n)))
    add("USD", "PKR", pkr, "pkr", 0.0007)
    for sym, base, vol, dr in (("XAU", 2300.0, 0.009, 0.0004), ("XAG", 28.0, 0.016, 0.0003)):
        rr = rng.normal(dr * keep, vol, n) + drift(rng, n, 0.985, 0.0005)
        series = base * np.exp(np.cumsum(rr - rr.mean()))
        iid = make_id("fx", f"{sym}USD")
        rets = np.diff(np.log(series), prepend=np.log(series[0]))
        df = _bars(rng, rets, idx, float(series[0]), vol, None, gap=0.05)
        df["close"] = series
        md.instruments[iid] = Instrument(iid, "fx", f"{sym}USD", f"{'Gold' if sym == 'XAU' else 'Silver'} / USD", None, "metal", True, {"group": "metal", "base": sym, "quote": "USD"})
        md.bars[iid] = df
        md.quotes[iid] = {"price": float(series[-1]), "prev_close": float(series[-2]), "chg_pct": float((series[-1] / series[-2] - 1) * 100), "volume": 0.0, "ts": idx[-1].strftime("%Y-%m-%dT21:00:00Z")}
    md.extras["macro"] = macro
    md.extras["history_pct"] = 100
    md.status = [SourceStatus("demo_fx", True, "Synthetic data", len(md.instruments), 0.0, idx[-1].strftime("%Y-%m-%dT21:10:00Z"))]
    return md


def make_macro(rng, idx) -> dict:
    n = len(idx)

    def walk(base, vol, drift=0.0):
        r = rng.normal(drift, vol, n) + _ar_drift(rng, n, 0.985, vol * 0.08)
        path = np.cumsum(r - r.mean())  # no net drift over the window: keeps the levels near the starting point
        return pd.Series(base * np.exp(path), index=idx)

    vix = pd.Series(np.clip(17 + _ar_drift(rng, n, 0.97, 1.2) + rng.normal(0, 0.8, n), 10, 45), index=idx)
    us10y = pd.Series(np.clip(4.2 + np.cumsum(rng.normal(0, 0.03, n)) * 0.2, 1.0, 7.0), index=idx)
    return {
        "series": {"DXY": walk(101.0, 0.004), "GOLD": walk(2300.0, 0.009, 0.0003), "BRENT": walk(78.0, 0.014), "SPX": walk(5200.0, 0.01, 0.0004), "VIX": vix, "US10Y": us10y},
        "pk": {"policy_rate": 10.5, "cpi_yoy": 4.1, "asof": idx[-1].strftime("%Y-%m-%d"), "note": "Demo values"},
    }


def make_demo(seed: int = 7, days: int = 1100, end: str | None = None, structure: bool = True) -> dict[str, MarketData]:
    """Three synthetic markets ending on ``end`` (default: the last weekday).

    ``structure=False`` makes every price a pure random walk around a shared market factor: nothing in it can be predicted, so an
    honest engine must find nothing it is willing to call actionable. The tests use it, and ``python -m acumen demo --noise``.
    """
    rng = np.random.default_rng(seed)
    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    while end_ts.weekday() >= 5:
        end_ts -= pd.Timedelta(days=1)
    bidx = pd.bdate_range(end=end_ts, periods=days)
    cidx = pd.date_range(end=end_ts, periods=days)
    macro = make_macro(rng, bidx)
    return {"psx": make_psx(rng, bidx, structure=structure), "fx": make_fx(rng, bidx, macro, structure=structure), "crypto": make_crypto(rng, cidx, structure=structure)}
