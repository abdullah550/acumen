"""Features, factors and composite at bar t must not change when later bars are appended."""

import numpy as np
import pandas as pd
import pytest
import yaml
from pathlib import Path

from acumen.analysis import regime as R
from acumen.analysis.features import compute_features
from acumen.analysis.signals import horizon_signals
from tests._synth import make_process

CFG = yaml.safe_load(open(Path(__file__).parents[1] / "acumen" / "default_config.yml"))
S = CFG["signals"]


def _frames(n_full=900, cut=700, seed=5):
    df = make_process(n_full, seed, "drift")
    bench = make_process(n_full, seed + 1, "rw")["close"]
    return df, bench, cut


def test_feature_frame_is_causal():
    df, bench, cut = _frames()
    full = compute_features(df, bench)
    part = compute_features(df.iloc[:cut], bench.iloc[:cut])
    a, b = full.iloc[:cut], part
    bad = []
    for col in a.columns:
        x, y = a[col].to_numpy(dtype=float), b[col].to_numpy(dtype=float)
        same = np.isclose(x, y, rtol=1e-7, atol=1e-9, equal_nan=True)
        if not same.all():
            bad.append((col, int((~same).sum()), int(np.flatnonzero(~same)[0])))
    assert not bad, f"look-ahead in feature columns: {bad}"


@pytest.mark.parametrize("h", ["short", "medium", "long"])
def test_composite_is_causal(h):
    df, bench, cut = _frames()
    tilt_full = R.regime_tilt_series(bench, S["regime_tilt"]).reindex(df.index).ffill().fillna(0.0)
    full = horizon_signals(compute_features(df, bench), tilt_full, h, S)
    dfc, bc = df.iloc[:cut], bench.iloc[:cut]
    tilt_c = R.regime_tilt_series(bc, S["regime_tilt"]).reindex(dfc.index).ffill().fillna(0.0)
    part = horizon_signals(compute_features(dfc, bc), tilt_c, h, S)
    assert np.allclose(full.comp.iloc[:cut].to_numpy(), part.comp.to_numpy(), equal_nan=True, rtol=1e-7, atol=1e-9)
    assert (full.dir[:cut] == part.dir).all()
    assert (full.setup[:cut] == part.setup).all()
