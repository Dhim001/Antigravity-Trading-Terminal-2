import unittest

from app.services.bots.strategy_catalog import list_strategy_catalog


class TestStrategyCatalog(unittest.TestCase):
    def test_lists_builtin_strategies(self):
        catalog = list_strategy_catalog()
        ids = {s["id"] for s in catalog if not s.get("custom")}
        self.assertIn("MACD_RSI", ids)
        self.assertIn("SUPERTREND_ADX", ids)
        for item in catalog:
            self.assertIn("name", item)
            self.assertIn("defaults", item)


if __name__ == "__main__":
    unittest.main()
