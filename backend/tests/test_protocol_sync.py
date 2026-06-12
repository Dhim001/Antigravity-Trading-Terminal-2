"""Verify frontend/backend protocol constants stay aligned."""

import json
import unittest
from pathlib import Path

from app.api.protocol import Action, MessageType

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_PROTOCOL = REPO_ROOT / "frontend" / "src" / "api" / "protocol.js"


def _parse_js_freeze_object(source: str, name: str) -> dict[str, str]:
    """Extract KEY: 'value' pairs from a Object.freeze({ ... }) block."""
    start = source.index(f"export const {name} = Object.freeze({{")
    brace = source.index("{", start)
    depth = 0
    end = brace
    for i, ch in enumerate(source[brace:], start=brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = source[brace + 1 : end]
    result = {}
    for line in block.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("//"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        result[key] = val
    return result


class TestProtocolSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js_source = FRONTEND_PROTOCOL.read_text(encoding="utf-8")
        cls.js_actions = _parse_js_freeze_object(cls.js_source, "Action")
        cls.js_messages = _parse_js_freeze_object(cls.js_source, "MessageType")

    def test_frontend_protocol_file_exists(self):
        self.assertTrue(FRONTEND_PROTOCOL.is_file())

    def test_action_values_match_backend(self):
        backend = {a.name: a.value for a in Action}
        self.assertEqual(set(backend.keys()), set(self.js_actions.keys()))
        for name, value in backend.items():
            self.assertEqual(self.js_actions[name], value, f"Action.{name} mismatch")

    def test_message_type_values_match_backend(self):
        backend = {m.name: m.value for m in MessageType}
        self.assertEqual(set(backend.keys()), set(self.js_messages.keys()))
        for name, value in backend.items():
            self.assertEqual(self.js_messages[name], value, f"MessageType.{name} mismatch")


if __name__ == "__main__":
    unittest.main()
