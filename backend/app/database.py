import sqlite3

from app.config import EQUITY_SYMBOLS, CRYPTO_SYMBOLS
from app.db.connection import get_connection, is_postgres, db_session


def _row_val(row, idx: int = 0):
    if isinstance(row, dict):
        return list(row.values())[idx]
    return row[idx]


def _serial_type() -> str:
    return "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _safe_alter(cursor, sql: str):
    """Idempotent schema migration — Postgres requires savepoints on expected failures."""
    stmt = sql
    if is_postgres():
        upper = sql.upper()
        if "ADD COLUMN" in upper and "IF NOT EXISTS" not in upper:
            stmt = sql.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS", 1)
        cursor.execute("SAVEPOINT safe_alter_sp")
        try:
            cursor.execute(stmt)
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT safe_alter_sp")
        cursor.execute("RELEASE SAVEPOINT safe_alter_sp")
    else:
        try:
            cursor.execute(stmt)
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
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            execution_mode TEXT NOT NULL DEFAULT 'BAR_CLOSE',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    # Create workspaces table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    _safe_alter(cursor, "ALTER TABLE bot_logs ADD COLUMN meta TEXT DEFAULT NULL")
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
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN order_group_id TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN leg_type TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN oco_group_id TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN stop_loss_price REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE orders ADD COLUMN take_profit_price REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN trailing_stop_percent REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN high_watermark REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE positions ADD COLUMN low_watermark REAL DEFAULT NULL")

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
    _safe_alter(cursor, "ALTER TABLE bot_trades ADD COLUMN insight_snapshot TEXT DEFAULT NULL")
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
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN high_watermark REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN low_watermark REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN entry_atr REAL DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_positions ADD COLUMN opened_at REAL DEFAULT NULL")
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
    _safe_alter(cursor, "ALTER TABLE bot_pending_fills ADD COLUMN insight_snapshot TEXT DEFAULT NULL")

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
    _safe_alter(cursor, "ALTER TABLE bot_signal_ledger ADD COLUMN order_id TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_signal_ledger ADD COLUMN broker TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_signal_ledger ADD COLUMN payload TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_signal_ledger ADD COLUMN message TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE bot_signal_ledger ADD COLUMN updated_at TEXT DEFAULT NULL")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_signal_ledger_status ON bot_signal_ledger (status)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            request_json TEXT NOT NULL,
            progress_json TEXT,
            run_id TEXT,
            error TEXT,
            results_json TEXT,
            client_key TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_jobs_status ON backtest_jobs (status, created_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ml_train_runs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT NOT NULL,
            duration_ms INTEGER,
            ok INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            metrics_json TEXT,
            config_hash TEXT,
            version_id TEXT,
            job_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    _safe_alter(cursor, "ALTER TABLE ml_train_runs ADD COLUMN timeframe TEXT")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ml_train_runs_sym_strat "
        "ON ml_train_runs (symbol, strategy, finished_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ml_train_runs_finished "
        "ON ml_train_runs (finished_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ml_train_runs_tf "
        "ON ml_train_runs (symbol, strategy, timeframe, finished_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS optimization_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT 'total_pnl',
            request_json TEXT NOT NULL,
            results_json TEXT NOT NULL,
            best_config TEXT
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_optimization_runs_symbol "
        "ON optimization_runs (symbol, created_at DESC)"
    )
    _safe_alter(cursor, "ALTER TABLE optimization_runs ADD COLUMN walk_forward_json TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_insights (
            insight_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            bar_time INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_insights_symbol_time "
        "ON agent_insights (symbol, bar_time DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vision_reports (
            report_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            bar_time INTEGER NOT NULL,
            payload TEXT NOT NULL,
            rag_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_reports_symbol_time "
        "ON vision_reports (symbol, bar_time DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_vision_reports_symbol_tf_time "
        "ON vision_reports (symbol, timeframe, bar_time DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chart_drawings (
            symbol TEXT PRIMARY KEY,
            drawings_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_journal (
            id TEXT PRIMARY KEY,
            trade_ref TEXT,
            order_id TEXT,
            bot_id TEXT,
            symbol TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            lesson TEXT NOT NULL DEFAULT '',
            screenshot_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_symbol ON trade_journal (symbol)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_updated ON trade_journal (updated_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS copilot_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            intent TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_copilot_messages_session ON copilot_messages (session_id, created_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_state (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_channels (
            id TEXT PRIMARY KEY,
            channel_type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            event_types TEXT NOT NULL DEFAULT '[]',
            config_encrypted TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_channels_type ON notification_channels (channel_type)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id TEXT PRIMARY KEY,
            dedupe_key TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            delivered_at REAL,
            UNIQUE (dedupe_key, channel_id)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log (created_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1m',
            condition_type TEXT NOT NULL,
            threshold REAL,
            signal TEXT,
            cooldown_sec INTEGER NOT NULL DEFAULT 300,
            notify_channels TEXT NOT NULL DEFAULT '[]',
            last_triggered_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_rules_symbol ON alert_rules (symbol, enabled)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rule_log (
            id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT,
            created_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_rule_log_rule ON alert_rule_log (rule_id, created_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            keys_encrypted TEXT NOT NULL,
            user_agent TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_channel ON push_subscriptions (channel_id)"
    )

    from app.services.archive.schema import init_archive_schema
    init_archive_schema(cursor)
    if is_postgres():
        _safe_alter(cursor, "ALTER TABLE market_ticks ALTER COLUMN time_ms TYPE BIGINT")
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

    from app.db.migrations import record_baseline_if_needed
    record_baseline_if_needed()

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

def get_db_stats(*, include_archive: bool = True):
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

        if include_archive:
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
            # Tick COUNT(*) is also an archive table scan — keep it behind the same
            # gate as get_archive_stats() so light callers (e.g. /health) stay cheap.
            try:
                cursor.execute("SELECT COUNT(*) FROM market_ticks")
                row = cursor.fetchone()
                stats["archive"] = stats.get("archive") or {}
                stats["archive"]["ticks"] = _row_val(row)
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
            from app.services.runtime.system_state import runtime_status_dict

            stats["runtime"] = runtime_status_dict()
        except Exception:
            pass
        try:
            from app.services.data_quality.loop import get_last_report
            from app.services.data_quality.monitor import data_quality_stats_from_report

            report = get_last_report()
            if report:
                stats["data_quality"] = data_quality_stats_from_report(report)
        except Exception:
            pass
        try:
            from app.services.altdata.store import altdata_counts

            stats["altdata"] = altdata_counts()
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

