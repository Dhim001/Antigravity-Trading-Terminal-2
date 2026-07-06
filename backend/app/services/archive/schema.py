"""Archive table definitions — called from init_db()."""

from app.db.connection import is_postgres


def _int64_type() -> str:
    """Postgres INTEGER is 32-bit; epoch milliseconds need BIGINT."""
    return "BIGINT" if is_postgres() else "INTEGER"


def init_archive_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_bars_1m (
            symbol  TEXT NOT NULL,
            time    INTEGER NOT NULL,
            open    REAL NOT NULL,
            high    REAL NOT NULL,
            low     REAL NOT NULL,
            close   REAL NOT NULL,
            volume  REAL NOT NULL DEFAULT 0,
            source  TEXT NOT NULL,
            PRIMARY KEY (symbol, time)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_bars_1h (
            symbol    TEXT NOT NULL,
            time      INTEGER NOT NULL,
            open      REAL NOT NULL,
            high      REAL NOT NULL,
            low       REAL NOT NULL,
            close     REAL NOT NULL,
            volume    REAL NOT NULL DEFAULT 0,
            source    TEXT NOT NULL,
            bar_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (symbol, time)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bars_1m_time ON market_bars_1m (time)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bars_1h_time ON market_bars_1h (time)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bars_1m_symbol_time ON market_bars_1m (symbol, time)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bars_1h_symbol_time ON market_bars_1h (symbol, time)"
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive_ingestion_state (
            symbol          TEXT PRIMARY KEY,
            last_bar_time   INTEGER,
            oldest_bar_time INTEGER,
            bars_total      INTEGER NOT NULL DEFAULT 0,
            last_backfill   REAL,
            last_gap_scan   REAL,
            last_error      TEXT,
            updated_at      REAL NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive_known_gaps (
            symbol       TEXT NOT NULL,
            bucket_time  INTEGER NOT NULL,
            reason       TEXT NOT NULL DEFAULT 'unfillable',
            recorded_at  REAL NOT NULL,
            PRIMARY KEY (symbol, bucket_time)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_known_gaps_symbol ON archive_known_gaps (symbol)"
    )
    tick_time = _int64_type()
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS market_ticks (
            symbol   TEXT NOT NULL,
            time_ms  {tick_time} NOT NULL,
            price    REAL NOT NULL,
            volume   REAL NOT NULL DEFAULT 0,
            source   TEXT NOT NULL,
            PRIMARY KEY (symbol, time_ms)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticks_time_ms ON market_ticks (time_ms)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON market_ticks (symbol, time_ms)"
    )

    def _safe_alter_tick(col_sql: str) -> None:
        from app.database import _safe_alter as _sa
        _sa(cursor, col_sql)

    _safe_alter_tick("ALTER TABLE market_ticks ADD COLUMN bid REAL DEFAULT NULL")
    _safe_alter_tick("ALTER TABLE market_ticks ADD COLUMN ask REAL DEFAULT NULL")
    _safe_alter_tick("ALTER TABLE market_ticks ADD COLUMN spread REAL DEFAULT NULL")
    _safe_alter_tick("ALTER TABLE market_ticks ADD COLUMN tick_type TEXT DEFAULT 'trade'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS economic_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            impact TEXT,
            country TEXT,
            source TEXT NOT NULL,
            raw_json TEXT,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_economic_events_sched ON economic_events (scheduled_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS corporate_events (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL,
            title TEXT,
            metadata_json TEXT,
            source TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_corporate_events_sym ON corporate_events (symbol, event_date)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sentiment_events (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            score REAL NOT NULL,
            mention_count INTEGER NOT NULL DEFAULT 1,
            headline TEXT,
            published_at TEXT,
            raw_json TEXT,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_updated ON sentiment_events (symbol, updated_at DESC)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crypto_derivatives_history (
            symbol            TEXT NOT NULL,
            recorded_at       REAL NOT NULL,
            funding_rate      REAL,
            open_interest     REAL,
            oi_change_24h_pct REAL,
            mark_price        REAL,
            quadrant          TEXT,
            score             INTEGER NOT NULL DEFAULT 0,
            source            TEXT NOT NULL,
            metadata_json     TEXT,
            PRIMARY KEY (symbol, recorded_at)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_crypto_deriv_sym_time ON crypto_derivatives_history (symbol, recorded_at DESC)"
    )
