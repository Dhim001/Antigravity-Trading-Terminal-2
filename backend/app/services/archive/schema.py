"""Archive table definitions — called from init_db()."""

from app.db.connection import is_postgres


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
        CREATE TABLE IF NOT EXISTS market_ticks (
            symbol   TEXT NOT NULL,
            time_ms  INTEGER NOT NULL,
            price    REAL NOT NULL,
            volume   REAL NOT NULL DEFAULT 0,
            source   TEXT NOT NULL,
            PRIMARY KEY (symbol, time_ms)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticks_time_ms ON market_ticks (time_ms)"
    )
