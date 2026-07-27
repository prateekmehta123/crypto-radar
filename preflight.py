"""
Pre-flight check. Run this FIRST on any new host, before deploying.

    python preflight.py

It answers four questions in order of how badly they can waste your time:

  1. Can this server reach the Binance Futures API at all? (HTTP 451 means no,
     and no amount of code fixes it.)
  2. Where is this server, according to the internet?
  3. Are the Telegram credentials valid, and does a test message arrive?
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


def check_telegram() -> bool:
    hdr("3. Are the Telegram credentials working?")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("   FAIL - TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set.")
        return False
    try:
        me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if me.status_code != 200:
            print(f"   FAIL - token rejected: {me.text[:200]}")
            return False
        name = me.json()["result"]["username"]
        print(f"   token OK - bot is @{name}")

        send = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\u2705 radar preflight: this chat is wired up.",
                  "disable_notification": True}, timeout=10)
        if send.status_code != 200:
            print(f"   FAIL - could not send to chat {chat}: {send.text[:250]}")
            print("          Most common cause: you have not sent the bot a message yet.")
            print("          Open the bot in Telegram, press Start, then re-run.")
            return False
        print(f"   OK - test message delivered to chat {chat}")
        return True
    except Exception as e:                                # noqa: BLE001
        print(f"   FAIL - {e}")
        return False


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
    tg_ok = check_telegram()
    db_ok = check_storage()

    hdr("verdict")
    if binance_ok and tg_ok and db_ok:
        print("   All checks passed. Safe to deploy.")
        return 0
    if not binance_ok:
        print("   BLOCKED: no Binance access. Fix this before anything else --")
        print("            nothing downstream can work without market data.")
    if not tg_ok:
        print("   BLOCKED: Telegram not configured. Alerts will be dropped.")
    if not db_ok:
        print("   WARNING: no database. Dedupe and OI history will not work.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
