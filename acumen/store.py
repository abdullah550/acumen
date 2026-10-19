"""History store: daily bars and small JSON state files, persisted between pipeline runs.

Layout under the store root::

    bars_psx.csv.gz       id,date,open,high,low,close,volume,approx_hl
    bars_crypto.csv.gz
    bars_fx.csv.gz
    state/<name>.json     small state files (ledger, backfill progress, node pins, ...)

The GitHub Actions workflow keeps this directory on a ``data`` branch that is force-pushed as a
single commit each run, so the repository never accumulates history.

Sealing
-------
A free GitHub Pages site needs a public repository, and so does everything on its ``data`` branch: the bars, and above all the
ledger of the ideas Acumen has issued. With a ``key`` (derived from the passphrase) every file is written as AES-256-GCM
ciphertext with an extra ``.enc`` suffix (``bars_psx.csv.gz.enc``, ``state/ledger.json.enc``) and the plain version is deleted.
Opening a sealed store without the key, or with the wrong one, raises :class:`StoreLocked` rather than starting from empty:
silently dropping years of history because of a mistyped secret would be far worse than a failed run.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from cryptography.exceptions import InvalidTag

from . import crypto_box
from .model import BAR_COLUMNS, normalise_bars

_CSV_COLUMNS = ["id", "date", *BAR_COLUMNS, "approx_hl"]
SEALED = ".enc"


class StoreLocked(Exception):
    """The store holds sealed files and the key for them is missing or wrong."""


class HistoryStore:
    def __init__(self, root: str | os.PathLike[str], key: bytes | None = None):
        self.root = Path(root)
        self.key = key
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "state").mkdir(exist_ok=True)

    # ------------------------------------------------------------------ sealing
    def _open(self, path: Path, *, compressed: bool) -> bytes:
        """The plain bytes of a sealed file."""
        if self.key is None:
            raise StoreLocked(f"{path.name} is encrypted but no passphrase was given: set ACUMEN_PASSPHRASE to the passphrase used before")
        try:
            return crypto_box.decrypt_bytes(self.key, path.read_bytes(), compressed=compressed)
        except (InvalidTag, ValueError) as exc:
            raise StoreLocked(f"{path.name} could not be decrypted: the passphrase is not the one the store was written with") from exc

    def _write(self, path: Path, data: bytes, *, compress: bool) -> None:
        """Write ``data`` plain, or sealed (beside it, with the suffix) when there is a key; the other form is removed."""
        sealed = path.with_name(path.name + SEALED)
        if self.key is None and sealed.exists():
            raise StoreLocked(f"{path.name} is sealed in this store: refusing to write a plain copy of it (give the passphrase, or start a new store)")
        target = sealed if self.key is not None else path
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(crypto_box.encrypt_bytes(self.key, data, compress=compress) if self.key is not None else data)
        tmp.replace(target)
        if self.key is not None and path.exists():
            path.unlink()  # the plain copy of a store that has just been sealed

    # ------------------------------------------------------------------ bars
    def _bars_path(self, market: str) -> Path:
        return self.root / f"bars_{market}.csv.gz"

    def load_bars(self, market: str) -> dict[str, pd.DataFrame]:
        path = self._bars_path(market)
        sealed = path.with_name(path.name + SEALED)
        if not path.exists() and not sealed.exists():
            return {}
        try:
            source: Any = io.BytesIO(self._open(sealed, compressed=False)) if sealed.exists() else path
            df = pd.read_csv(source, dtype={"id": "string"}, parse_dates=["date"], compression="gzip")
            out: dict[str, pd.DataFrame] = {}
            if "approx_hl" not in df.columns:
                df["approx_hl"] = False
            for inst_id, part in df.groupby("id", sort=False):
                frame = part.drop(columns=["id"]).set_index("date")
                frame["approx_hl"] = frame["approx_hl"].fillna(False).astype(bool)
                out[str(inst_id)] = normalise_bars(frame)
            return out
        except StoreLocked:
            raise
        except Exception:
            # A truncated or corrupt store must never take the pipeline down: start again from empty
            # and let the backfill rebuild it.
            return {}

    def save_bars(self, market: str, bars: dict[str, pd.DataFrame], keep_days: int | None = None) -> None:
        frames = []
        for inst_id in sorted(bars):
            df = bars[inst_id]
            if df is None or len(df) == 0:
                continue
            part = df.copy()
            if keep_days:
                part = part.iloc[-keep_days:]
            if "approx_hl" not in part.columns:
                part["approx_hl"] = False
            part = part.reset_index()
            part.insert(0, "id", inst_id)
            frames.append(part[_CSV_COLUMNS])
        path = self._bars_path(market)
        if not frames:
            sealed = path.with_name(path.name + SEALED)
            if self.key is None and sealed.exists():
                raise StoreLocked(f"{path.name} is sealed in this store: refusing to delete it without the passphrase")
            for gone in (path, sealed):
                if gone.exists():
                    gone.unlink()
            return
        big = pd.concat(frames, ignore_index=True)
        big["date"] = pd.to_datetime(big["date"]).dt.strftime("%Y-%m-%d")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(big.to_csv(index=False, float_format="%.10g", lineterminator="\n").encode("utf-8"))
        self._write(path, buf.getvalue(), compress=False)  # the gzip is already compressed

    def merge_bars(
        self,
        current: dict[str, pd.DataFrame],
        updates: dict[str, pd.DataFrame],
        overwrite_days: int = 5,
    ) -> dict[str, int]:
        """Merge ``updates`` into ``current`` in place. Returns the number of new rows per id.

        Rows already present are kept, except the most recent ``overwrite_days`` rows of the existing
        series which the update may replace (sources revise the latest sessions, and a bar captured
        mid-session must be replaced by the final one). True high/low never gets overwritten by an
        approximated one.
        """
        added: dict[str, int] = {}
        for inst_id, upd in updates.items():
            upd = normalise_bars(upd)
            if len(upd) == 0:
                continue
            old = current.get(inst_id)
            if old is None or len(old) == 0:
                current[inst_id] = upd
                added[inst_id] = len(upd)
                continue
            cutoff = old.index[-overwrite_days:][0] if len(old) > overwrite_days else old.index[0]
            new_dates = upd.index.difference(old.index)
            replace_dates = upd.index[(upd.index >= cutoff) & upd.index.isin(old.index)]
            merged = old.copy()
            if "approx_hl" not in merged.columns:
                merged["approx_hl"] = False
            if "approx_hl" not in upd.columns:
                upd = upd.assign(approx_hl=False)
            for d in replace_dates:
                row = upd.loc[d]
                old_row = merged.loc[d]
                if bool(row["approx_hl"]) and not bool(old_row["approx_hl"]):
                    # keep the old true high/low, take the update's close/volume
                    merged.loc[d, ["close", "volume"]] = row[["close", "volume"]].to_numpy()
                    merged.loc[d, "high"] = max(old_row["high"], row["close"])
                    merged.loc[d, "low"] = min(old_row["low"], row["close"])
                else:
                    merged.loc[d, upd.columns] = row.to_numpy()
            if len(new_dates):
                merged = pd.concat([merged, upd.loc[new_dates]])
            # Upgrade approximated old rows when the update carries true high/low for them.
            upgrade = upd.index[(~upd["approx_hl"]) & upd.index.isin(merged.index)]
            for d in upgrade:
                if bool(merged.loc[d, "approx_hl"]):
                    merged.loc[d, upd.columns] = upd.loc[d].to_numpy()
            current[inst_id] = normalise_bars(merged.sort_index())
            added[inst_id] = int(len(new_dates))
        return added

    def coverage(self, bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
        if not bars:
            return {"instruments": 0, "bars": 0, "first": None, "last": None, "median_bars": 0}
        lengths = [len(v) for v in bars.values() if len(v)]
        firsts = [v.index[0] for v in bars.values() if len(v)]
        lasts = [v.index[-1] for v in bars.values() if len(v)]
        return {
            "instruments": len(lengths),
            "bars": int(sum(lengths)),
            "first": min(firsts).strftime("%Y-%m-%d") if firsts else None,
            "last": max(lasts).strftime("%Y-%m-%d") if lasts else None,
            "median_bars": int(pd.Series(lengths).median()) if lengths else 0,
        }

    # ------------------------------------------------------------------ json state
    def _state_path(self, name: str) -> Path:
        return self.root / "state" / f"{name}.json"

    def load_json(self, name: str, default: Any = None) -> Any:
        path = self._state_path(name)
        sealed = path.with_name(path.name + SEALED)
        try:
            if sealed.exists():
                return json.loads(self._open(sealed, compressed=True).decode("utf-8"))
            if not path.exists():
                return default
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except StoreLocked:
            raise
        except (OSError, ValueError):
            return default

    def save_json(self, name: str, obj: Any) -> None:
        text = json.dumps(obj, separators=(",", ":"), default=_json_default)
        self._write(self._state_path(name), text.encode("utf-8"), compress=True)


def _json_default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"not JSON serialisable: {type(o)}")
