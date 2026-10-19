"""Small helpers shared by the providers: number and date parsing, sanity checks, health lines."""

from __future__ import annotations

import datetime as dt
import math
import re
import time
from typing import Any, Iterable

import pandas as pd

from ..model import BAR_COLUMNS, SourceStatus, normalise_bars

PKT = dt.timezone(dt.timedelta(hours=5))
_NUM_RX = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?|[-+]?\.\d+")
_EMPTY = {"", "-", "—", "–", "n/a", "na", "nan", "none", "null", "--", "nil"}


def to_float(x: Any) -> float | None:
    """Parse a number out of whatever a website or API gave: ``"1,234.5"``, ``"(12.3)"``, ``"▼ -0.23"``, ``"4.5%"``.

    Returns ``None`` for empty / not-a-number / infinite values, never raises.
    """
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    s = str(x).strip()
    if s.lower() in _EMPTY:
        return None
    neg = s.startswith("(") and s.endswith(")")
    m = _NUM_RX.search(s.replace("−", "-"))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return -v if neg and v > 0 else v


def positive(x: Any) -> float | None:
    v = to_float(x)
    return v if v is not None and v > 0 else None


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_to_date(epoch: float, offset_hours: float = 0.0) -> pd.Timestamp:
    """Seconds since 1970 -> the calendar date in a fixed offset (PSX publishes Pakistan wall-clock seconds, read as UTC)."""
    return pd.Timestamp(dt.datetime.fromtimestamp(float(epoch) + offset_hours * 3600.0, tz=dt.timezone.utc).date())


def epoch_to_date_ms(ms: float) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.fromtimestamp(float(ms) / 1000.0, tz=dt.timezone.utc).date())


def pkt_now(now: dt.datetime | None = None) -> dt.datetime:
    return (now or utc_now()).astimezone(PKT)


def weekdays_back(end: dt.date, count: int) -> list[dt.date]:
    """The ``count`` weekdays ending at ``end`` (inclusive), newest first."""
    out: list[dt.date] = []
    d = end
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out


def bars_frame(rows: Iterable[tuple[Any, ...]], columns: list[str]) -> pd.DataFrame:
    """Build a normalised bar frame from ``(date, *values)`` rows; ``columns`` names the values (subset of BAR_COLUMNS)."""
    recs = []
    for r in rows:
        if r is None or len(r) < 2:
            continue
        rec = {"date": pd.Timestamp(r[0])}
        for name, v in zip(columns, r[1:]):
            rec[name] = to_float(v)
        recs.append(rec)
    if not recs:
        return normalise_bars(pd.DataFrame())
    df = pd.DataFrame(recs).set_index("date")
    return normalise_bars(df)


def clean_bars(df: pd.DataFrame, *, max_gap: float | None = None, label: str = "") -> tuple[pd.DataFrame, list[str]]:
    """Drop bars that cannot be real and say what was dropped.

    * a non-positive or missing close, or a high below the low (swapped, repaired by normalise_bars when possible);
    * when ``max_gap`` is given, a close more than that fraction away from both neighbours (a single-bar spike).
    """
    notes: list[str] = []
    if df is None or len(df) == 0:
        return df, notes
    out = normalise_bars(df)
    if max_gap and len(out) >= 5:
        c = out["close"]
        up = (c / c.shift(1) - 1).abs()
        dn = (c / c.shift(-1) - 1).abs()
        spike = (up > max_gap) & (dn > max_gap)
        if spike.any():
            notes.append(f"{label} dropped {int(spike.sum())} single-bar spike(s)")
            out = out[~spike]
    return out, notes


def status(source: str, ok: bool, message: str = "", items: int = 0, started: float | None = None, now: dt.datetime | None = None, **details: Any) -> SourceStatus:
    return SourceStatus(source=source, ok=ok, message=message, items=int(items), duration_s=0.0 if started is None else max(time.time() - started, 0.0), at=iso(now or utc_now()), details=details)


def last_complete_date(bars: dict[str, pd.DataFrame]) -> pd.Timestamp | None:
    lasts = [b.index[-1] for b in bars.values() if b is not None and len(b)]
    return max(lasts) if lasts else None


def slim(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only the bar columns (plus ``approx_hl`` when present)."""
    cols = [c for c in BAR_COLUMNS if c in frame.columns] + (["approx_hl"] if "approx_hl" in frame.columns else [])
    return frame[cols]


def splice(old: pd.DataFrame | None, extra: pd.DataFrame | None, *, tol: float = 0.03, join_limit: float = 0.2) -> tuple[pd.DataFrame | None, str]:
    """Extend ``old`` at either end with bars from a second source. A day ``old`` already has is never rewritten.

    The extra bars are only used when both sources agree about the days they share (median close within ``tol``, and no
    more than a fifth of the shared days off by more than that); a second source that adjusts for splits differently
    would otherwise put a cliff into the series. With too few shared days the two ends must at least join within
    ``join_limit``. Returns ``(frame, why)``; ``why`` is empty when the extra bars were used.
    """
    if extra is None or len(extra) == 0:
        return old, "no data"
    if old is None or len(old) == 0:
        return normalise_bars(extra), ""
    shared = old.index.intersection(extra.index)
    older = extra[extra.index < old.index[0]]
    newer = extra[extra.index > old.index[-1]]
    rejected = False
    if len(shared) >= 5:
        ratio = extra.loc[shared, "close"] / old.loc[shared, "close"]
        med = float(ratio.median())
        if abs(med - 1.0) > tol or float((ratio - med).abs().gt(tol).mean()) > 0.2:
            return old, f"the two sources disagree by about {abs(med - 1.0) * 100:.0f}%"
    else:
        if len(older) and abs(float(older["close"].iloc[-1]) / float(old["close"].iloc[0]) - 1.0) > join_limit:
            older, rejected = older.iloc[0:0], True
        if len(newer) and abs(float(newer["close"].iloc[0]) / float(old["close"].iloc[-1]) - 1.0) > join_limit:
            newer, rejected = newer.iloc[0:0], True
    if not len(older) and not len(newer):
        return old, "the two sources do not join" if rejected else "nothing to add"
    return normalise_bars(pd.concat([older, old, newer])), ""
