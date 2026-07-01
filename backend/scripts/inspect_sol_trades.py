"""One-off: inspect SOLUSDT trades and bot position state."""
import json
import sqlite3
from app.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print("DB:", DB_PATH)
print("Tables:", [r[0] for r in cur.fetchall()])

for table in ("trade_journal", "fills", "orders", "positions", "bot_positions", "bot_logs"):
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        print(f"{table}: {cur.fetchone()[0]} rows")
    except sqlite3.OperationalError:
        pass

print("\n=== bot_trades SOLUSDT (last 10) ===")
cur.execute(
    """
    SELECT * FROM bot_trades
    WHERE symbol LIKE '%SOL%'
    ORDER BY rowid DESC
    LIMIT 10
    """
)
for row in cur.fetchall():
    print(dict(row))

print("\n=== orders SOLUSDT (last 10) ===")
cur.execute(
    """
    SELECT id, symbol, side, price, quantity, status, average_fill_price, bot_id
    FROM orders
    WHERE symbol LIKE '%SOL%'
    ORDER BY rowid DESC
    LIMIT 10
    """
)
for row in cur.fetchall():
    print(dict(row))

print("\n=== trade_journal schema ===")
cur.execute("PRAGMA table_info(trade_journal)")
print([dict(r) for r in cur.fetchall()])

print("\n=== bot_positions SOLUSDT ===")
cur.execute(
    "SELECT * FROM bot_positions WHERE symbol LIKE '%SOL%'"
)
for row in cur.fetchall():
    print(dict(row))

print("\n=== positions SOLUSDT ===")
cur.execute("SELECT * FROM positions WHERE symbol LIKE '%SOL%'")
for row in cur.fetchall():
    print(dict(row))

print("\n=== recent bot_logs SOLUSDT ===")
cur.execute(
    """
    SELECT timestamp, level, message FROM bot_logs
    WHERE message LIKE '%SOL%'
    ORDER BY timestamp DESC LIMIT 15
    """
)
for row in cur.fetchall():
    print(dict(row))

conn.close()
