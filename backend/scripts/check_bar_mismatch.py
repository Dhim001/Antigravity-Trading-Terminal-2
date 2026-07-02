"""Query bot_logs for CHART_AGENT bar_time mismatch frequency."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "trading-massive.db"
BOT_ID = "2093f2da-7522-4fbc-9000-785faf019e23"


def ts_label(epoch):
    if epoch is None:
        return "—"
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%H:%M:%S UTC")
    except (TypeError, ValueError, OSError):
        return str(epoch)


def main():
    if not DB.exists():
        print(f"DB not found: {DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT id, symbol, strategy, status FROM bots WHERE strategy = 'CHART_AGENT'"
    )
    print("=== CHART_AGENT bots ===")
    for b in cur.fetchall():
        print(f"  {b['id'][:8]}… {b['symbol']} {b['status']}")

    cur.execute(
        """
        SELECT COUNT(*) AS n FROM bot_logs
        WHERE message LIKE '%bar_time mismatch%'
        """
    )
    total = cur.fetchone()["n"]
    print(f"\n=== bar_time mismatch total: {total} ===")

    cur.execute(
        """
        SELECT bot_id, level, message, timestamp, meta
        FROM bot_logs
        WHERE message LIKE '%bar_time mismatch%'
        ORDER BY timestamp DESC
        LIMIT 40
        """
    )
    rows = cur.fetchall()
    for r in rows:
        meta = json.loads(r["meta"]) if r["meta"] else {}
        cached_bar = "?"
        msg = r["message"]
        if "cached=" in msg:
            try:
                part = msg.split("cached=")[1]
                cached_bar = part.split(",")[0].split(")")[0]
            except IndexError:
                pass
        bar = meta.get("bar_time")
        print(
            f"  {r['timestamp']} | bot={r['bot_id'][:8]} | "
            f"eval_bar={bar} ({ts_label(bar)}) | cached~{cached_bar} ({ts_label(cached_bar)})"
        )

    cur.execute(
        """
        SELECT substr(timestamp, 1, 13) AS hr, COUNT(*) AS n
        FROM bot_logs
        WHERE message LIKE '%bar_time mismatch%'
        GROUP BY hr
        ORDER BY hr DESC
        LIMIT 24
        """
    )
    print("\n=== mismatches by hour ===")
    for r in cur.fetchall():
        print(f"  {r['hr']}:00 -> {r['n']} events")

    cur.execute(
        """
        SELECT message, COUNT(*) AS n
        FROM bot_logs
        WHERE bot_id = ? AND message LIKE 'CHART_AGENT skipped:%'
        GROUP BY message
        ORDER BY n DESC
        LIMIT 20
        """,
        (BOT_ID,),
    )
    print(f"\n=== skip breakdown for {BOT_ID[:8]}… ===")
    for r in cur.fetchall():
        print(f"  {r['n']:4d}  {r['message']}")

    cur.execute(
        """
        SELECT timestamp, message
        FROM bot_logs
        WHERE bot_id = ?
        ORDER BY timestamp DESC
        LIMIT 15
        """,
        (BOT_ID,),
    )
    print(f"\n=== last 15 log lines for bot ===")
    for r in cur.fetchall():
        print(f"  {r['timestamp']}  {r['message'][:100]}")

    cur.execute(
        """
        SELECT
          SUM(CASE WHEN message LIKE '%bar_time mismatch%' THEN 1 ELSE 0 END) AS mismatches,
          COUNT(*) AS total
        FROM bot_logs
        WHERE bot_id = ? AND message LIKE 'CHART_AGENT skipped:%'
        """,
        (BOT_ID,),
    )
    row = cur.fetchone()
    mm, total = row["mismatches"], row["total"]
    pct = round(100 * mm / total, 1) if total else 0
    print(f"\n=== mismatch rate (all time): {mm}/{total} = {pct}% ===")

    cur.execute(
        """
        SELECT
          SUM(CASE WHEN message LIKE '%bar_time mismatch%' THEN 1 ELSE 0 END) AS mismatches,
          COUNT(*) AS total
        FROM bot_logs
        WHERE bot_id = ? AND message LIKE 'CHART_AGENT skipped:%'
          AND timestamp >= '2026-07-02'
        """,
        (BOT_ID,),
    )
    row = cur.fetchone()
    mm2, total2 = row["mismatches"], row["total"]
    pct2 = round(100 * mm2 / total2, 1) if total2 else 0
    print(f"=== mismatch rate (Jul 2 only): {mm2}/{total2} = {pct2}% ===")

    conn.close()


if __name__ == "__main__":
    main()
