"""Strategy SDK v2 — full lifecycle hooks, BarWindow, and state persistence.

Users subclass StrategyV2 and implement on_bar() with access to historical
bars, persistent state, and structured signal output.  Fully backward-
compatible with existing evaluate(row, config) functions.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

from app.config import BASE_DIR
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

STATE_DIR = os.path.join(BASE_DIR, "data", "strategy_state")


# ── Data types ──────────────────────────────────────────────────────────────


@dataclass
class Bar:
    """Single OHLCV bar exposed to user strategies."""
    time: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    # Computed indicators (when available)
    rsi: float | None = None
    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    atr: float | None = None
    macd: float | None = None
    macd_signal: float | None = None

    @classmethod
    def from_row(cls, row) -> "Bar":
        """Create from a pandas Series or dict-like row."""
        return cls(
            time=int(row.get("time") or 0),
            open=float(row.get("open") or 0),
            high=float(row.get("high") or 0),
            low=float(row.get("low") or 0),
            close=float(row.get("close") or 0),
            volume=float(row.get("volume") or 0),
            rsi=_safe(row.get("RSI_14")),
            ema_9=_safe(row.get("EMA_9")),
            ema_21=_safe(row.get("EMA_21")),
            ema_50=_safe(row.get("EMA_50")),
            atr=_safe(row.get("ATR_14") or row.get("ATRr_14")),
            macd=_safe(row.get("MACD_12_26_9")),
            macd_signal=_safe(row.get("MACDs_12_26_9")),
        )


class BarWindow:
    """Lightweight lookback window — exposes last N bars as a list.

    Safe for user code: bounded, read-only copy.
    """
    _MAX_HISTORY = 250

    def __init__(self, bars: list[Bar], max_size: int = 250):
        self._bars = bars[-max_size:] if len(bars) > max_size else list(bars)

    def __len__(self) -> int:
        return len(self._bars)

    def __getitem__(self, idx: int) -> Bar:
        return self._bars[idx]

    def __iter__(self):
        return iter(self._bars)

    @property
    def latest(self) -> Bar | None:
        return self._bars[-1] if self._bars else None

    def closes(self) -> list[float]:
        return [b.close for b in self._bars]

    def highs(self) -> list[float]:
        return [b.high for b in self._bars]

    def lows(self) -> list[float]:
        return [b.low for b in self._bars]

    def volumes(self) -> list[float]:
        return [b.volume for b in self._bars]

    def sma(self, period: int) -> float | None:
        """Simple moving average of last `period` closes."""
        if len(self._bars) < period:
            return None
        vals = [b.close for b in self._bars[-period:]]
        return sum(vals) / period


@dataclass
class Signal:
    """Structured signal output from a strategy evaluation."""
    action: str = "NONE"  # BUY, SELL, CLOSE, NONE
    confidence: float = 0.5
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    stop_loss_distance: float | None = None
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    NONE: str = "NONE"
    BUY: str = "BUY"
    SELL: str = "SELL"
    CLOSE: str = "CLOSE"

    def to_dict(self) -> dict:
        d: dict = {"signal": self.action}
        if self.confidence != 0.5:
            d["confidence"] = self.confidence
        if self.stop_loss_price is not None:
            d["stop_loss_price"] = self.stop_loss_price
        if self.take_profit_price is not None:
            d["take_profit_price"] = self.take_profit_price
        if self.stop_loss_distance is not None:
            d["stop_loss_distance"] = self.stop_loss_distance
        if self.reason:
            d["reason"] = self.reason
        if self.metadata:
            d.update(self.metadata)
        return d


@dataclass
class Fill:
    """Order fill notification passed to on_fill()."""
    side: str = ""
    price: float = 0.0
    quantity: float = 0.0
    pnl: float | None = None
    is_exit: bool = False


@dataclass
class StrategyContext:
    """Context passed during lifecycle hooks."""
    symbol: str = ""
    timeframe: str = ""
    bot_id: str = ""
    allocation: float = 0.0
    config: dict = field(default_factory=dict)


# ── StrategyV2 base class ────────────────────────────────────────────────


class StrategyV2:
    """Base class for user strategies with full lifecycle.

    Override on_bar() at minimum.  State is persisted to SQLite-backed JSON.
    """

    def on_init(self, context: StrategyContext) -> None:
        """Called once at bot creation. Set up state, subscribe to indicators."""
        pass

    def on_bar(self, bar: Bar, history: BarWindow, state: dict) -> Signal:
        """Called on each new bar with lookback window access."""
        return Signal(action="NONE")

    def on_fill(self, fill: Fill, state: dict) -> None:
        """Called after an order fills. Update tracking state."""
        pass

    def on_stop(self, context: StrategyContext) -> None:
        """Called when the bot is stopped. Persist final state."""
        pass

    @staticmethod
    def schema() -> dict:
        """Return JSON Schema for config validation in the UI."""
        return {}


# ── State persistence ────────────────────────────────────────────────────


def _state_path(bot_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(bot_id))
    return os.path.join(STATE_DIR, f"{safe}.json")


def load_strategy_state(bot_id: str) -> dict:
    """Load persisted state from disk, or return empty dict."""
    path = _state_path(bot_id)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def save_strategy_state(bot_id: str, state: dict) -> None:
    """Persist strategy state to disk."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(bot_id)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
    except Exception as exc:
        logger.warning("Failed to save strategy state for %s: %s", bot_id, exc)


# ── V2 adapter for BacktesterService / BotManager ────────────────────────


class StrategyV2Adapter(BaseStrategy):
    """Wraps a StrategyV2 subclass for use with the existing bot runtime."""

    def __init__(self, config: dict, v2_class: type[StrategyV2], bot_id: str = ""):
        super().__init__(config)
        self._v2 = v2_class()
        self._bot_id = bot_id
        self._history: list[Bar] = []
        self._state = load_strategy_state(bot_id) if bot_id else {}
        self._initialized = False

    def _ensure_init(self, symbol: str = "", timeframe: str = ""):
        if not self._initialized:
            ctx = StrategyContext(
                symbol=symbol,
                timeframe=timeframe,
                bot_id=self._bot_id,
                allocation=float(self.config.get("allocation") or 10000),
                config=dict(self.config),
            )
            self._v2.on_init(ctx)
            self._initialized = True

    def evaluate(self, df_row) -> dict:
        """Called per bar by the backtester/manager."""
        self._ensure_init()
        bar = Bar.from_row(df_row)
        self._history.append(bar)
        # Cap history to prevent memory leak in long backtests
        if len(self._history) > 300:
            self._history = self._history[-250:]
        window = BarWindow(self._history)
        try:
            result = self._v2.on_bar(bar, window, self._state)
            if isinstance(result, Signal):
                return result.to_dict()
            if isinstance(result, dict):
                return result
        except Exception as exc:
            logger.error("StrategyV2 on_bar error: %s", exc)
        return {"signal": "NONE"}

    def on_fill(self, fill_data: dict) -> None:
        """Notify the strategy of a fill."""
        try:
            fill = Fill(
                side=fill_data.get("side", ""),
                price=float(fill_data.get("price") or 0),
                quantity=float(fill_data.get("quantity") or 0),
                pnl=fill_data.get("pnl"),
                is_exit=bool(fill_data.get("is_exit")),
            )
            self._v2.on_fill(fill, self._state)
        except Exception as exc:
            logger.error("StrategyV2 on_fill error: %s", exc)

    def save_state(self) -> None:
        """Persist current state to disk."""
        if self._bot_id:
            save_strategy_state(self._bot_id, self._state)


# ── Helpers ──────────────────────────────────────────────────────────────


def _safe(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None
