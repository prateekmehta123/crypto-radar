"""
Pre-flight check. Run this FIRST on any new host, before deploying.

    python preflight.py

It answers four questions in order of how badly they can waste your time:

  1. Can this server reach the Binance Futures API at all? (HTTP 451 means no,
     and no amount of code fixes it.)
  2. Where is this server, according to the internet?
  3. Is the dashboard configured safely for how you plan to reach it?
  4. Can the process write its SQLite database?

Exits non-zero if anything critical fails.
"""

from __future__ import annotations

import os
import sys

import requests


def hdr(t: str) -> None:
    print(f"\n{t}\n{'-' * len(t)}")


def check_egress_ip() -> str | None:
    hdr("1. Where is this server?")
    try:
        r = requests.get("https://ipinfo.io/json", timeout=10)
        d = r.json()
        country = d.get("country", "?")
        print(f"   egress IP : {d.get('ip')}")
        print(f"   location  : {d.get('city', '?')}, {d.get('region', '?')}, {country}")
        print(f"   network   : {d.get('org', '?')}")
        if country in ("US", "MY", "CA"):
            print(f"   >> {country} is a Binance-restricted location. Expect HTTP 451.")
        return country
    except Exception as e:                                # noqa: BLE001
        print(f"   could not determine location: {e}")
        return None


def check_binance(proxy: str) -> bool:
    hdr("2. Can this server reach the Binance Futures API?")
    kw = {"timeout": 15}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
        print(f"   using BINANCE_PROXY ({proxy.split('@')[-1]})")
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ping", **kw)
    except Exception as e:                                # noqa: BLE001
        print(f"   FAIL - network error: {e}")
        return False

    if r.status_code == 200:
        t = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr",
                         params={"symbol": "BTCUSDT"}, **kw)
        px = t.json().get("lastPrice", "?") if t.status_code == 200 else "?"
        print(f"   OK - reachable. BTCUSDT last price {px}")
        return True

    if r.status_code == 451:
        print("   FAIL - HTTP 451: this IP is in a Binance-restricted location.")
        print("          The Futures API has NO public mirror for blocked regions.")
        print("          (data-api.binance.vision works for spot only, not fapi.)")
        print("          Options: set BINANCE_PROXY, or host outside US/MY/CA.")
        return False

    print(f"   FAIL - HTTP {r.status_code}: {r.text[:200]}")
    return False


def check_dashboard() -> bool:
    hdr("3. Is the dashboard configured safely?")
    host = os.environ.get("HOST", "127.0.0.1")
    pw = os.environ.get("RADAR_PASSWORD", "")
    if host in ("127.0.0.1", "localhost", "::1"):
        print("   OK - local only (HOST=%s)." % host)
        print("        Reachable at http://localhost:8080 on this machine.")
        print("        To expose it on a server, set HOST=0.0.0.0 and RADAR_PASSWORD.")
        return True
    if not pw:
        print("   FAIL - HOST=%s exposes the dashboard, but RADAR_PASSWORD is not set." % host)
        print("          The app will refuse to start. Set a password.")
        return False
    print("   OK - exposed on %s and password-protected." % host)
    return True


def check_storage() -> bool:
    hdr("4. Can the process write its database?")
    path = os.environ.get("RADAR_DB", "radar.db")
    try:
        from radar.store import Store
        s = Store(path)
        s.write_snapshots([{"ts": 1, "symbol": "__PREFLIGHT__", "close": 1.0}])
        s.close()
        os.path.getsize(path)
        print(f"   OK - wrote {path}")
        print("   NOTE: on ephemeral filesystems this file is wiped between runs,")
        print("         which means open-interest history never accumulates.")
        return True
    except Exception as e:                                # noqa: BLE001
        print(f"   FAIL - {e}")
        return False


def main() -> int:
    print("radar preflight")
    proxy = os.environ.get("BINANCE_PROXY", "").strip()
    check_egress_ip()
    binance_ok = check_binance(proxy)
    dash_ok = check_dashboard()
    db_ok = check_storage()

    hdr("verdict")
    if binance_ok and dash_ok and db_ok:
        print("   All checks passed.")
        print("   Start it with:  python main.py     then open http://localhost:8080")
        return 0
    if not binance_ok:
        print("   BLOCKED: no Binance access. Fix this before anything else --")
        print("            nothing downstream can work without market data.")
    if not dash_ok:
        print("   BLOCKED: the dashboard would refuse to start. See section 3.")
    if not db_ok:
        print("   WARNING: no database. Dedupe and OI history will not work.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
