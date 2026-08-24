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


def test_resting_sl_still_fills_at_limit_when_size_passed():
    fill = sl_tp_limit_fill_price(
        "SL",
        market_price=94.90,
        stop_loss_price=95.09,
        previous_stop_loss_price=95.09,
        size=1.0,
    )
    assert fill == 95.09


def test_invented_chandelier_sl_fills_at_last_not_new_stop():
    """Recycle / wick-arm: new trail 95.09, last 93.60, prior stop 91.57."""
    fill = sl_tp_limit_fill_price(
        "SL",
        market_price=93.60,
        stop_loss_price=95.09,
        previous_stop_loss_price=91.570955,
        size=21.4,
    )
    assert fill == 93.60


def test_invented_short_chandelier_sl_fills_at_last():
    fill = sl_tp_limit_fill_price(
        "SL",
        market_price=99.0,
        stop_loss_price=96.42,
        previous_stop_loss_price=102.0,
        size=-1.0,
    )
    assert fill == 99.0


def test_gap_through_old_and_new_sl_fills_at_resting():
    fill = sl_tp_limit_fill_price(
        "SL",
        market_price=91.00,
        stop_loss_price=95.09,
        previous_stop_loss_price=91.570955,
        size=1.0,
    )
    assert fill == 91.570955
