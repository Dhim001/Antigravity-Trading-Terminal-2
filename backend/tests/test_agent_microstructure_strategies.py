"""Agent / microstructure strategies — no silent evaluate crashes."""

from __future__ import annotations

from app.services.bots.strategies import get_strategy
from app.services.bots.strategies_microstructure import (
    CvdDivergenceStrategy,
    VpocReversionStrategy,
    WyckoffStrategy,
)


def _bar(i: int, *, close: float, high: float, low: float, volume: float = 100.0, cvd: float = 0.0):
    return {
        "time": 1_700_000_000 + i * 60,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "cvd": cvd,
        "ATR_14": 1.0,
        "ADX_14": 20.0,
        "RSI_14": 50.0,
    }


def test_cvd_evaluate_does_not_crash_on_deque_window():
    strat = CvdDivergenceStrategy({})
    for i in range(12):
        out = strat.evaluate(_bar(i, close=100 + i * 0.1, high=101, low=99, cvd=i * 10.0))
        assert out.get("signal") in ("BUY", "SELL", "NONE")
        assert not str(out.get("reject_reason") or "").startswith("evaluate error")


def test_wyckoff_evaluate_does_not_crash_on_deque_slice():
    strat = WyckoffStrategy({})
    for i in range(25):
        out = strat.evaluate(_bar(i, close=100.0, high=100.5, low=99.5, volume=50))
        assert out.get("signal") in ("BUY", "SELL", "NONE")
        assert not str(out.get("reject_reason") or "").startswith("evaluate error")


def test_vpoc_uses_self_lookback():
    strat = VpocReversionStrategy({"profile_lookback": 40})
    assert strat.lookback == 40
    for i in range(25):
        # Price drifting below a prior high-volume zone
        c = 90.0 - i * 0.05
        out = strat.evaluate({
            **_bar(i, close=c, high=c + 0.2, low=c - 0.2, volume=200 if i < 10 else 40),
            "RSI_14": 30.0,
        })
        assert out.get("signal") in ("BUY", "SELL", "NONE")
        assert not str(out.get("reject_reason") or "").startswith("evaluate error")


def test_factory_agent_strategies():
    for key, cls in (
        ("CVD_DIVERGENCE", CvdDivergenceStrategy),
        ("WYCKOFF_SPRING", WyckoffStrategy),
        ("VPOC_REVERSION", VpocReversionStrategy),
    ):
        assert isinstance(get_strategy(key, {}), cls)
