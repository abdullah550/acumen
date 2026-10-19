"""Market level summaries for the Command Center: regime, breadth, sectors, movers, currency strength, crypto heat."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import indicators as ind
from . import regime as reg
from .instrument import Snapshot, _pct_days, _ytd


def _r(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _med(vals):
    v = [x for x in vals if x is not None and math.isfinite(x)]
    return float(np.median(v)) if v else None


def thin(series: pd.Series, max_points: int = 260, nd: int = 4) -> list[list]:
    s = series.dropna()
    if len(s) > max_points:
        keep = np.unique(np.linspace(0, len(s) - 1, max_points).round().astype(int))
        s = s.iloc[keep]
    return [[d.strftime("%Y-%m-%d"), round(float(v), nd)] for d, v in s.items()]


def breadth(snaps: list[Snapshot]) -> dict:
    eq = [s for s in snaps if s.kind in ("equity", "crypto", "etf") and s.enough_history]
    if not eq:
        return {}
    a50 = [s.row["above50"] for s in eq if s.row.get("above50") is not None]
    a200 = [s.row["above200"] for s in eq if s.row.get("above200") is not None]
    chg = [s.row["chg1d"] for s in snaps if s.row.get("chg1d") is not None and s.kind != "index"]
    adv = sum(1 for c in chg if c > 0.005)
    dec = sum(1 for c in chg if c < -0.005)
    nh = sum(1 for s in eq if (s.last_feat.get("prox52") or 0) >= 0.995)
    nl = sum(1 for s in eq if s.last_feat.get("lo252") and s.last <= s.last_feat["lo252"] * 1.005)
    return {
        "pct_above_50": _r(100.0 * sum(a50) / len(a50), 1) if a50 else None,
        "pct_above_200": _r(100.0 * sum(a200) / len(a200), 1) if a200 else None,
        "adv": adv, "dec": dec, "unch": len(chg) - adv - dec, "new_highs": nh, "new_lows": nl,
    }


def movers(snaps: list[Snapshot], top: int = 6, min_tier: str = "C") -> dict:
    tiers = "ABCD"
    ok_ = [s for s in snaps if s.kind != "index" and s.row.get("chg1d") is not None and tiers.index(s.liquidity.get("tier") or "D") <= tiers.index(min_tier)]
    def item(s):
        return {"id": s.id, "sym": s.sym, "name": s.name, "px": s.row["px"], "chg_1d": s.row["chg1d"]}
    ok_.sort(key=lambda s: s.row["chg1d"], reverse=True)
    gain = [item(s) for s in ok_[:top] if s.row["chg1d"] > 0]
    lose = [item(s) for s in ok_[::-1][:top] if s.row["chg1d"] < 0]
    active = sorted([s for s in snaps if s.kind != "index" and s.row.get("vol")], key=lambda s: (s.row["vol"] or 0) * (s.row["px"] or 0), reverse=True)[:top]
    act = [{"id": s.id, "sym": s.sym, "name": s.name, "px": s.row["px"], "volume": s.row["vol"], "value": _r((s.row["vol"] or 0) * (s.row["px"] or 0), 0)} for s in active]
    return {"gainers": gain, "losers": lose, "active": act}


def sectors(snaps: list[Snapshot], bench_3m: float | None) -> list[dict]:
    groups: dict[str, list[Snapshot]] = {}
    for s in snaps:
        if s.kind == "equity" and s.sector and s.enough_history:
            groups.setdefault(s.sector, []).append(s)
    out = []
    for name, mem in groups.items():
        if len(mem) < 2:
            continue
        chg = lambda k: _med([m.row.get(k) for m in mem])
        c3 = chg("chg3m")
        a50 = [m.row["above50"] for m in mem if m.row.get("above50") is not None]
        scores = [m.horizons["medium"].comp for m in mem if m.horizons["medium"].comp is not None]
        top = sorted(mem, key=lambda m: (m.horizons["medium"].conv or 0) * (1 if (m.horizons["medium"].raw_dir or 0) > 0 else 0), reverse=True)[:2]
        out.append({
            "name": name, "n": len(mem), "chg_1d": _r(chg("chg1d")), "chg_1w": _r(chg("chg1w")), "chg_1m": _r(chg("chg1m")), "chg_3m": _r(c3),
            "rs": _r(c3 - bench_3m) if (c3 is not None and bench_3m is not None) else None,
            "pct_above_50": _r(100.0 * sum(a50) / len(a50), 0) if a50 else None,
            "score": _r(np.mean(scores)) if scores else None, "top": [m.sym for m in top],
        })
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return out


def index_block(close: pd.Series, symbol: str, name: str, bars: int = 504) -> dict:
    """Benchmark index: headline changes plus the last ``bars`` closes (about two years) and the 50- and 200-day averages.

    The averages are computed on the full history and aligned to ``series`` (``null`` while there is not enough history), so the
    chart never has to rebuild them from a thinned series.
    """
    c = close.dropna()
    tail = c.iloc[-bars:]
    avg = lambda n: [None if not math.isfinite(v) else round(float(v), 2) for v in ind.sma(c, n).reindex(tail.index)]
    return {
        "symbol": symbol, "name": name, "close": _r(c.iloc[-1], 2),
        "chg_1d": _r((c.iloc[-1] / c.iloc[-2] - 1) * 100) if len(c) > 1 else None,
        "chg_1w": _r(_pct_days(c, 7)), "chg_1m": _r(_pct_days(c, 30)), "chg_ytd": _r(_ytd(c)),
        "series": [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in tail.items()],
        "ma50": avg(50), "ma200": avg(200),
    }


def equity_overview(market: str, snaps: list[Snapshot], bench_close: pd.Series | None, bench_sym: str, bench_name: str, n_ideas: int, n_total: int) -> dict:
    """Overview block for PSX or crypto (an index / BTC-led regime, breadth, movers)."""
    reg_block = reg.describe(bench_close, bench_name) if bench_close is not None and len(bench_close) > 60 else {"code": "range", "label": "Not enough history", "score": None, "note": "The benchmark series is too short to classify the regime."}
    b = breadth([s for s in snaps if s.id != f"{market.upper()}:{bench_sym}"])
    reg_block["breadth"] = b
    if b.get("pct_above_50") is not None:
        w = "healthy" if b["pct_above_50"] >= 60 else "weak" if b["pct_above_50"] <= 40 else "mixed"
        reg_block["note"] = (reg_block.get("note", "") + f" Breadth is {w}: {b['pct_above_50']:.0f}% of names are above their 50-day average.").strip()
    bench_3m = _pct_days(bench_close, 91) if bench_close is not None and len(bench_close) > 100 else None
    analysed = sum(1 for s in snaps if s.enough_history and s.kind != "index")
    body = {
        "asof": max((s.asof for s in snaps), default=None), "regime": reg_block,
        "movers": movers([s for s in snaps if s.kind != "index"]),
        "counts": {"instruments": n_total, "analysed": analysed, "ideas": n_ideas},
    }
    if market == "psx":
        body["index"] = index_block(bench_close, bench_sym, bench_name) if bench_close is not None and len(bench_close) > 2 else None
        body["sectors"] = sectors(snaps, bench_3m)
    return body


def crypto_heat(snaps: list[Snapshot], fundamentals: dict[str, dict], top: int = 40) -> list[dict]:
    rows = []
    for s in snaps:
        f = fundamentals.get(s.id) or {}
        mc = f.get("mcap")
        rows.append((mc if mc else 0.0, s))
    rows.sort(key=lambda x: x[0], reverse=True)
    out = []
    for mc, s in rows[:top]:
        out.append({"id": s.id, "sym": s.sym, "name": s.name, "mcap": _r(mc, 0) if mc else None, "chg_1d": s.row.get("chg1d"), "chg_7d": s.row.get("chg1w"), "chg_30d": s.row.get("chg1m")})
    return out


MAJORS = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]


def usd_legs(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """USD value of one unit of each currency, from the pairs quoted against USD (USD itself is 1)."""
    cols = {"USD": None}
    for ccy in MAJORS[1:]:
        direct = bars.get(f"FX:{ccy}USD")
        inverse = bars.get(f"FX:USD{ccy}")
        if direct is not None and len(direct) > 10:
            cols[ccy] = direct["close"]
        elif inverse is not None and len(inverse) > 10:
            cols[ccy] = 1.0 / inverse["close"]
    idx = None
    for s in cols.values():
        if s is not None:
            idx = s.index if idx is None else idx.union(s.index)
    if idx is None:
        return pd.DataFrame()
    df = pd.DataFrame({c: (pd.Series(1.0, index=idx) if s is None else s.reindex(idx).ffill()) for c, s in cols.items()})
    return df.dropna(how="all")


def fx_strength(bars: dict[str, pd.DataFrame]) -> dict:
    v = usd_legs(bars)
    if v.empty or len(v) < 10 or len(v.columns) < 4:
        return {"strength": [], "matrix": {"ccys": [], "chg_1w": []}}
    ccys = [c for c in MAJORS if c in v.columns]

    def chg(days: int) -> pd.Series:
        last = v.index[-1]
        base = v.loc[: last - pd.Timedelta(days=days)]
        if base.empty:
            return pd.Series(np.nan, index=ccys)
        b = base.iloc[-1]
        return (v.iloc[-1] / b - 1.0) * 100.0

    c1, c7, c30 = chg(1), chg(7), chg(30)
    # currency strength = average change against the other majors
    def strength(c: pd.Series) -> dict[str, float]:
        return {a: float(np.nanmean([(1 + c[a] / 100) / (1 + c[b] / 100) * 100 - 100 for b in ccys if b != a])) for a in ccys}
    s1, s7, s30 = strength(c1), strength(c7), strength(c30)
    rows = [{"ccy": c, "score": _r(math.tanh(s7[c] / 1.5), 2), "chg_1d": _r(s1[c]), "chg_1w": _r(s7[c]), "chg_1m": _r(s30[c])} for c in ccys]
    rows.sort(key=lambda r: -(r["score"] or 0))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    mat = []
    for a in ccys:
        mat.append([None if a == b else _r(((1 + c7[a] / 100) / (1 + c7[b] / 100) - 1) * 100, 2) for b in ccys])
    return {"strength": rows, "matrix": {"ccys": ccys, "chg_1w": mat}}


def fx_regime(macro: dict[str, pd.Series] | None, strength_rows: list[dict]) -> dict:
    """Risk appetite and dollar trend from a few macro series (falls back to 'mixed' when they are missing)."""
    m = macro or {}
    votes, notes = [], []
    dxy = m.get("DXY")
    spx, vix = m.get("SPX"), m.get("VIX")
    usd_trend = None
    out: dict = {"code": "mixed", "label": "Mixed", "score": 0.0, "note": "Macro inputs (S&P 500, VIX, dollar index) were not available.", "dxy": None}
    if spx is not None and len(spx) > 200:
        up = bool(spx.iloc[-1] > spx.rolling(200).mean().iloc[-1])
        votes.append(1.0 if up else -1.0)
        notes.append("S&P 500 is " + ("above" if up else "below") + " its 200-day average")
    if vix is not None and len(vix) > 5:
        v = float(vix.iloc[-1])
        votes.append(1.0 if v < 18 else -1.0 if v > 25 else 0.0)
        notes.append(f"VIX at {v:.1f}")
    if dxy is not None and len(dxy) > 60:
        s50 = dxy.rolling(50).mean().iloc[-1]
        usd_trend = "firming" if dxy.iloc[-1] > s50 else "softening"
        out["dxy"] = {"close": _r(dxy.iloc[-1], 2), "chg_1m": _r(_pct_days(dxy, 30))}
        notes.append(f"Dollar index {usd_trend} against its 50-day average")
    if votes:
        sc = float(np.mean(votes))
        risk = "Risk-on" if sc >= 0.5 else "Risk-off" if sc <= -0.5 else "Mixed"
        out.update({"code": "risk_on" if sc >= 0.5 else "risk_off" if sc <= -0.5 else "mixed", "score": _r(sc, 2),
                    "label": risk + (f", USD {usd_trend}" if usd_trend else ""), "note": ". ".join(notes) + "."})
    return out
