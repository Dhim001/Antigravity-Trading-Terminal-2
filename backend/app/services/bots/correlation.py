"""Rolling price correlation — dynamic exposure groups with static fallback."""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

from app.config import (
    CORRELATION_GROUPS,
    CRYPTO_SYMBOLS,
    RISK_CORRELATION_LOOKBACK_DAYS,
    RISK_CORRELATION_MIN_DAYS,
    RISK_CORRELATION_REFRESH_SEC,
    RISK_CORRELATION_THRESHOLD,
    RISK_CORRELATION_WINSORIZE_PCT,
    RISK_DYNAMIC_CORRELATION_ENABLED,
    RISK_EQUITY_MARKET_TZ,
)
from app.database import get_connection
from app.services.bots.portfolio_risk import list_bot_exposures
from app.services.massive_symbols import is_crypto_terminal_symbol
from app.services.synthetic_data import YF_SYMBOL_MAP

logger = logging.getLogger(__name__)

_EQUITY_TZ = ZoneInfo(RISK_EQUITY_MARKET_TZ)


def is_crypto_symbol(symbol: str) -> bool:
    sym = str(symbol or "").upper()
    return is_crypto_terminal_symbol(sym) or sym in CRYPTO_SYMBOLS


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x < 1e-12 or den_y < 1e-12:
        return 0.0
    return round(num / (den_x * den_y), 3)


def winsorize(values: list[float], pct: float) -> list[float]:
    if not values or pct <= 0:
        return list(values)
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    lo_idx = max(0, int(math.floor(pct * n)))
    hi_idx = min(n - 1, int(math.ceil((1.0 - pct) * n)) - 1)
    lo, hi = sorted_vals[lo_idx], sorted_vals[hi_idx]
    return [min(hi, max(lo, v)) for v in values]


def _utc_day_key(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _equity_session_day_key(ts: int) -> str | None:
    dt = datetime.fromtimestamp(int(ts), tz=_EQUITY_TZ)
    if dt.weekday() >= 5:
        return None
    return dt.strftime("%Y-%m-%d")


def daily_closes_from_bars(bars: list[dict], *, crypto: bool) -> dict[str, float]:
    daily: dict[str, float] = {}
    for bar in bars:
        close = float(bar.get("close") or 0)
        if close <= 0:
            continue
        key = _utc_day_key(int(bar["time"])) if crypto else _equity_session_day_key(int(bar["time"]))
        if key is None:
            continue
        daily[key] = close
    return daily


def log_returns(closes_by_day: dict[str, float]) -> dict[str, float]:
    days = sorted(closes_by_day.keys())
    rets: dict[str, float] = {}
    for i in range(1, len(days)):
        prev_day, day = days[i - 1], days[i]
        c0 = closes_by_day[prev_day]
        c1 = closes_by_day[day]
        if c0 > 0 and c1 > 0:
            rets[day] = math.log(c1 / c0)
    return rets


def _fetch_yfinance_daily_closes(yf_symbol: str, lookback_days: int) -> dict[str, float]:
    period = f"{max(int(lookback_days) + 10, 90)}d"
    try:
        hist = yf.Ticker(yf_symbol).history(period=period, interval="1d", auto_adjust=True)
    except Exception as exc:
        logger.debug("yfinance correlation fetch failed for %s: %s", yf_symbol, exc)
        return {}
    if hist is None or hist.empty:
        return {}
    out: dict[str, float] = {}
    for idx, row in hist.iterrows():
        close = float(row["Close"])
        if close <= 0:
            continue
        ts = int(idx.to_pydatetime().replace(tzinfo=_EQUITY_TZ).timestamp())
        if yf_symbol.endswith("-USD") or yf_symbol.startswith("X:"):
            key = _utc_day_key(ts)
        else:
            key = _equity_session_day_key(ts)
            if key is None:
                continue
        out[key] = close
    return out


def fetch_daily_closes(symbol: str, lookback_days: int, *, feed=None) -> tuple[dict[str, float], str]:
    """Prefer yfinance daily adjusted closes for cross-symbol consistency."""
    crypto = is_crypto_symbol(symbol)
    yf_sym = YF_SYMBOL_MAP.get(symbol, symbol)

    daily = _fetch_yfinance_daily_closes(yf_sym, lookback_days)
    if len(daily) >= RISK_CORRELATION_MIN_DAYS:
        return daily, "yfinance"

    now = int(time.time())
    from_ts = now - int((lookback_days + 15) * 86400)

    try:
        from app.services.archive.query import query_market_history

        bars = query_market_history(symbol, from_ts, now, interval="1h")
        if len(bars) >= 10:
            daily = daily_closes_from_bars(bars, crypto=crypto)
            if len(daily) >= RISK_CORRELATION_MIN_DAYS:
                return daily, "archive"
    except Exception as exc:
        logger.debug("Archive correlation fetch failed for %s: %s", symbol, exc)

    if feed and hasattr(feed, "candles"):
        candles = getattr(feed, "candles", {}).get(symbol) or []
        if len(candles) >= 10:
            daily = daily_closes_from_bars(candles, crypto=crypto)
            if len(daily) >= RISK_CORRELATION_MIN_DAYS:
                return daily, "feed"

    return daily if daily else {}, "yfinance" if daily else "none"


def trim_to_window(returns_by_day: dict[str, float], window_days: int) -> dict[str, float]:
    if window_days <= 0:
        return returns_by_day
    days = sorted(returns_by_day.keys())
    if len(days) <= window_days:
        return returns_by_day
    keep = days[-window_days:]
    return {d: returns_by_day[d] for d in keep}


def align_return_series(
    symbol_closes: dict[str, dict[str, float]],
    *,
    min_days: int | None = None,
    window_days: int | None = None,
    winsorize_pct: float | None = None,
) -> tuple[list[str], dict[str, list[float]], list[str]]:
    """Align log-return series on common days; trim to rolling window."""
    min_days = min_days if min_days is not None else RISK_CORRELATION_MIN_DAYS
    window_days = window_days if window_days is not None else RISK_CORRELATION_LOOKBACK_DAYS
    winsorize_pct = RISK_CORRELATION_WINSORIZE_PCT if winsorize_pct is None else winsorize_pct

    symbols = sorted(symbol_closes.keys())
    if len(symbols) < 2:
        return [], {}, []

    returns_by_symbol = {
        sym: trim_to_window(log_returns(symbol_closes[sym]), window_days)
        for sym in symbols
    }

    common_days: set[str] | None = None
    for sym in symbols:
        days = set(returns_by_symbol[sym].keys())
        common_days = days if common_days is None else common_days & days

    common = sorted(common_days or [])
    if len(common) < min_days:
        return [], {}, common

    if len(common) > window_days:
        common = common[-window_days:]

    series = {sym: [returns_by_symbol[sym][day] for day in common] for sym in symbols}

    if winsorize_pct > 0:
        flat = [v for vals in series.values() for v in vals]
        if flat:
            lo_idx = max(0, int(math.floor(winsorize_pct * len(flat))))
            hi_idx = min(len(flat) - 1, int(math.ceil((1.0 - winsorize_pct) * len(flat)) - 1))
            sorted_flat = sorted(flat)
            lo, hi = sorted_flat[lo_idx], sorted_flat[hi_idx]
            series = {
                sym: [min(hi, max(lo, v)) for v in vals]
                for sym, vals in series.items()
            }

    return symbols, series, common


def build_correlation_matrix(symbols: list[str], series: dict[str, list[float]]) -> list[list[float]]:
    matrix: list[list[float]] = []
    for i, sym_a in enumerate(symbols):
        row: list[float] = []
        for j, sym_b in enumerate(symbols):
            if i == j:
                row.append(1.0)
            else:
                row.append(pearson(series[sym_a], series[sym_b]))
        matrix.append(row)
    return matrix


def pairwise_correlation_matrix(
    symbols: list[str],
    day_series: dict[str, dict[str, float]],
    *,
    min_days: int | None = None,
) -> tuple[list[list[float]], int]:
    """Pearson matrix using only overlapping days per pair (no zero-fill)."""
    min_days = min_days if min_days is not None else RISK_CORRELATION_MIN_DAYS
    min_overlap = min_days
    matrix: list[list[float]] = []
    for sym_a in symbols:
        row: list[float] = []
        for sym_b in symbols:
            if sym_a == sym_b:
                row.append(1.0)
                continue
            common = sorted(set(day_series.get(sym_a, {})) & set(day_series.get(sym_b, {})))
            if len(common) < min_overlap:
                row.append(0.0)
            else:
                xs = [day_series[sym_a][d] for d in common]
                ys = [day_series[sym_b][d] for d in common]
                row.append(pearson(xs, ys))
        matrix.append(row)
    max_overlap = 0
    for i, sym_a in enumerate(symbols):
        for sym_b in symbols[i + 1 :]:
            n = len(set(day_series.get(sym_a, {})) & set(day_series.get(sym_b, {})))
            max_overlap = max(max_overlap, n)
    return matrix, max_overlap


def _group_name(members: list[str]) -> str:
    members = sorted(members)
    if len(members) == 1:
        return f"DYN_{members[0]}"
    label = "_".join(members[:3])
    if len(members) > 3:
        label += f"_+{len(members) - 3}"
    return f"DYN_{label}"


def cluster_correlated(
    symbols: list[str],
    matrix: list[list[float]],
    threshold: float,
) -> dict[str, list[str]]:
    n = len(symbols)
    if n == 0:
        return {}
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if matrix[i][j] >= threshold:
                union(i, j)

    buckets: dict[int, list[str]] = defaultdict(list)
    for i, sym in enumerate(symbols):
        buckets[find(i)].append(sym)

    groups: dict[str, list[str]] = {}
    for members in buckets.values():
        name = _group_name(members)
        groups[name] = sorted(members)
    return groups


def collect_correlation_universe() -> list[str]:
    symbols: set[str] = set()
    for row in list_bot_exposures():
        symbols.add(str(row["symbol"]).upper())

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT DISTINCT symbol FROM bots
            WHERE status IN ('RUNNING', 'PAUSED', 'ERROR')
            """
        )
        for row in cursor.fetchall():
            sym = row["symbol"] if isinstance(row, dict) else row[0]
            if sym:
                symbols.add(str(sym).upper())
    finally:
        conn.close()

    for members in CORRELATION_GROUPS.values():
        symbols.update(members)
    return sorted(symbols)


def _split_universe(symbols: list[str]) -> tuple[list[str], list[str]]:
    equity = [s for s in symbols if not is_crypto_symbol(s)]
    crypto = [s for s in symbols if is_crypto_symbol(s)]
    return equity, crypto


@dataclass
class CorrelationSnapshot:
    symbol_to_group: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)
    symbols: list[str] = field(default_factory=list)
    matrix: list[list[float]] = field(default_factory=list)
    common_days: list[str] = field(default_factory=list)
    computed_at: float = 0.0
    source: str = "none"
    enabled: bool = False
    threshold: float = 0.7
    lookback_days: int = 60
    return_type: str = "log"
    asset_buckets: dict[str, list[str]] = field(default_factory=dict)


class DynamicCorrelationStore:
    def __init__(self, *, ttl_sec: float | None = None):
        self._ttl_sec = float(ttl_sec if ttl_sec is not None else RISK_CORRELATION_REFRESH_SEC)
        self._snapshot = CorrelationSnapshot(enabled=RISK_DYNAMIC_CORRELATION_ENABLED)

    def needs_refresh(self) -> bool:
        if not RISK_DYNAMIC_CORRELATION_ENABLED:
            return False
        if not self._snapshot.computed_at:
            return True
        return (time.time() - self._snapshot.computed_at) >= self._ttl_sec

    def lookup(self, symbol: str) -> str | None:
        if not RISK_DYNAMIC_CORRELATION_ENABLED:
            return None
        return self._snapshot.symbol_to_group.get(str(symbol or "").upper())

    def get_snapshot(self) -> CorrelationSnapshot:
        return self._snapshot

    def update(self, snapshot: CorrelationSnapshot) -> None:
        self._snapshot = snapshot


_store: DynamicCorrelationStore | None = None


def get_correlation_store() -> DynamicCorrelationStore:
    global _store
    if _store is None:
        _store = DynamicCorrelationStore()
    return _store


def static_correlation_group(symbol: str) -> str:
    sym = str(symbol or "").upper()
    for group, members in CORRELATION_GROUPS.items():
        if sym in members:
            return group
    if is_crypto_symbol(sym):
        return "CRYPTO"
    if sym in ("SPY", "QQQ"):
        return "INDEX_ETF"
    return "US_EQUITY"


def resolve_correlation_group(symbol: str) -> str:
    sym = str(symbol or "").upper()
    if RISK_DYNAMIC_CORRELATION_ENABLED:
        dynamic = get_correlation_store().lookup(sym)
        if dynamic:
            return dynamic
    return static_correlation_group(sym)


def _compute_bucket(
    symbols: list[str],
    *,
    lookback: int,
    thresh: float,
    feed=None,
) -> CorrelationSnapshot | None:
    if len(symbols) < 2:
        return None

    closes: dict[str, dict[str, float]] = {}
    source_counts: dict[str, int] = defaultdict(int)
    for sym in symbols:
        daily, src = fetch_daily_closes(sym, lookback, feed=feed)
        if len(daily) >= RISK_CORRELATION_MIN_DAYS:
            closes[sym] = daily
            source_counts[src] += 1

    aligned_symbols, series, common_days = align_return_series(closes)
    if len(aligned_symbols) < 2:
        return None

    matrix = build_correlation_matrix(aligned_symbols, series)
    groups = cluster_correlated(aligned_symbols, matrix, thresh)
    source = max(source_counts.items(), key=lambda kv: kv[1])[0] if source_counts else "none"

    return CorrelationSnapshot(
        symbol_to_group={sym: g for g, members in groups.items() for sym in members},
        groups=groups,
        symbols=aligned_symbols,
        matrix=matrix,
        common_days=common_days,
        computed_at=time.time(),
        source=source,
        enabled=True,
        threshold=thresh,
        lookback_days=lookback,
        return_type="log",
    )


def compute_rolling_correlation(
    symbols: list[str] | None = None,
    *,
    lookback_days: int | None = None,
    threshold: float | None = None,
    feed=None,
) -> CorrelationSnapshot:
    lookback = int(lookback_days if lookback_days is not None else RISK_CORRELATION_LOOKBACK_DAYS)
    thresh = float(threshold if threshold is not None else RISK_CORRELATION_THRESHOLD)
    universe = symbols or collect_correlation_universe()
    if len(universe) < 2:
        return CorrelationSnapshot(
            enabled=RISK_DYNAMIC_CORRELATION_ENABLED,
            threshold=thresh,
            lookback_days=lookback,
            computed_at=time.time(),
            source="insufficient_symbols",
            return_type="log",
        )

    equity_syms, crypto_syms = _split_universe(universe)
    buckets: dict[str, list[str]] = {}
    parts: list[CorrelationSnapshot] = []

    if len(equity_syms) >= 2:
        part = _compute_bucket(equity_syms, lookback=lookback, thresh=thresh, feed=feed)
        if part:
            parts.append(part)
            buckets["equity"] = part.symbols
    if len(crypto_syms) >= 2:
        part = _compute_bucket(crypto_syms, lookback=lookback, thresh=thresh, feed=feed)
        if part:
            parts.append(part)
            buckets["crypto"] = part.symbols

    if not parts:
        return CorrelationSnapshot(
            enabled=RISK_DYNAMIC_CORRELATION_ENABLED,
            threshold=thresh,
            lookback_days=lookback,
            computed_at=time.time(),
            source="insufficient_data",
            return_type="log",
        )

    merged_symbols: list[str] = []
    merged_matrix: list[list[float]] = []
    merged_groups: dict[str, list[str]] = {}
    merged_map: dict[str, str] = {}
    common_days: list[str] = []
    sources: set[str] = set()

    for part in parts:
        merged_groups.update(part.groups)
        merged_map.update(part.symbol_to_group)
        sources.add(part.source)
        if len(part.common_days) > len(common_days):
            common_days = part.common_days

    merged_symbols = sorted({sym for part in parts for sym in part.symbols})
    index = {sym: i for i, sym in enumerate(merged_symbols)}
    for sym_a in merged_symbols:
        row: list[float] = []
        for sym_b in merged_symbols:
            if sym_a == sym_b:
                row.append(1.0)
                continue
            same_bucket = None
            for part in parts:
                if sym_a in part.symbols and sym_b in part.symbols:
                    same_bucket = part
                    break
            if same_bucket:
                i = same_bucket.symbols.index(sym_a)
                j = same_bucket.symbols.index(sym_b)
                row.append(same_bucket.matrix[i][j])
            else:
                row.append(0.0)
        merged_matrix.append(row)

    source = "mixed" if len(sources) > 1 else next(iter(sources))

    return CorrelationSnapshot(
        symbol_to_group=merged_map,
        groups=merged_groups,
        symbols=merged_symbols,
        matrix=merged_matrix,
        common_days=common_days,
        computed_at=time.time(),
        source=source,
        enabled=True,
        threshold=thresh,
        lookback_days=lookback,
        return_type="log",
        asset_buckets=buckets,
    )


def refresh_correlation_cache(*, feed=None, force: bool = False) -> CorrelationSnapshot:
    store = get_correlation_store()
    if not RISK_DYNAMIC_CORRELATION_ENABLED:
        return store.get_snapshot()
    if not force and not store.needs_refresh():
        return store.get_snapshot()

    snapshot = compute_rolling_correlation(feed=feed)
    store.update(snapshot)
    if snapshot.groups:
        logger.info(
            "Dynamic correlation refreshed: %d group(s), %d symbol(s), source=%s, window=%dd log returns",
            len(snapshot.groups),
            len(snapshot.symbols),
            snapshot.source,
            snapshot.lookback_days,
        )
    return snapshot


def correlation_status() -> dict:
    snap = get_correlation_store().get_snapshot()
    return {
        "enabled": RISK_DYNAMIC_CORRELATION_ENABLED,
        "threshold": RISK_CORRELATION_THRESHOLD,
        "lookback_days": RISK_CORRELATION_LOOKBACK_DAYS,
        "min_days": RISK_CORRELATION_MIN_DAYS,
        "refresh_sec": RISK_CORRELATION_REFRESH_SEC,
        "return_type": snap.return_type or "log",
        "winsorize_pct": RISK_CORRELATION_WINSORIZE_PCT,
        "computed_at": snap.computed_at or None,
        "source": snap.source,
        "symbol_count": len(snap.symbols),
        "group_count": len(snap.groups),
        "groups": snap.groups,
        "asset_buckets": snap.asset_buckets,
    }


def get_price_correlation_matrix(
    *,
    symbols: list[str] | None = None,
    feed=None,
) -> dict:
    snap = refresh_correlation_cache(feed=feed)
    if symbols:
        allowed = {s.upper() for s in symbols if s}
        filtered = [s for s in snap.symbols if s in allowed]
    else:
        filtered = list(snap.symbols)

    if len(filtered) < 2:
        return {
            "symbols": filtered,
            "matrix": [],
            "period": f"{snap.lookback_days}d",
            "mode": "price",
            "return_type": "log",
            "source": snap.source,
            "groups": snap.groups,
            "dynamic_enabled": snap.enabled,
            "asset_buckets": snap.asset_buckets,
        }

    index = {sym: i for i, sym in enumerate(snap.symbols)}
    matrix = []
    for sym_a in filtered:
        i = index[sym_a]
        row = []
        for sym_b in filtered:
            j = index[sym_b]
            row.append(snap.matrix[i][j] if snap.matrix else (1.0 if i == j else 0.0))
        matrix.append(row)

    return {
        "symbols": filtered,
        "matrix": matrix,
        "period": f"{snap.lookback_days}d",
        "mode": "price",
        "return_type": "log",
        "source": snap.source,
        "groups": snap.groups,
        "dynamic_enabled": snap.enabled,
        "common_days": len(snap.common_days),
        "asset_buckets": snap.asset_buckets,
    }


def summarize_basket_correlation(
    symbols: list[str],
    *,
    feed=None,
    threshold: float | None = None,
) -> dict:
    """High-correlation pairs and shared dynamic groups for a symbol basket."""
    from app.config import RISK_CORRELATION_THRESHOLD

    thresh = threshold if threshold is not None else RISK_CORRELATION_THRESHOLD
    syms = []
    seen: set[str] = set()
    for raw in symbols:
        sym = str(raw or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        syms.append(sym)

    if len(syms) < 2:
        return {
            "symbols": syms,
            "threshold": thresh,
            "high_pairs": [],
            "shared_groups": {},
            "warning": False,
            "message": "Select at least 2 symbols for correlation analysis.",
        }

    matrix_data = get_price_correlation_matrix(symbols=syms, feed=feed)
    filtered = matrix_data.get("symbols") or syms
    matrix = matrix_data.get("matrix") or []

    high_pairs: list[dict] = []
    for i, sym_a in enumerate(filtered):
        for j in range(i + 1, len(filtered)):
            corr = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else 0.0
            if abs(corr) >= thresh:
                high_pairs.append({
                    "a": sym_a,
                    "b": filtered[j],
                    "correlation": round(float(corr), 3),
                })

    groups: dict[str, list[str]] = {}
    for sym in filtered:
        grp = resolve_correlation_group(sym)
        groups.setdefault(grp, []).append(sym)
    shared_groups = {g: members for g, members in groups.items() if len(members) > 1}

    warning = bool(high_pairs or shared_groups)
    if high_pairs:
        top = high_pairs[0]
        message = (
            f"{top['a']}/{top['b']} correlation {top['correlation']:.2f} "
            f"≥ threshold {thresh:.2f}."
        )
    elif shared_groups:
        g, members = next(iter(shared_groups.items()))
        message = f"{len(members)} symbols share correlation group '{g}'."
    else:
        message = "No high-correlation pairs in basket."

    return {
        "symbols": filtered,
        "threshold": thresh,
        "high_pairs": high_pairs,
        "shared_groups": shared_groups,
        "warning": warning,
        "message": message,
        "lookback_days": matrix_data.get("period"),
        "source": matrix_data.get("source"),
        # Full matrix for Lab heatmap (same order as symbols).
        "matrix": matrix,
        "period": matrix_data.get("period"),
        "mode": matrix_data.get("mode") or "price",
        "return_type": matrix_data.get("return_type") or "log",
        "common_days": matrix_data.get("common_days"),
    }


def _day_key_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_symbol_daily_returns_from_snapshots(
    cutoff: float,
) -> dict[str, dict[str, float]]:
    """Symbol -> day -> daily return (fraction of allocated capital)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.bot_id, s.equity, s.timestamp, b.symbol, b.allocation
        FROM bot_snapshots s
        JOIN bots b ON b.id = s.bot_id
        WHERE b.allocation > 0
        ORDER BY s.bot_id, s.timestamp ASC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    bot_day_equity: dict[str, dict[str, float]] = defaultdict(dict)
    bot_meta: dict[str, dict] = {}

    for row in rows:
        bot_id = row["bot_id"]
        ts = row.get("timestamp")
        if isinstance(ts, str):
            try:
                raw = ts.strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw.replace(" ", "T", 1))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts_f = dt.timestamp()
            except ValueError:
                continue
        else:
            ts_f = float(ts) if ts else 0.0
        if ts_f < cutoff:
            continue
        day = _day_key_from_ts(ts_f)
        bot_day_equity[bot_id][day] = float(row["equity"])
        bot_meta[bot_id] = {
            "symbol": str(row["symbol"]).upper(),
            "allocation": float(row["allocation"]),
        }

    symbol_day_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    symbol_day_cap: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for bot_id, day_equities in bot_day_equity.items():
        meta = bot_meta.get(bot_id)
        if not meta:
            continue
        symbol = meta["symbol"]
        allocation = meta["allocation"]
        days = sorted(day_equities.keys())
        prev_equity = None
        for day in days:
            equity = day_equities[day]
            if prev_equity is not None:
                daily_pnl = equity - prev_equity
                symbol_day_pnl[symbol][day] += daily_pnl
                symbol_day_cap[symbol][day] += allocation
            prev_equity = equity

    out: dict[str, dict[str, float]] = {}
    for symbol, day_pnls in symbol_day_pnl.items():
        out[symbol] = {}
        for day, pnl in day_pnls.items():
            cap = symbol_day_cap[symbol].get(day, 0.0)
            if cap > 0:
                out[symbol][day] = pnl / cap
    return out


def build_symbol_daily_returns_from_exits(
    trades: list[dict],
    *,
    account_equity: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Fallback: exit-trade PnL normalized by bot allocation or account equity."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, symbol, allocation FROM bots")
    bot_alloc = {
        row["id"]: {"symbol": str(row["symbol"]).upper(), "allocation": float(row["allocation"])}
        for row in cursor.fetchall()
    }
    conn.close()

    symbol_day_pnl: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    symbol_day_cap: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for t in trades:
        day = _day_key_from_ts(float(t["timestamp"]))
        pnl = float(t["pnl"])
        bot_id = t.get("bot_id")
        if bot_id and bot_id in bot_alloc:
            sym = bot_alloc[bot_id]["symbol"]
            cap = bot_alloc[bot_id]["allocation"]
        else:
            sym = str(t.get("symbol") or "").upper()
            cap = account_equity if account_equity > 0 else 0.0
        if not sym:
            continue
        symbol_day_pnl[sym][day] += pnl
        if cap > 0:
            symbol_day_cap[sym][day] = max(symbol_day_cap[sym].get(day, 0.0), cap)

    out: dict[str, dict[str, float]] = {}
    for symbol, day_pnls in symbol_day_pnl.items():
        out[symbol] = {}
        for day, pnl in day_pnls.items():
            cap = symbol_day_cap[symbol].get(day, 0.0)
            if cap > 0:
                out[symbol][day] = pnl / cap
    return out


def get_trade_pnl_correlation_matrix(
    trades: list[dict],
    *,
    period: str | int | None = "1M",
    symbols: list[str] | None = None,
    account_equity: float = 0.0,
) -> dict:
    from datetime import timedelta

    def _period_cutoff(period_val: str | int | None) -> float:
        if not period_val or str(period_val).upper() == "ALL":
            return 0.0
        days_map = {"1D": 1, "1W": 7, "1M": 30}
        if isinstance(period_val, str):
            days = days_map.get(period_val.upper())
            if days is None:
                try:
                    days = int(period_val)
                except ValueError:
                    return 0.0
        else:
            days = int(period_val)
        return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()

    cutoff = _period_cutoff(period)
    day_returns = build_symbol_daily_returns_from_snapshots(cutoff)

    if sum(len(v) for v in day_returns.values()) < RISK_CORRELATION_MIN_DAYS:
        day_returns = build_symbol_daily_returns_from_exits(trades, account_equity=account_equity)

    if symbols:
        allowed = {s.upper() for s in symbols if s}
        day_returns = {sym: days for sym, days in day_returns.items() if sym in allowed}

    sym_list = sorted(day_returns.keys())
    if len(sym_list) < 2:
        return {
            "symbols": sym_list,
            "matrix": [],
            "period": period or "ALL",
            "mode": "trade_pnl",
            "return_type": "daily_return_on_capital",
            "dynamic_enabled": RISK_DYNAMIC_CORRELATION_ENABLED,
            "pairwise": True,
        }

    matrix, overlap = pairwise_correlation_matrix(sym_list, day_returns)
    return {
        "symbols": sym_list,
        "matrix": matrix,
        "period": period or "ALL",
        "mode": "trade_pnl",
        "return_type": "daily_return_on_capital",
        "dynamic_enabled": RISK_DYNAMIC_CORRELATION_ENABLED,
        "common_days": overlap,
        "pairwise": True,
    }
