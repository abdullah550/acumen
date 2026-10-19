"""One full run: analyse every market and write the data files the app reads (see docs/DATA_CONTRACT.md)."""

from __future__ import annotations

import datetime as dt
import math
import multiprocessing as mp
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import __version__
from .analysis import fundamental as fu
from .analysis import ideas as idm
from .analysis import ledger as led
from .analysis import macro as mac
from .analysis import market as mk
from .analysis import portfolio as pf
from .analysis import regime as reg
from .analysis.calibrate import calibrate
from .analysis.instrument import CAL_DAYS, HORIZONS, Snapshot, analyse_instrument
from .config import Config
from .model import Instrument, MarketData
from .outputs import Writer, build_detail
from .store import HistoryStore

DISCLAIMER = (
    "Acumen produces model estimates for research. It is not investment advice and nothing here is a guarantee: targets, "
    "probabilities and expected results come from past data and simple models, and markets can move against any plan. "
    "You are responsible for your own decisions. Never risk money you cannot afford to lose."
)
LABELS = {"psx": "Pakistan Stock Exchange", "fx": "Forex", "crypto": "Crypto"}
SHORT_LABELS = {"psx": "PSX", "fx": "Forex", "crypto": "Crypto"}
HORIZON_WORDS = {"short": "swing", "medium": "positional", "long": "investment"}
CURRENCY = {"psx": "PKR", "fx": "quote", "crypto": "USD"}
STALE_DAYS = {"psx": 5, "fx": 4, "crypto": 2}
CAVEATS = [
    "No parameter was optimised on this data: thresholds, stops and targets are fixed rules; the history is used only to measure them.",
    "Trades are simulated bar by bar with entries at the next open, stops assumed to trigger before targets inside a bar, and round-trip costs from the configuration.",
    "The universe contains only instruments that trade today (survivorship bias flatters old results).",
    "Fundamentals, valuation and carry are not back-tested (no point-in-time data); they only tilt live conviction.",
    "Win rates by conviction band, and their intervals, treat trades as independent, but trades opened in the same weeks are not (instruments move together), so those intervals are optimistic. The comparison with random entries does allow for trades that overlap in time and for each instrument's own luck.",
    "Where high/low prices are approximated, stops and targets are tested against closes only.",
]

_CTX: dict[str, Any] = {}


def pool_list(pools: list[tuple[str, str]]) -> str:
    """Compact wording for a list of (market, horizon) pools: ``PSX (all horizons); Forex (swing and positional)``."""
    by: dict[str, list[str]] = {}
    for market, horizon in pools:
        by.setdefault(market, []).append(horizon)
    parts = []
    for market in ("psx", "fx", "crypto"):
        hs = [h for h in ("short", "medium", "long") if h in by.get(market, [])]
        if not hs:
            continue
        words = [HORIZON_WORDS[h] for h in hs]
        what = "all horizons" if len(hs) == 3 else " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]
        parts.append(f"{SHORT_LABELS[market]} ({what})")
    return "; ".join(parts)


def _worker(inst_id: str):
    c = _CTX
    try:
        inst = c["insts"][inst_id]
        is_bench = inst_id == c["bench_id"]
        return analyse_instrument(
            inst, c["bars"][inst_id], None if is_bench else c["bench"], c["tilt"], c["cfg"],
            bench_name=None if (is_bench or c["bench"] is None) else c["bench_name"], usd_pkr=c["usd_pkr"],
        )
    except Exception as exc:  # one instrument must never take the run down
        return ("error", inst_id, f"{type(exc).__name__}: {exc}")


def _analyse_market(md: MarketData, cfg: Config, usd_pkr: float, workers: int, log: Callable[[str], None]) -> tuple[list[Snapshot], list[str], pd.Series | None, dict]:
    market = md.market
    ucfg = cfg.get_path(f"universe.{market}", {}) or {}
    bench_info = md.extras.get("benchmark") or {}
    bench_id = f"{market.upper()}:{bench_info['symbol']}" if bench_info.get("symbol") else None
    bench_close = md.bars[bench_id]["close"] if bench_id in md.bars else None
    tilt = reg.regime_tilt_series(bench_close, cfg["signals"]["regime_tilt"]) if (bench_close is not None and market != "fx" and len(bench_close) > 220) else None
    ids = []
    for iid, inst in md.instruments.items():
        bars = md.bars.get(iid)
        if bars is None or len(bars) < int(ucfg.get("min_bars", 30)):
            continue
        if market == "psx":
            if inst.kind == "debt" or (ucfg.get("exclude_etf") and inst.kind == "etf"):
                continue
            if float(bars["close"].iloc[-1]) < float(ucfg.get("min_price", 0)):
                continue
        if market == "crypto" and ucfg.get("exclude_stables", True) and (inst.meta or {}).get("stable"):
            continue
        ids.append(iid)
    _CTX.update({"insts": md.instruments, "bars": md.bars, "bench": bench_close, "bench_id": bench_id, "bench_name": bench_info.get("name"),
                 "tilt": tilt, "cfg": cfg, "usd_pkr": usd_pkr})
    results: list = []
    if workers > 1 and len(ids) > 40 and "fork" in mp.get_all_start_methods():
        with mp.get_context("fork").Pool(workers) as pool:
            results = pool.map(_worker, ids, chunksize=8)
    else:
        results = [_worker(i) for i in ids]
    snaps, errors = [], []
    for r in results:
        if r is None:
            continue
        if isinstance(r, tuple) and r and r[0] == "error":
            errors.append(f"{r[1]}: {r[2]}")
            continue
        snaps.append(r)
    log(f"  {market}: analysed {len(snaps)} of {len(ids)} instruments" + (f", {len(errors)} failed" if errors else ""))
    return snaps, errors, bench_close, bench_info


def _fundamentals(snaps_by_market: dict[str, list[Snapshot]], data: dict[str, MarketData], cfg: Config, asof_year: int) -> dict[str, dict]:
    """id -> {"fundamental": block|None, "valuation": block|None}."""
    out: dict[str, dict] = {}
    psx = data.get("psx")
    if psx is not None:
        snaps = [s for s in snaps_by_market.get("psx", []) if s.kind == "equity"]
        funds = {s.id: psx.fundamentals[s.id] for s in snaps if s.id in psx.fundamentals}
        sectors = {s.id: s.sector for s in snaps}
        stats = fu.sector_stats(funds, sectors)
        mcaps = {i: f.get("mcap") for i, f in funds.items() if f.get("mcap")}
        mcap_rank = pd.Series(mcaps).rank(pct=True).to_dict() if mcaps else {}
        vols = [s.row.get("vol30") for s in snaps if s.row.get("vol30") is not None]
        vol_med = float(np.median(vols)) if vols else None
        for s in snaps:
            f = funds.get(s.id)
            if not f:
                continue
            blk = fu.psx_fundamental(s.id, f, s.sector, stats, mcap_rank.get(s.id), s.row.get("vol30"), vol_med, asof_year)
            val = fu.valuation(f, s.last, s.row.get("beta"), s.sector, stats, cfg["valuation"])
            out[s.id] = {"fundamental": blk, "valuation": val}
    cr = data.get("crypto")
    if cr is not None:
        for s in snaps_by_market.get("crypto", []):
            f = cr.fundamentals.get(s.id)
            if f:
                out[s.id] = {"fundamental": fu.crypto_fundamental(f, s.last), "valuation": None}
    return out


def _market_ctx(snaps: list[Snapshot], market: str, today: dt.date) -> dict:
    if not snaps:
        return {"asof": None, "stale": True, "stale_days": None}
    asof = max(s.asof for s in snaps)
    days = (pd.Timestamp(today) - pd.Timestamp(asof)).days
    return {"asof": asof, "stale": days > STALE_DAYS[market], "stale_days": days}


def _profile(snap: Snapshot, md: MarketData, fund_blk: dict | None, carry: float | None) -> dict:
    f = md.fundamentals.get(snap.id, {}) if md else {}
    inst = md.instruments[snap.id]
    if snap.market == "psx":
        return {"listed_in": (inst.meta or {}).get("listed_in", []), "shares": f.get("shares"), "free_float_pct": f.get("free_float_pct"),
                "mcap": f.get("mcap"), "sector_code": (inst.meta or {}).get("sector_code"), "is_etf": inst.kind == "etf"}
    if snap.market == "fx":
        m = inst.meta or {}
        base, quote = m.get("base", snap.sym[:3]), m.get("quote", snap.sym[3:])
        return {"base": base, "quote": quote, "group": m.get("group", "minor"), "pip": 0.01 if (quote == "JPY" or m.get("group") == "metal") else 0.0001,
                "carry_pct": None if carry is None else round(carry, 2), "synthetic": bool(m.get("synthetic", False)), "retail_tradable": bool(snap.tradable)}
    ath = f.get("ath")
    return {"rank": f.get("rank"), "mcap": f.get("mcap"), "ath": ath, "ath_date": f.get("ath_date"),
            "ath_dd_pct": None if not ath else round((snap.last / ath - 1) * 100, 1), "circ_supply": f.get("circ_supply"),
            "max_supply": f.get("max_supply"), "exchange": (inst.meta or {}).get("exchange"), "cg_id": f.get("cg_id")}


def run(
    cfg: Config,
    data: dict[str, MarketData],
    out_dir: str | Path,
    *,
    store: HistoryStore | None = None,
    passphrase: str | None = None,
    demo: bool = False,
    now: dt.datetime | None = None,
    workers: int = 1,
    log: Callable[[str], None] = print,
) -> dict:
    """Analyse ``data`` and write the site data files into ``out_dir``. Returns a small summary dict."""
    t0 = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now.date()
    log(f"Acumen build {now_iso} ({'demo' if demo else 'live'})")
    fx_bars = data["fx"].bars if "fx" in data else {}
    usd_pkr = float(fx_bars["FX:USDPKR"]["close"].iloc[-1]) if "FX:USDPKR" in fx_bars and len(fx_bars["FX:USDPKR"]) else float(cfg.get_path("app.usd_pkr_fallback", 283.0))

    # ---- pass 1
    snaps_by_market: dict[str, list[Snapshot]] = {}
    errors: dict[str, list[str]] = {}
    benches: dict[str, tuple[pd.Series | None, dict]] = {}
    for market, md in data.items():
        snaps, errs, bench_close, bench_info = _analyse_market(md, cfg, usd_pkr, workers, log)
        snaps_by_market[market] = snaps
        errors[market] = errs
        benches[market] = (bench_close, bench_info)
    all_snaps = [s for ss in snaps_by_market.values() for s in ss]
    snaps_by_id = {s.id: s for s in all_snaps}
    ctx = {m: _market_ctx(ss, m, today) for m, ss in snaps_by_market.items()}

    # ---- calibration pools
    cals: dict[tuple[str, str], dict] = {}
    min_trades = int(cfg.get_path("backtest.calibration_min_trades", 200))
    match_days = dict(cfg.get_path("backtest.match_days", {}) or {})
    edge_alpha = float(cfg.get_path("backtest.edge_alpha", 0.01))
    min_spans = float(cfg.get_path("backtest.edge_min_spans", 10))
    min_matched = int(cfg.get_path("backtest.edge_min_matched", 3))
    for market, ss in snaps_by_market.items():
        for h in HORIZONS:
            frames = [s.horizons[h].trades.assign(inst=s.id) for s in ss if s.tradable and s.horizons[h].trades is not None and len(s.horizons[h].trades)]
            pool = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["conv", "r", "hit1", "hit2", "bars_t1", "bars_t2", "entry_date"])
            lo = int(cfg["horizons"][h]["min_days"] / max(CAL_DAYS[market], 1.0))
            base_parts = [s.horizons[h].baseline for s in ss if s.tradable and s.horizons[h].baseline is not None and len(s.horizons[h].baseline)]
            baseline = pd.concat(base_parts, ignore_index=True) if base_parts else None
            window = int(match_days.get(h) or 7)
            # a trade's result stays tied to its neighbours' for as long as it can stay open, plus the matching window
            lag_days = int(np.ceil(int(cfg["horizons"][h]["time_stop_bars"]) * CAL_DAYS[market])) + window
            cals[(market, h)] = calibrate(pool, min_trades=min_trades, default_eta=(max(2, lo // 2), max(6, lo)), baseline=baseline,
                                          lag_days=lag_days, match_days=window, edge_alpha=edge_alpha, min_spans=min_spans, min_matched=min_matched)
    log("  calibration: " + ", ".join(f"{m}/{h}={c['status']}({c['n_trades']})" for (m, h), c in cals.items()))

    # ---- fundamentals, carry, ideas
    asof_year = today.year
    fund = _fundamentals(snaps_by_market, data, cfg, asof_year)
    rates = (cfg.get_path("rates.policy_rate_pct", {}) or {})
    carry: dict[str, float | None] = {}
    for s in snaps_by_market.get("fx", []):
        inst = data["fx"].instruments[s.id]
        m = inst.meta or {}
        carry[s.id] = fu.fx_carry(m.get("base", ""), m.get("quote", ""), rates) if m.get("group") != "metal" else None
    ideas: list[dict] = []
    for s in all_snaps:
        if s.kind == "index":
            continue
        fb = fund.get(s.id, {})
        bn = benches[s.market][1].get("name")
        for h in HORIZONS:
            idea = idm.make_idea(s, h, cals.get((s.market, h)), fb.get("fundamental"), fb.get("valuation"), carry.get(s.id), cfg, ctx[s.market],
                                 None if s.id == f"{s.market.upper()}:{benches[s.market][1].get('symbol')}" else bn, usd_pkr)
            if idea:
                ideas.append(idea)
    ideas.sort(key=lambda i: (not i["actionable"], -i["conviction"], -(i["exp_r"] if i["exp_r"] is not None else -9.0)))
    cap = int(cfg.get_path("output.top_ideas_per_horizon", 40))
    kept, counts = [], {}
    for i in ideas:
        key = (i["market"], i["horizon"], i["actionable"])
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= (cap if i["actionable"] else 20):
            kept.append(i)
    ideas = kept
    idm.fill_rows(all_snaps, ideas, fund, carry, cfg, ctx)
    ideas_by_id: dict[str, dict[str, dict]] = {}
    for i in ideas:
        ideas_by_id.setdefault(i["id"], {})[i["horizon"]] = i
    log(f"  ideas: {len(ideas)} ({sum(1 for i in ideas if i['actionable'])} actionable)")

    # ---- portfolio, ledger, macro
    portfolio = pf.build_portfolios(ideas, snaps_by_id, cfg, usd_pkr)
    bars_all = {iid: b for md in data.values() for iid, b in md.bars.items()}
    insts_all = {iid: inst for md in data.values() for iid, inst in md.instruments.items()}
    ledger_state = store.load_json(led.STATE_NAME) if store else None
    ledger_state, ledger_view = led.update(ledger_state, ideas, bars_all, insts_all, cfg, today.isoformat())
    if store:
        store.save_json(led.STATE_NAME, ledger_state)
    macro_extras = next((md.extras["macro"] for md in data.values() if md.extras.get("macro")), {})
    macro_body, headline = mac.macro_block({"macro": macro_extras}, fx_bars, cfg, today.isoformat())

    # ---- writer
    writer = Writer(out_dir, passphrase)

    # ---- details
    peers_by_sector: dict[str, list[Snapshot]] = {}
    for s in snaps_by_market.get("psx", []):
        if s.kind == "equity" and s.sector:
            peers_by_sector.setdefault(s.sector, []).append(s)
    for s in all_snaps:
        md = data[s.market]
        fb = fund.get(s.id, {})
        peers = []
        if s.market == "psx" and s.sector:
            cand = [p for p in peers_by_sector.get(s.sector, []) if p.id != s.id]
            cand.sort(key=lambda p: (ideas_by_id.get(p.id, {}).get("medium", {}).get("conviction", 0)), reverse=True)
            for p in cand[:5]:
                best = max(ideas_by_id.get(p.id, {}).values(), key=lambda i: i["conviction"], default=None)
                peers.append({"id": p.id, "sym": p.sym, "name": p.name, "conviction": best["conviction"] if best else None, "chg1m": p.row.get("chg1m"), "pe": p.row.get("pe")})
        plans = {h: ideas_by_id.get(s.id, {}).get(h) for h in HORIZONS}
        cal_h = {h: cals[(s.market, h)] for h in HORIZONS if (s.market, h) in cals}
        bn = benches[s.market][1].get("name")
        det = build_detail(s, plans, cal_h, fb.get("fundamental"), fb.get("valuation"), peers, md.quotes.get(s.id), _profile(s, md, fb.get("fundamental"), carry.get(s.id)),
                           cfg, None if s.id == f"{s.market.upper()}:{benches[s.market][1].get('symbol')}" else bn, now_iso, CURRENCY[s.market] if s.market != "fx" else (md.instruments[s.id].meta or {}).get("quote", "quote"))
        writer.write_detail(s.market, s.sym, det)

    # ---- overview
    n_ideas = {m: sum(1 for i in ideas if i["market"] == m) for m in data}
    overview_markets: dict[str, Any] = {}
    for market, ss in snaps_by_market.items():
        bench_close, bench_info = benches[market]
        if market in ("psx", "crypto"):
            body = mk.equity_overview(market, ss, bench_close, bench_info.get("symbol", ""), bench_info.get("name", ""), n_ideas[market], len(data[market].instruments))
            if market == "crypto":
                body["global"] = data["crypto"].extras.get("global", {})
                body["heat"] = mk.crypto_heat([s for s in ss], data["crypto"].fundamentals)
        else:
            st = mk.fx_strength(data["fx"].bars)
            body = {"asof": ctx["fx"]["asof"], "regime": mk.fx_regime(macro_extras.get("series") if macro_extras else None, st["strength"]),
                    "strength": st["strength"], "matrix": st["matrix"], "movers": mk.movers([s for s in ss if s.tradable]),
                    "counts": {"instruments": len(data["fx"].instruments), "analysed": sum(1 for s in ss if s.enough_history), "ideas": n_ideas["fx"]}}
        overview_markets[market] = body
    actionable = [i for i in ideas if i["actionable"]]
    top_ideas, per_pool = [], {}
    for i in actionable:  # best first, at most 4 per market and horizon so one pool cannot fill the list
        key = (i["market"], i["horizon"])
        if per_pool.get(key, 0) >= 4:
            continue
        per_pool[key] = per_pool.get(key, 0) + 1
        top_ideas.append(idm.summary(i))
        if len(top_ideas) >= 12:
            break
    bal = portfolio["profiles"].get("balanced", {})
    bal_stats = bal.get("stats", {})
    pool_n = sum(c["n_trades"] for c in cals.values())
    n_cal = sum(1 for c in cals.values() if c["calibrated"])
    alerts: list[dict] = []
    if demo:
        alerts.append({"level": "warn", "market": "all", "text": "DEMO DATA: every price, company and result here is synthetic. Nothing is real."})
    for market, c in ctx.items():
        if c["stale"] and not demo:
            alerts.append({"level": "warn", "market": market, "text": f"{LABELS[market]} data is {c['stale_days']} days old" if c["stale_days"] is not None else f"{LABELS[market]} has no usable data"})
    for market, md in data.items():
        for st_ in md.status:
            if not st_.ok:
                alerts.append({"level": "warn", "market": market, "text": f"Source {st_.source}: {st_.message or 'failed'}"})
    unval = [(m, h) for (m, h), c in cals.items() if c["status"] == "unvalidated"]
    if unval:
        alerts.append({"level": "info", "market": "all", "text": "Conviction has not ranked outcomes reliably out of sample for " + pool_list(unval) + ". Those probabilities are pooled base rates."})
    skilled = [(m, h) for (m, h), c in cals.items() if (c.get("edge") or {}).get("passed")]
    drift = [(m, h) for (m, h), c in cals.items() if (c.get("edge") or {}).get("tested") and not c["edge"]["passed"] and (c.get("avg_r") or 0) > 0]
    if drift:
        alerts.append({"level": "info", "market": "all", "text": "Positive past results for " + pool_list(drift) + " are not clearly better than random entries with the same exits, so they may simply be the market drifting."})
    if skilled:
        alerts.append({"level": "info", "market": "all", "text": "Signals have beaten random entries with the same exits in " + pool_list(skilled) + ". Ideas in every other market and horizon are research candidates only."})
    if not skilled:
        alerts.append({"level": "warn", "market": "all", "text": "Nothing is marked actionable: no market and horizon has yet shown that its signals beat random entries with the same exits (see Evidence). "
                       "The best research candidates are still listed with full plans; treat them as screening results, not recommendations."})
    elif not actionable:
        alerts.append({"level": "info", "market": "all", "text": "No idea passes the actionability rules right now (expected result after costs must be positive). Research-only ideas are still listed."})
    overview = {
        "generated_at": now_iso, "markets": overview_markets, "top_ideas": top_ideas,
        "portfolio": {"profile": "balanced", "n": bal_stats.get("n", 0), "heat_pct": bal_stats.get("heat_pct"), "exp_r_total": bal_stats.get("exp_r_total"), "by_market": bal_stats.get("by_market", {})},
        "track_record": {**{k: ledger_view["stats"][k] for k in ("since", "open", "closed", "win", "avg_r")}, "note": "Live results since Acumen started issuing ideas"},
        "calibration": {"calibrated": n_cal > 0, "pooled_trades": pool_n, "note": f"{n_cal} of {len(cals)} market and horizon pools passed the out-of-sample check"},
        "alerts": alerts, "macro": headline,
    }

    # ---- instruments, ideas, calibration, prices
    rows = [s.row for s in all_snaps]
    rows.sort(key=lambda r: (r["m"], r["sym"]))
    first_dates, last_dates = [], []
    for s in all_snaps:
        for h in HORIZONS:
            t = s.horizons[h].trades
            if t is not None and len(t):
                first_dates.append(pd.Timestamp(t["entry_date"].min()))
                last_dates.append(pd.Timestamp(t["exit_date"].max()))
    cal_body = {
        "generated_at": now_iso, "period": [min(first_dates).strftime("%Y-%m-%d"), max(last_dates).strftime("%Y-%m-%d")] if first_dates else [None, None],
        "markets": {m: {"horizons": {h: cals[(m, h)] for h in HORIZONS}} for m in data}, "caveats": CAVEATS,
    }
    quotes = {}
    for s in all_snaps:
        q = data[s.market].quotes.get(s.id)
        if q and q.get("price"):
            quotes[s.id] = [q["price"], q.get("chg_pct"), q.get("ts") or now_iso]
        else:
            quotes[s.id] = [s.last, s.row.get("chg1d"), now_iso]
    files = {
        "overview": writer.write("overview", overview),
        "instruments": writer.write("instruments", {"generated_at": now_iso, "rows": rows}),
        "ideas": writer.write("ideas", {"generated_at": now_iso, "ideas": ideas}),
        "portfolio": writer.write("portfolio", {"generated_at": now_iso, **portfolio}),
        "calibration": writer.write("calibration", cal_body),
        "ledger": writer.write("ledger", {"generated_at": now_iso, **ledger_view}),
        "macro": writer.write("macro", {"generated_at": now_iso, **macro_body}),
        "prices": writer.write("prices", {"generated_at": now_iso, "quotes": quotes}),
    }

    # ---- meta
    markets_meta, sources = {}, []
    for market, md in data.items():
        c = ctx[market]
        failed = [s_ for s_ in md.status if not s_.ok]
        hist = md.extras.get("history_pct", 100)
        # A failed source alone does not make a market stale: prices can be current while, say, fundamentals failed.
        # Source failures are listed in `sources` and summarised in the note; staleness is about the age of the prices.
        status = "error" if not snaps_by_market[market] else "warming" if hist < 100 else "stale" if c["stale"] else "ok"
        parts = []
        if failed:
            parts.append("; ".join(f"{s_.source}: {s_.message}" for s_ in failed))
        if c["stale"] and c["stale_days"] is not None:
            parts.append(f"Latest bar is {c['stale_days']} days old")
        parts += [str(n) for n in md.extras.get("notes", [])]
        note = " · ".join(parts)[:300] or None
        ok_times = [s_.at for s_ in md.status if s_.ok and s_.at]
        markets_meta[market] = {"label": LABELS[market], "currency": CURRENCY[market], "asof": c["asof"], "instruments": len(snaps_by_market[market]), "status": status,
                                "history_pct": hist, "last_success": max(ok_times) if ok_times else None, "note": note}
        for s_ in md.status:
            sources.append({"id": s_.source, "name": s_.source, "market": market, "ok": bool(s_.ok), "message": s_.message, "at": s_.at, "items": s_.items, "duration_s": round(s_.duration_s, 1)})
    meta = {
        "schema": 1, "app": "Acumen", "version": __version__, "generated_at": now_iso, "demo": bool(demo), "files": files,
        "detail_path": f"detail/{{market}}/{{symbol}}.{writer.ext}", "markets": markets_meta, "sources": sources,
        "config": {
            "account_currency": cfg.get_path("app.account_currency", "PKR"), "default_equity": cfg.get_path("app.default_equity", 1000000),
            "horizons": {h: {k: v for k, v in cfg["horizons"][h].items()} for h in HORIZONS}, "profiles": cfg["portfolio"]["profiles"], "usd_pkr": round(usd_pkr, 2),
            "max_position_pct": cfg.get_path("portfolio.max_position_pct", 20), "max_pct_of_adv": cfg.get_path("portfolio.max_pct_of_adv", 10),
            # thresholds the interface quotes when it explains why something is (not) a trade idea
            "signals": {"enter_score": cfg.get_path("signals.enter_score", 0.30), "max_tilt_points": cfg.get_path("signals.max_tilt_points", 12),
                        "min_exp_r": cfg.get_path("signals.min_exp_r", 0.02)},
            "grades": {"A": 75, "B": 60, "C": 45},
            "min_rr": cfg.get_path("plan.min_rr", 1.5),
        },
        "health": {"analysed": len(all_snaps), "failed": sum(len(e) for e in errors.values()), "failed_examples": [x for e in errors.values() for x in e][:8], "seconds": round(time.time() - t0, 1)},
        "disclaimer": DISCLAIMER,
    }
    writer.write_meta(meta)
    log(f"  wrote {writer.files} files, {writer.bytes_written / 1e6:.1f} MB in {time.time() - t0:.0f}s")
    return {"files": writer.files, "bytes": writer.bytes_written, "ideas": len(ideas), "actionable": len(actionable), "snapshots": len(all_snaps), "failed": meta["health"]["failed"], "seconds": time.time() - t0}
