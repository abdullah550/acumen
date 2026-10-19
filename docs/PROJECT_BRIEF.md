# Acumen: project brief (shared context for everyone working on the repo)

## What we are building

**Acumen** is a personal market-intelligence app for one owner in Pakistan. It analyses every instrument it
can get data for in three markets, then publishes ranked, sized, backtested trade ideas with entry, stop,
profit targets and a time horizon:

* **PSX**: every listed equity on the Pakistan Stock Exchange (about 500 trading symbols), plus the KSE-100 as benchmark.
* **Forex**: majors, minors, exotics, PKR crosses, gold and silver.
* **Crypto**: the top few hundred coins by market cap that trade on major exchanges.

The owner never supplies data. A scheduled GitHub Actions job fetches everything from public sources,
runs the analysis, writes JSON files and deploys a static Progressive Web App (PWA) to GitHub Pages,
so it opens from every device, installs on Android, and works offline with the last data.

Inspiration is BlackRock's Aladdin: one screen for risk, regime, screening and portfolio construction.
We build a personal, honest version of that idea.

## Honesty rules (non-negotiable)

1. **No guarantees.** Targets, probabilities and "expected R" are model estimates. Every screen that shows
   a trade plan must be able to show where the number came from (factor breakdown, backtest, calibration).
2. **Show evidence.** Conviction is mapped to observed historical win rates (calibration) when enough data
   exists, and is labelled "uncalibrated" when it does not. Small samples are flagged, never hidden.
3. **No look-ahead.** Every indicator, factor and signal at bar `t` may only use bars `<= t`. Entries in
   backtests happen at the next bar's open. Where the data cannot be point-in-time (fundamentals, carry
   rates) it is excluded from backtests and only applied as a bounded tilt on live conviction.
4. **Demo data is labelled.** Synthetic data exists only for tests and the preview. It uses fictional
   company names, sets `demo: true` in `meta.json`, and the app shows a permanent banner.
5. **Stale is visible.** Every market shows its as-of date and source health. Failed sources degrade the
   app to older data with a warning instead of failing silently.
6. **Be a good citizen to data providers.** Identify the client honestly in the User-Agent, throttle,
   cache, back off on 429/503 (skip the cycle, never retry-storm), never rotate proxies or disguise the client.
7. **Not investment advice.** The app carries a disclaimer and never says "guaranteed", "risk-free" or "sure".

## Architecture

```
GitHub Actions (cron)                                  Browser / Android PWA
  fetch  ->  history store  ->  analysis  ->  JSON  ->  static site on GitHub Pages
  (public APIs)  (data branch)   (pandas)    (encrypted optional)   (reads JSON, works offline)
```

* `acumen/fetch/`     providers per market, HTTP client with throttle/breaker, health reporting
* `acumen/analysis/`  indicators, patterns, levels, factors, signals, trade plans, backtest, calibration,
                      regime, fundamentals, valuation, portfolio, ledger
* `acumen/build.py`   orchestrates everything and writes `site/data/*`
* `acumen/crypto_box.py` optional AES-256-GCM encryption of every data file (key from a passphrase the
                      owner types once per device), so a public Pages site does not republish market data
* `web/`              the PWA (source in `web/src`, built output committed in `web/dist`)
* `tests/`            pytest; everything runs offline

The sandbox used to write this code cannot reach any market data host (egress policy). **All fetch code is
therefore tested against fixtures, never against the live network.** Parsers must validate shapes and
sanity-check values, and `python -m acumen doctor` exists so the owner can check every provider live
from a real machine or from the first Actions run.

## Conventions

* Python 3.11, type hints, no dependencies beyond `requirements.txt` (numpy, pandas, scipy, requests,
  beautifulsoup4, lxml, PyYAML, cryptography, python-dateutil). Dev: pytest, jsonschema.
* Data model is in `acumen/model.py`, storage in `acumen/store.py`, configuration in
  `acumen/config.py` + `acumen/default_config.yml`. Read those first.
* Instrument ids are `PSX:HBL`, `FX:EURUSD`, `CRYPTO:BTC`. Bars are daily, tz-naive, normalised dates.
* Pure functions where possible. No global state. No network in tests. No prints in library code
  (use the `log` callable passed in, default `print` only in CLI entry points).
* Times: PSX sessions are Pakistan dates (UTC+5, no DST). Crypto and FX use UTC dates.
* Currency: PSX prices in PKR, crypto in USD (USDT), FX in the pair's quote currency.
* JSON output must be strict (no NaN/Infinity: use `null`), numbers rounded to sensible precision.

## Definition of done for any module

1. Public functions have docstrings that say what is returned and what "no data" looks like.
2. Unit tests exist and pass with `pytest -q` (no network).
3. No look-ahead (there is a test that appending future bars does not change earlier outputs).
4. Failure of one instrument or one provider never aborts a whole run.
