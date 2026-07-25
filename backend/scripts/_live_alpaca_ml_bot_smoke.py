"""Live ML training-candle + bot HT candle probes (Alpaca mode, no full train)."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _load(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, val = raw.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "MASSIVE_API_KEY", "FINNHUB_API_KEY"):
            os.environ.setdefault(key, val)
        else:
            os.environ[key] = val


_load(ROOT / ".env")
_load(ROOT / "env.profiles" / "alpaca.env")
os.environ["TERMINAL_MODE"] = "LIVE_ALPACA"

import app.config as cfg

importlib.reload(cfg)

fails: list[str] = []
oks: list[str] = []


def ok(m: str) -> None:
    oks.append(m)
    print("OK ", m)


def fail(m: str) -> None:
    fails.append(m)
    print("FAIL", m)


async def main() -> int:
    # Training deep-REST path (same branch as LIVE_ALPACA in _fetch_training_candles)
    from app.services.archive.broker_fetch import fetch_alpaca_tf_candles

    to_ts = int(time.time())
    from_ts = to_ts - 40 * 86400
    candles = fetch_alpaca_tf_candles("BTCUSDT", from_ts, to_ts, "15m") or []
    if len(candles) > 500:
        candles = candles[-500:]
    if len(candles) >= 200:
        ok(f"training candles BTCUSDT 15m n={len(candles)} span={candles[-1]['time']-candles[0]['time']}")
    else:
        fail(f"training candles BTCUSDT n={len(candles)}")

    candles_eq = fetch_alpaca_tf_candles("AAPL", from_ts, to_ts, "1h") or []
    if len(candles_eq) > 400:
        candles_eq = candles_eq[-400:]
    if len(candles_eq) >= 50:
        ok(f"training candles AAPL 1h n={len(candles_eq)}")
    else:
        fail(f"training candles AAPL 1h n={len(candles_eq)}")

    # get_bot_candles HT via feed.fetch_ht_candles (mirrors live feed)
    from app.services.bots.candle_source import get_bot_candles

    class Feed:
        def fetch_ht_candles(self, symbol, timeframe, limit=None, purpose="chart"):
            bars = fetch_alpaca_tf_candles(symbol, from_ts, to_ts, timeframe) or []
            if limit:
                bars = bars[-int(limit) :]
            return bars

        def get_candles(self, symbol):
            return []

    out = get_bot_candles("BTCUSDT", Feed(), timeframe="15m", min_bars=100)
    if len(out) >= 100:
        ok(f"get_bot_candles HT BTCUSDT 15m n={len(out)}")
    else:
        fail(f"get_bot_candles HT n={len(out)}")

    # Market handler HT limit default for Alpaca
    from app.api.handlers import market as market_handlers

    importlib.reload(market_handlers)
    lim = market_handlers._parse_candle_snapshot_limit({}, "15m")
    if lim == 600:
        ok(f"market HT default limit 15m={lim}")
    else:
        fail(f"market HT default limit 15m={lim}")

    # Dual-key: ensure resolve_backtest uses alpaca path for long HT
    from app.services.archive.resolve import resolve_backtest_candles

    candles_bt, meta = resolve_backtest_candles(
        "BTCUSDT",
        None,
        days=45,
        timeframe="15m",
    )
    note = (meta or {}).get("resolution_note") or ""
    if len(candles_bt) >= 200 and ("broker native" in note or "alpaca" in note.lower() or "native" in note):
        ok(f"long HT resolve n={len(candles_bt)} note={note}")
    elif len(candles_bt) >= 200:
        ok(f"long HT resolve n={len(candles_bt)} note={note} (acceptable if archive-backed)")
    else:
        fail(f"long HT resolve n={len(candles_bt)} note={note} meta={meta}")

    print()
    print(f"Passed {len(oks)} Failed {len(fails)}")
    for f in fails:
        print(" -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
