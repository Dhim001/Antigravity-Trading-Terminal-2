"""Tests for take-profit resolution and backtest TP exits."""

from app.services.bots.backtester import BacktesterService
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit


class _StubScreener:
    def process_candles(self, symbol, candles, config, strategy_name, full_history=False):
        import pandas as pd

        rows = []
        for i, c in enumerate(candles):
            close = float(c["close"])
            rows.append({
                "time": c.get("time", i),
                "open": close,
                "high": close * 1.001,
                "low": close * 0.999,
                "close": close,
                "volume": c.get("volume", 1000),
            })
        return pd.DataFrame(rows)


def test_resolve_take_profit_percent_mode():
    config = {"take_profit_percent": 3.0, "tp_mode": "percent"}
    pct, price = resolve_take_profit(config, {}, "BUY", 100.0)
    assert pct == 3.0
    assert price == 103.0


def test_resolve_take_profit_strategy_mode():
    config = {"tp_mode": "strategy"}
    signal = {"take_profit_price": 105.0}
    pct, price = resolve_take_profit(config, signal, "BUY", 100.0)
    assert price == 105.0
    assert pct == 5.0


def test_resolve_take_profit_strategy_overrides_when_mode_auto():
    config = {"take_profit_percent": 2.0, "tp_mode": "auto"}
    signal = {"take_profit_price": 104.0}
    _, price = resolve_take_profit(config, signal, "BUY", 100.0)
    assert price == 104.0


def test_resolve_take_profit_percent_mode_ignores_strategy():
    config = {"take_profit_percent": 2.0, "tp_mode": "percent"}
    signal = {"take_profit_price": 110.0}
    pct, price = resolve_take_profit(config, signal, "BUY", 100.0)
    assert pct == 2.0
    assert price == 102.0


def test_resolve_take_profit_none_mode():
    config = {"take_profit_percent": 3.0, "tp_mode": "none"}
    pct, price = resolve_take_profit(config, {"take_profit_price": 110.0}, "BUY", 100.0)
    assert pct is None
    assert price is None


def test_resolve_take_profit_invalid_direction():
    config = {"tp_mode": "strategy"}
    signal = {"take_profit_price": 95.0}
    pct, price = resolve_take_profit(config, signal, "BUY", 100.0)
    assert pct is None
    assert price is None


def test_merge_tp_config_applies_strategy_defaults():
    merged = merge_tp_config("BRS_SCALPING", {"bb_length": 20})
    assert merged["tp_mode"] == "strategy"
    merged_macd = merge_tp_config("MACD_RSI", {})
    assert merged_macd["take_profit_percent"] == 3.0


def test_backtest_closes_on_take_profit_percent():
    """Synthetic uptrend: fixed TP should close before trailing stop on a sharp move."""
    candles = []
    price = 100.0
    for i in range(60):
        candles.append({"time": i * 60_000, "close": price, "volume": 1000})
        if i >= 50:
            price += 0.5

    svc = BacktesterService(_StubScreener())

    class AlwaysBuy:
        def evaluate(self, row):
            if row.get("time", 0) == candles[50]["time"]:
                return {
                    "signal": "BUY",
                    "stop_loss_distance": 2.0,
                }
            return {"signal": "NONE"}

    svc.screener.process_candles = lambda *a, **k: _StubScreener().process_candles(*a, **k)
    from app.services.bots import backtester as bt_mod
    from app.services.bots import indicators as ind_mod

    orig_get = bt_mod.get_strategy
    orig_prep = ind_mod.prepare_strategy_df
    bt_mod.get_strategy = lambda name, cfg: AlwaysBuy()
    ind_mod.prepare_strategy_df = lambda df, name, cfg: df

    try:
        result = svc.run_backtest(
            "TEST",
            "MACD_RSI",
            {
                "take_profit_percent": 1.0,
                "tp_mode": "percent",
                "trailing_stop_percent": 5.0,
            },
            candles,
        )
    finally:
        bt_mod.get_strategy = orig_get
        ind_mod.prepare_strategy_df = orig_prep

    assert "error" not in result
    tp_exits = [t for t in result["trades"] if t.get("reason") == "TP"]
    assert len(tp_exits) >= 1
