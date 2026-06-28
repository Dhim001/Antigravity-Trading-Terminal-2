"""Portfolio exposure classification — asset class, sector, strategy heatmaps."""

from __future__ import annotations

from collections import defaultdict

from app.database import get_connection
from app.services.analytics.portfolio import MANUAL_STRATEGY
from app.services.bots.correlation import is_crypto_symbol
from app.services.bots.portfolio_risk import build_portfolio_snapshot, list_bot_exposures, symbol_correlation_group


def asset_class_for_symbol(symbol: str) -> str:
    sym = str(symbol or "").upper()
    if is_crypto_symbol(sym):
        return "Crypto"
    if sym in ("SPY", "QQQ"):
        return "Index ETF"
    return "US Equity"


def sector_for_symbol(symbol: str) -> str:
    return symbol_correlation_group(symbol)


def _mark_prices(snapshot, bot_rows: list[dict], oms) -> dict[str, float]:
    marks: dict[str, float] = {}
    for row in bot_rows:
        sym = row["symbol"]
        if sym in marks:
            continue
        exp = snapshot.symbol_exposure.get(sym, 0.0)
        total_size = sum(abs(float(r["size"])) for r in bot_rows if r["symbol"] == sym)
        if exp > 0 and total_size > 0:
            marks[sym] = exp / total_size
        else:
            marks[sym] = float(row.get("avg_price") or 0)
    account = oms.get_account_data()
    for sym, pos in (account.get("positions") or {}).items():
        if sym not in marks:
            size = abs(float(pos.get("size") or 0))
            exp = snapshot.symbol_exposure.get(sym, 0.0)
            if exp > 0 and size > 0:
                marks[sym] = exp / size
            else:
                marks[sym] = float(pos.get("avg_price") or pos.get("mark") or 0)
    return marks


def collect_position_exposures(oms) -> list[dict]:
    snapshot = build_portfolio_snapshot(oms)
    bot_rows = list_bot_exposures()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT bp.bot_id, bp.symbol, bp.size, bp.avg_price, b.strategy
        FROM bot_positions bp
        JOIN bots b ON b.id = bp.bot_id
        WHERE ABS(bp.size) > 1e-8
        """
    )
    bot_meta = {
        (row["bot_id"], row["symbol"]): dict(row)
        for row in cursor.fetchall()
    }
    conn.close()

    marks = _mark_prices(snapshot, bot_rows, oms)
    rows: list[dict] = []
    covered: set[tuple[str, str]] = set()

    for row in bot_rows:
        sym = row["symbol"]
        meta = bot_meta.get((row["bot_id"], sym), {})
        mark = marks.get(sym) or float(row.get("avg_price") or 0)
        notional = abs(float(row["size"]) * mark)
        if notional <= 0:
            continue
        covered.add((row["bot_id"], sym))
        rows.append({
            "symbol": sym,
            "strategy": meta.get("strategy") or "Unknown",
            "asset_class": asset_class_for_symbol(sym),
            "sector": sector_for_symbol(sym),
            "notional": round(notional, 2),
        })

    account = oms.get_account_data()
    bot_symbols = {r["symbol"] for r in bot_rows}
    for sym, pos in (account.get("positions") or {}).items():
        size = float(pos.get("size") or 0)
        if abs(size) < 1e-8:
            continue
        mark = marks.get(sym) or float(pos.get("avg_price") or 0)
        bot_notional = sum(
            r["notional"] for r in rows if r["symbol"] == sym
        )
        total_notional = abs(size * mark)
        manual_notional = max(0.0, total_notional - bot_notional)
        if manual_notional > 0.01:
            rows.append({
                "symbol": sym,
                "strategy": MANUAL_STRATEGY,
                "asset_class": asset_class_for_symbol(sym),
                "sector": sector_for_symbol(sym),
                "notional": round(manual_notional, 2),
            })
        elif sym not in bot_symbols and total_notional > 0.01:
            rows.append({
                "symbol": sym,
                "strategy": MANUAL_STRATEGY,
                "asset_class": asset_class_for_symbol(sym),
                "sector": sector_for_symbol(sym),
                "notional": round(total_notional, 2),
            })

    return rows


def _aggregate_slices(rows: list[dict], field: str) -> list[dict]:
    buckets: dict[str, float] = defaultdict(float)
    symbols: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        key = str(row.get(field) or "Unknown")
        buckets[key] += float(row["notional"])
        symbols[key].add(row["symbol"])
    total = sum(buckets.values()) or 1.0
    return [
        {
            "key": key,
            "notional": round(val, 2),
            "pct": round((val / total) * 100, 2),
            "symbols": sorted(symbols[key]),
        }
        for key, val in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    ]


def _cross_strategy_sector(rows: list[dict]) -> dict:
    strategies = sorted({r["strategy"] for r in rows})
    sectors = sorted({r["sector"] for r in rows})
    if not strategies or not sectors:
        return {"rows": [], "cols": [], "matrix": []}

    row_idx = {s: i for i, s in enumerate(strategies)}
    col_idx = {s: j for j, s in enumerate(sectors)}
    matrix = [[0.0 for _ in sectors] for _ in strategies]
    for row in rows:
        matrix[row_idx[row["strategy"]]][col_idx[row["sector"]]] += float(row["notional"])

    rounded = [[round(v, 2) for v in r] for r in matrix]
    return {"rows": strategies, "cols": sectors, "matrix": rounded}


def get_exposure_heatmap(oms) -> dict:
    rows = collect_position_exposures(oms)
    snapshot = build_portfolio_snapshot(oms)
    total = round(sum(r["notional"] for r in rows), 2)

    if not rows:
        return {
            "total_notional": 0.0,
            "account_equity": round(snapshot.account_equity, 2),
            "position_count": 0,
            "by_asset_class": [],
            "by_sector": [],
            "by_strategy": [],
            "cross_strategy_sector": {"rows": [], "cols": [], "matrix": []},
        }

    return {
        "total_notional": total,
        "account_equity": round(snapshot.account_equity, 2),
        "position_count": len(rows),
        "by_asset_class": _aggregate_slices(rows, "asset_class"),
        "by_sector": _aggregate_slices(rows, "sector"),
        "by_strategy": _aggregate_slices(rows, "strategy"),
        "cross_strategy_sector": _cross_strategy_sector(rows),
    }
