import json
import unittest

import msgpack

from app.api.protocol import MessageType
from app.api.wire_codec import MSGPACK_MARKER, encode_wire_payload


class TestWireCodec(unittest.TestCase):
    def test_small_history_stays_json(self):
        payload = {
            "type": MessageType.HISTORY_UPDATE.value,
            "data": {"AAPL": [[1, 2, 3, 4, 5, 6]]},
        }
        wire = encode_wire_payload(payload)
        self.assertIsInstance(wire, str)
        parsed = json.loads(wire)
        self.assertEqual(parsed["type"], MessageType.HISTORY_UPDATE.value)

    def test_large_history_uses_msgpack(self):
        candles = [[i, 100.0, 101.0, 99.0, 100.5, 1000.0] for i in range(5000)]
        payload = {
            "type": MessageType.HISTORY_UPDATE.value,
            "data": {"AAPL": candles},
        }
        wire = encode_wire_payload(payload)
        self.assertIsInstance(wire, bytes)
        self.assertTrue(wire.startswith(MSGPACK_MARKER))
        decoded = msgpack.unpackb(wire[1:], raw=False)
        self.assertEqual(decoded["type"], MessageType.HISTORY_UPDATE.value)
        self.assertEqual(len(decoded["data"]["AAPL"]), 5000)

    def test_market_update_always_json(self):
        payload = {"type": MessageType.MARKET_UPDATE.value, "data": {"AAPL": {"price": 1}}}
        wire = encode_wire_payload(payload)
        self.assertIsInstance(wire, str)


if __name__ == "__main__":
    unittest.main()
