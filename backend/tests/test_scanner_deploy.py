import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.bots.scanner_deploy import ScannerDeployAgent


class MockBotManager:
    def __init__(self):
        self.active_bots = {}
        self.screener = AsyncMock()
        self.backtester = AsyncMock()
        self.create_bot = AsyncMock(return_value="bot_123")
        self.oms = MagicMock()
        self.oms.feed = MagicMock()


class TestScannerDeployAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_manager = MockBotManager()

    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_ENABLED", False)
    async def test_scanner_deploy_disabled(self):
        agent = ScannerDeployAgent(self.bot_manager)
        results = await agent.evaluate()
        self.assertEqual(results["scanned"], 0)
        self.assertEqual(len(results["deployed"]), 0)

    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_ENABLED", True)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION", 500.0)
    async def test_scanner_deploy_max_allocation_blocks(self):
        self.bot_manager.active_bots = {
            "bot1": {"status": "RUNNING", "allocation": 600.0}
        }
        
        agent = ScannerDeployAgent(self.bot_manager)
        results = await agent.evaluate()
        self.assertEqual(results["scanned"], 0)

    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_ENABLED", True)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MAX_CONCURRENT_BOTS", 1)
    async def test_scanner_deploy_max_concurrent_blocks(self):
        self.bot_manager.active_bots = {
            "bot1": {"status": "RUNNING", "allocation": 100.0, "config": {"pipeline_source": "scanner"}}
        }
        
        agent = ScannerDeployAgent(self.bot_manager)
        results = await agent.evaluate()
        self.assertEqual(results["scanned"], 0)

    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_ENABLED", True)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MAX_CONCURRENT_BOTS", 5)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MAX_PORTFOLIO_ALLOCATION", 10000.0)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MIN_CONFIDENCE", 0.65)
    @patch("app.services.bots.scanner_deploy.SCANNER_DEPLOY_MIN_SCORE", 3)
    @patch("app.services.bots.scanner_deploy.summarize_basket_correlation")
    @patch("app.services.archive.resolve.resolve_backtest_candles")
    @patch("app.services.scanner.market_scanner.MarketScannerService.scan")
    async def test_scanner_deploy_success(self, mock_scan, mock_resolve, mock_corr):
        # Mock summarize_basket_correlation to return safe values
        mock_corr.return_value = {"high_pairs": []}
        mock_resolve.return_value = (
            [{"time": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(60)],
            {},
        )

        # Setup screener scan mock
        mock_scan.return_value = {
            "rows": [
                {"symbol": "BTC/USD", "signal": "BUY", "confidence": 0.8, "score": 5},
                {"symbol": "ETH/USD", "signal": "BUY", "confidence": 0.5, "score": 1}, # Skipped
            ]
        }

        # Sync BacktesterService API — AsyncMock still works via to_thread
        self.bot_manager.backtester.run_backtest = MagicMock(return_value={
            "total_pnl": 150.0,
            "win_rate": 60.0,
            "trade_count": 8,
            "summary": {"total_pnl": 150.0, "win_rate": 60.0, "total_trades": 8},
        })
        agent = ScannerDeployAgent(self.bot_manager, backtester=self.bot_manager.backtester)

        results = await agent.evaluate()
        self.assertEqual(results["scanned"], 2)
        self.assertEqual(results["candidates"], 1)
        self.assertEqual(len(results["deployed"]), 1)
        self.assertEqual(results["deployed"][0]["symbol"], "BTCUSDT")
        self.bot_manager.create_bot.assert_called_once()
        create_args = self.bot_manager.create_bot.call_args[0]
        self.assertEqual(create_args[1], "BTCUSDT")
        bt_args = self.bot_manager.backtester.run_backtest.call_args[0]
        self.assertEqual(bt_args[0], "BTCUSDT")
        self.assertEqual(len(bt_args), 4)  # symbol, strategy, config, candles
