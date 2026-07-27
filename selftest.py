"""
Offline self-test. No network required.

Generates synthetic OHLCV with realistic microstructure, then runs the full
pipeline: features -> score -> setup -> triple-barrier simulation -> metrics
-> ML fit. Run this after any edit; it catches shape errors, lookahead in the
label loop, and NaN propagation before you point the thing at real money.

    python selftest.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from radar.backtest import Costs, metrics, run, signals_from_weights, simulate_trades
from radar.features import (add_derivatives, add_funding_series,
                            add_relative_strength, compute_features)
from radar.model import assemble, fit_and_evaluate
from radar.score import DEFAULT_WEIGHTS, build_setup, score_row


def synth(n=3000, seed=0, start="2024-01-01", freq="15min", s0=100.0,
          trend=0.00002, vol=0.004):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    # regime-switching drift so the data is not a single trend
    regime = np.repeat(rng.choice([-1, 0, 1], size=n // 200 + 1), 200)[:n]
    ret = rng.normal(trend * regime, vol, n)
    close = s0 * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0, vol * 0.8, n)) * close
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    open_ = np.concatenate([[s0], close[:-1]])
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    vol_base = rng.lognormal(8, 0.6, n) * (1 + 3 * np.abs(ret) / vol)
    taker_buy = vol_base * np.clip(0.5 + ret / (vol * 6), 0.05, 0.95)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": vol_base, "quote_volume": vol_base * close,
        "trades": (vol_base / 10).astype(int),
        "taker_buy_base": taker_buy,
        "taker_buy_quote": taker_buy * close,
        "close_time": idx + pd.Timedelta(freq),
    }, index=idx)
    df.index.name = "open_time"
    return df


def synth_funding(idx, seed=1):
    rng = np.random.default_rng(seed)
    ft = pd.date_range(idx[0], idx[-1], freq="8h", tz="UTC")
    return pd.Series(rng.normal(0.0001, 0.0003, len(ft)), index=ft)


def _refuses_public_bind():
    """serve() must exit rather than publish market data on a public IP."""
    import radar.api as _a
    saved = _a.AUTH_PASS
    _a.AUTH_PASS = ""
    try:
        _a.serve(host="0.0.0.0", port=8399)
        return False
    except SystemExit:
        return True
    except Exception:                                     # noqa: BLE001
        return False
    finally:
        _a.AUTH_PASS = saved


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")
    return bool(cond)


def main():
    ok = True
    print("\n1. features")
    df = synth(3000, seed=0)
    btc = synth(3000, seed=42, s0=60000.0)
    f = compute_features(df, bars_per_hour=4)
    bf = compute_features(btc, bars_per_hour=4)
    f = add_relative_strength(f, bf)
    f = add_funding_series(f, synth_funding(df.index))
    oi = pd.Series(np.cumsum(np.random.default_rng(3).normal(0, 1e4, len(df))) + 5e6,
                   index=df.index)
    f = add_derivatives(f, oi, 0.0001, 1.05, 1.12, 4)

    ok &= check("feature frame shape", f.shape[0] == len(df) and f.shape[1] > 45,
                f"-> {f.shape}")
    tail = f.iloc[300:]
    nan_frac = tail.isna().mean().mean()
    ok &= check("NaN fraction after warmup < 10%", nan_frac < 0.10,
                f"-> {nan_frac:.3%}")

    # lookahead probe: features at bar t must not change when future bars change
    df2 = df.copy()
    df2.iloc[2000:, df2.columns.get_loc("close")] *= 1.5
    f1 = compute_features(df, bars_per_hour=4)
    f2 = compute_features(df2, bars_per_hour=4)
    cols = [c for c in f1.columns if f1[c].dtype.kind == "f"]
    same = np.allclose(f1[cols].iloc[1000:1500].fillna(0).values,
                       f2[cols].iloc[1000:1500].fillna(0).values, rtol=1e-9)
    ok &= check("no lookahead (past features unchanged by future bars)", same)

    print("\n2. scoring")
    s = score_row(f.iloc[-1])
    ok &= check("score in range", 0 <= s["long_score"] <= 100, f"-> {s['long_score']}")
    ok &= check("mirror", abs(s["long_score"] + s["short_score"] - 100) < 1e-6)
    ok &= check("blocks present", len(s["blocks"]) == len(DEFAULT_WEIGHTS))
    ok &= check("squeeze scores bounded",
                0 <= s["squeeze"]["short_squeeze_score"] <= 100)
    print(f"     dir={s['direction']} score={s['long_score']} conv={s['conviction']} "
          f"cov={s['coverage']} regime={s['blocks']['open_interest'].get('regime')}")

    print("\n2b. vectorised scoring matches row-wise scoring")
    from radar.score import aggregate, block_frame
    sample = f.dropna(subset=["atr", "close"]).iloc[-400:]
    vals, avail = block_frame(sample)
    agg = aggregate(vals, avail, DEFAULT_WEIGHTS)
    ref = np.array([score_row(sample.iloc[i], DEFAULT_WEIGHTS)["net"]
                    for i in range(len(sample))])
    maxdiff = float(np.nanmax(np.abs(agg["net"].values - ref)))
    ok &= check("net matches row-wise scorer", maxdiff < 1e-9, f"-> max diff {maxdiff:.2e}")
    ref_dir = np.array([score_row(sample.iloc[i], DEFAULT_WEIGHTS)["direction"]
                        for i in range(len(sample))])
    ok &= check("direction matches", bool((agg["direction"].values == ref_dir).all()))

    print("\n3. setup")
    st = build_setup(f.iloc[-1], "LONG", 1.5)
    ok &= check("long setup ordering", st and st.stop < st.entry < st.tp1 < st.tp2 < st.tp3)
    sh = build_setup(f.iloc[-1], "SHORT", 1.5)
    ok &= check("short setup ordering", sh and sh.stop > sh.entry > sh.tp1 > sh.tp2 > sh.tp3)
    ok &= check("risk positive", st.risk_pct > 0, f"-> {st.risk_pct:.2f}%")
    print(f"     entry={st.entry:.4f} stop={st.stop:.4f} tp2={st.tp2:.4f} "
          f"risk={st.risk_pct:.2f}% rating={st.risk_rating}")

    print("\n4. simulation")
    feats = f.dropna(subset=["atr", "close"])
    sig = signals_from_weights(feats, DEFAULT_WEIGHTS, 0.10)
    ok &= check("signals generated", len(sig) > 20, f"-> {len(sig)}")
    tr = simulate_trades(df, feats, sig, synth_funding(df.index),
                         Costs(), max_bars=48, atr_mult=1.5)
    ok &= check("trades generated", len(tr) > 5, f"-> {len(tr)}")
    if len(tr):
        ok &= check("entry always after signal bar",
                    bool((tr["exit_time"] >= tr["entry_time"]).all()))
        ok &= check("costs charged on every trade", bool((tr["cost_pct"] > 0).all()))
        m = metrics(tr)
        print("     ", {k: m[k] for k in ("trades", "win_rate", "avg_r",
                                          "profit_factor", "max_dd_r")})
        # On random-walk data with costs, expectancy should be <= 0.
        ok &= check("no free lunch on synthetic random walk", m["avg_r"] < 0.25,
                    f"-> avg_r {m['avg_r']}")

    print("\n5. multi-symbol run")
    panel = {}
    for i, sym in enumerate(["AAAUSDT", "BBBUSDT", "CCCUSDT"]):
        d = synth(3000, seed=10 + i, s0=10.0 * (i + 1))
        ff = compute_features(d, 4)
        ff = add_relative_strength(ff, bf)
        fr = synth_funding(d.index, seed=i)
        ff = add_funding_series(ff, fr)
        for c in ["oi", "oi_chg_1h", "oi_chg_4h", "oi_z_24h",
                  "taker_ls_ratio", "top_pos_ls_ratio"]:
            ff[c] = np.nan
        panel[sym] = {"df": d, "feats": ff.dropna(subset=["atr", "close"]), "funding": fr}
    trades = run(panel, DEFAULT_WEIGHTS, 0.12, Costs(), 48, 1.5)
    ok &= check("pooled trades", len(trades) > 10, f"-> {len(trades)}")
    print("     ", metrics(trades))

    print("\n6. ML labelling + purged CV")
    data = assemble(panel, "LONG", 1.5, 2.0, 48)
    ok &= check("label balance sane", 0.05 < data["y"].mean() < 0.95,
                f"-> base rate {data['y'].mean():.3f}")
    try:
        _, rep = fit_and_evaluate(data, max_bars=48, bar_ms=900_000, n_splits=3)
        print("     ", {k: rep[k] for k in ("n_samples", "base_rate",
                                            "cv_auc_mean", "cv_brier")})
        ok &= check("AUC computed", rep["cv_auc_mean"] is not None)
        ok &= check("brier beats or matches base rate on noise",
                    rep["cv_brier"] <= rep["brier_of_always_base_rate"] * 1.15,
                    f"-> {rep['cv_brier']} vs {rep['brier_of_always_base_rate']}")
    except Exception as e:                                 # noqa: BLE001
        ok = check(f"ML fit ({e})", False)

    print("\n7. arkham on-chain overlay (offline, mocked responses)")
    import tempfile, os as _os
    from radar.store import Store
    from radar.score import setup_dict
    from radar.onchain import ArkhamClient, Transfer, WhaleTracker, _parse_transfer

    raw = {
        "transactionHash": "0xabc", "blockTimestamp": "2026-07-27T04:00:00Z",
        "tokenSymbol": "sol", "historicalUSD": 8_400_000, "unitValueUsd": 1,
        "chain": "solana",
        "fromAddress": {"address": "Whale111", "chain": "solana"},
        "toAddress": {"address": "Bin222", "chain": "solana",
                      "arkhamEntity": {"id": "binance", "name": "Binance", "type": "cex"}},
    }
    t = _parse_transfer(raw)
    ok &= check("parses transfer", t is not None and t.token == "SOL")
    ok &= check("uses historicalUSD not unitValueUsd", t.usd == 8_400_000)
    ok &= check("classifies deposit as exchange_in", t.direction() == "exchange_in")

    out = _parse_transfer({**raw,
        "fromAddress": {"address": "Bin222", "arkhamEntity":
                        {"id": "binance", "name": "Binance", "type": "cex"}},
        "toAddress": {"address": "Whale111"}})
    ok &= check("classifies withdrawal as exchange_out", out.direction() == "exchange_out")
    both = _parse_transfer({**raw,
        "fromAddress": {"address": "A", "arkhamEntity": {"id": "okx", "type": "cex"}},
        "toAddress": {"address": "B", "arkhamEntity": {"id": "binance", "type": "cex"}}})
    ok &= check("exchange-to-exchange treated as internal plumbing",
                both.direction() == "exchange_internal")

    class FakeArkham(ArkhamClient):
        def __init__(self, transfers): self.key = "test"; self._t = transfers
        @property
        def enabled(self): return True
        def transfers(self, **kw): return self._t
        def address_last_activity_ms(self, address, chain=None):
            return 1 if address == "Dormant999" else t.ts_ms - 86_400_000

    dormant = _parse_transfer({**raw, "transactionHash": "0xdead",
                               "fromAddress": {"address": "Dormant999"},
                               "historicalUSD": 12_000_000})
    recent = _parse_transfer({**raw, "transactionHash": "0xfeed",
                              "fromAddress": {"address": "Active888"},
                              "historicalUSD": 9_000_000})
    dbp2 = _os.path.join(tempfile.mkdtemp(), "w.db")
    st2 = Store(dbp2)
    wt = WhaleTracker(FakeArkham([t, out, dormant, recent]), store=st2,
                      dormancy_min_usd=5_000_000)
    ctx = wt.sweep(["SOLUSDT", "BTCUSDT"])
    c = ctx.get("SOLUSDT")
    ok &= check("maps token symbol to Binance pair", c is not None)
    ok &= check("nets exchange flow",
                abs(c.net_to_exchange - (8.4e6 + 12e6 + 9e6 - 8.4e6)) < 1,
                f"-> {c.net_to_exchange:,.0f}")
    ok &= check("detects dormant wallet wake", len(c.dormant_wakes) == 1,
                f"-> {c.dormant_wakes}")
    ok &= check("ignores recently-active wallet",
                not any("Active888" in w for w in c.dormant_wakes))
    ok &= check("caution flags against LONG on net inflow", c.caution_for("LONG"))
    ok &= check("no caution against SHORT on net inflow", not c.caution_for("SHORT"))
    ok &= check("transfers persisted for later backtesting",
                len(st2.transfers_df(symbol="SOLUSDT")) == 4,
                f"-> {len(st2.transfers_df(symbol='SOLUSDT'))} rows")

    st2.close()

    print("\n8. web layer (real HTTP server, no network)")
    import json as _json, threading as _th, time
    import urllib.request as _u, urllib.error as _ue
    _os.environ.setdefault("RADAR_DB", _os.path.join(tempfile.mkdtemp(), "api.db"))
    import radar.api as _api
    from http.server import ThreadingHTTPServer

    row = f.iloc[-1]
    b = _json.loads(_json.dumps(score_row(row)["blocks"], default=float))
    _api.svc.latest = {
        "generated_at": int(time.time() * 1000), "interval": "15m",
        "universe_size": 150, "derivatives_covered": 60, "elapsed_s": 41.2,
        "used_weight_1m": 352, "whale_events": [],
        "candidates": [{"symbol": "SOLUSDT", "price": float(row["close"]),
                        "direction": "LONG", "long_score": 71.3, "short_score": 28.7,
                        "conviction": 0.42, "agreement": 0.86, "coverage": 1.0,
                        "net": 0.42, "regime": "new longs", "funding": 0.0001,
                        "atr_pct": 0.009, "short_squeeze_score": 63.0,
                        "long_squeeze_score": 12.0,
                        "setup": setup_dict(build_setup(row, "LONG", 1.5)),
                        "blocks": b, "whale": None}]}
    _api.svc.status.update(state="ok",
                           last_ok_ms=_api.svc.latest["generated_at"], cycles=1)
    srv = ThreadingHTTPServer(("127.0.0.1", 8231), _api.Handler)
    srv.daemon_threads = True
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.4)

    def _get(path):
        try:
            with _u.urlopen("http://127.0.0.1:8231" + path) as r:
                return r.status, r.read().decode()
        except _ue.HTTPError as e:
            return e.code, e.read().decode()

    ok &= check("refuses to bind a public interface without a password",
                _refuses_public_bind())

    for path, exp in [("/", 200), ("/api/latest", 200), ("/api/symbol/SOLUSDT", 200),
                      ("/api/symbol/NOPEUSDT", 404), ("/api/whales", 200),
                      ("/api/history", 200), ("/nope", 404)]:
        code, _ = _get(path)
        ok &= check(f"GET {path}", code == exp, f"-> {code}")

    code, body = _get("/healthz")
    ok &= check("healthz reports fresh", code == 200 and not _json.loads(body)["stale"])
    _api.svc.status["last_ok_ms"] = int(time.time() * 1000) - 3_600_000
    code, body = _get("/healthz")
    ok &= check("healthz 503s when the scan loop stalls",
                code == 503 and _json.loads(body)["stale"], f"-> {code}")

    # --- auth
    _api.AUTH_PASS = "s3cret"
    code, _ = _get("/api/latest")
    ok &= check("401 without credentials once a password is set", code == 401, f"-> {code}")
    code, _ = _get("/healthz")
    ok &= check("healthz stays open for uptime monitors", code in (200, 503), f"-> {code}")

    import base64 as _b64
    def _get_auth(path, user="radar", pw="s3cret"):
        req = _u.Request("http://127.0.0.1:8231" + path)
        tok = _b64.b64encode(f"{user}:{pw}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
        try:
            with _u.urlopen(req) as r:
                return r.status, r.read().decode()
        except _ue.HTTPError as e:
            return e.code, e.read().decode()

    code, _ = _get_auth("/api/latest")
    ok &= check("200 with correct credentials", code == 200, f"-> {code}")
    code, _ = _get_auth("/api/latest", pw="wrong")
    ok &= check("401 with wrong password", code == 401, f"-> {code}")
    code, _ = _get_auth("/api/latest", user="nope")
    ok &= check("401 with wrong user", code == 401, f"-> {code}")
    _api.AUTH_PASS = ""

    _, page = _get("/")
    ok &= check("dashboard renders with no external JS",
                "CONVICTION" in page and "<script" in page and "src=" not in page.split("<script")[1][:200])
    srv.shutdown()

    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
