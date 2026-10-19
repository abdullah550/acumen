# Acumen data contract (schema 1)

The analysis pipeline writes these files into `site/data/`. The web app reads only these files.
Both sides must follow this document. When the pipeline and this document disagree, fix the pipeline.

## Conventions

* Strict JSON. Missing or not computable values are `null`. Never `NaN`, `Infinity` or strings like `"n/a"`.
* Dates `YYYY-MM-DD`. Timestamps ISO 8601 UTC with `Z`.
* Units: any key that starts with `chg`, ends with `_pct`, or is a volatility/drawdown/yield figure is in
  **percent units** (`1.5` means 1.5%). Keys named `p_win`, `prob`, `win` are **fractions** (`0.55` means 55%).
  Money is in the instrument's quote currency (PKR for PSX, USD for crypto, quote currency for FX) unless a key
  says otherwise.
* Parallel arrays (`dates`, `close`, `sma50`, ...) all have the same length. Indicator arrays contain `null`
  where the value is not defined yet.
* Instrument id `"<MARKET>:<SYMBOL>"` upper case: `PSX:DEMOA`, `FX:EURUSD`, `CRYPTO:BTC`. `m` / `market` is `psx|fx|crypto`.
* Horizons are `short` (Swing, days to weeks), `medium` (Positional, weeks to months), `long` (Investment, months to 2 years).
* Grades: `A` conviction >= 75, `B` >= 60, `C` >= 45, `D` below 45 (ideas graded `D` are not published). An `A` is only given when the
  conviction-to-probability mapping of its pool passed the out-of-sample check (`calibrated: true`); otherwise it is shown as `B`.
* Direction `dir`: `long` or `short`. On PSX a `short` signal is not actionable (retail short selling is not
  practical); it is reported with `action: "AVOID"` and `actionable: false`.
* Everything except `meta.json` may be encrypted (see below). The app must handle both forms transparently.

## Files

| Name | Path | Purpose |
|---|---|---|
| meta | `data/meta.json` (always plain) | index of files, market health, sources, config, encryption parameters |
| overview | `data/overview.{json,enc}` | Command Center: regimes, heatmaps, movers, top ideas, portfolio summary, track record |
| instruments | `data/instruments.{json,enc}` | Screener rows for every instrument |
| ideas | `data/ideas.{json,enc}` | All qualified trade ideas with full plans |
| portfolio | `data/portfolio.{json,enc}` | Model portfolios, risk stats, correlations, stress tests |
| calibration | `data/calibration.{json,enc}` | Pooled backtest evidence behind conviction and probabilities |
| ledger | `data/ledger.{json,enc}` | Live track record: ideas issued by Acumen and what happened to them |
| macro | `data/macro.{json,enc}` | Cross-asset and Pakistan macro panel |
| prices | `data/prices.{json,enc}` | Latest quotes for every instrument (refreshed more often than the rest) |
| detail | `data/detail/<market>/<SYMBOL>.{json,enc}` | Full data for one instrument (lazy loaded) |

`<SYMBOL>` is the symbol with any character outside `[A-Za-z0-9._-]` replaced by `_`.

### Encryption

`meta.json` carries `enc`: `null` when files are plain, otherwise

```json
{"alg":"AES-256-GCM","kdf":"PBKDF2-SHA256","iter":600000,"salt":"<base64, 16 bytes>",
 "gzip":true,"check":"<base64 of iv(12)+ciphertext+tag for the plaintext \"acumen-ok\">"}
```

Key = PBKDF2-HMAC-SHA256(passphrase UTF-8, salt, iter) -> 32 bytes. Every file: `iv(12 bytes) || AES-GCM ciphertext+tag`
where the plaintext is the UTF-8 JSON, gzip-compressed first when `gzip` is true. Files use extension `.enc`
and are read as binary. The app derives the key once, verifies it by decrypting `check`, keeps the raw key
in localStorage (per device) and never sends it anywhere.

### meta.json

```json
{
  "schema": 1, "app": "Acumen", "version": "1.0.0",
  "generated_at": "2026-09-30T11:52:10Z", "demo": false,
  "enc": null, "ext": "json",
  "files": {"overview":"overview.json","instruments":"instruments.json","ideas":"ideas.json",
            "portfolio":"portfolio.json","calibration":"calibration.json","ledger":"ledger.json",
            "macro":"macro.json","prices":"prices.json"},
  "detail_path": "detail/{market}/{symbol}.json",
  "markets": {
    "psx":    {"label":"Pakistan Stock Exchange","currency":"PKR","asof":"2026-09-29","instruments":482,
               "status":"ok","history_pct":100,"last_success":"2026-09-30T11:50:02Z","note":null},
    "fx":     {"label":"Forex","currency":"quote","asof":"2026-09-30","instruments":210,"status":"ok","history_pct":100,"last_success":"...","note":null},
    "crypto": {"label":"Crypto","currency":"USD","asof":"2026-09-30","instruments":388,"status":"stale","history_pct":100,"last_success":"...","note":"Binance data unreachable, using OKX"}
  },
  "sources": [{"id":"psx_dps","name":"PSX Data Portal","market":"psx","ok":true,"message":"","at":"2026-09-30T11:49:00Z","items":482,"duration_s":41.2}],
  "health": {"analysed": 455, "failed": 0, "failed_examples": [], "seconds": 46.8},
  "config": {
    "account_currency": "PKR", "default_equity": 1000000,
    "horizons": {"short":{"label":"Swing","min_days":3,"max_days":25,"time_stop_bars":20}, "medium":{...}, "long":{...}},
    "profiles": {"conservative":{"risk_per_trade_pct":0.5,"max_heat_pct":4,"max_positions":6,"min_grade":"A","max_per_sector":2,"max_per_market_pct":60}, "balanced":{...}, "aggressive":{...}},
    "usd_pkr": 283.1, "max_position_pct": 20, "max_pct_of_adv": 10,
    "signals": {"enter_score": 0.3, "max_tilt_points": 12, "min_exp_r": 0.02},
    "grades": {"A": 75, "B": 60, "C": 45}, "min_rr": 1.5
  },
  "disclaimer": "Acumen produces model estimates for research. It is not investment advice..."
}
```

`health`: how many instruments were analysed, how many failed (`failed_examples` names a few with the reason) and how long the analysis took.
A `sources[]` line with `ok: false` is a data source that failed in this run; `message` says how (the app lists these under Data health).

`status` per market: `ok` (fresh), `stale` (latest data older than expected or a source failed and a fallback/old data is used),
`error` (no usable data), `warming` (history backfill still running, `history_pct` < 100).

### overview

```json
{
  "generated_at": "...",
  "markets": {
    "psx": {
      "asof": "2026-09-29",
      "regime": {"code":"bull","label":"Bull, volatile","score":0.42,
                 "trend":{"symbol":"KSE100","close":170944.0,"sma50":165100.0,"sma200":151200.0,"above_200":true,"slope_200_pct":8.1},
                 "volatility":{"daily_vol_pct":1.2,"percentile":63},
                 "breadth":{"pct_above_50":61.2,"pct_above_200":55.0,"adv":210,"dec":180,"unch":30,"new_highs":12,"new_lows":4},
                 "note":"Index above rising 200-day average, breadth healthy."},
      "index": {"symbol":"KSE100","name":"KSE-100","close":170944.0,"chg_1d":0.4,"chg_1w":1.1,"chg_1m":3.2,"chg_ytd":41.0,
                "series":[["2026-01-05",121000.0]], "ma50":[null,121500.0], "ma200":[null,null]},
      "sectors": [{"name":"Commercial Banks","n":12,"chg_1d":0.5,"chg_1w":1.2,"chg_1m":4.1,"chg_3m":9.0,"rs":1.4,"pct_above_50":66.0,"score":0.35,"top":["DEMOA","DEMOB"]}],
      "movers": {"gainers":[{"id":"PSX:DEMOA","sym":"DEMOA","name":"...","px":12.3,"chg_1d":7.5}], "losers":[...], "active":[{"id":"...","sym":"...","name":"...","px":1,"volume":1234567,"value":9999999}]},
      "counts": {"instruments":482,"analysed":455,"ideas":61}
    },
    "fx": {
      "asof": "2026-09-30",
      "regime": {"code":"risk_on","label":"Risk-on, USD softening","score":0.2,"note":"...", "dxy": {"close":101.2,"chg_1m":-1.4}},
      "strength": [{"ccy":"USD","score":0.61,"chg_1d":0.2,"chg_1w":0.9,"chg_1m":2.4,"rank":1}],
      "matrix": {"ccys":["USD","EUR","GBP","JPY","CHF","AUD","NZD","CAD"], "chg_1w":[[null,-0.4,...],[...]]},
      "movers": {"gainers":[{"id":"FX:GBPJPY","sym":"GBPJPY","name":"GBP/JPY","px":201.4,"chg_1d":0.9}], "losers":[...]},
      "counts": {"instruments":210,"analysed":205,"ideas":22}
    },
    "crypto": {
      "asof": "2026-09-30",
      "regime": {"code":"range","label":"Range","score":0.0,"note":"..."},
      "global": {"total_mcap":2.9e12,"btc_dominance":58.1,"eth_dominance":12.4,"mcap_chg_24h":-0.6,"fear_greed":null},
      "heat": [{"id":"CRYPTO:BTC","sym":"BTC","name":"Bitcoin","mcap":1.7e12,"chg_1d":-0.4,"chg_7d":2.1,"chg_30d":5.5}],
      "movers": {...}, "counts": {...}
    }
  },
  "top_ideas": [ <Idea summary> , ... up to 12, best first across markets and horizons ],
  "portfolio": {"profile":"balanced","n":8,"heat_pct":5.8,"exp_r_total":1.9,"by_market":{"psx":45.0,"fx":30.0,"crypto":25.0}},
  "track_record": {"since":"2026-08-01","open":14,"closed":31,"win":0.55,"avg_r":0.38,"note":"Live results since Acumen started issuing ideas"},
  "calibration": {"calibrated":true,"pooled_trades":18500,"note":"..."},
  "alerts": [{"level":"warn","market":"psx","text":"PSX data is 2 sessions old"}, {"level":"info","market":"fx","text":"..."}],
  "macro": {"usd_pkr":283.1,"policy_rate_pk":10.5,"dxy":101.2,"gold":2450.0,"brent":78.1,"vix":17.2}
}
```

<Idea summary> = a flat subset of the full Idea object below, with plain numbers where the full object has nested ones:
`{"id","sym","name","market","horizon","dir","action","actionable","setup","conviction","grade","price","rr","p_win","exp_r","entry":302.6,"stop":290.8,"targets":[326.2,343.9],"eta":[13,31]}`
(`entry` is the entry price, `targets` the two target prices, `eta` the fast and slow calendar-day estimate to the first target). The app may equally use full ideas from `ideas.json`.

`overview.calibration` is `{"calibrated": bool, "pooled_trades": int, "note": str}`: whether any pool's conviction mapping passed the out-of-sample check.
`overview.alerts[]` are `{"level":"warn|info","market":"all|psx|fx|crypto","text"}`. Besides data problems they say, in words, where signals have and have not
beaten random entries (see `calibration.json`), so a reader who never opens the Evidence screen still learns whether anything is actionable.

### Idea object (used in ideas.json and in detail.plans)

```json
{
  "id":"PSX:DEMOA","sym":"DEMOA","name":"Demo Bank Ltd","market":"psx","sector":"Commercial Banks","kind":"equity",
  "horizon":"medium","dir":"long","action":"BUY","actionable":true,
  "setup":"Trend pullback",
  "conviction":78,"grade":"A","score":0.61,"calibrated":true,
  "asof":"2026-09-29","price":250.3,
  "entry":{"type":"limit","px":248.5,"low":246.9,"high":250.3,"valid_bars":5,
           "note":"Buy the pullback toward the 20-day average. Skip if it closes above 253."},
  "stop":236.2,"stop_pct":-5.0,"risk_per_unit":12.3,
  "targets":[
    {"px":267.0,"r":1.5,"pct":7.4,"prob":0.52,"eta_days":[8,20],"scale_out":0.5},
    {"px":279.5,"r":2.5,"pct":12.5,"prob":0.33,"eta_days":[15,40],"scale_out":0.5}
  ],
  "time_stop_days":60,"horizon_days":[15,60],
  "rr":2.1,"p_win":0.54,"exp_r":0.48,"exp_pct":2.6,
  "risk_pct_suggest":1.0,
  "reasons":["Price is above rising 50 and 200-day averages","RSI reset to 46 inside an uptrend"],
  "warnings":["Thin trading: median daily value PKR 6.1m"],
  "factors":{"trend":0.82,"momentum":0.40,"reversion":0.20,"structure":0.35,"volume":0.10,"pattern":0.00,"rs":0.30,"fundamental":0.30,"carry":null},
  "tilt":4.0,
  "backtest":{"n":34,"win":0.56,"avg_r":0.41,"pf":1.7,"max_dd_r":5.2,"avg_bars":17,"reliable":true,
              "oos":{"n":12,"win":0.50,"avg_r":0.22}},
  "evidence":{"pool_n":1450,"pool_win":0.51,"pool_avg_r":0.18,"bucket":"70-80","status":"calibrated","skill":true,"edge_r":0.126},
  "liquidity":{"adv_value":6100000,"tier":"B","max_units_by_adv":12000},
  "blockers":[]
}
```

* `actionable` is `true` exactly when `blockers` is empty. An idea with blockers is still published, as a research candidate, together with the reasons.
* `blockers` codes: `short_psx` (a short signal on the Pakistan Stock Exchange), `not_tradable` (index, or an instrument a retail trader cannot trade),
  `stale` (the market's latest data is too old), `illiquid` (tier D liquidity outside forex), `no_edge` (the market and horizon have not been shown to beat
  random entries, see `calibration.json`; includes pools that cannot be tested because the history is too short for the horizon), `no_evidence` (no pooled
  result to take an expectation from), `negative_expectancy` (pooled expected result after costs is not above `config.signals.min_exp_r`),
  `rr` (reward to risk below `config.min_rr`), `wide_stop` (stop wider than the market's limit, so the position cannot be sized sensibly).
* `evidence.status` is the pool's calibration status (`calibrated`, `unvalidated`, `insufficient`); `evidence.skill` is whether the pool beat random entries
  (`calibration.json` -> `edge.passed`); `evidence.edge_r` is the pool's average advantage over random entries in R per trade, before costs.

* `action`: `BUY`, `SELL` (short in FX/crypto), or `AVOID` (PSX short signal, non-tradable instrument).
* `entry.type`: `market` (act at next open) or `limit` (wait for price inside `[low, high]`).
* `targets[].prob` is the modelled probability of reaching that target before the stop. `eta_days` is a [fast, slow] range in calendar days.
* `p_win` is the probability that the whole trade ends with a positive result net of costs. `exp_r` is the expected result in units of initial risk, net of costs.
* `risk_per_unit` = |entry reference - stop| in quote currency per unit, so the app computes `units = equity * risk% / risk_per_unit`.
* `liquidity.max_units_by_adv` = units that would equal `max_pct_of_adv` of average daily traded value. `tier` A (deep) to D (very thin).

### instruments.json

```json
{"generated_at":"...","rows":[
 {"id":"PSX:DEMOA","m":"psx","sym":"DEMOA","name":"Demo Bank Ltd","sec":"Commercial Banks","kind":"equity","tradable":true,
  "px":250.3,"asof":"2026-09-29","chg1d":0.8,"chg1w":1.9,"chg1m":4.2,"chg3m":9.9,"chg6m":18.0,"chg1y":41.0,"ytd":22.0,
  "vol":812345,"adv":6100000,"liq":"B",
  "rsi":56.2,"adx":24.1,"atrp":2.1,"bbw":6.4,"d50":3.1,"d200":12.5,"above50":true,"above200":true,
  "hi52d":-4.2,"lo52d":48.0,"rvol":1.3,"vol30":28.4,"mdd1y":-17.5,"sharpe1y":1.1,"beta":0.9,
  "pe":6.1,"pb":1.2,"dy":8.5,"roe":22.0,"eps":41.0,"mcap":5.2e10,"fv":290.0,"upside":15.9,
  "sig":{"short":{"dir":"long","conv":66,"grade":"B","score":0.41},"medium":{"dir":"long","conv":78,"grade":"A","score":0.61},"long":{"dir":null,"conv":52,"grade":"C","score":0.18}},
  "best":{"h":"medium","dir":"long","conv":78,"grade":"A"},
  "spark":[0.0,0.02,...30 values, close normalised to first=0, in fraction],
  "bars":1180,"flags":["approx_hl"]}
]}
```

`dir` inside `sig` is `null` when the composite is below the entry threshold (no idea). `flags` may contain
`approx_hl`, `stale`, `low_liq`, `new`, `no_fundamentals`, `not_tradable`. FX/crypto rows carry the same keys; fundamentals keys are `null` for FX,
crypto rows use `mcap`, `rank` (extra key) and `pe/pb/dy/roe/eps` are `null`. FX rows add `carry` (annualised rate differential, percent).

### ideas.json

`{"generated_at":"...","ideas":[<Idea>...]}` sorted by conviction descending. Contains every idea with `dir` set, all three horizons.
Actionable ideas first, `AVOID` ideas after.

### detail/<market>/<SYMBOL>.json

```json
{
  "id":"PSX:DEMOA","sym":"DEMOA","name":"...","market":"psx","sector":"...","kind":"equity","tradable":true,
  "currency":"PKR","asof":"2026-09-29","generated_at":"...",
  "profile":{"...market specific..."},
  "price":{"last":250.3,"prev_close":248.3,"chg_1d":0.8,"day_high":251.0,"day_low":247.0,"volume":812345,"ts":"2026-09-29T11:00:00Z"},
  "chart":{
    "dates":["2026-01-05"],"open":[0],"high":[0],"low":[0],"close":[0],"volume":[0],
    "overlays":{"sma20":[],"sma50":[],"sma200":[],"ema20":[],"bb_upper":[],"bb_lower":[],"supertrend":[],
                "donchian_high":[],"donchian_low":[],"ichimoku_a":[],"ichimoku_b":[]},
    "panes":{"rsi":[],"macd":[],"macd_signal":[],"macd_hist":[],"stoch_k":[],"stoch_d":[],"adx":[],"plus_di":[],"minus_di":[],
             "atr":[],"obv":[],"cmf":[],"mfi":[]},
    "levels":[{"px":245.0,"kind":"support","strength":0.8,"label":"Support 245.0"}],
    "markers":[{"date":"2026-09-10","kind":"pattern","label":"Bullish engulfing","dir":"long"}],
    "weekly":{"dates":[],"close":[],"sma10":[],"sma40":[]}
  },
  "scores":{"by_horizon":{"short":{"score":0.41,"dir":"long","conviction":66,"grade":"B","calibrated":true,
     "factors":{"trend":{"value":0.8,"weight":0.15,"contribution":0.12,"detail":"Above SMA20/50/200, ADX 24"}}}, "medium":{...},"long":{...}}},
  "plans":{"short":<Idea|null>,"medium":<Idea|null>,"long":<Idea|null>},
  "technical":{
    "summary":"Uptrend with a controlled pullback ...",
    "trend":{"label":"Uptrend","score":0.8,"points":["Close above SMA50 and SMA200","ADX 24 (trending)"]},
    "momentum":{"label":"Neutral","score":0.1,"points":["RSI 56","MACD above signal, histogram rising"]},
    "volatility":{"label":"Normal","score":0.0,"points":["ATR 2.1% of price","Bollinger width at 40th percentile"]},
    "volume":{"label":"Accumulation","score":0.3,"points":["CMF +0.12","Up-day volume 1.3x average"]},
    "indicators":[{"name":"RSI(14)","value":56.2,"signal":"neutral"},{"name":"MACD(12,26,9)","value":1.4,"signal":"bullish"}],
    "patterns":[{"date":"2026-09-10","name":"Bullish engulfing","dir":"long"}]
  },
  "fundamental":null,
  "valuation":null,
  "risk":{"vol_ann":28.4,"beta":0.9,"beta_vs":"KSE100","var95_1d":-2.7,"cvar95_1d":-3.9,"max_dd_1y":-17.5,
          "downside_dev":18.0,"sharpe_1y":1.1,"sortino_1y":1.6,"gap_freq":4.0,"corr_benchmark":0.72,
          "liquidity":{"adv_value":6100000,"tier":"B","days_to_exit_1pct_equity":0.4}},
  "backtest":{"horizons":{"short":{"stats":{"n":40,"win":0.5,"avg_r":0.3,"pf":1.5,"max_dd_r":6.0,"avg_bars":9,"reliable":true},
      "oos":{"n":14,"win":0.5,"avg_r":0.2},
      "equity":[["2024-01-10",0.0],["2024-02-01",1.2]],
      "calibration":{"status":"unvalidated","calibrated":false,"n_trades":1438,"win":0.4124,"avg_r":0.1582},
      "trades":[{"entry_date":"2026-08-01","exit_date":"2026-08-12","dir":"long","entry":240.0,"exit":255.0,"r":1.4,"bars":8,"reason":"target1"}]},
    "medium":{...},"long":{...}}},
  "peers":[{"id":"PSX:DEMOB","sym":"DEMOB","name":"...","conviction":70,"chg1m":3.1,"pe":5.5}],
  "notes":["High and low prices before 2025-06 are approximated from open and close."]
}
```

`fundamental` (PSX equities, crypto) and `valuation` (PSX equities):

```json
"fundamental":{
  "metrics":{"pe":6.1,"pb":1.2,"dy":8.5,"roe":22.0,"eps":41.0,"bvps":208.0,"mcap":5.2e10,"payout_pct":52.0,"eps_growth_pct":12.0,"net_margin_pct":31.0},
  "sector_rel":{"pe_vs_sector":-0.22,"pb_vs_sector":-0.10,"dy_vs_sector":0.35,"roe_vs_sector":0.18},
  "scores":{"value":0.5,"quality":0.4,"income":0.6,"growth":0.2,"safety":0.1,"composite":0.4},
  "flags":["Dividend paid in each of the last 5 years"],
  "coverage":0.8,
  "history":{"dividends":[{"date":"2026-08-10","amount":5.0}],"eps":[{"period":"2025","value":38.0}]}},
"valuation":{
  "models":[{"name":"Graham number","value":260.0,"weight":0.2,"note":"sqrt(22.5 x EPS x BVPS)"},
            {"name":"Sector P/E","value":290.0,"weight":0.4,"note":"EPS x sector median P/E 7.1"}],
  "fair_value":285.0,"low":255.0,"high":310.0,"upside_pct":13.9,"margin_of_safety_pct":12.2,
  "verdict":"undervalued","confidence":"medium","notes":["Cost of equity assumed 17%"]}
```

Crypto `fundamental.metrics`: `mcap, rank, vol_mcap, circ_supply_pct, ath_dd_pct, age_days`. `valuation` is `null` for crypto and FX.

Market-specific `profile`:

* PSX: `{"listed_in":["KSE100","ALLSHR"],"shares":1.2e9,"free_float_pct":35.0,"mcap":5.2e10,"sector_code":"0825","is_etf":false}`
* FX: `{"base":"EUR","quote":"USD","group":"major|minor|exotic|pkr|metal","pip":0.0001,"carry_pct":1.75,"synthetic":false,"retail_tradable":true}`
* Crypto: `{"rank":12,"mcap":1.2e10,"ath":69000,"ath_date":"2021-11-10","ath_dd_pct":-38.0,"circ_supply":1.2e8,"max_supply":null,"exchange":"binance_vision","cg_id":"bitcoin"}`

### portfolio.json

```json
{"generated_at":"...","equity_reference":1000000,
 "profiles":{"balanced":{
   "label":"Balanced","risk_per_trade_pct":1.0,"max_heat_pct":6.0,
   "positions":[{"id":"PSX:DEMOA","sym":"DEMOA","name":"...","market":"psx","sector":"Commercial Banks","horizon":"medium","dir":"long",
                 "conviction":78,"grade":"A","entry":248.5,"stop":236.2,"target1":267.0,"target2":279.5,
                 "risk_pct":1.0,"weight_pct":14.2,"rr":2.1,"p_win":0.54,"exp_r":0.48}],
   "stats":{"n":8,"heat_pct":5.8,"exp_r_total":2.4,"expected_return_pct":2.4,"worst_case_pct":-5.8,"var95_pct":-3.1,"cvar95_pct":-4.4,
            "diversification_ratio":1.35,"by_market":{"psx":45.0,"fx":30.0,"crypto":25.0},"by_sector":{"Commercial Banks":14.2},"by_horizon":{"short":30.0,"medium":50.0,"long":20.0}},
   "correlation":{"ids":["PSX:DEMOA"],"matrix":[[1.0]]},
   "stress":[{"name":"KSE-100 falls 10%","pnl_pct":-3.4,"detail":"Uses each holding's beta to KSE-100"}]}},
 "notes":["Sizes assume every stop is hit at its level; gaps can be worse."]}
```

`risk_pct` is the suggested loss if the stop is hit, in percent of equity; `weight_pct` the position value as percent of equity
at `equity_reference` (the app recomputes both for the user's own equity and risk setting).

### calibration.json

```json
{"generated_at":"...","period":["2021-02-11","2026-10-13"],
 "markets":{"psx":{"horizons":{"medium":{
    "status":"unvalidated","calibrated":false,"n_trades":1438,"win":0.4124,"avg_r":0.1582,"hit1":0.4019,"hit2":0.2629,
    "buckets":[{"lo":30,"hi":40,"n":375,"win":0.36,"avg_r":0.003,"ci":[0.313,0.41]}],
    "mapping":[[0,0.4124],[5,0.4124]], "mapping_r":[[0,0.158]], "mapping_t1":[[0,0.402]], "mapping_t2":[[0,0.263]],
    "oos":{"n":576,"spearman":0.017,"p":0.28,"rho_ci":[-0.039,0.068],"top_half_avg_r":0.185,"bottom_half_avg_r":0.055,"passed":false},
    "eta_bars":{"t1":[9,22],"t2":[18,36],"empirical":true},
    "note":"Not validated (conviction did not rank newer trades reliably). Probabilities shown are pooled base rates; use conviction only as a screening rank.",
    "baseline":{"n":8706,"win":0.3704,"avg_r":0.0086},
    "edge_r":0.1255,"edge_se":0.0471,
    "edge":{"tested":true,"passed":true,"n":1438,"n_base":8706,"matched":1437,"instruments":64,"spans":20.5,"lag_days":98,"match_days":14,"df":19,
            "edge_r":0.1255,"se":0.0471,"ci":[0.0059,0.2451],"p":0.0077,"recent_r":0.1244,"plan_r":0.2942,"base_r":0.1687,"alpha":0.01,"level":0.98,"why":""},
    "close_basis_share":0.084}}}},
 "caveats":["..."]}
```

One pool per market and horizon (`psx|fx|crypto` x `short|medium|long`), made of every trade the plans would have taken in the stored history of that market.
Pooling is what gives enough trades to say anything; a single instrument rarely has them.

*Conviction to probability.* `status` is `calibrated` when the conviction-to-outcome mapping, fitted on the older 60% of the trades, ranked the newer 40% correctly
(`oos.passed`); `unvalidated` when it did not (every `mapping*` is then flat, the pooled base rate, and the note says so); `insufficient` below `n_trades`
200. `mapping` is win probability against conviction (pairs `[conviction, value]` on a grid of 5), `mapping_r` the expected result in R after costs,
`mapping_t1` and `mapping_t2` the probability of reaching each target before the stop. `buckets` always shows the observed results by conviction band.

*Edge against random entries* (`edge`). For every plan trade, the same exits (stops, targets, time stop) are applied to random entries (`baseline`: several independent
streams of them per instrument, same direction as the plan trade). The plan trade is compared, before costs, with the average of the random entries opened on its own
day or in the next `match_days` days, so the market's own moves cancel and only skill is left. `edge_r` is the average difference in R per trade; `plan_r` and `base_r` the
average results behind it. The uncertainty `se` allows for what makes trades dependent: trades opened within `lag_days` of each other share the same price moves
(Newey-West over calendar days) and the trades of one instrument share that instrument's luck (clustering by instrument). `p` is one-sided (t distribution, `df` =
the smaller of `instruments` and `spans`, less one) and `ci` the two-sided interval of level `level` = 1 - 2 `alpha`. `recent_r` is the same difference over the newest
40% of the trades. `passed` needs all of: `n` at least 200 and `matched` at least 200 (trades with three or more random entries around their day), `spans` (history
length in units of `lag_days`) at least 10, `edge_r` > 0 with `p` < `alpha`, and `recent_r` > 0. When `tested` is `false`, `why` says what was missing, for example a horizon
too long for the history ("the trades cover 4.4 stretches of 410 days (need 10)"). The 1-year horizon is untestable with five years of data and is therefore never actionable.

Validation on simulated markets (the real simulator, banked worlds): with nothing to find (100 stock-like, 100 crypto-like and 60 forex-like worlds, so 520 pools that were testable), `passed` at `alpha` 0.01 happened in none of them (a perfectly calibrated test would give about 5); at `alpha` 0.05 in 0% to 5% of worlds depending on market and horizon, 13 of 520 overall (the requirement that the newest 40% of trades agree makes it conservative). With a trend planted that gives the plan about +0.08 to +0.15 R per trade (before costs), `passed` at 0.01 in 25% to 60% of worlds (80% to 85% at 0.05); with a planted edge of about 0.01 to 0.02 R per trade it passed in none at 0.01. With five years of data, small edges cannot be told apart from luck by this or any other method.

An idea is actionable only when its pool has `edge.passed` and a positive expected result after costs, so a pool can beat random entries and still produce no
actionable idea because costs eat the difference.

`edge_r` and `edge_se` at pool level repeat `edge.edge_r` and `edge.se` when the test ran (otherwise the plain, optimistic comparison with all random entries).
`close_basis_share` is the share of trades tested against closing prices only because the instrument's high and low were approximated.

`caveats` lists what the numbers do not cover (survivorship bias, costs, intrabar order, approximated highs and lows).

### ledger.json

```json
{"generated_at":"...","stats":{"since":"2026-08-01","open":14,"closed":31,"win":0.55,"avg_r":0.38,"by_horizon":{"short":{"closed":12,"win":0.5,"avg_r":0.2}},"by_market":{}},
 "open":[{"id":"PSX:DEMOA","horizon":"medium","dir":"long","issued":"2026-09-01","entry":248.5,"stop":236.2,"target1":267.0,"target2":279.5,"conviction":78,"status":"pending|filled","last":251.0,"r_now":0.2}],
 "closed":[{"id":"...","horizon":"short","dir":"long","issued":"2026-08-03","closed":"2026-08-11","entry":10.0,"exit":11.2,"r":1.3,"reason":"target1|target2|stop|time|expired"}]}
```

Only actionable ideas of conviction 60 or more are recorded, once per instrument and horizon, and followed with the same rules as the backtest
(`pending`: the entry has not been reached; `filled`: in the trade, `r_now` is the open result; a closed line carries `exit`, `r` and `reason`).
Closed lines are kept for a year. The ledger is the only part of the output that cannot be recomputed from market data: it lives in the history store.

### macro.json

```json
{"generated_at":"...",
 "series":{"USDPKR":{"label":"USD/PKR","last":283.1,"asof":"2026-09-29","chg_1d":0.0,"chg_1w":0.1,"chg_1m":0.3,"spark":[283.0,...30]},
           "DXY":{...},"GOLD":{...},"BRENT":{...},"SPX":{...},"VIX":{...},"US10Y":{...}},
 "rates":{"as_of":"2025-12-15","stale":true,"policy_pct":{"USD":3.75,"PKR":10.5}},
 "pakistan":{"policy_rate":10.5,"cpi_yoy":4.1,"real_rate":6.4,"asof":"2026-08-31","note":null}}
```

Each `series` entry also has `unit` (`"index"`, `"percent"`, `"price"`). `rates.policy_pct` comes from the configuration (`rates.policy_rate_pct`), not from a feed:
`rates.as_of` is the date it was last reviewed and `stale` becomes `true` after 120 days. Forex carry and the Pakistan real rate use these values, so keep them current
(README, "Keeping rates current"). `pakistan` is read from the data sources when they answer; `asof` is the date of the observation, `note` says when it is a fallback.

### prices.json

`{"generated_at":"...","quotes":{"PSX:DEMOA":[250.3,0.8,"2026-09-29T11:00:00Z"],"FX:EURUSD":[1.0842,-0.1,"..."]}}` where each
value is `[price, chg_1d_pct, iso_timestamp]`. The app overlays these on ledger positions and watchlists to show live-ish P/L
and to tell when a stop or target level has been crossed.

## History store (not read by the app)

Between runs the pipeline keeps price history and small state files in a store directory (`--store`, default `store/`; on GitHub it is the single commit of the
`data` branch): `bars_<market>.csv.gz` (columns `id,date,open,high,low,close,volume,approx_hl`) and `state/<name>.json` (the ledger, backfill progress, node pins).
With a passphrase every store file is sealed: `bars_psx.csv.gz.enc`, `state/ledger.json.enc`, AES-256-GCM, key = PBKDF2-HMAC-SHA256 of the passphrase with its own
salt (different from the site's key, so the browser never holds the key to the history), same `iv || ciphertext+tag` layout, gzip inside for JSON state and none for the
bars (already gzip). The pipeline never writes a plain copy beside a sealed file; opening a sealed store without the passphrase, or with a wrong one, stops the run with an
error instead of starting again from empty history. A plain store is sealed the next time it is written with a passphrase.

## Screens and where their data comes from

| Screen | Files |
|---|---|
| Command Center | overview (+ meta for freshness) |
| Ideas | ideas (+ instruments for filters/sparklines, prices for live price) |
| Screener | instruments |
| Instrument | detail (+ prices) |
| Portfolio | portfolio, ideas, user settings from localStorage |
| Markets | overview (sectors, strength, heat), macro |
| Evidence (track record and calibration) | ledger, calibration |
| Data health | meta |
