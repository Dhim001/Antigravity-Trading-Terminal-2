import sqlite3

from app.config import EQUITY_SYMBOLS, CRYPTO_SYMBOLS
from app.db.connection import get_connection, is_postgres


def _row_val(row, idx: int = 0):
    if isinstance(row, dict):
        return list(row.values())[idx]
    return row[idx]


def _serial_type() -> str:
    return "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _safe_alter(cursor, sql: str):
    try:
        cursor.execute(sql)
    except Exception:
        pass


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    if not is_postgres():
        cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Create accounts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            asset TEXT PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0.0,
            locked REAL NOT NULL DEFAULT 0.0
        )
    """)
    
    # Create orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            type TEXT NOT NULL,       -- LIMIT, MARKET
            side TEXT NOT NULL,       -- BUY, SELL
            price REAL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL,     -- PENDING, FILLED, CANCELED, REJECTED
            filled_quantity REAL DEFAULT 0.0,
            average_fill_price REAL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create positions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            size REAL NOT NULL DEFAULT 0.0,
            avg_price REAL NOT NULL DEFAULT 0.0
        )
    """)
    
    # Create bots table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bots (
            id TEXT PRIMARY KEY,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            status TEXT NOT NULL,
            allocation REAL NOT NULL,
            config TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create bot_logs table
    serial = _serial_type()
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS bot_logs (
            id {serial},
            bot_id TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()

    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN stop_loss_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN take_profit_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN stop_loss_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN take_profit_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN stop_loss_price REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN take_profit_price REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN bot_id TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN signal_id TEXT DEFAULT NULL")

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS bot_trades (
            id {serial},
            bot_id TEXT NOT NULL,
            order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            pnl REAL,
            signal_id TEXT,
            is_exit INTEGER NOT NULL DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS bot_snapshots (
            id {serial},
            bot_id TEXT NOT NULL,
            equity REAL NOT NULL,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            open_positions INTEGER DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_trades_bot_id ON bot_trades(bot_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_snapshots_bot_id ON bot_snapshots(bot_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_bot_id ON orders(bot_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bot_logs_bot_id ON bot_logs(bot_id)")
    conn.commit()
    
    # Collect all unique base assets dynamically from config
    assets = {"USD", "USDT"}
    for info in EQUITY_SYMBOLS.values():
        assets.add(info["asset"])
    for info in CRYPTO_SYMBOLS.values():
        assets.add(info["asset"])

    # Seed/Migrate accounts individually to preserve existing data while adding new assets
    assets_to_seed = []
    for asset in sorted(list(assets)):
        initial_balance = 100000.0 if asset in ("USD", "USDT") else 0.0
        assets_to_seed.append((asset, initial_balance))

    for asset, initial_balance in assets_to_seed:
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE asset = ?", (asset,))
        row = cursor.fetchone()
        if _row_val(row) == 0:
            cursor.execute(
                "INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
                (asset, initial_balance),
            )
    conn.commit()
        
    conn.close()

def reset_db():
    conn = get_connection()
    cursor = conn.cursor()

    if not is_postgres():
        cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Clear active data tables
    cursor.execute("DELETE FROM positions;")
    cursor.execute("DELETE FROM orders;")
    cursor.execute("DELETE FROM bot_trades;")
    cursor.execute("DELETE FROM bot_snapshots;")
    cursor.execute("DELETE FROM bot_logs;")
    cursor.execute("UPDATE bots SET status = 'STOPPED'")
    
    # Collect all unique base assets dynamically from config
    assets = {"USD", "USDT"}
    for info in EQUITY_SYMBOLS.values():
        assets.add(info["asset"])
    for info in CRYPTO_SYMBOLS.values():
        assets.add(info["asset"])

    # Reset account balances to defaults
    assets_to_reset = []
    for asset in sorted(list(assets)):
        initial_balance = 100000.0 if asset in ("USD", "USDT") else 0.0
        assets_to_reset.append((asset, initial_balance))

    for asset, initial_balance in assets_to_reset:
        if is_postgres():
            cursor.execute(
                """
                INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)
                ON CONFLICT (asset) DO UPDATE SET balance = EXCLUDED.balance, locked = 0.0
                """,
                (asset, initial_balance),
            )
        else:
            cursor.execute(
                "INSERT OR REPLACE INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
                (asset, initial_balance),
            )
        
    conn.commit()
    conn.close()

def get_db_stats():
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {}
    try:
        cursor.execute("SELECT COUNT(*) FROM positions WHERE size != 0.0")
        row = cursor.fetchone()
        stats["positions_count"] = _row_val(row)

        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'PENDING'")
        row = cursor.fetchone()
        stats["pending_orders_count"] = _row_val(row)

        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'FILLED'")
        row = cursor.fetchone()
        stats["filled_trades_count"] = _row_val(row)
    except Exception:
        stats = {
            "positions_count": 0,
            "pending_orders_count": 0,
            "filled_trades_count": 0
        }
    finally:
        conn.close()
        
    return stats

