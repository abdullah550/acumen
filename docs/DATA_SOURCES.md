# Where Acumen's data comes from

Acumen collects everything itself, from public websites and public APIs, and never asks you for data. This page lists every
source, what it is used for, what it costs you (nothing, apart from the optional CoinGecko key), how it can fail and what Acumen then
does. All of these are free services that nobody has promised to keep the way they are.

**Read this first.** The code that talks to these services was written and tested in an environment that cannot reach any of them,
so every parser is tested against recorded shapes, not against the live services. Before you rely on a number, run

* **Actions > Check data sources > Run workflow** on GitHub (it also runs every Monday on its own), or
* `python -m acumen doctor` on your own computer (`--only psx` or `--only crypto.okx` to check part of it).

Each line of its report says `ok`, `warn` (a source Acumen can do without is down and it says what it falls back to) or `fail` (a source the market
cannot do without is down), and the last lines give one verdict per market: `ok`, `degraded` or `down`. Some providers answer a home
connection but refuse GitHub's servers, or the other way round, so run it from both.

## Pakistan Stock Exchange (about 500 stocks and the KSE-100)

| What | Source | Notes |
|---|---|---|
| Symbol list, sector, today's market watch | PSX data portal, `dps.psx.com.pk` | The exchange's own site back end. Undocumented. Primary source. |
| Daily open, high, low, close, volume for every stock | the same portal, one request per past trading day | Gives every symbol for that day in one reply. The first runs walk backwards from today (a budget of `data.psx.max_requests_per_run`, 450, per run) until `data.psx.history_years` (5) are reached; true high and low are kept for `data.psx.ohlc_days` (400) trading days and the older history is close only. |
| Corporate actions (bonus, rights, dividends) | derived from the portal's "last day close" column | The portal's prices are not adjusted, but its last-day close is. When it differs from yesterday's stored close, all earlier prices are scaled by the ratio. If more than a quarter of all stocks show a "corporate action" on one day, a trading day was missed instead and no adjustment is made. |
| Fundamentals (market cap, P/E, EPS, dividend yield, P/B, ROE ...) | TradingView's public screener (`scanner.tradingview.com`), one request for every Pakistan stock | Unofficial endpoint. TradingView is credited on the app's About page. If it stops answering, fundamentals are missing and the app says so; prices are unaffected. Also a last-resort price snapshot. |
| Fallback history for liquid stocks with too little history, and the KSE-100 if the portal's index route fails | Yahoo Finance chart API | See below. |

How it can fail and what happens:

* **Some servers behind the portal's name do not serve the data routes** (they answer 404 although the site is up). Acumen probes a small route first and, when
  the address DNS gives is bad, tries other known addresses and pins the first that works. The certificate is still checked for the real name.
* **Rate limits.** Requests are spaced `data.psx.min_interval_s` (1.2 s) apart. When the host starts refusing, Acumen stops asking for the rest of the run and
  continues next run. A first full backfill therefore takes several runs for the oldest years; the newest year, which matters most, comes first.
* **A day with no rows** is only remembered as a market holiday once a later day returns rows, so an outage never leaves a permanent hole.
* **The portal changes its pages.** Every parser checks the shape of the reply and refuses to guess (`ParseError`); the market then shows older data with a
  stale warning, and the doctor tells you which route stopped matching.

PSX prices are in rupees. Short selling is not practical for retail investors there, so a short signal on a PSX stock is shown as "avoid", never as an idea.

## Forex, gold, silver and the rupee (about sixty instruments)

Sources, tried in this order, each only for what the one before could not deliver (`data.fx.sources`):

1. **Yahoo Finance** (`query1/query2.finance.yahoo.com`): true open, high, low, close for five years, one request per pair. Gold, silver and USD/PKR come from here too.
   It is an unofficial endpoint and it sometimes refuses shared cloud addresses (HTTP 429). Acumen then switches the host off for the rest of the run, as it must, and moves on.
2. **Frankfurter** (`api.frankfurter.dev`): the European Central Bank's daily reference rates. One fixing per day, so the bars are close only (marked
   `approx_hl`, which the app discloses and which makes the back-test use closing prices only). Every currency against the dollar in a handful of requests, but no metals and no rupee.
3. **fawazahmed0 currency-api** (`cdn.jsdelivr.net`, `*.currency-api.pages.dev`): a daily snapshot of nearly every currency, gold, silver and the rupee, one request
   per missing day. It can only bridge a short gap (`data.fx.fawaz_max_days`, 10 days).

Bars from the last two are provisional: when Yahoo works again it replaces them.

Pairs follow the market convention for the G8 (EUR > GBP > AUD > NZD > USD > CAD > CHF > JPY: the earlier currency is the base); exotics are quoted per US dollar.
The **rupee crosses** (EUR/PKR, AED/PKR ...) are *synthetic*: the dollar rate times the other currency's dollar rate (the dirham and the riyal are pegged, so their
rates are constants). They are shown for information and flagged as not offered by most retail brokers. A day's bar is only kept once it cannot change any more: the
previous UTC day, or today after the New York close on a Friday.

## Crypto (the top coins by market cap that trade on a major exchange)

| What | Source | Notes |
|---|---|---|
| Which coins exist, market cap, rank, supply, all-time high, global figures | CoinGecko (`api.coingecko.com`, free tier) | The free tier allows a few dozen calls a minute; Acumen spaces calls 2.6 s apart (1.2 s with the optional key). A free "demo" key makes the limits steadier: create one at coingecko.com and store it as the repository secret `COINGECKO_API_KEY`. |
| Daily candles | Binance public market data (`data-api.binance.vision`), then OKX, Coinbase, Kraken, KuCoin (`data.crypto.sources`) | Each exchange is asked once per run for its list of USDT/USD markets, so a coin is only requested where it exists. The first that returns enough history wins and is remembered; when the venue changes, new bars are only accepted if they agree with what is stored. KuCoin is last because its daily candle boundary is the least documented. |
| Close-only fallback history | CoinGecko market chart | For coins no exchange lists; limited to `data.crypto.coingecko_fallback_max` (40) per run and to one year on the free tier. These bars have no true high or low. |
| Fear and greed index | alternative.me | One number, shown for context only. It is not used in any signal. |

Stablecoins and wrapped, bridged or staked copies of another coin are left out. A UTC day that has not finished is never stored. Crypto is quoted in US dollars (USDT).

## Macro backdrop

| What | Source |
|---|---|
| Dollar index, US 10-year yield, gold, Brent, S&P 500, VIX, an emerging-markets fund | Yahoo (`data.macro.yahoo_symbols`) |
| Fed funds rate, US 2-year yield, 10-year minus 2-year spread; the 10-year yield again as a fallback for Yahoo's `^TNX`, whose units have changed in the past | FRED, plain CSV, no key (`fred.stlouisfed.org`) |
| Pakistan consumer-price inflation | IMF DataMapper, then the World Bank. These are **annual averages** (the IMF's includes an estimate for the current year), not the latest monthly reading: the Pakistan Bureau of Statistics publishes that as a PDF, which Acumen does not parse. The app says so. |
| State Bank of Pakistan policy rate, and every other central bank's rate used for forex carry | **Typed in by hand** in `rates.policy_rate_pct` of `default_config.yml`. An IMF-reported series on FRED (`INTDSRPKM193N`) replaces the Pakistan figure only when its newest reading is more recent than the table's `rates.as_of`. |

A macro source that fails is replaced by the copy saved on an earlier run (up to 14 days old) and the note says so. **Keep the rate table current**: the policy
rates feed the forex carry factor and the Pakistan real rate. `rates.as_of` is when you last checked them; the app flags the table as stale after 120 days (see the
README, "Keeping rates current").

## What is not used

* No paid data, no broker feed, no scraping behind a login, no news or sentiment feed, no analyst forecasts.
* No **point-in-time fundamentals**. Fundamentals are today's values, so they cannot be back-tested without look-ahead; they are kept out of the back-test and only
  move live conviction by a bounded tilt (`signals.max_tilt_points`, 12 points).
* No intraday data. Everything is daily bars.
* No corporate-action history for crypto and forex (not needed) and no dividends data for PSX beyond what the screener's yield implies.

## Good manners

Acumen identifies itself honestly (`AcumenPersonalAnalytics/1.0`, plus your contact address when you set `ACUMEN_CONTACT`), never rotates addresses or pretends to be a browser,
spaces its requests, caches within a run, honours `429` and `503` by stopping rather than retrying in a loop, and switches a failing host off after a few failures in a row.
It runs a few times a day at most. These are public services: if a provider asks you to stop, stop, and if you want heavier use, take their paid plan. You are responsible for
making sure your use is within each provider's terms.

## When a source disappears for good

Each provider lives in its own module (`acumen/fetch/psx_dps.py`, `psx_tv.py`, `yahoo.py`, `fx.py`, `crypto.py`, `macro.py`), behind a small function that returns daily bars or
a health line, so replacing one means changing one file and its test fixture. The analysis never knows where a bar came from, except for the `approx_hl` flag.
