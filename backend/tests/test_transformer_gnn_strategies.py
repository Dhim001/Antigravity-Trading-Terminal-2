"""Tests for P5 Transformer Signal + P6 GNN Cross-Asset strategies."""

import pytest
import numpy as np


def _make_candles(n=200, trend=0.1):
    candles = []
    for i in range(n):
        c = 100.0 + i * trend
        candles.append({
            "time": 1700000000 + i * 60,
            "open": c - 0.5, "high": c + 1.0, "low": c - 1.0, "close": c,
            "volume": 1000.0 + i, "ATR_14": 2.0, "RSI_14": 50.0,
            "MACDh_12_26_9": 0.1, "STOCHk_14_3_3": 50.0,
            "ADX_14": 25.0, "EMA_9": c - 0.2, "EMA_21": c - 0.5,
        })
    return candles


# ── Transformer tests ─────────────────────────────────────────────────────


class TestTransformerSequences:
    def test_builds_correct_shape(self):
        from app.services.bots.ml_transformer_trainer import build_transformer_sequences
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        candles = _make_candles(300)
        labels = label_triple_barrier(candles, max_holding_bars=30)
        X, y = build_transformer_sequences(candles, labels, lookback=60, max_holding_bars=30)
        assert X.ndim == 3
        assert X.shape[1] == 60
        assert X.shape[2] == 34
        assert set(np.unique(y)).issubset({0, 1, 2})

    def test_insufficient_candles(self):
        from app.services.bots.ml_transformer_trainer import build_transformer_sequences
        from app.services.bots.ml_triple_barrier import label_triple_barrier
        candles = _make_candles(50)
        labels = label_triple_barrier(candles, max_holding_bars=30)
        X, y = build_transformer_sequences(candles, labels, lookback=60, max_holding_bars=30)
        assert len(X) == 0


class TestTransformerStrategy:
    def test_none_without_model(self):
        from app.services.bots.strategies_transformer import TransformerSignalStrategy
        strat = TransformerSignalStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102,
               "volume": 1000, "ATR_14": 3, "RSI_14": 55, "MACDh_12_26_9": 0.5,
               "STOCHk_14_3_3": 60, "ADX_14": 28, "EMA_9": 101, "EMA_21": 100,
               "_symbol": "BTCUSDT"}
        for i in range(70):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"


class TestTransformerRegistration:
    def test_factory(self):
        from app.services.bots.strategies import get_strategy
        from app.services.bots.strategies_transformer import TransformerSignalStrategy
        strat = get_strategy("TRANSFORMER_SIGNAL", {"symbol": "BTCUSDT"})
        assert isinstance(strat, TransformerSignalStrategy)

    def test_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        ids = [s["id"] for s in list_strategy_catalog()]
        assert "TRANSFORMER_SIGNAL" in ids


# ── GNN tests ─────────────────────────────────────────────────────────────


class TestAdjacencyMatrix:
    def test_builds_from_correlations(self):
        from app.services.bots.ml_gnn_trainer import build_adjacency_from_correlations
        np.random.seed(42)
        base = np.random.randn(100)
        returns = {
            "BTC": base.tolist(),
            "ETH": (base * 0.8 + np.random.randn(100) * 0.2).tolist(),
            "XRP": np.random.randn(100).tolist(),
        }
        symbols, adj = build_adjacency_from_correlations(returns, min_corr=0.3)
        assert len(symbols) == 3
        assert adj.shape == (3, 3)
        # Diagonal should be 1 (self-loops)
        assert all(adj[i, i] == 1.0 for i in range(3))
        # BTC-ETH should be correlated
        btc_idx = symbols.index("BTC")
        eth_idx = symbols.index("ETH")
        assert adj[btc_idx, eth_idx] > 0.3

    def test_uncorrelated_no_edges(self):
        from app.services.bots.ml_gnn_trainer import build_adjacency_from_correlations
        np.random.seed(42)
        returns = {
            "A": np.random.randn(100).tolist(),
            "B": np.random.randn(100).tolist(),
        }
        symbols, adj = build_adjacency_from_correlations(returns, min_corr=0.9)
        # Off-diagonal should be 0 (uncorrelated at high threshold)
        assert adj[0, 1] == 0.0 or adj[0, 1] < 0.9


class TestGnnStrategy:
    def test_none_without_model(self):
        from app.services.bots.strategies_gnn import GnnCrossAssetStrategy
        strat = GnnCrossAssetStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102,
               "volume": 1000, "ATR_14": 3, "RSI_14": 55, "MACDh_12_26_9": 0.5,
               "STOCHk_14_3_3": 60, "ADX_14": 28, "EMA_9": 101, "EMA_21": 100,
               "_symbol": "BTCUSDT"}
        for i in range(30):
            result = strat.evaluate({**bar, "close": 100 + i * 0.1})
        assert result["signal"] == "NONE"

    def test_none_without_basket_id(self):
        from app.services.bots.strategies_gnn import GnnCrossAssetStrategy
        strat = GnnCrossAssetStrategy({"symbol": "BTCUSDT"})
        bar = {"time": 1700000000, "open": 100, "high": 105, "low": 95, "close": 102,
               "volume": 1000, "ATR_14": 3, "RSI_14": 55, "MACDh_12_26_9": 0.5,
               "STOCHk_14_3_3": 60, "ADX_14": 28, "EMA_9": 101, "EMA_21": 100,
               "_symbol": "BTCUSDT"}
        for i in range(25):
            strat.evaluate({**bar, "close": 100 + i * 0.1})
        result = strat.evaluate({**bar})
        assert result["signal"] == "NONE"


class TestGnnRegistration:
    def test_factory(self):
        from app.services.bots.strategies import get_strategy
        from app.services.bots.strategies_gnn import GnnCrossAssetStrategy
        strat = get_strategy("GNN_CROSS_ASSET", {"symbol": "BTCUSDT"})
        assert isinstance(strat, GnnCrossAssetStrategy)

    def test_catalog(self):
        from app.services.bots.strategy_catalog import list_strategy_catalog
        ids = [s["id"] for s in list_strategy_catalog()]
        assert "GNN_CROSS_ASSET" in ids
