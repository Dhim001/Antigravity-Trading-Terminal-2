"""Batch market scanner — reuses FeatureBuilder + rule_engine."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.bots.candle_source import get_bot_candles
from app.services.agent.feature_builder import FeatureBuilder
from app.services.agent.rule_engine import macd_cross_label, score_dataframe
from app.services.bots.screener import MarketScreenerService

logger = logging.getLogger(__name__)


class MarketScannerService:
    def __init__(self, feed=None):
        self.feed = feed
        self.feature_builder = FeatureBuilder(MarketScreenerService())

    async def scan(
        self,
        symbols: list[str],
        *,
        signal_filter: str = "any",
        sort_by: str = "score",
    ) -> dict:
        filt = (signal_filter or "any").upper()
        if filt not in ("ANY", "BUY", "SELL", "NONE"):
            filt = "ANY"

        rows: list[dict] = []
        for sym in symbols:
            sym = sym.upper().strip()
            if not sym:
                continue
            try:
                candles = get_bot_candles(sym, self.feed)
                if not candles or len(candles) < 30:
                    continue
                df = self.feature_builder.build(sym, candles)
                if df is None or df.empty:
                    continue
                insight = score_dataframe(df, sym)
                if insight is None:
                    continue
                if filt != "ANY" and insight.signal != filt:
                    continue
                idx = len(df) - 2
                row = df.iloc[idx]
                prev = df.iloc[idx - 1] if idx > 0 else None
                rsi = row.get("RSI_14")
                rows.append({
                    "symbol": sym,
                    "signal": insight.signal,
                    "score": insight.score,
                    "confidence": insight.confidence,
                    "rsi": round(float(rsi), 2) if rsi is not None else None,
                    "macd_cross": macd_cross_label(row, prev),
                    "bar_time": insight.bar_time,
                    "insight_id": insight.insight_id,
                    "atr_regime": (insight.sub_reports or {}).get("risk", {}).get("atr_regime"),
                })
            except Exception as exc:
                logger.debug("Scan skip %s: %s", sym, exc)

        if sort_by == "score":
            rows.sort(key=lambda r: abs(r.get("score") or 0), reverse=True)
        elif sort_by == "symbol":
            rows.sort(key=lambda r: r.get("symbol") or "")

        return {
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
            "rows": rows,
        }
