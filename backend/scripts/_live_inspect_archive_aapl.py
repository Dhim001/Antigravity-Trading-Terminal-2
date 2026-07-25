"""Check archive 1m AAPL for bad closes around backtest cliff."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "trading-alpaca.db"
con = sqlite3.connect(DB)
cur = con.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables", tables)
for t in tables:
    if any(k in t.lower() for k in ("candle", "bar", "ohlc", "market", "archive")):
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        except Exception as exc:
            n = exc
        print("count", t, n)

# Prefer market_candles / candles_1m style
candidates = [t for t in tables if "candle" in t.lower() or t.lower() in ("bars", "ohlcv")]
print("candidates", candidates)

for t in candidates or tables:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info([{t}])")]
    if not {"symbol", "time", "close"}.issubset(set(cols)) and not {"symbol", "ts", "close"}.issubset(set(cols)):
        continue
    time_col = "time" if "time" in cols else "ts"
    print("using", t, cols)
    rows = cur.execute(
        f"""
        SELECT {time_col}, open, high, low, close, volume
        FROM [{t}]
        WHERE symbol=? AND {time_col} BETWEEN ? AND ?
        ORDER BY {time_col}
        """,
        ("AAPL", 1784928000, 1784975000),
    ).fetchall()
    print("rows in window", len(rows))
    prev = None
    for r in rows:
        ts, o, h, l, c, v = r
        if prev and prev[4] and abs(c / prev[4] - 1) > 0.05:
            print("JUMP", prev, "->", r)
        prev = r
    # also find global min/max close recently
    stats = cur.execute(
        f"""
        SELECT MIN(close), MAX(close), COUNT(*)
        FROM [{t}]
        WHERE symbol=? AND {time_col} > ?
        """,
        ("AAPL", 1784300000),
    ).fetchone()
    print("recent close min/max/n", stats)
    bad = cur.execute(
        f"""
        SELECT {time_col}, open, high, low, close, volume
        FROM [{t}]
        WHERE symbol=? AND {time_col} > ? AND (close < 200 OR close > 400)
        ORDER BY {time_col}
        LIMIT 20
        """,
        ("AAPL", 1784300000),
    ).fetchall()
    print("outlier closes", bad)
    break
