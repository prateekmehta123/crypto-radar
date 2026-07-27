# AI Crypto Radar v1

Binance USDⓈ-M perpetual futures scanner, setup generator, and walk-forward backtester.
Public market data only — **no API keys anywhere in this codebase**. It cannot place an
order even if you asked it to.

```
radar/
  client.py     Binance public REST client, dual rate limiter, mirror hosts
  features.py   feature engineering — one code path for live and backtest
  score.py      transparent weighted scoring + entry/stop/target construction
  store.py      SQLite snapshot store (this is how you get OI history)
  scan.py       the scanner CLI
  api.py        stdlib HTTP server + background scan loop
web/index.html  the dashboard (single file, no build step)
  backtest.py   triple-barrier simulation, costs, walk-forward weight tuning
  model.py      optional P(target before stop) model, purged CV + calibration
selftest.py     offline end-to-end test, no network needed
```

---

## Read this before you run anything

Four things in the original spec are not buildable from public Binance data. Skipping them
is not a limitation of effort; it is what the data allows.

**Open interest history stops at 30 days.** `/futures/data/openInterestHist` retains one
month. There is no endpoint that returns 2023 open interest. So "backtest the OI signals
over 1–3 years" cannot be done today by anyone, at any price, from this API. Two
consequences baked into the design:

- Long backtests run on kline-derived features (price, volume, taker buy/sell, CVD,
  structure, volatility) plus funding — all of which *do* go back years — with the OI
  blocks marked unavailable and excluded from the weighted average rather than treated
  as zero.
- `store.py` writes every scan snapshot to SQLite from the first run. After ~30 days you
  own OI history that Binance no longer serves, and the OI blocks become backtestable.
  Start the scanner now even if you don't trade it for a month.

**Spoofing, iceberg, and hidden-order detection are not implemented.** These require
full order-by-order L3 data. Binance publishes L2 depth snapshots — aggregated price
levels — where a pulled quote and a filled quote look identical. Every retail "spoofing
detector" is guessing. I left it out rather than ship a coin flip with a confident label.

**Liquidation heatmaps are estimates, everywhere.** Nobody, including the commercial
vendors, sees resting stop orders. What is inferable is where *price structure* implies
stops sit, so `liquidity_targets()` finds clusters of near-equal highs and lows and the
setup builder uses them as targets. It's labelled as inference, not as data.

**Whale wallet flows, exchange reserves, unlocks, and listings need paid feeds** —
Glassnode, CryptoQuant, Nansen. Not in scope here. The scoring aggregator re-normalises
over available blocks, so adding a block later is a config change, not a rewrite.

And one thing about the spec's framing: nothing in `score.py` is called a probability.
"Pump Probability: 81%" from a hand-weighted sum is a number that feels like information
and isn't. The blocks output a signed score and a conviction. Actual calibrated
probabilities only come out of `model.py`, which is fitted on labelled outcomes and
prints a reliability table you should read before the AUC.

---

## Install

```bash
pip install -r requirements.txt
python selftest.py          # no network needed; verifies the whole pipeline
```

`selftest.py` runs synthetic random-walk data through features → score → setup →
simulation → ML. Two of its checks matter most: past feature values must not change when
future bars change (no lookahead), and the strategy must show **no** edge on random data
after costs. If a backtest ever shows a beautiful equity curve, run this first — a
lookahead bug produces exactly that.

## Run the dashboard

```bash
python main.py        # then open http://localhost:8080
```

Binds `127.0.0.1` by default, so this works with no configuration. Exposing it to a
network needs `HOST=0.0.0.0` and `RADAR_PASSWORD` — the app refuses to publish market
data on a public interface without a password.

The scan loop runs in a background thread; HTTP handlers only read the last published
snapshot, so refreshing never triggers a scan. No web framework — five read-only GET
endpoints on `http.server` handle one user polling every 20 seconds without adding
FastAPI and uvicorn as install-time risks.

## Scan from the CLI

```bash
# one pass over the top 150 perps by 24h volume
python -m radar.scan --show 25

# run continuously, writing snapshots to SQLite and a JSON feed
python -m radar.scan --loop 300 --db radar.db --json-out latest.json
```

```
SYMBOL        DIR    SCORE  CONV REGIME            FUND%   SQZ        ENTRY         STOP          TP2  RISK%  RATING
SOLUSDT       LONG    71.3  0.42 new longs        0.0112    63       184.22       180.91       190.84   1.80  Medium
```

`CONV` (conviction) is the column to sort on, not `SCORE`. It's `|net| × agreement ×
coverage` — a 71 built from six blocks that agree is worth more than a 78 from two blocks
with the rest missing.

Useful flags: `--interval`, `--top-n`, `--deriv-top`, `--min-conviction`, `--atr-mult`,
`--account-risk-pct`.

### Rate limits

Two independent buckets, both enforced client-side with sliding windows, plus backoff on
429/418 and self-throttling from the `X-MBX-USED-WEIGHT-1M` header:

| bucket | limit | default usage per cycle |
|---|---|---|
| `/fapi/*` weight | 2400/min | ~350 |
| `/futures/data/*` | ~1000/5min | 180 |

Bulk endpoints do the heavy lifting — `ticker/24hr` and `premiumIndex` return every
symbol in one request. Per-symbol derivatives are fetched only for the top `--deriv-top`
by preliminary score.

The spec's "every USDT perp every 2 minutes" is roughly 1,300 `/futures/data` requests per
cycle against a ~1,000-per-5-minute limit. That is a same-day IP ban. The defaults sit at
about a third of both limits.

## Backtest

```bash
python -m radar.backtest \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
  --interval 15m --days 365 --folds 4 --random-search 25 \
  --max-bars 48 --atr-mult 1.5 --out backtest_report.json
```

First run downloads and caches klines to `data/` (slow — a year of 15m bars is ~35k bars
per symbol). Later runs read the cache.

What it does that matters:

- **Entry fills at the open of the bar after the signal.** Never the signal bar's close.
- **Stop and target hit in the same bar counts as a stop.** Without tick data you can't
  know the order. Assuming the win is how a 45% strategy backtests at 70%.
- **Costs are charged**: taker fee both sides, slippage, and funding actually paid over
  the hold. Tune `--taker-fee` and `--slippage`; raise slippage hard for thin alts.
- **Weights are tuned in-sample per fold and reported out-of-sample.** Read the
  `out_of_sample` block. The tuned in-sample numbers are there so you can measure the gap.

That gap is the point. On pure synthetic noise the harness produces in-sample −0.03 R and
out-of-sample −0.22 R from the same weights — the search finds patterns in randomness and
they evaporate. Expect a smaller version of the same thing on real data. If your OOS
result is close to your IS result, you have something. If OOS collapses, the weights fit
noise, and no amount of extra features will fix it.

The objective is `total_R − 1.5 × max_drawdown_R`, not win rate. Win rate is trivially
gamed by widening stops until everything eventually comes back.

## Probability model (optional)

```bash
python -m radar.model --days 365 --direction LONG --tp-mult 2.0 --max-bars 48
```

Labels every bar with whether a 2R target was hit before a 1.5×ATR stop, fits a gradient
boosting classifier, and evaluates with **purged, embargoed** time-series CV — a trade
open for 48 bars leaks its label into any test set that starts within 48 bars, so
overlapping training rows are dropped and a buffer is embargoed after each split.

Read the reliability table before the AUC:

```
bucket    n  predicted  actual
0.5-0.6  412      0.548   0.531
0.6-0.7  201      0.641   0.618
```

Predicted and actual should track. A model with 0.60 AUC and honest probabilities is
tradeable; one with 0.68 AUC that says 80% when it means 55% will size you into ruin.
`brier_of_always_base_rate` is the benchmark — if the model's Brier score doesn't beat
constantly predicting the base rate, it has learned nothing.

## Deployment

**Not a developer? Read START-HERE.md** — it covers running it locally first, then a
step-by-step AWS Lightsail deployment with no assumed command-line knowledge.

For everyone else, see DEPLOY.md. Short version: EC2 `t4g.small` in `ap-south-1` (US regions are
geo-blocked by Binance), `deploy/install.sh` does the install, systemd keeps it
alive, and either an SSH tunnel or nginx+TLS fronts the dashboard. A Dockerfile is
included for ECS — mount EFS at `/data` there, or you lose the accumulated
open-interest history on every restart.

The app refuses to bind a public interface without `RADAR_PASSWORD` set.

## Fitting your existing stack

`--json-out latest.json` produces a flat JSON feed. Your GitHub Pages frontends can read
it directly if the scanner runs on Railway and serves the file, which matches the pattern
you already use for the BTC dashboard — and it sidesteps the CORS problem, since the
browser hits your host rather than Binance.

## Honest expectations

This gives you a repeatable, cost-aware, lookahead-free research loop and a ranked
watchlist with pre-computed invalidation levels. It does not give you an edge — it gives
you the apparatus to find out whether one exists and to stop believing in it quickly when
it doesn't.

Two failure modes to watch for. Testing many weight combinations on the same data will
eventually produce a good-looking one by chance; the walk-forward split is the defence,
and the more candidates you search, the weaker that defence gets. And crypto regimes shift
faster than fitted weights adapt — re-run the walk-forward monthly and watch whether the
selected weights are stable across folds. Weights that swing wildly fold to fold are
telling you the signal isn't there.

Paper trade the signals against live output for a month before risking capital. The gap
between a backtest and a fill is where most of these systems die.
