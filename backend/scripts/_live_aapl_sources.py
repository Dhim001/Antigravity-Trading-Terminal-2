import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "trading-alpaca.db"
con = sqlite3.connect(db)
cur = con.cursor()
rows = cur.execute(
    """
    SELECT time, close, volume, source FROM market_bars_1m
    WHERE symbol=? AND time BETWEEN ? AND ?
    ORDER BY time LIMIT 50
    """,
    ("AAPL", 1784928000, 1784932000),
).fetchall()
for r in rows:
    print(r)
print("--- outlier sources ---")
print(
    cur.execute(
        """
        SELECT source, COUNT(*), MIN(close), MAX(close)
        FROM market_bars_1m
        WHERE symbol=? AND time>? AND close<200
        GROUP BY source
        """,
        ("AAPL", 1784300000),
    ).fetchall()
)
print(
    cur.execute(
        """
        SELECT source, COUNT(*), MIN(close), MAX(close)
        FROM market_bars_1m
        WHERE symbol=? AND time>? AND close>300
        GROUP BY source
        """,
        ("AAPL", 1784300000),
    ).fetchall()
)
