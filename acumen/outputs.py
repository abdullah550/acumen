"""Output helpers: JSON cleaning, file writing (optionally encrypted), per-instrument detail assembly."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import crypto_box
from .analysis import texts as tx
from .analysis.instrument import HORIZONS, Snapshot


def clean(o: Any) -> Any:
    """Recursively convert to strict-JSON-safe Python values (NaN/inf -> None, numpy -> python, dates -> str)."""
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, (int,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        v = float(o)
        return v if math.isfinite(v) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return [clean(v) for v in o.tolist()]
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def dumps(o: Any) -> bytes:
    return json.dumps(clean(o), allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def safe_symbol(sym: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", sym)


class Writer:
    """Writes data files plain or encrypted (``.enc``) and remembers what it wrote for meta.json."""

    def __init__(self, out_dir: str | Path, passphrase: str | None = None):
        self.root = Path(out_dir)
        (self.root / "detail").mkdir(parents=True, exist_ok=True)
        self.passphrase = passphrase or None
        self.enc_meta = None
        self.key = None
        if self.passphrase:
            self.enc_meta, self.key = crypto_box.make_enc_meta(self.passphrase)
        self.ext = "enc" if self.passphrase else "json"
        self.bytes_written = 0
        self.files = 0

    def _emit(self, path: Path, obj: Any, plain: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = dumps(obj)
        if self.key is not None and not plain:
            data = crypto_box.encrypt_bytes(self.key, raw, compress=True)
        else:
            data = raw
        path.write_bytes(data)
        self.bytes_written += len(data)
        self.files += 1

    def write(self, name: str, obj: Any) -> str:
        fname = f"{name}.{self.ext}"
        self._emit(self.root / fname, obj)
        return fname

    def write_detail(self, market: str, symbol: str, obj: Any) -> None:
        self._emit(self.root / "detail" / market / f"{safe_symbol(symbol)}.{self.ext}", obj)

    def write_meta(self, meta: dict) -> None:
        meta = dict(meta)
        meta["enc"] = self.enc_meta
        meta["ext"] = self.ext
        self._emit(self.root / "meta.json", meta, plain=True)


def _factor_block(snap: Snapshot, h: str, weights: dict[str, float], bench_name: str | None) -> dict:
    hr = snap.horizons[h]
    if hr.comp is None:
        return {"score": None, "dir": None, "conviction": None, "grade": None, "calibrated": False, "factors": {}}
    avail = {k: v for k, v in hr.factors.items() if v is not None and weights.get(k, 0) > 0}
    wsum = sum(weights[k] for k in avail) or 1.0
    det = tx.factor_details(snap.last_feat, h, bench_name)
    factors = {}
    for k, v in hr.factors.items():
        w = weights.get(k, 0.0)
        wn = (w / wsum) if k in avail else 0.0
        factors[k] = {"value": None if v is None else round(v, 2), "weight": round(wn, 2), "contribution": None if v is None else round(v * wn, 3), "detail": det.get(k, "")}
    return {"score": round(hr.comp, 3), "dir": ("long" if hr.raw_dir > 0 else "short") if hr.raw_dir else None,
            "conviction": int(round(hr.conv)), "grade": None, "calibrated": False, "factors": factors}


def build_detail(
    snap: Snapshot,
    plans: dict[str, dict | None],
    cals: dict[str, dict],
    fund: dict | None,
    val: dict | None,
    peers: list[dict],
    quote: dict | None,
    profile: dict,
    cfg: Any,
    bench_name: str | None,
    now_iso: str,
    currency: str,
) -> dict:
    weights = cfg["signals"]["weights"]
    by_h = {}
    for h in HORIZONS:
        blk = _factor_block(snap, h, weights[h], bench_name)
        p = plans.get(h)
        if p:
            blk["conviction"], blk["grade"], blk["calibrated"] = p["conviction"], p["grade"], p["calibrated"]
        elif blk["conviction"] is not None:
            from .analysis.factors import grade_from_conviction

            blk["grade"] = grade_from_conviction(blk["conviction"])
        by_h[h] = blk
    d = snap.detail
    row = snap.row
    prev_close = float(d["chart"]["close"][-2]) if len(d["chart"]["close"]) > 1 and d["chart"]["close"][-2] is not None else None
    price = {
        "last": row["px"], "prev_close": prev_close, "chg_1d": row["chg1d"],
        "day_high": d["chart"]["high"][-1], "day_low": d["chart"]["low"][-1], "volume": row["vol"], "ts": (quote or {}).get("ts") or now_iso,
    }
    if quote and quote.get("price"):
        price["last"] = quote["price"]
        if quote.get("chg_pct") is not None:
            price["chg_1d"] = quote["chg_pct"]
    bt = {}
    for h in HORIZONS:
        b = d["backtest"][h]
        bt[h] = {"stats": b["stats"], "oos": b["oos"], "equity": b["equity"], "trades": b["trades"], "calibration": {k: cals[h].get(k) for k in ("status", "calibrated", "n_trades", "win", "avg_r")} if h in cals else None}
    return {
        "id": snap.id, "sym": snap.sym, "name": snap.name, "market": snap.market, "sector": snap.sector, "kind": snap.kind, "tradable": snap.tradable,
        "currency": currency, "asof": snap.asof, "generated_at": now_iso, "profile": profile, "price": price,
        "chart": d["chart"], "scores": {"by_horizon": by_h}, "plans": plans, "technical": d["technical"],
        "fundamental": fund, "valuation": val, "risk": d["risk"], "backtest": {"horizons": bt}, "peers": peers, "notes": d["notes"],
    }
