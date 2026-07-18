"""Order Flow Imbalance strategy — BAIR/MLOFI + candle proxy."""

from __future__ import annotations

import pandas as pd

from app.services.bots.indicators import prepare_strategy_df
from app.services.bots.strategies import get_strategy
from app.services.bots.strategies_microstructure import (
    OrderFlowImbalanceStrategy,
    compute_bair_mlofi,
)


def test_compute_bair_mlofi_bid_dominance():
    book = {
        "bids": [[100.0, 80.0], [99.9, 40.0], [99.8, 20.0]],
        "asks": [[100.1, 10.0], [100.2, 10.0], [100.3, 10.0]],
    }
    bair, mlofi = compute_bair_mlofi(book, levels=3)
    assert bair is not None and bair > 0.5
    assert mlofi is not None and mlofi > 0.3


def test_compute_bair_mlofi_empty():
    assert compute_bair_mlofi(None) == (None, None)
    assert compute_bair_mlofi({"bids": [], "asks": []}) == (None, None)


def test_orderflow_buy_from_orderbook():
    strat = OrderFlowImbalanceStrategy({})
    row = {
        "ATR_14": 1.0,
        "RSI_14": 50.0,
        "volume": 200.0,
        "volume_ma_20": 100.0,
        "_orderbook": {
            "bids": [[100.0, 90.0], [99.9, 50.0], [99.8, 30.0], [99.7, 20.0], [99.6, 10.0]],
            "asks": [[100.1, 10.0], [100.2, 10.0], [100.3, 10.0], [100.4, 10.0], [100.5, 10.0]],
        },
    }
    out = strat.evaluate(row)
    assert out["signal"] == "BUY"
    assert out.get("ofi_source") == "orderbook"
    assert out.get("stop_loss_distance") == 1.5


def test_orderflow_sell_from_orderbook():
    strat = OrderFlowImbalanceStrategy({})
    row = {
        "ATR_14": 1.0,
        "RSI_14": 50.0,
        "volume": 200.0,
        "volume_ma_20": 100.0,
        "_orderbook": {
            "bids": [[100.0, 10.0], [99.9, 10.0], [99.8, 10.0], [99.7, 10.0], [99.6, 10.0]],
            "asks": [[100.1, 90.0], [100.2, 50.0], [100.3, 30.0], [100.4, 20.0], [100.5, 10.0]],
        },
    }
    out = strat.evaluate(row)
    assert out["signal"] == "SELL"
    assert out.get("ofi_source") == "orderbook"


def test_orderflow_rejects_without_volume_surge():
    strat = OrderFlowImbalanceStrategy({})
    row = {
        "ATR_14": 1.0,
        "RSI_14": 50.0,
        "volume": 100.0,
        "volume_ma_20": 100.0,
        "ofi_bair_proxy": 0.9,
        "ofi_mlofi_proxy": 0.8,
    }
    out = strat.evaluate(row)
    assert out["signal"] == "NONE"
    assert "volume" in (out.get("reject_reason") or "")


def test_orderflow_candle_proxy_buy():
    strat = OrderFlowImbalanceStrategy({})
    row = {
        "ATR_14": 1.0,
        "RSI_14": 45.0,
        "volume": 200.0,
        "volume_ma_20": 100.0,
        "ofi_bair_proxy": 0.7,
        "ofi_mlofi_proxy": 0.5,
    }
    out = strat.evaluate(row)
    assert out["signal"] == "BUY"
    assert out.get("ofi_source") == "candle_proxy"


def test_prepare_strategy_df_adds_proxy_columns():
    rows = []
    price = 100.0
    for i in range(30):
        # Bullish closes near high → positive proxy
        o, h, l, c = price, price + 1, price - 0.2, price + 0.9
        rows.append({
            "time": 1_700_000_000 + i * 60,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 150.0,
        })
        price = c
    df = pd.DataFrame(rows)
    out = prepare_strategy_df(df, "ORDERFLOW_IMBALANCE", {})
    assert "ofi_bair_proxy" in out.columns
    assert "ofi_mlofi_proxy" in out.columns
    assert "volume_ma_20" in out.columns
    assert float(out["ofi_bair_proxy"].iloc[-1]) > 0.5


def test_factory_returns_orderflow_strategy():
    strat = get_strategy("ORDERFLOW_IMBALANCE", {})
    assert isinstance(strat, OrderFlowImbalanceStrategy)
