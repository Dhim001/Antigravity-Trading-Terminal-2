"""Tests for vision report SQLite persistence."""

from __future__ import annotations

import json
import unittest

from app.database import init_db
from app.db.connection import get_connection
from app.services.agent.models import VisionReport
from app.services.agent.vision_store import (
    build_rag_text,
    get_vision_exact,
    lookup_vision_near_bar,
    persist_vision_report,
    search_vision_semantic,
    vision_report_id,
)


class TestVisionStore(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vision_reports")
        conn.commit()
        conn.close()

    def test_vision_report_id(self) -> None:
        self.assertEqual(vision_report_id("aapl", "4H", 1700), "AAPL:4h:1700")

    def test_build_rag_text(self) -> None:
        report = VisionReport(
            symbol="BTCUSDT",
            timeframe="4h",
            bar_time=1700,
            structure="Ascending channel",
            patterns=["higher lows", "range top"],
            notes="No breakout yet",
        )
        text = build_rag_text(report)
        self.assertIn("Ascending channel", text)
        self.assertIn("higher lows", text)

    def test_persist_and_get_exact(self) -> None:
        report = VisionReport(
            symbol="ETHUSDT",
            timeframe="1h",
            bar_time=1800,
            structure="Double bottom forming",
            patterns=["support hold"],
        )
        persist_vision_report(report)
        hit = get_vision_exact("ETHUSDT", "1h", 1800)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["structure"], "Double bottom forming")
        self.assertIn("Double bottom", hit.get("rag_text", build_rag_text(hit)))

    def test_lookup_near_bar_within_period(self) -> None:
        report = VisionReport(
            symbol="AAPL",
            timeframe="4h",
            bar_time=10_000,
            structure="Bull flag",
        )
        persist_vision_report(report)
        near = lookup_vision_near_bar("AAPL", 10_000 + 3600)
        self.assertIsNotNone(near)
        self.assertEqual(near["structure"], "Bull flag")

    def test_search_semantic_keyword_fallback(self) -> None:
        persist_vision_report(VisionReport(
            symbol="NVDA",
            timeframe="4h",
            bar_time=100,
            structure="Breakout above resistance with volume",
        ))
        persist_vision_report(VisionReport(
            symbol="NVDA",
            timeframe="4h",
            bar_time=200,
            structure="Sideways consolidation",
        ))
        hits = search_vision_semantic("NVDA", "breakout resistance volume", limit=2)
        self.assertGreaterEqual(len(hits), 1)
        self.assertIn("Breakout", hits[0]["structure"])

    def test_sqlite_row_has_rag_text_column(self) -> None:
        report = VisionReport(symbol="MSFT", timeframe="4h", bar_time=42, structure="Test")
        persist_vision_report(report)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT rag_text, payload FROM vision_reports WHERE report_id = ?",
            (vision_report_id("MSFT", "4h", 42),),
        )
        row = cursor.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        rag = row[0] if not isinstance(row, dict) else row.get("rag_text")
        self.assertIn("Test", rag)
        payload = json.loads(row[1] if not isinstance(row, dict) else row.get("payload"))
        self.assertEqual(payload["symbol"], "MSFT")


if __name__ == "__main__":
    unittest.main()
