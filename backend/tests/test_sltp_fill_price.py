"""SL/TP limit fill pricing — live paper OMS must match backtester."""

from app.services.bots.positions import sl_tp_limit_fill_price


def test_tp_fills_at_limit_when_market_gapped_above():
    """Reproduces SOLUSDT-style bug: TP at ~$80, market at $145 → fill at TP."""
    fill = sl_tp_limit_fill_price(
        "TP",
        market_price=145.50,
        stop_loss_price=75.0,
        take_profit_price=79.98,
    )
    assert fill == 79.98


def test_sl_fills_at_limit_when_market_gapped_below():
    fill = sl_tp_limit_fill_price(
        "SL",
        market_price=90.0,
        stop_loss_price=95.0,
        take_profit_price=110.0,
    )
    assert fill == 95.0


def test_falls_back_to_market_without_limit():
    fill = sl_tp_limit_fill_price("TP", market_price=100.0)
    assert fill == 100.0
