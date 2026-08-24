"""Per-bot position slices (account positions remain aggregated in OMS)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.database import get_connection

_EPS = 1e-8


def _parse_trade_timestamp(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    text = str(ts).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _empty_position() -> dict:
    return {
        "size": 0.0,
        "avg_price": 0.0,
        "stop_loss_percent": None,
        "take_profit_percent": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "high_watermark": None,
        "low_watermark": None,
        "entry_atr": None,
        "opened_at": None,
    }


def _risk_prices(
    size: float,
    avg_price: float,
    *,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Compute absolute SL/TP prices from percents or explicit targets."""
    sl_pct = stop_loss_percent
    tp_pct = take_profit_percent
    sl_price = stop_loss_price
    tp_price = take_profit_price

    if size > 0:
        if sl_pct is not None and sl_price is None:
            sl_price = avg_price * (1 - sl_pct / 100)
        if tp_price is not None:
            tp_pct = None
        elif tp_pct is not None:
            tp_price = avg_price * (1 + tp_pct / 100)
    elif size < 0:
        if sl_pct is not None and sl_price is None:
            sl_price = avg_price * (1 + sl_pct / 100)
        if tp_price is not None:
            tp_pct = None
        elif tp_pct is not None:
            tp_price = avg_price * (1 - tp_pct / 100)

    return sl_pct, tp_pct, sl_price, tp_price


def evaluate_risk_trigger(
    size: float,
    avg_price: float,
    market_price: float,
    *,
    stop_loss_percent: float | None,
    take_profit_percent: float | None,
    stop_loss_price: float | None,
    take_profit_price: float | None,
    chandelier_stop_enabled: bool = False,
    chandelier_multiplier: float = 3.0,
    chandelier_arm_r: float = 0.0,
    chandelier_tighten_r: float = 2.0,
    chandelier_tighten_mult: float = 2.0,
    high_watermark: float | None = None,
    low_watermark: float | None = None,
    entry_atr: float | None = None,
    current_atr: float | None = None,
    bar_high: float | None = None,
    bar_low: float | None = None,
) -> tuple[str | None, float | None, float | None, float | None]:
    """
    Returns (trigger_type 'SL'|'TP'|None, updated trailing stop_loss_price, updated_high, updated_low).
    """
    if abs(size) <= _EPS:
        return None, None, None, None

    sl_price = stop_loss_price
    tp_price = take_profit_price
    trigger_type = None

    # Last trade plus optional candle wicks (live OMS). Backtests already pass
    # bar high/low via the watermark the bar loop maintains.
    excursion_high = market_price
    excursion_low = market_price
    if bar_high is not None:
        try:
            bh = float(bar_high)
            if bh > 0:
                excursion_high = max(excursion_high, bh)
        except (TypeError, ValueError):
            pass
    if bar_low is not None:
        try:
            bl = float(bar_low)
            if bl > 0:
                excursion_low = min(excursion_low, bl)
        except (TypeError, ValueError):
            pass

    # Always track excursion marks (MAE/MFE). Previously only the chandelier
    # branch updated these, so signal / %-stop exits reported mfe=0 forever.
    updated_high = (
        max(float(high_watermark), excursion_high)
        if high_watermark is not None
        else excursion_high
    )
    updated_low = (
        min(float(low_watermark), excursion_low)
        if low_watermark is not None
        else excursion_low
    )

    if chandelier_stop_enabled:
        from app.services.bots.profit_lock import ratchet_chandelier_stop

        atr = current_atr if current_atr is not None and current_atr > 0 else (entry_atr or 0.0)
        if size > 0:
            sl_price = ratchet_chandelier_stop(
                is_long=True,
                avg_price=avg_price,
                extreme=float(updated_high if updated_high is not None else market_price),
                atr=float(atr or 0.0),
                current_sl=sl_price,
                multiplier=chandelier_multiplier,
                arm_r=chandelier_arm_r,
                tighten_r=chandelier_tighten_r,
                tighten_mult=chandelier_tighten_mult,
                stop_loss_percent=stop_loss_percent,
                entry_atr=entry_atr,
            )
            if sl_price is not None and market_price <= sl_price:
                trigger_type = "SL"
            elif tp_price is not None and market_price >= tp_price:
                trigger_type = "TP"
        elif size < 0:
            sl_price = ratchet_chandelier_stop(
                is_long=False,
                avg_price=avg_price,
                extreme=float(updated_low if updated_low is not None else market_price),
                atr=float(atr or 0.0),
                current_sl=sl_price,
                multiplier=chandelier_multiplier,
                arm_r=chandelier_arm_r,
                tighten_r=chandelier_tighten_r,
                tighten_mult=chandelier_tighten_mult,
                stop_loss_percent=stop_loss_percent,
                entry_atr=entry_atr,
            )
            if sl_price is not None and market_price >= sl_price:
                trigger_type = "SL"
            elif tp_price is not None and market_price <= tp_price:
                trigger_type = "TP"
    else:
        if size > 0:
            if stop_loss_percent is not None:
                potential_sl = market_price * (1 - stop_loss_percent / 100)
                if sl_price is None or potential_sl > sl_price:
                    sl_price = potential_sl
            if sl_price is not None and market_price <= sl_price:
                trigger_type = "SL"
            elif tp_price is not None and market_price >= tp_price:
                trigger_type = "TP"
        else:
            if stop_loss_percent is not None:
                potential_sl = market_price * (1 + stop_loss_percent / 100)
                if sl_price is None or potential_sl < sl_price:
                    sl_price = potential_sl
            if sl_price is not None and market_price >= sl_price:
                trigger_type = "SL"
            elif tp_price is not None and market_price <= tp_price:
                trigger_type = "TP"

    trailing_sl = sl_price
    return trigger_type, trailing_sl, updated_high, updated_low


def sl_tp_limit_fill_price(
    trigger_type: str,
    *,
    market_price: float,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
    previous_stop_loss_price: float | None = None,
    size: float | None = None,
) -> float:
    """
    Fill price for SL/TP exits — limit-style at the stored trigger level.

    Matches backtester intra-bar logic: when price gaps through a *resting*
    TP/SL, fill at the limit rather than the gapped quote.

    A chandelier ratchet that invents a new stop already through last is not
    a resting order — fill at last, not at the new stop (recycle / wick-arm).
    Pass ``size`` + ``previous_stop_loss_price`` to enable that guard.
    """
    if trigger_type == "TP" and take_profit_price is not None:
        return float(take_profit_price)
    if trigger_type == "SL" and stop_loss_price is not None:
        new_sl = float(stop_loss_price)
        last = float(market_price)
        if size is None:
            return new_sl
        prev = None
        if previous_stop_loss_price is not None:
            try:
                prev = float(previous_stop_loss_price)
            except (TypeError, ValueError):
                prev = None
        is_long = float(size) > 0
        if is_long:
            if prev is not None and last <= prev:
                return prev
            if prev is None or new_sl > prev + _EPS:
                return last
        else:
            if prev is not None and last >= prev:
                return prev
            if prev is None or new_sl < prev - _EPS:
                return last
        return new_sl
    return float(market_price)


def get_bot_position(bot_id: str, symbol: str) -> dict:
    """Return bot-local size/avg/risk for risk and snapshot math."""
    if not bot_id or not symbol:
        return _empty_position()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT size, avg_price, stop_loss_percent, take_profit_percent,
                   stop_loss_price, take_profit_price,
                   high_watermark, low_watermark, entry_atr, opened_at
            FROM bot_positions WHERE bot_id = ? AND symbol = ?
            """,
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        if not row:
            return _empty_position()
        return {
            "size": float(row["size"]),
            "avg_price": float(row["avg_price"]),
            "stop_loss_percent": row["stop_loss_percent"],
            "take_profit_percent": row["take_profit_percent"],
            "stop_loss_price": row["stop_loss_price"],
            "take_profit_price": row["take_profit_price"],
            "high_watermark": row["high_watermark"],
            "low_watermark": row["low_watermark"],
            "entry_atr": row["entry_atr"],
            "opened_at": float(row["opened_at"]) if row["opened_at"] is not None else None,
        }
    finally:
        conn.close()


def get_bot_size(bot_id: str, symbol: str) -> float:
    if not bot_id or not symbol:
        return 0.0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT size FROM bot_positions WHERE bot_id = ? AND symbol = ?",
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        return float(row["size"] if row else 0.0)
    finally:
        conn.close()


def list_owners_grouped() -> dict[str, list[dict]]:
    """All bot position slices keyed by symbol (single query)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bp.bot_id, bp.symbol, bp.size, bp.avg_price,
                   bp.stop_loss_percent, bp.take_profit_percent, bp.stop_loss_price, bp.take_profit_price,
                   bp.high_watermark, bp.low_watermark, bp.entry_atr, bp.opened_at,
                   b.config as bot_config, b.timeframe as bot_timeframe,
                   b.strategy as bot_strategy
            FROM bot_positions bp
            LEFT JOIN bots b ON bp.bot_id = b.id
            WHERE ABS(bp.size) > ?
            ORDER BY bp.symbol, ABS(bp.size) DESC
            """,
            (_EPS,),
        )
        grouped: dict[str, list[dict]] = {}
        for row in cursor.fetchall():
            sym = row["symbol"]
            grouped.setdefault(sym, []).append({
                "bot_id": row["bot_id"],
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
                "high_watermark": row["high_watermark"],
                "low_watermark": row["low_watermark"],
                "entry_atr": row["entry_atr"],
                "opened_at": float(row["opened_at"]) if row["opened_at"] is not None else None,
                "bot_config": json.loads(row["bot_config"]) if row["bot_config"] else {},
                "timeframe": row["bot_timeframe"] or "1m",
                "strategy": row["bot_strategy"] or "",
            })
        return grouped
    finally:
        conn.close()


def get_symbol_owners(symbol: str) -> list[dict]:
    if not symbol:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bp.bot_id, bp.size, bp.avg_price,
                   bp.stop_loss_percent, bp.take_profit_percent, bp.stop_loss_price, bp.take_profit_price,
                   bp.high_watermark, bp.low_watermark, bp.entry_atr, bp.opened_at,
                   b.config as bot_config, b.timeframe as bot_timeframe,
                   b.strategy as bot_strategy
            FROM bot_positions bp
            LEFT JOIN bots b ON bp.bot_id = b.id
            WHERE bp.symbol = ? AND ABS(bp.size) > ?
            ORDER BY ABS(bp.size) DESC
            """,
            (symbol, _EPS),
        )
        return [
            {
                "bot_id": row["bot_id"],
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
                "high_watermark": row["high_watermark"],
                "low_watermark": row["low_watermark"],
                "entry_atr": row["entry_atr"],
                "opened_at": float(row["opened_at"]) if row["opened_at"] is not None else None,
                "bot_config": json.loads(row["bot_config"]) if row["bot_config"] else {},
                "timeframe": row["bot_timeframe"] or "1m",
                "strategy": row["bot_strategy"] or "",
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def owners_for_account_payload(symbol: str) -> list[dict]:
    """Lightweight owner list for wire payloads."""
    return [
        {"bot_id": o["bot_id"], "size": o["size"]}
        for o in get_symbol_owners(symbol)
    ]


def infer_opened_at(bot_id: str, symbol: str) -> float | None:
    """Best-effort opened_at from the latest entry trade for legacy rows."""
    if not bot_id or not symbol:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT timestamp FROM bot_trades
            WHERE bot_id = ? AND symbol = ? AND is_exit = 0
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        if not row:
            return None
        ts = row["timestamp"] if isinstance(row, dict) else row[0]
        return _parse_trade_timestamp(ts)
    finally:
        conn.close()


def ensure_opened_at(bot_id: str, symbol: str) -> float | None:
    """Return opened_at, backfilling from trades or now when missing."""
    pos = get_bot_position(bot_id, symbol)
    if abs(pos["size"]) <= _EPS:
        return None
    if pos.get("opened_at"):
        return float(pos["opened_at"])

    opened_at = infer_opened_at(bot_id, symbol) or time.time()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE bot_positions SET opened_at = ? WHERE bot_id = ? AND symbol = ?",
            (opened_at, bot_id, symbol),
        )
        conn.commit()
    finally:
        conn.close()
    return opened_at


def get_recent_closed_trades_pnl(bot_id: str, limit: int = 3) -> list[float]:
    """Return a list of PNLs for the bot's most recent closed trades."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT pnl 
            FROM bot_trades 
            WHERE bot_id = ? AND is_exit = 1 AND pnl IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (bot_id, limit),
        )
        rows = cursor.fetchall()
        return [float(row["pnl"]) if isinstance(row, dict) else float(row[0]) for row in rows]
    finally:
        conn.close()


def update_bot_risk(
    bot_id: str,
    symbol: str,
    avg_price: float,
    side: str,
    *,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> None:
    """Set per-bot virtual SL/TP on an open slice."""
    if not bot_id or not symbol:
        return

    pos = get_bot_position(bot_id, symbol)
    size = pos["size"]
    if abs(size) <= _EPS:
        return

    sl_pct, tp_pct, sl_price, tp_price = _risk_prices(
        size,
        avg_price if avg_price else pos["avg_price"],
        stop_loss_percent=stop_loss_percent,
        take_profit_percent=take_profit_percent,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_positions
            SET stop_loss_percent = ?, take_profit_percent = ?,
                stop_loss_price = ?, take_profit_price = ?
            WHERE bot_id = ? AND symbol = ?
            """,
            (sl_pct, tp_pct, sl_price, tp_price, bot_id, symbol),
        )
        conn.commit()
    finally:
        conn.close()


def apply_fill(
    bot_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    *,
    risk: dict | None = None,
    feed: Any = None,
) -> None:
    """Update bot-local position after a fill attributed to bot_id."""
    if not bot_id or not symbol or quantity <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Load bot config to see if chandelier is enabled
        cursor.execute("SELECT config, timeframe, strategy FROM bots WHERE id = ?", (bot_id,))
        bot_row = cursor.fetchone()
        entry_atr = None
        # Seed both excursion marks at fill price; evaluate_risk_trigger expands them.
        high_watermark = price
        low_watermark = price

        if bot_row:
            from app.services.bots.indicators import merge_strategy_config

            raw_cfg = json.loads(bot_row["config"]) if bot_row["config"] else {}
            bot_config = merge_strategy_config(bot_row["strategy"] or "", raw_cfg)
            timeframe = bot_row["timeframe"] or "1m"
            if bot_config.get("chandelier_stop_enabled"):
                if feed is not None:
                    try:
                        from app.services.bots.candle_source import get_bot_candles
                        import pandas as pd
                        import pandas_ta as ta
                        
                        candles = get_bot_candles(symbol, feed, timeframe=timeframe)
                        if candles and len(candles) >= 15:
                            df = pd.DataFrame(candles)
                            atr_len = bot_config.get("atr_length", 14)
                            atr_series = ta.atr(df["high"], df["low"], df["close"], length=atr_len)
                            if atr_series is not None and not atr_series.empty:
                                import math
                                val = atr_series.iloc[-1]
                                if not math.isnan(val):
                                    entry_atr = float(val)
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).debug("Failed to calculate entry ATR in apply_fill: %s", exc)

        cursor.execute(
            """
            SELECT size, avg_price, stop_loss_percent, take_profit_percent,
                   stop_loss_price, take_profit_price,
                   high_watermark, low_watermark, entry_atr, opened_at
            FROM bot_positions WHERE bot_id = ? AND symbol = ?
            """,
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        delta = quantity if side == "BUY" else -quantity
        now = time.time()
        opened_at: float | None = None

        if not row:
            new_size = delta
            new_avg = price if abs(new_size) > _EPS else 0.0
            sl_pct = tp_pct = sl_price = tp_price = None
            if abs(new_size) > _EPS:
                opened_at = now
            if risk and abs(new_size) > _EPS:
                sl_pct, tp_pct, sl_price, tp_price = _risk_prices(
                    new_size, new_avg, **risk
                )
        else:
            current_size = float(row["size"])
            current_avg = float(row["avg_price"])
            new_size = current_size + delta
            if abs(new_size) <= _EPS:
                new_size = 0.0
                new_avg = 0.0
                sl_pct = tp_pct = sl_price = tp_price = None
                high_watermark = low_watermark = entry_atr = None
                opened_at = None
            else:
                flipped = (current_size > 0 and new_size < 0) or (current_size < 0 and new_size > 0)
                if flipped:
                    opened_at = now
                    # New direction — reset excursion window at this fill.
                    high_watermark = price
                    low_watermark = price
                else:
                    opened_at = float(row["opened_at"]) if row["opened_at"] is not None else now
                    prev_hi = row["high_watermark"]
                    prev_lo = row["low_watermark"]
                    high_watermark = max(float(prev_hi), price) if prev_hi is not None else price
                    low_watermark = min(float(prev_lo), price) if prev_lo is not None else price

                entry_atr = row["entry_atr"] if row["entry_atr"] is not None else entry_atr

                if side == "BUY" and current_size >= 0:
                    new_avg = ((current_size * current_avg) + quantity * price) / new_size
                    sl_pct, tp_pct, sl_price, tp_price = (
                        _risk_prices(new_size, new_avg, **risk) if risk
                        else (
                            row["stop_loss_percent"],
                            row["take_profit_percent"],
                            row["stop_loss_price"],
                            row["take_profit_price"],
                        )
                    )
                elif side == "SELL" and current_size < 0:
                    new_avg = ((abs(current_size) * current_avg) + quantity * price) / abs(new_size)
                    sl_pct, tp_pct, sl_price, tp_price = (
                        _risk_prices(new_size, new_avg, **risk) if risk
                        else (
                            row["stop_loss_percent"],
                            row["take_profit_percent"],
                            row["stop_loss_price"],
                            row["take_profit_price"],
                        )
                    )
                else:
                    new_avg = current_avg if new_size > 0 else (price if new_size < 0 else 0.0)
                    sl_pct = tp_pct = sl_price = tp_price = None

        if abs(new_size) <= _EPS:
            cursor.execute(
                "DELETE FROM bot_positions WHERE bot_id = ? AND symbol = ?",
                (bot_id, symbol),
            )
        elif not row:
            cursor.execute(
                """
                INSERT INTO bot_positions (
                    bot_id, symbol, size, avg_price,
                    stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price,
                    high_watermark, low_watermark, entry_atr, opened_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (bot_id, symbol, new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price, high_watermark, low_watermark, entry_atr, opened_at),
            )
        else:
            cursor.execute(
                """
                UPDATE bot_positions
                SET size = ?, avg_price = ?,
                    stop_loss_percent = ?, take_profit_percent = ?,
                    stop_loss_price = ?, take_profit_price = ?,
                    high_watermark = ?, low_watermark = ?, entry_atr = ?,
                    opened_at = ?
                WHERE bot_id = ? AND symbol = ?
                """,
                (new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price, high_watermark, low_watermark, entry_atr, opened_at, bot_id, symbol),
            )
        conn.commit()
    finally:
        conn.close()


def apply_unattributed_close(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    *,
    feed: Any = None,
) -> float:
    """Reduce bot slices when an account fill has no ``bot_id`` (manual close/rev).

    Only closes existing same-direction inventory (FIFO by largest slice). Never
    opens new bot exposure from a manual order. Returns quantity applied to bots.
    """
    if not symbol or quantity <= _EPS:
        return 0.0
    side_u = (side or "").upper()
    if side_u not in ("BUY", "SELL"):
        return 0.0

    owners = get_symbol_owners(symbol)
    if not owners:
        return 0.0

    # SELL reduces longs; BUY reduces shorts.
    eligible = [
        o
        for o in owners
        if (o["size"] > _EPS if side_u == "SELL" else o["size"] < -_EPS)
    ]
    if not eligible:
        return 0.0

    remaining = float(quantity)
    applied = 0.0
    for owner in eligible:
        if remaining <= _EPS:
            break
        take = min(abs(float(owner["size"])), remaining)
        if take <= _EPS:
            continue
        apply_fill(
            owner["bot_id"],
            symbol,
            side_u,
            take,
            price,
            feed=feed,
        )
        remaining -= take
        applied += take
    return applied


def clear_symbol(symbol: str) -> None:
    if not symbol:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_positions WHERE symbol = ?", (symbol,))
        conn.commit()
    finally:
        conn.close()


def clear_bot(bot_id: str) -> None:
    if not bot_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_positions WHERE bot_id = ?", (bot_id,))
        conn.commit()
    finally:
        conn.close()
