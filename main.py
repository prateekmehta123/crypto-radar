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
import sys


def _fail(msg: str) -> None:
    """Exit with something a human can act on, not a traceback."""
    print("\n" + "-" * 62, file=sys.stderr)
    print(msg.strip(), file=sys.stderr)
    print("-" * 62 + "\n", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    try:
        from radar.api import serve
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "") or str(e)
        if missing.startswith("radar"):
            _fail("Can't find the radar package.\n\n"
                  "You're running this from the wrong folder. Move to the folder\n"
                  "containing main.py and try again:\n\n"
                  "    dir main.py        (Windows)\n"
                  "    ls main.py         (Mac/Linux)")
        _fail(f"A required package is missing: {missing}\n\n"
              "Install the dependencies first:\n\n"
              "    pip install -r requirements.txt\n\n"
              "On Windows, if pip is not recognised, use:\n\n"
              "    python -m pip install -r requirements.txt")

    # Localhost by default so `python main.py` just works while you try it out.
    # Exposing it to a network is an explicit opt-in that requires a password.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 8080))

    try:
        serve(host=host, port=port)
    except OSError as e:
        # errno 98 (Linux) / 10048 (Windows) -- address already in use
        if getattr(e, "errno", None) in (48, 98, 10048):
            _fail(f"Port {port} is already being used.\n\n"
                  "Something else is on that port -- most likely another copy of\n"
                  "this app you started earlier and left running.\n\n"
                  "Either close the other window, or run this one on a different\n"
                  "port:\n\n"
                  f"    Windows:    $env:PORT=8081; python main.py\n"
                  f"    Mac/Linux:  PORT=8081 python main.py\n\n"
                  "Then open http://localhost:8081 instead.")
        if getattr(e, "errno", None) in (13, 10013):
            _fail(f"Not allowed to use port {port}.\n\n"
                  "Ports below 1024 need admin rights. Use a higher one:\n\n"
                  "    Windows:    $env:PORT=8080; python main.py\n"
                  "    Mac/Linux:  PORT=8080 python main.py")
        raise
    except KeyboardInterrupt:
        print("\nStopped.")
