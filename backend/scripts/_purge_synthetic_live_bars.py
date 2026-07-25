"""Purge synthetic LIVE_* archive bars that disagree with broker REST history."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "trading-alpaca.db"


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    before = cur.execute(
        "SELECT COUNT(*) FROM market_bars_1m WHERE source LIKE 'LIVE_%'"
    ).fetchone()[0]
    cur.execute(
        """
        DELETE FROM market_bars_1m
        WHERE rowid IN (
            SELECT l.rowid
            FROM market_bars_1m AS l
            JOIN market_bars_1m AS r
              ON r.symbol = l.symbol
             AND r.source IN ('ALPACA_REST', 'MASSIVE_REST', 'BINANCE_REST')
             AND ABS(r.time - l.time) <= 180
             AND r.close > 0
             AND ABS(l.close * 1.0 / r.close - 1.0) > 0.15
            WHERE l.source LIKE 'LIVE_%'
              AND INSTR(l.symbol, 'USDT') = 0
        )
        """
    )
    deleted_conflict = cur.rowcount
    # Orphan LIVE equity placeholders (fractional volume, no REST neighbor minute).
    cur.execute(
        """
        DELETE FROM market_bars_1m
        WHERE source LIKE 'LIVE_%'
          AND INSTR(symbol, 'USDT') = 0
          AND (volume - CAST(volume AS INTEGER)) > 0.001
          AND NOT EXISTS (
              SELECT 1 FROM market_bars_1m AS r
              WHERE r.symbol = market_bars_1m.symbol
                AND r.source IN ('ALPACA_REST', 'MASSIVE_REST', 'BINANCE_REST')
                AND ABS(r.time - market_bars_1m.time) <= 60
          )
        """
    )
    deleted_orphan = cur.rowcount
    con.commit()
    after = cur.execute(
        "SELECT COUNT(*) FROM market_bars_1m WHERE source LIKE 'LIVE_%'"
    ).fetchone()[0]
    aapl_bad = cur.execute(
        """
        SELECT COUNT(*) FROM market_bars_1m
        WHERE symbol='AAPL' AND source='LIVE_ALPACA' AND close < 250
        """
    ).fetchone()[0]
    print(
        f"LIVE before={before} after={after} "
        f"deleted_conflict={deleted_conflict} deleted_orphan={deleted_orphan} "
        f"aapl_live_lt250={aapl_bad}"
    )
    con.close()


if __name__ == "__main__":
    main()
