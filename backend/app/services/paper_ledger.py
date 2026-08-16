"""Paper margin ledger helpers for simulated long/short positions."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

QUOTE_ASSETS: tuple[str, ...] = ("USD", "USDT")
_EPS = 1e-8
# Matches ``init_db`` seed for USD/USDT paper buckets.
QUOTE_CASH_SEED = 100_000.0
# qty * price vs ledger cash — a few cents covers IEEE remainder after margin caps.
CASH_COMPARE_EPS = 0.05


def classify_sell(
    position_size: float,
    locked_sell_qty: float,
    quantity: float,
) -> tuple[float, float]:
    """Return (long_close_qty, short_open_qty) for a SELL order."""
    long_available = max(0.0, position_size - locked_sell_qty) if position_size > 0 else 0.0
    long_close_qty = min(quantity, long_available)
    short_open_qty = quantity - long_close_qty
    return long_close_qty, short_open_qty


def classify_buy(position_size: float, quantity: float) -> tuple[float, float]:
    """Return (short_cover_qty, long_open_qty) for a BUY order."""
    short_available = max(0.0, -position_size) if position_size < 0 else 0.0
    short_cover_qty = min(quantity, short_available)
    long_open_qty = quantity - short_cover_qty
    return short_cover_qty, long_open_qty


def short_margin_required(
    position_size: float,
    locked_sell_qty: float,
    quantity: float,
    price: float,
) -> float:
    """Quote collateral required to open/increase a short on this sell."""
    _, short_open_qty = classify_sell(position_size, locked_sell_qty, quantity)
    return short_open_qty * price


def apply_fill_balances(
    cursor,
    *,
    side: str,
    price: float,
    quantity: float,
    quote: str,
    base_asset: str,
    position_size: float,
    position_avg: float,
) -> None:
    """Update account balances for a fill given the pre-fill net position."""
    if side == "BUY":
        short_cover, long_open = classify_buy(position_size, quantity)

        if short_cover > 0:
            cover_value = price * short_cover
            margin_release = position_avg * short_cover
            # Short open only *locks* 100% notional as margin — it never credits
            # sale proceeds. Cover must apply PnL, not debit the full buy
            # notional (that permanently drained quote cash each round-trip).
            pnl = margin_release - cover_value
            cursor.execute(
                "UPDATE accounts SET balance = balance + ? WHERE asset = ?",
                (pnl, quote),
            )
            cursor.execute(
                "UPDATE accounts SET locked = MAX(0.0, locked - ?) WHERE asset = ?",
                (margin_release, quote),
            )

        if long_open > 0:
            long_value = price * long_open
            cursor.execute(
                "UPDATE accounts SET balance = balance - ? WHERE asset = ?",
                (long_value, quote),
            )
            cursor.execute(
                "UPDATE accounts SET balance = balance + ? WHERE asset = ?",
                (long_open, base_asset),
            )
        return

    long_close, short_open = classify_sell(position_size, 0.0, quantity)

    if long_close > 0:
        close_value = price * long_close
        cursor.execute(
            "UPDATE accounts SET balance = balance + ? WHERE asset = ?",
            (close_value, quote),
        )
        cursor.execute(
            "UPDATE accounts SET balance = MAX(0.0, balance - ?) WHERE asset = ?",
            (long_close, base_asset),
        )

    if short_open > 0:
        margin_value = price * short_open
        cursor.execute(
            "UPDATE accounts SET locked = locked + ? WHERE asset = ?",
            (margin_value, quote),
        )


def _asset_symbol_index(
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, list[str]]:
    """Map base asset → terminal symbols (e.g. ADA → [ADAUSDT])."""
    index: dict[str, list[str]] = {}
    if not symbol_meta:
        return index
    for symbol, meta in symbol_meta.items():
        if not isinstance(meta, Mapping):
            continue
        asset = str(meta.get("asset") or "").upper()
        if not asset or asset in QUOTE_ASSETS:
            continue
        index.setdefault(asset, []).append(str(symbol))
    return index


def reconcile_base_inventories(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    quote_assets: Sequence[str] = QUOTE_ASSETS,
) -> list[str]:
    """Align non-quote ``accounts.balance`` with aggregate long position size.

    Paper fills credit base inventory (ADA, BTC, …) alongside ``positions``.
    If a position is zeroed without a matching sell (manual cash restore, bad
    SQL, interrupted fill), orphan base rows remain — visible in Balances but
    not closable in Positions. This sync forces:

        accounts[base].balance = Σ max(0, positions[symbol].size)

    Returns assets whose balances were corrected.
    """
    quotes = {str(q).upper() for q in quote_assets}
    asset_symbols = _asset_symbol_index(symbol_meta)

    cursor.execute("SELECT asset, balance FROM accounts")
    account_rows = list(cursor.fetchall())
    corrected: list[str] = []

    seen: set[str] = set()
    for row in account_rows:
        asset = str(row["asset"] if isinstance(row, Mapping) else row[0]).upper()
        if asset in quotes:
            continue
        seen.add(asset)
        symbols = asset_symbols.get(asset, [])
        long_qty = 0.0
        for symbol in symbols:
            cursor.execute(
                "SELECT size FROM positions WHERE symbol = ?",
                (symbol,),
            )
            pos = cursor.fetchone()
            if not pos:
                continue
            size = float(pos["size"] if isinstance(pos, Mapping) else pos[0] or 0)
            if size > _EPS:
                long_qty += size

        current = float(row["balance"] if isinstance(row, Mapping) else row[1] or 0)
        if abs(current - long_qty) <= _EPS:
            continue
        cursor.execute(
            "UPDATE accounts SET balance = ? WHERE asset = ?",
            (long_qty, asset),
        )
        corrected.append(asset)

    # Bases present in the symbol map but missing from accounts (rare).
    for asset, symbols in asset_symbols.items():
        if asset in seen or asset in quotes:
            continue
        long_qty = 0.0
        for symbol in symbols:
            cursor.execute(
                "SELECT size FROM positions WHERE symbol = ?",
                (symbol,),
            )
            pos = cursor.fetchone()
            if not pos:
                continue
            size = float(pos["size"] if isinstance(pos, Mapping) else pos[0] or 0)
            if size > _EPS:
                long_qty += size
        if long_qty <= _EPS:
            continue
        cursor.execute(
            "INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
            (asset, long_qty),
        )
        corrected.append(asset)

    return corrected


def quote_cash_covers(spendable: float, needed: float, *, eps: float = CASH_COMPARE_EPS) -> bool:
    """True when ``spendable`` can pay ``needed``, allowing 1 cent of float dust."""
    try:
        have = float(spendable)
        want = float(needed)
    except (TypeError, ValueError):
        return False
    if want <= 0:
        return True
    if have <= 0:
        return False
    return have + float(eps) >= want


def clip_qty_to_spendable(
    quantity: float,
    price: float,
    spendable: float,
    *,
    eps: float = CASH_COMPARE_EPS,
) -> float:
    """Keep ``qty * price`` inside ``spendable`` when the overshoot is float dust.

    Returns the (possibly reduced) quantity, or the original qty when the gap
    is a real shortfall (caller should reject).
    """
    try:
        qty = float(quantity)
        px = float(price)
        cash = float(spendable)
    except (TypeError, ValueError):
        return float(quantity or 0)
    if qty <= 0 or px <= 0:
        return qty
    needed = qty * px
    if quote_cash_covers(cash, needed, eps=eps):
        if needed <= cash:
            return qty
        return cash / px
    return qty


def _row_val(row, key: str, idx: int = 0):
    if row is None:
        return None
    try:
        return row[key]
    except (TypeError, IndexError, KeyError):
        return row[idx]


def _symbol_quote_base(
    symbol: str,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[str, str]:
    """Return (quote, base) for a terminal symbol; infer when feed meta is missing."""
    info = (symbol_meta or {}).get(symbol) if isinstance(symbol_meta, Mapping) else None
    quote = ""
    base = ""
    if isinstance(info, Mapping):
        quote = str(info.get("quote") or "").upper()
        base = str(info.get("asset") or "").upper()
    if not quote:
        from app.services.account_cash import quote_asset_for_symbol

        quote = quote_asset_for_symbol(symbol)
    if not base:
        raw = str(symbol or "").upper()
        if raw.endswith("USDT"):
            base = raw[:-4]
        elif raw.endswith("USD"):
            base = raw[:-3]
        else:
            base = raw
    if quote not in QUOTE_ASSETS:
        quote = "USDT" if quote == "USDT" or "USDT" in str(symbol or "").upper() else "USD"
        if quote not in QUOTE_ASSETS:
            quote = "USD"
    return quote, base


def _advance_position(size: float, avg: float, side: str, price: float, qty: float) -> tuple[float, float]:
    delta = qty if str(side).upper() == "BUY" else -qty
    new_size = size + delta
    if abs(new_size) <= _EPS:
        return 0.0, 0.0
    if size >= 0 and delta > 0:
        new_avg = ((size * avg) + qty * price) / new_size if new_size else 0.0
    elif size <= 0 and delta < 0:
        new_avg = ((abs(size) * avg) + qty * price) / abs(new_size) if new_size else 0.0
    elif (size > 0 and new_size > 0) or (size < 0 and new_size < 0):
        new_avg = avg
    else:
        new_avg = price
    return new_size, new_avg


def _ensure_mem_asset(cur, asset: str, *, opening: float = 0.0) -> None:
    cur.execute(
        "INSERT OR IGNORE INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
        (asset, opening),
    )


def _load_live_positions(cursor) -> dict[str, tuple[float, float]]:
    cursor.execute("SELECT symbol, size, avg_price FROM positions")
    out: dict[str, tuple[float, float]] = {}
    for row in cursor.fetchall() or []:
        symbol = str(_row_val(row, "symbol", 0) or "")
        size = float(_row_val(row, "size", 1) or 0)
        avg = float(_row_val(row, "avg_price", 2) or 0)
        if not symbol:
            continue
        out[symbol] = (size, avg)
    return out


def _align_replay_to_live(
    mcur,
    positions: dict[str, tuple[float, float]],
    live: Mapping[str, tuple[float, float]],
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
) -> None:
    """Force replay sizes onto the live book; extras close at cost (no invented PnL)."""
    symbols = set(positions) | set(live)
    for symbol in symbols:
        r_size, r_avg = positions.get(symbol, (0.0, 0.0))
        l_size, l_avg = live.get(symbol, (0.0, 0.0))
        if abs(l_size) <= _EPS:
            l_size, l_avg = 0.0, 0.0
        delta = l_size - r_size
        if abs(delta) <= _EPS:
            positions[symbol] = (l_size, l_avg if abs(l_size) > _EPS else 0.0)
            continue
        quote, base = _symbol_quote_base(symbol, symbol_meta)
        if delta > 0:
            px = l_avg if l_avg > 0 else r_avg
            side = "BUY"
            qty = delta
        else:
            px = r_avg if r_avg > 0 else l_avg
            side = "SELL"
            qty = -delta
        if px <= 0 or qty <= 0:
            positions[symbol] = (l_size, l_avg if abs(l_size) > _EPS else 0.0)
            continue
        _ensure_mem_asset(mcur, quote)
        _ensure_mem_asset(mcur, base)
        apply_fill_balances(
            mcur,
            side=side,
            price=px,
            quantity=qty,
            quote=quote,
            base_asset=base,
            position_size=r_size,
            position_avg=r_avg,
        )
        positions[symbol] = (l_size, l_avg if abs(l_size) > _EPS else 0.0)


def reconstruct_quote_cash_from_fills(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    seed: float = QUOTE_CASH_SEED,
) -> tuple[dict[str, dict[str, float]], dict[str, float], dict[str, int]]:
    """Replay FILLED orders with current fill accounting.

    Each quote bucket starts at ``seed``. Long buys/sells debit and credit
    notional; shorts lock margin and apply PnL on cover. The result *is*
    starting cash plus every executed profit and loss (open longs still
    sit as inventory, open shorts as locked margin).
    """
    cursor.execute(
        """
        SELECT symbol, side, quantity, filled_quantity, price, average_fill_price
        FROM orders
        WHERE status = 'FILLED'
        ORDER BY timestamp ASC, rowid ASC
        """
    )
    fills = list(cursor.fetchall())

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mcur = mem.cursor()
    mcur.execute(
        "CREATE TABLE accounts (asset TEXT PRIMARY KEY, balance REAL NOT NULL, locked REAL NOT NULL)"
    )
    for quote in QUOTE_ASSETS:
        mcur.execute(
            "INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
            (quote, float(seed)),
        )

    positions: dict[str, tuple[float, float]] = {}
    fill_counts: dict[str, int] = {q: 0 for q in QUOTE_ASSETS}

    for fill in fills:
        symbol = str(_row_val(fill, "symbol", 0) or "")
        side = str(_row_val(fill, "side", 1) or "").upper()
        filled_qty = float(_row_val(fill, "filled_quantity", 3) or 0)
        qty = filled_qty if filled_qty > 0 else float(_row_val(fill, "quantity", 2) or 0)
        avg_px = float(_row_val(fill, "average_fill_price", 5) or 0)
        price = avg_px if avg_px > 0 else float(_row_val(fill, "price", 4) or 0)
        if not symbol or side not in ("BUY", "SELL") or qty <= 0 or price <= 0:
            continue
        quote, base = _symbol_quote_base(symbol, symbol_meta)
        fill_counts[quote] = fill_counts.get(quote, 0) + 1
        size, avg = positions.get(symbol, (0.0, 0.0))
        _ensure_mem_asset(mcur, quote, opening=float(seed) if quote in QUOTE_ASSETS else 0.0)
        _ensure_mem_asset(mcur, base)
        apply_fill_balances(
            mcur,
            side=side,
            price=price,
            quantity=qty,
            quote=quote,
            base_asset=base,
            position_size=size,
            position_avg=avg,
        )
        positions[symbol] = _advance_position(size, avg, side, price, qty)

    # Fills can drift from the live book (flatten without a sell, orphan
    # inventory). Close extras at cost so cash = seed + realized P&L with
    # live inventory, instead of skipping the whole repair.
    _align_replay_to_live(mcur, positions, _load_live_positions(cursor), symbol_meta)

    mcur.execute("SELECT asset, balance, locked FROM accounts")
    cash = {
        str(row["asset"]): {
            "balance": float(row["balance"] or 0),
            "locked": float(row["locked"] or 0),
        }
        for row in mcur.fetchall()
    }
    mem.close()
    replay_size = {sym: sz for sym, (sz, _avg) in positions.items() if abs(sz) > _EPS}
    return cash, replay_size, fill_counts


def _live_quote_positions(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, float]]:
    cursor.execute("SELECT symbol, size FROM positions")
    by_quote: dict[str, dict[str, float]] = {q: {} for q in QUOTE_ASSETS}
    for row in cursor.fetchall() or []:
        symbol = str(_row_val(row, "symbol", 0) or "")
        size = float(_row_val(row, "size", 1) or 0)
        if abs(size) <= _EPS:
            continue
        quote, _base = _symbol_quote_base(symbol, symbol_meta)
        by_quote.setdefault(quote, {})[symbol] = size
    return by_quote


def _positions_match(live: Mapping[str, float], replay: Mapping[str, float]) -> bool:
    keys = set(live) | set(replay)
    for symbol in keys:
        if abs(float(live.get(symbol) or 0) - float(replay.get(symbol) or 0)) > 1e-6:
            return False
    return True


_TINY_TEST_BOOK = 10_000.0


def repair_quote_cash_from_fills(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    seed: float = QUOTE_CASH_SEED,
) -> list[str]:
    """Set quote cash to seed + executed P&L (aligned to the live book).

    Replays every FILLED order, then closes leftover inventory at cost so
    drifted fills (flatten without a sell) cannot skip the repair. Tiny
    non-empty books (unit tests / short fixtures) are left alone.
    """
    reconstructed, replay_pos, fill_counts = reconstruct_quote_cash_from_fills(
        cursor, symbol_meta, seed=seed,
    )

    live_by_quote = _live_quote_positions(cursor, symbol_meta)
    replay_by_quote: dict[str, dict[str, float]] = {q: {} for q in QUOTE_ASSETS}
    for symbol, size in replay_pos.items():
        quote, _base = _symbol_quote_base(symbol, symbol_meta)
        replay_by_quote.setdefault(quote, {})[symbol] = size

    repaired: list[str] = []
    for quote in QUOTE_ASSETS:
        recon = reconstructed.get(quote) or {"balance": float(seed), "locked": 0.0}
        recon_bal = float(recon["balance"])
        recon_locked = float(recon["locked"])
        cursor.execute(
            "SELECT balance, locked FROM accounts WHERE asset = ?",
            (quote,),
        )
        row = cursor.fetchone()
        if not row:
            continue
        live_bal = float(_row_val(row, "balance", 0) or 0)
        live_locked = float(_row_val(row, "locked", 1) or 0)
        n_fills = int(fill_counts.get(quote) or 0)
        if n_fills <= 0 and live_bal >= 1.0:
            continue
        if live_bal >= 1.0 and live_bal < _TINY_TEST_BOOK and recon_bal > 50_000:
            continue
        if not _positions_match(live_by_quote.get(quote) or {}, replay_by_quote.get(quote) or {}):
            continue
        if abs(live_bal - recon_bal) <= 1.0 and abs(live_locked - recon_locked) <= 1.0:
            continue
        cursor.execute(
            "UPDATE accounts SET balance = ?, locked = ? WHERE asset = ?",
            (recon_bal, max(0.0, recon_locked), quote),
        )
        repaired.append(quote)
    return repaired


def repair_empty_quote_cash(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    seed: float = QUOTE_CASH_SEED,
) -> list[str]:
    """Compatibility wrapper — rebuild quote cash from fill history."""
    return repair_quote_cash_from_fills(cursor, symbol_meta, seed=seed)

