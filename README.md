# Acumen

A personal market-intelligence app for stocks on the **Pakistan Stock Exchange**, **forex** (majors, minors, exotics, rupee crosses, gold and silver) and **crypto**.
It fetches its own data from public sources, runs technical, fundamental, valuation and risk analysis on every instrument it can get data for, back-tests the trade
plans it would have issued, and publishes ranked ideas with an entry, a stop, profit targets, a time estimate and a position size. It installs on your phone like an app
and opens from every device. You never have to feed it data.

> **Estimates, not promises.** Targets, probabilities and "expected R" are model estimates from historical simulation. Markets change, history can mislead, and every
> trade can lose. Acumen is a research tool, not investment advice, and nobody involved is a licensed adviser. It never places a trade for you.

## What to expect from the ideas (please read)

Acumen is deliberately strict about what it calls **actionable**. A market and time horizon can only produce an actionable idea if the plans Acumen would have taken
there in the stored history **beat random entries that use the same stops and targets**, by more than luck can explain (a test that allows for trades that overlap in
time and for each instrument's own luck), the newest 40% of those trades agree, and the expected result after costs is still positive. Everything else is shown as a
*research candidate* with the reason next to it (no edge shown, not tradable, stale data, too illiquid, reward to risk too low ...).

What that means in practice:

* **On real data you may see few actionable ideas, or none, for long stretches.** That is the honest answer when nothing has been shown to work, not a fault. The
  **Evidence** screen says, market by market and horizon by horizon, what passed and what did not.
* **The test has limits.** On 260 simulated markets with nothing to find, it never raised a false alarm at the default setting (520 tests). When an edge of about 0.1 R per trade was planted (0.1 R is 10% of the amount risked on each trade, on average), it was found in between a quarter and 60% of the simulated worlds, depending on market and horizon; an edge of about 0.02 R per trade was never found. With five years of history, small edges cannot be told apart from luck by this or any other method. So a pass means "this looks real", and no pass means "not shown", not "does not exist".
* **The one-year (Investment) horizon is research only**, because five years of history cannot test strategies that hold for a year.
* **Acumen starts a live record the day it first runs**: every actionable idea of conviction 60 or more is followed forward in time with the same entry, stop and target
  rules, so over months you can compare what it said with what happened. That record, not the back-test, is the strongest evidence on the Evidence screen.
* If you would rather see more ideas, you can relax the test (`backtest.edge_alpha: 0.05` in `config.yml`). You then accept roughly a 1-in-20 chance per market and
  horizon of acting on noise. That is your decision to make, and it is why the setting exists.

**What has not been verified.** The code that reads the public data sources was written without access to them. It is tested against recorded shapes, never the live
services. Run **Check data sources** (below) before you trust a number. The data sources are described in `docs/DATA_SOURCES.md`.

## What you get

| Screen | What it is for |
|---|---|
| **Command Center** | One page: market regimes, how many ideas are actionable and why not, the best ideas, data health, the macro backdrop. |
| **Trade Ideas** | Every idea with entry, stop, two targets (and how much to close at each), reward to risk, estimated chance and time to reach each target, conviction grade, position size for your equity and risk profile, and the reasons or blockers. |
| **Screener** | Filter and sort every instrument by score, trend, momentum, valuation, liquidity, sector. |
| **Instrument** | Chart with levels and the plan, scores by factor, technicals, fundamentals and valuation (stocks), risk, the back-test with out-of-sample results, peers. |
| **Portfolio** | Your positions and journal (kept on your device), risk heat, correlation, value at risk, position sizing profiles. |
| **Markets** | Regime of each market, breadth, sectors, forex strength, crypto dominance, macro series and Pakistan rates. |
| **Evidence** | Whether the ideas have earned trust: live record, the test against random entries per market and horizon, calibration of conviction to observed results. |
| **Data & Settings** | Data freshness and source health, encryption, install, backup of your own data, About and credits. |

## Setup (about 30 minutes, once, plus the first run)

You need a computer for this one-time setup, and a free GitHub account. Everything runs on GitHub's servers afterwards and costs nothing: GitHub provides Actions
(the scheduled job) and Pages (the website) free for public repositories.

**Why public?** Free Pages needs a public repository. The code is public, but your **data and the ideas are encrypted** with a passphrase only you know (step 4), and so
is the price history Acumen stores between runs. Without the passphrase set, everything on the site and in the repository is readable by anyone who finds the address.

### 1. Create the repository

1. Sign in at github.com, click **+** > **New repository**.
2. Name: `acumen` (or any name). Choose **Public**. Do **not** tick "Add a README". Click **Create repository**.

### 2. Put the files in

Unzip the project somewhere (for example `Documents\acumen`). Then either:

* **GitHub Desktop (no command line).** Install it from desktop.github.com and sign in. **File** > **Add local repository** > choose the unzipped folder > click
  "create a repository" > **Create repository**. Type a summary ("Add Acumen"), click **Commit to main**, then **Publish repository**, **untick "Keep this code private"**, **Publish repository**.
* **Command line.** In the unzipped folder:
  ```
  git init -b main
  git add .
  git commit -m "Add Acumen"
  git remote add origin https://github.com/YOUR-NAME/acumen.git
  git push -u origin main
  ```

Keep the branch name `main`. (Dragging files into the GitHub web page is not recommended: it limits how many files go up at once and skips the hidden `.github` folder that holds the workflows.)

### 3. Turn on GitHub Pages

Repository > **Settings** > **Pages** > **Build and deployment** > **Source**: choose **GitHub Actions**.

### 4. Add your passphrase (and two optional extras)

Repository > **Settings** > **Secrets and variables** > **Actions**.

* **Secrets** tab > **New repository secret**: name `ACUMEN_PASSPHRASE`, value a long passphrase, such as four or five unrelated words. **Write it down: it cannot be recovered**,
  and the app asks for it once on each device.
* Optional secret `COINGECKO_API_KEY`: a free "demo" key from coingecko.com. It makes the crypto universe download steadier.
* Optional **Variables** tab > **New repository variable**: `ACUMEN_CONTACT`, an email address or web address. It is added to the User-Agent that Acumen sends so a provider can
  reach you if the requests cause trouble. Use an address you do not mind a data provider seeing.

### 5. Check the data sources first

**Actions** tab > **Check data sources** > **Run workflow**. It takes a couple of minutes. Open the run, then its **doctor.txt** (under *Artifacts*) or the log of the *Check every
source* step. Each line says `ok`, `warn` or `fail`; the last lines give a verdict per market. `degraded` means a fallback source is in use, `down` means that market will show
errors. If a source fails from GitHub's servers but answers from your own computer, run `python -m acumen doctor` there too, and see `docs/DATA_SOURCES.md` for what each failure costs.
Do this again from time to time: the sources are websites nobody promised to keep the same, and the check also runs every Monday.

### 6. First run

**Actions** tab > **Refresh Acumen** > **Run workflow**. The first run downloads years of history for about a thousand instruments and can take **an hour or more** (the fetching is
stopped after 105 minutes at the latest; what it had fetched is kept and the next run carries on from there, and the oldest years of Pakistan history keep filling in over the following runs). Later runs
are much shorter; the run's log shows the real time. It then repeats **four times a day** (05:17, 11:17, 17:17 and 23:17 Pakistan time), and again whenever you change the code or `config.yml`.
When it finishes, the address of your app is shown on the run page and under **Settings** > **Pages**: `https://YOUR-NAME.github.io/acumen/`.

### 7. Open it and install it

Open the address, enter the passphrase (it is checked inside your browser and never sent anywhere).

* **Android (Chrome):** menu (three dots) > **Install app** (or **Add to Home screen**).
* **iPhone / iPad (Safari):** Share > **Add to Home Screen**.
* **Windows / Mac / Chromebook (Chrome or Edge):** the install icon in the address bar.

After the first load it also opens offline, with the data of the last refresh.

## Using it

* **Look at the "as of" dates and the data-health line first.** Every market shows when its newest data is from and whether any source failed. A stale market cannot produce
  actionable ideas. The app says so instead of showing old numbers as current.
* **Reading an idea.** *Entry* is a price to buy or sell at (usually a pullback order that expires if it is not reached in a few bars). *Stop* is where the idea is wrong.
  *Targets* are price levels to take profit, with the share of the position to close at each. *Chance* and *days* are the model's estimates from similar past trades, shown with how many trades
  stand behind them. *R* is your risk on the trade (entry to stop), so "+2 R" means twice what you risked. The size is worked out from your account size and the risk profile you choose
  (conservative 0.5%, balanced 1%, aggressive 1.5% of equity per trade, with limits on total risk, positions per sector and share of daily volume).
* **Your journal, positions, watchlist and preferences stay on the device** you enter them on. They are not synced between devices. **Data & Settings** > backup exports them to a file and imports them again.
* **Pakistani stocks are long-only here.** Retail short selling is not practical on PSX, so a short signal is shown as "avoid".
* **Costs are included** in every back-test and expectation (PSX about 0.6% round trip, crypto 0.25%, forex by pair type). Real costs depend on your broker, so adjust `costs` in `config.yml`.

## Keeping it healthy

* **Keeping rates current.** The central-bank policy rates feed the forex carry factor and the Pakistan real rate, and nobody supplies them: they are a table in `acumen/default_config.yml`
  with the date they were last reviewed (`rates.as_of`). The app marks the table **stale** after 120 days. To update it, create a file called `config.yml` in the repository (copy `config.example.yml`),
  and put only what changed:
  ```yaml
  rates:
    as_of: "2026-11-01"
    policy_rate_pct:
      PKR: 10.5
      USD: 3.75
  ```
  Commit it (GitHub's web editor is fine). The change triggers a new refresh. Look the rates up on the central banks' own sites (the State Bank of Pakistan for the rupee).
* **GitHub pauses scheduled jobs in public repositories after 60 days without activity.** If the data stops updating, open the **Actions** tab and click **Enable workflow**, or run *Refresh Acumen* by hand.
* **Changing the passphrase.** The history Acumen keeps between runs is sealed with a key made from the passphrase, so a new passphrase cannot open the old history, and a run with the wrong one
  **stops with an error instead of starting again from empty history.** To change it on purpose: set the new secret, delete the `data` branch (repository > **Branches**, or `git push origin --delete data`), and
  run *Refresh Acumen*; the history, and your live record in it, start again from zero. Then enter the new passphrase on each device (**Data & Settings** > **Lock now**).
* **If a market shows an error**, run *Check data sources*. The rest of the app keeps working: one market failing never stops the others.
* **Logs without opening a run.** The latest source check and the latest refresh log are also kept as one file each on the branches `report-doctor` (`doctor.txt`) and `report-refresh` (`refresh.txt`).
  The repository is public, so your contact address and any secret are blanked out of them first.
* **Settings you may want** (all in `config.yml`, only the keys you change): `app.account_currency` (`PKR` or `USD`), `app.default_equity`, `portfolio.profiles`, `costs`, `universe` filters,
  `backtest.edge_alpha`, `signals.min_exp_r`, the horizons. Everything is documented in `acumen/default_config.yml`.

## Privacy and security

* With `ACUMEN_PASSPHRASE` set, every data file the site serves is encrypted with **AES-256-GCM** (key: PBKDF2-SHA256, 600,000 rounds, from your passphrase), and so is the stored price history on the `data` branch (with a
  different key). The code, the workflows and the app's pages are public; they contain no data of yours.
* The browser keeps a key derived from the passphrase on the device so you are not asked every time. Anyone who can use that browser profile can open the data. **Data & Settings** > **Lock now** removes it.
* Your positions and journal never leave your device. Nothing you do in the app is sent anywhere.
* The only outside requests are the app's own files and data from your GitHub Pages site. The scheduled job asks the public data providers; the User-Agent it sends names the project and, if you set it, your contact.

## Credits

* Price charts: **TradingView Lightweight Charts™**, © TradingView, Inc., Apache License 2.0, https://www.tradingview.com/
* Pakistan fundamentals come from TradingView's public screener. Pakistan prices come from the PSX data portal. Forex, macro and some fallbacks from Yahoo Finance, the European Central Bank (via Frankfurter),
  FRED, the IMF and the World Bank. Crypto from CoinGecko and the public market data of Binance, OKX, Coinbase, Kraken and KuCoin. See `docs/DATA_SOURCES.md`. All are used within what their public, free access allows, and you are responsible for your use being
  within their terms.
* Fonts: IBM Plex Sans and IBM Plex Mono (SIL Open Font License).

## For developers

```
acumen/            Python package: fetch/ (providers), analysis/ (indicators, factors, signals, plans, simulation, calibration, regime, fundamentals, portfolio, ledger), build.py, cli.py
web/               the app: source in src/, built bundle committed in dist/ (esbuild, no framework), service worker in public/
tests/             pytest, runs offline against fixtures
docs/              DATA_CONTRACT.md (the JSON files the app reads), DATA_SOURCES.md, PROJECT_BRIEF.md (rules for contributors)
scripts/           keep_report.sh (keeps a workflow's log on a branch, with secrets blanked out)
.github/workflows/ refresh.yml (scheduled run and deploy), doctor.yml (source check), ci.yml (tests and bundle check)
```

```
pip install -r requirements-dev.txt
python -m pytest tests -q                       # offline; a few minutes
python -m acumen demo --out site/data           # synthetic, clearly labelled demo data (add --noise for markets with nothing to find)
python -m acumen doctor                         # check every data source live
python -m acumen refresh --store store --out site/data   # fetch + analyse + write (what the workflow runs)
cd web && npm ci && npm run build               # rebuild web/dist after changing web/src; commit the result
```

Commands: `refresh` (fetch then build), `fetch`, `build` (from the stored history), `demo`, `doctor`, `version`; exit status 0 = ok, 1 = a run error (for `doctor`: a market has no working source; add `--strict` to also fail on any warning), 2 = bad usage or a locked store.

To look at the app locally, put the demo data next to a copy of the built app and serve that folder (this keeps `web/dist`, which is committed, clean):

```
python -m acumen demo --out site/data
cp -R web/dist/. site/
python -m http.server -d site 8000              # then open http://localhost:8000
```

The demo's names are fictional and the app shows a permanent **DEMO** banner while viewing it. `site/` and `store/` are git-ignored.
