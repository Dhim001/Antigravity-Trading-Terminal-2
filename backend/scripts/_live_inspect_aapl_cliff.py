"""Inspect AAPL price cliff seen in live Alpaca backtest."""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

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
import app.services.archive.broker_fetch as bf

importlib.reload(bf)

to_ts = int(time.time())
from_ts = to_ts - 10 * 86400
bars = bf.fetch_alpaca_tf_candles("AAPL", from_ts, to_ts, "1h")
print(f"1h bars={len(bars)}")
if bars:
    print("first", bars[0])
    print("last", bars[-1])

cliff_times = [1784934000, 1784937600, 1784941200, 1784966400]
for t in cliff_times:
    near = [b for b in bars if abs(b["time"] - t) <= 7200]
    print("near", t, near)

rets = []
for a, b in zip(bars, bars[1:]):
    if a["close"]:
        rets.append((abs(b["close"] / a["close"] - 1), a, b))
rets.sort(reverse=True)
print("top jumps:")
for r, a, b in rets[:10]:
    print(f"{r*100:.2f}%", a, "->", b)

# Around the cliff window print all 1h bars
print("window:")
for b in bars:
    if 1784928000 <= b["time"] <= 1784970000:
        print(b)
