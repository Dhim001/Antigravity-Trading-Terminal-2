"""One-off Alpaca credential + market data smoke test (no secrets printed)."""
from __future__ import annotations

import json
import os
import sys

import requests

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
ENV_PATH = os.path.join(ROOT, ".env")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }


def _probe(label: str, url: str) -> dict:
    out: dict = {"label": label, "url": url}
    try:
        resp = requests.get(url, headers=_headers(), timeout=15)
        out["status"] = resp.status_code
        if resp.ok:
            data = resp.json()
            out["ok"] = True
            if isinstance(data, dict) and "bars" in data:
                bars = data["bars"]
                if isinstance(bars, dict):
                    sym = next(iter(bars), None)
                    rows = bars.get(sym, []) if sym else []
                    out["symbol"] = sym
                    out["bars_count"] = len(rows)
                    if rows:
                        out["latest_bar"] = rows[-1]
                else:
                    out["bars_count"] = len(bars)
            elif isinstance(data, dict) and data.get("account_number"):
                out["account_status"] = data.get("status")
                out["buying_power"] = data.get("buying_power")
            elif isinstance(data, list):
                out["list_count"] = len(data)
                if data:
                    out["sample"] = {k: data[0].get(k) for k in ("symbol", "name", "status") if k in data[0]}
            else:
                out["keys"] = list(data.keys())[:8] if isinstance(data, dict) else str(type(data))
        else:
            out["ok"] = False
            out["error"] = resp.text[:200]
    except Exception as exc:
        out["ok"] = False
        out["exception"] = str(exc)
    return out


def main() -> int:
    _load_env()
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")

    print("credentials_present:", bool(key and secret))
    print("ALPACA_BASE_URL:", base)

    from app.services.alpaca_data import get_alpaca_ws_url, resolve_equity_data_feed

    feed = resolve_equity_data_feed(force_refresh=True)
    ws_url = get_alpaca_ws_url(force_refresh=True)
    print("resolved_equity_feed:", feed)
    print("resolved_ws_url:", ws_url)

    results = []
    # User has /v2 in base — show both paths
    if base.endswith("/v2"):
        results.append(_probe("account_as_configured", f"{base}/account"))
        fixed = base[:-3].rstrip("/")
        results.append(_probe("account_correct_base", f"{fixed}/v2/account"))
    else:
        results.append(_probe("account", f"{base}/v2/account"))

    results.append(
        _probe(
            "daily_bars_aapl",
            "https://data.alpaca.markets/v2/stocks/bars"
            f"?symbols=AAPL&timeframe=1Day&limit=3&feed={feed}",
        )
    )
    results.append(
        _probe(
            "assets_list",
            "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity&limit=3",
        )
    )

    print(json.dumps(results, indent=2, default=str))
    ok = all(r.get("ok") for r in results if r["label"] != "account_as_configured")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
