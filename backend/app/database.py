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


def _ensure_sim_market_state_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sim_market_state (
            symbol TEXT PRIMARY KEY,
            price REAL NOT NULL,
            candles_json TEXT NOT NULL,
            target_json TEXT,
            updated_at REAL NOT NULL
        )
    """)


def _ensure_performance_indexes(cursor) -> None:
    """Idempotent indexes for hot read/write paths (safe on existing DBs)."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_bot_trades_bot_time ON bot_trades (bot_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_bot_trades_bot_exit ON bot_trades (bot_id, is_exit)",
        "CREATE INDEX IF NOT EXISTS idx_bot_trades_order_id ON bot_trades (order_id)",
        "CREATE INDEX IF NOT EXISTS idx_bot_snapshots_bot_time ON bot_snapshots (bot_id, timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_symbol_status ON orders (symbol, status)",
        "CREATE INDEX IF NOT EXISTS idx_bot_positions_symbol ON bot_positions (symbol)",
    ]
    for sql in indexes:
        _safe_alter(cursor, sql)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    if not is_postgres():
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA busy_timeout = 5000;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
    
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
    _safe_alter(cursor, "ALTER TABLE bots ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'BAR_CLOSE'")
    
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
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN realized_pnl REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN cost_basis REAL DEFAULT NULL")

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
            signal_bar_time INTEGER DEFAULT NULL,
            is_exit INTEGER NOT NULL DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    _safe_alter(cursor, "ALTER TABLE bot_trades ADD COLUMN signal_bar_time INTEGER DEFAULT NULL")
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_positions (
            bot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            size REAL NOT NULL DEFAULT 0.0,
            avg_price REAL NOT NULL DEFAULT 0.0,
            stop_loss_percent REAL DEFAULT NULL,
            take_profit_percent REAL DEFAULT NULL,
            stop_loss_price REAL DEFAULT NULL,
            take_profit_price REAL DEFAULT NULL,
            PRIMARY KEY (bot_id, symbol),
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN stop_loss_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN take_profit_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN stop_loss_price REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN take_profit_price REAL DEFAULT NULL")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_pending_fills (
            id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            signal_price REAL NOT NULL,
            signal_id TEXT,
            is_exit INTEGER NOT NULL DEFAULT 0,
            entry_price REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_pending_fills_bot ON bot_pending_fills (bot_id)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_signal_ledger (
            signal_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            bar_time INTEGER,
            signal_kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'claimed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_signal_ledger_bot ON bot_signal_ledger (bot_id)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ambiguous_orders (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL,
            bot_id TEXT,
            broker TEXT,
            payload TEXT,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ambiguous_orders_status ON ambiguous_orders (status)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            config TEXT,
            days INTEGER NOT NULL DEFAULT 7,
            results TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol ON backtest_runs (symbol, created_at DESC)"
    )

    from app.services.archive.schema import init_archive_schema
    init_archive_schema(cursor)
    _ensure_performance_indexes(cursor)
    _ensure_sim_market_state_table(cursor)

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
    cursor.execute("DELETE FROM bot_signal_ledger;")
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

        try:
            from app.services.archive.query import get_archive_stats
            from app.services.archive.writer import get_archive_writer

            archive_stats = get_archive_stats()
            writer = get_archive_writer()
            stats["archive"] = {
                **archive_stats,
                "pending_flush": writer.pending_count,
                "total_flushed": writer.total_flushed,
            }
        except Exception:
            pass
        try:
            from app.services.reconciliation import list_ambiguous_orders

            stats["reconciliation"] = {
                "pending_count": len(list_ambiguous_orders(include_resolved=False)),
            }
        except Exception:
            pass
        try:
            cursor.execute("SELECT COUNT(*) FROM market_ticks")
            row = cursor.fetchone()
            stats["archive"] = stats.get("archive") or {}
            stats["archive"]["ticks"] = _row_val(row)
        except Exception:
            pass
    except Exception:
        stats = {
            "positions_count": 0,
            "pending_orders_count": 0,
            "filled_trades_count": 0
        }
    finally:
        conn.close()
        
    return stats

