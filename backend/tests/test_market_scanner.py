"""Market scanner batch service tests."""

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.agent.models import ChartAgentInsight
from app.services.scanner.market_scanner import MarketScannerService

_FAKE_CANDLES = [[i, 100.0, 101.0, 99.0, 100.5, 1000.0] for i in range(40)]
_FAKE_DF = pd.DataFrame({"RSI_14": [55.0], "close": [100.5]})


def _insight(symbol: str, signal: str) -> ChartAgentInsight:
    return ChartAgentInsight(symbol=symbol, bar_time=1_700_000_000, signal=signal, score=2, confidence=0.4)


def _score_for_symbol(_df, symbol: str) -> ChartAgentInsight:
    signals = {
        "BTCUSDT": "BUY",
        "ETHUSDT": "SELL",
        "MSFT": "NONE",
        "TSLA": "BUY",
    }
    return _insight(symbol, signals.get(str(symbol).upper(), "NONE"))


class MarketScannerFilterTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.scanner.market_scanner.score_dataframe")
    @patch("app.services.scanner.market_scanner.get_bot_candles", return_value=_FAKE_CANDLES)
    async def test_signal_filter_buy_matches_uppercase_insights(
        self, _candles, mock_score,
    ):
        mock_score.side_effect = _score_for_symbol

        scanner = MarketScannerService()
        scanner.feature_builder.build = MagicMock(return_value=_FAKE_DF)
        baseline = await scanner.scan(["BTCUSDT", "ETHUSDT", "MSFT", "TSLA"], signal_filter="any")
        self.assertEqual(baseline["count"], 4)

        buy_rows = [r for r in baseline["rows"] if r["signal"] == "BUY"]
        self.assertEqual(len(buy_rows), 2)

        filtered = await scanner.scan(
            [r["symbol"] for r in buy_rows],
            signal_filter="buy",
        )
        self.assertEqual(filtered["count"], len(buy_rows))
        self.assertTrue(all(r["signal"] == "BUY" for r in filtered["rows"]))

    @patch("app.services.scanner.market_scanner.score_dataframe")
    @patch("app.services.scanner.market_scanner.get_bot_candles", return_value=_FAKE_CANDLES)
    async def test_signal_filter_none_excludes_actionable(
        self, _candles, mock_score,
    ):
        mock_score.side_effect = [
            _insight("BTCUSDT", "NONE"),
            _insight("ETHUSDT", "BUY"),
        ]

        scanner = MarketScannerService()
        scanner.feature_builder.build = MagicMock(return_value=_FAKE_DF)
        result = await scanner.scan(["BTCUSDT", "ETHUSDT"], signal_filter="NONE")
        self.assertEqual(result["count"], 1)
        self.assertTrue(all(r["signal"] == "NONE" for r in result["rows"]))


if __name__ == "__main__":
    unittest.main()
