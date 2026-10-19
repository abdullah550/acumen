"""Per-horizon signals for one instrument: factor frame, composite score, conviction, direction and setup names."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import factors as F


@dataclass
class HorizonSignals:
    horizon: str
    fac: pd.DataFrame
    comp: pd.Series  # composite score in [-1, 1] (includes regime tilt)
    conv: pd.Series  # technical conviction 0..100 from |comp| (NaN where comp is NaN)
    dir: np.ndarray  # +1 / -1 / 0 after the entry threshold and market rules (PSX: no shorts)
    raw_dir: np.ndarray  # +1 / -1 / 0 after the entry threshold only
    setup: np.ndarray  # setup name for the direction on each bar (object array)


def horizon_signals(f: pd.DataFrame, tilt: pd.Series | None, horizon: str, scfg: dict, allow_short: bool = True) -> HorizonSignals:
    """Factors, composite and entry signals for one horizon.

    ``tilt`` is the regime tilt aligned to ``f.index`` (or ``None``). ``allow_short=False`` zeroes short
    signals (used for PSX, where retail short selling is not practical); the composite itself is unchanged
    so the app can still report an "avoid" condition.
    """
    fac = F.compute_factors(f, horizon)
    comp = F.composite(fac, scfg["weights"][horizon], tilt)
    conv = 100.0 * ((comp.abs() - 0.10) / F.CONV_SPAN).clip(0.0, 1.0)
    enter = float(scfg["enter_score"])
    c = comp.to_numpy()
    with np.errstate(invalid="ignore"):
        d = np.where(c >= enter, 1, np.where(c <= -enter, -1, 0)).astype(int)
    raw = d.copy()
    if not allow_short:
        d = np.where(d < 0, 0, d)
    up = F.classify_setup(f, fac, +1, horizon).to_numpy()
    dn = F.classify_setup(f, fac, -1, horizon).to_numpy()
    setup = np.where(np.nan_to_num(c) >= 0, up, dn)
    return HorizonSignals(horizon, fac, comp, conv, d, raw, setup)
