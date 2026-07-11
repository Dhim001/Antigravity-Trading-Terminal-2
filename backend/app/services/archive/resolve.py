"""Merge live feed candles with archived history for backtests and chart loads."""

from __future__ import annotations

import time
from typing import Any

from app.config import ARCHIVE_ENABLED, ARCHIVE_RETENTION_1M_DAYS
from app.services.archive.query import query_market_history
from app.services.market.resample import resample_candles_for_timeframe
from app.services.market.timeframes import is_valid_timeframe, normalize_timeframe, timeframe_to_secs


def merge_candle_series(*series: list[dict], align_secs: int = 60) -> list[dict]:
    """Merge multiple OHLCV lists; later series win on duplicate timestamps."""
    step = max(1, int(align_secs))

    def _align(t: int) -> int:
        return (int(t) // step) * step

    by_time: dict[int, dict] = {}
    for candles in series:
        if not candles:
            continue
        for bar in candles:
            if bar.get("time") is None:
                continue
            t = _align(bar["time"])
            by_time[t] = {
                "time": t,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume") or 0),
            }
    return [by_time[t] for t in sorted(by_time)]


def resolve_candles_for_range(
    symbol: str,
    feed,
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    days: int | None = None,
    interval: str = "auto",
) -> tuple[list[dict], dict[str, Any]]:
    """
    Combine archived bars with the in-memory feed buffer for a time window.
    Returns (candles, metadata).
    """
    now = int(time.time())
    if days is not None:
        from_ts = now - int(days) * 86400
        to_ts = now
    else:
        to_ts = int(to_ts if to_ts is not None else now)
        from_ts = int(from_ts if from_ts is not None else now - 7 * 86400)

    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    live: list[dict] = []
    if feed is not None and hasattr(feed, "get_candles"):
        live = feed.get_candles(symbol) or []

    archived: list[dict] = []
    if ARCHIVE_ENABLED:
        archived = query_market_history(symbol, from_ts, to_ts, interval=interval)

    merged = merge_candle_series(archived, live)
    windowed = [b for b in merged if from_ts <= b["time"] <= to_ts]

    meta = {
        "symbol": symbol,
        "from": from_ts,
        "to": to_ts,
        "count": len(windowed),
        "live_bars": len(live),
        "archived_bars": len(archived),
        "interval": interval,
        "archive_enabled": ARCHIVE_ENABLED,
    }
    if windowed:
        meta["oldest"] = windowed[0]["time"]
        meta["newest"] = windowed[-1]["time"]

    from app.config import BACKTEST_PRICE_ADJUST
    from app.services.altdata.adjustments import apply_price_adjustments

    if BACKTEST_PRICE_ADJUST != "raw" and windowed:
        windowed = apply_price_adjustments(windowed, symbol, mode=BACKTEST_PRICE_ADJUST)
        meta["price_adjust"] = BACKTEST_PRICE_ADJUST

    return windowed, meta


def _replayed_span_days(candles: list[dict]) -> float:
    if not candles:
        return 0.0
    return round(max(0.0, (candles[-1]["time"] - candles[0]["time"]) / 86400.0), 2)


def _coverage_ok(candles: list[dict], days: int, *, min_ratio: float = 0.85) -> bool:
    """True when candle span covers most of the requested window."""
    if days <= 0 or not candles:
        return False
    return _replayed_span_days(candles) >= days * min_ratio


def _attach_backtest_range_meta(
    meta: dict[str, Any],
    candles: list[dict],
    *,
    days: int,
    effective_days: int,
    symbol: str = "",
) -> None:
    """Record requested vs actually replayed window for UI parity."""
    meta["days_requested"] = days
    meta["days"] = days
    meta["count"] = len(candles)
    if candles:
        meta["oldest"] = candles[0]["time"]
        meta["newest"] = candles[-1]["time"]
    replayed = _replayed_span_days(candles)
    meta["replayed_days"] = replayed

    notes: list[str] = []
    if meta.get("timeframe_note"):
        notes.append(str(meta["timeframe_note"]))

    if replayed > 0 and replayed < days * 0.9:
        if effective_days < days and not meta.get("timeframe_note"):
            notes.append(
                f"Replayed ~{replayed}d (requested {days}d; archive capped to {effective_days}d)"
            )
        else:
            notes.append(f"Replayed ~{replayed}d of {days}d requested")
    elif effective_days < days and not meta.get("timeframe_note"):
        notes.append(f"Range capped to {effective_days}d (1m archive retention)")

    if notes:
        meta["range_note"] = " · ".join(notes)

    if candles and symbol:
        from app.services.altdata.event_policy import backtest_event_manifest

        meta["event_manifest"] = backtest_event_manifest(
            symbol,
            int(candles[0]["time"]),
            int(candles[-1]["time"]),
        )


def _broker_fill_candles(
    symbol: str,
    local: list[dict],
    *,
    from_ts: int,
    to_ts: int,
    timeframe: str,
) -> tuple[list[dict], str | None]:
    """Fetch missing history from broker REST; merge with local (local wins on overlap)."""
    from app.services.archive.broker_fetch import fetch_broker_tf_candles

    requested_days = max(1, (to_ts - from_ts) // 86400)
    if _coverage_ok(local, requested_days):
        return local, None

    tf_key = "1m" if timeframe == "tick" else timeframe
    try:
        align = timeframe_to_secs(tf_key)
    except ValueError:
        align = 60

    fetch_from, fetch_to = from_ts, to_ts
    if local:
        oldest = int(local[0]["time"])
        newest = int(local[-1]["time"])
        # Local already has the recent tail — only pull the older gap.
        if newest >= to_ts - 2 * 86400 and oldest > from_ts + align:
            fetch_to = oldest

    remote = fetch_broker_tf_candles(symbol, fetch_from, fetch_to, tf_key)
    if not remote and (fetch_from, fetch_to) != (from_ts, to_ts):
        remote = fetch_broker_tf_candles(symbol, from_ts, to_ts, tf_key)
    if not remote:
        return local, None

    merged = merge_candle_series(remote, local, align_secs=align)
    windowed = [b for b in merged if from_ts <= b["time"] <= to_ts]
    return windowed, f"broker REST {tf_key}"


def resolve_backtest_candles(
    symbol: str,
    feed,
    *,
    days: int = 7,
    interval: str | None = None,
    timeframe: str = "1m",
) -> tuple[list[dict], dict[str, Any]]:
    """
    Load historical OHLCV for backtests.

    Short windows use local 1m archive (resampled for higher TFs).
    Longer windows keep a lean 1m retention by filling from broker REST
    (native TF aggs when possible) or rolled-up 1h archive — without
    permanently storing 90d of 1m bars.
    """
    days = max(1, min(int(days), 365))
    if timeframe and str(timeframe).lower() == "tick":
        tf = "1m"
        meta_timeframe = "tick"
        timeframe_note = "Tick backtest replays simulated paths from 1m archive"
    elif not is_valid_timeframe(timeframe):
        raise ValueError(f"Unsupported backtest timeframe: {timeframe}")
    else:
        tf = normalize_timeframe(timeframe)
        meta_timeframe = tf
        timeframe_note = None

    retention = int(ARCHIVE_RETENTION_1M_DAYS)
    effective_days = days
    source_note: str | None = None
    needs_external = days > retention

    # ── 1m path ──────────────────────────────────────────────────────────
    if tf == "1m":
        if interval is None:
            interval = "1m"
        candles, meta = resolve_candles_for_range(
            symbol, feed, days=days, interval=interval
        )
        if needs_external and not _coverage_ok(candles, days):
            filled, src = _broker_fill_candles(
                symbol,
                candles,
                from_ts=int(meta["from"]),
                to_ts=int(meta["to"]),
                timeframe="1m",
            )
            if src:
                candles = filled
                source_note = src
                meta["broker_filled"] = True
            else:
                # Last resort: mixed 1m+1h (coarse older history).
                mixed, mixed_meta = resolve_candles_for_range(
                    symbol, feed, days=days, interval="auto"
                )
                if _coverage_ok(mixed, days) or len(mixed) > len(candles):
                    candles, meta = mixed, mixed_meta
                    source_note = "mixed 1m (recent) + 1h (older)"
                    timeframe_note = (
                        f"Older than {retention}d uses 1h archive "
                        "(broker 1m unavailable)"
                    )

        meta["days"] = days
        meta["effective_days"] = effective_days
        meta["timeframe"] = meta_timeframe
        meta["interval"] = meta.get("interval") or interval
        if timeframe_note:
            meta["timeframe_note"] = timeframe_note
        meta["resolution_note"] = source_note or (
            "1m bars"
            if meta.get("interval") == "1m"
            else "mixed 1m (recent) + 1h (older)"
        )
        _attach_backtest_range_meta(
            meta, candles, days=days, effective_days=effective_days, symbol=symbol,
        )
        return candles, meta

    # ── Higher TF within 1m retention: resample local 1m only ────────────
    if not needs_external:
        candles_1m, meta = resolve_candles_for_range(
            symbol, feed, days=days, interval="1m"
        )
        resampled = resample_candles_for_timeframe(candles_1m, tf)
        meta["days"] = days
        meta["effective_days"] = effective_days
        meta["timeframe"] = meta_timeframe
        meta["interval"] = "1m"
        meta["bars_1m"] = len(candles_1m)
        meta["resolution_note"] = f"1m bars resampled to {tf}"
        if timeframe_note:
            meta["timeframe_note"] = timeframe_note
        _attach_backtest_range_meta(
            meta, resampled, days=days, effective_days=effective_days, symbol=symbol,
        )
        return resampled, meta

    # ── Long HT path: native broker TF (preferred) ───────────────────────
    now = int(time.time())
    from_ts = now - days * 86400
    to_ts = now
    local_seed, meta = resolve_candles_for_range(
        symbol, feed, days=min(days, retention), interval="1m"
    )
    # Seed with resampled local recent bars so broker gap-fill can merge.
    local_tf = (
        resample_candles_for_timeframe(local_seed, tf) if local_seed else []
    )
    filled, src = _broker_fill_candles(
        symbol,
        local_tf,
        from_ts=from_ts,
        to_ts=to_ts,
        timeframe=tf,
    )
    if src and _coverage_ok(filled, days):
        meta["from"] = from_ts
        meta["to"] = to_ts
        meta["days"] = days
        meta["effective_days"] = days
        meta["timeframe"] = meta_timeframe
        meta["interval"] = tf
        meta["broker_filled"] = True
        meta["bars_1m"] = len(local_seed)
        meta["resolution_note"] = f"broker native {tf}"
        if timeframe_note:
            meta["timeframe_note"] = timeframe_note
        else:
            meta["timeframe_note"] = (
                f"Long-horizon {tf}: broker REST (1m archive retention={retention}d)"
            )
        _attach_backtest_range_meta(
            meta, filled, days=days, effective_days=days, symbol=symbol,
        )
        return filled, meta

    # ── Fallback: 1h archive for 1h / 4h / 1d ────────────────────────────
    if tf in ("1h", "4h", "1d"):
        hourly, meta = resolve_candles_for_range(
            symbol, feed, days=days, interval="1h"
        )
        if hourly:
            out = hourly if tf == "1h" else resample_candles_for_timeframe(hourly, tf)
            meta["days"] = days
            meta["effective_days"] = days
            meta["timeframe"] = meta_timeframe
            meta["interval"] = "1h"
            meta["resolution_note"] = (
                "1h archive" if tf == "1h" else f"1h archive resampled to {tf}"
            )
            meta["timeframe_note"] = (
                timeframe_note
                or f"Long-horizon {tf} from 1h archive (broker native unavailable)"
            )
            _attach_backtest_range_meta(
                meta, out, days=days, effective_days=days, symbol=symbol,
            )
            return out, meta

    # ── Last resort: cap to 1m retention and resample ────────────────────
    effective_days = min(days, retention)
    candles_1m, meta = resolve_candles_for_range(
        symbol, feed, days=effective_days, interval="1m"
    )
    resampled = resample_candles_for_timeframe(candles_1m, tf)
    meta["days"] = days
    meta["effective_days"] = effective_days
    meta["timeframe"] = meta_timeframe
    meta["interval"] = "1m"
    meta["bars_1m"] = len(candles_1m)
    meta["resolution_note"] = f"1m bars resampled to {tf}"
    meta["timeframe_note"] = (
        timeframe_note
        or f"Range capped to {effective_days}d for {tf} (no broker/1h history)"
    )
    _attach_backtest_range_meta(
        meta, resampled, days=days, effective_days=effective_days, symbol=symbol,
    )
    return resampled, meta
