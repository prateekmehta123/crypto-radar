"""
Entrypoint: serves the dashboard and runs the scan loop behind it.

    python main.py        then open http://localhost:8080

The scan loop lives in a background thread inside radar/api.py. HTTP requests
only read the latest published snapshot, so opening ten tabs cannot trigger ten
scans against the Binance rate limiter.

Configuration is entirely environment variables. On AWS these live in
/etc/radar.env, loaded by the systemd unit in deploy/.

  HOST                 optional   default 127.0.0.1 (safe: local only).
                                  Set 0.0.0.0 to expose it -- requires RADAR_PASSWORD.
  PORT                 optional   default 8080
  RADAR_USER           optional   default "radar"
  RADAR_PASSWORD       required when HOST is not loopback -- dashboard login
  BINANCE_PROXY        optional   set if the host IP is geo-blocked (HTTP 451)
  RADAR_DB             optional   default radar.db
  SCAN_INTERVAL_S      optional   default 300
  KLINE_INTERVAL       optional   default 15m
  TOP_N                optional   default 150   symbols by 24h volume
  DERIV_TOP            optional   default 60    symbols to pull OI for
  SCAN_MIN_CONVICTION  optional   default 0.15  bar for appearing at all
  ATR_MULT             optional   default 1.5   stop distance
  ACCOUNT_RISK_PCT     optional   default 0.5

  ARKHAM_API_KEY       optional   enables the on-chain overlay
  WHALE_MIN_USD        optional   default 1000000
  DORMANCY_DAYS        optional   default 180
  DORMANCY_MIN_USD     optional   default 5000000
"""

from __future__ import annotations

import os

from radar.api import serve

if __name__ == "__main__":
    # Localhost by default so `python main.py` just works while you try it out.
    # Exposing it to a network is an explicit opt-in that requires a password.
    serve(host=os.environ.get("HOST", "127.0.0.1"),
          port=int(os.environ.get("PORT", 8080)))
