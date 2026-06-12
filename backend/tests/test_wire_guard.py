"""Fail CI if raw WebSocket wire types appear outside the outbound catalog."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"

ALLOWED_FILES = {
    BACKEND_APP / "api" / "outbound.py",
    BACKEND_APP / "api" / "protocol.py",
}

# Match server push / reply type strings in dict literals
WIRE_TYPE_PATTERN = re.compile(
    r'["\']type["\']\s*:\s*["\'](terminal_config|market_update|orderbook_update|'
    r"account_update|trade_history|order_result|history_update|system_stats|"
    r"bots_update|bot_detail|bot_log|bot_logs_history|backtest_result|error)['\"]"
)


class TestWireProtocolGuard(unittest.TestCase):
    def test_no_raw_wire_types_outside_outbound(self):
        violations = []
        for path in BACKEND_APP.rglob("*.py"):
            if path in ALLOWED_FILES:
                continue
            if path.name == "export_api_docs.py":
                continue
            text = path.read_text(encoding="utf-8")
            for match in WIRE_TYPE_PATTERN.finditer(text):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{match.group(0)}")

        self.assertEqual(
            violations,
            [],
            "Raw wire type strings must use app.api.outbound builders:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
