"""Tests for EXECUTION_RISK Phase 4 (contradictory-position blocker) and
Phase 5 (drawdown budget ladder)."""

import asyncio

from app.services.bots.risk_gate import (
    RiskGate,
    check_contrary_position,
    evaluate_dd_ladder,
    get_bot_entry_hold,
    resolve_contrary_policy,
)


def _bot(bot_id="b1", symbol="AAPL", allocation=1000.0, policy=None, cfg_extra=None):
    cfg = dict(cfg_extra or {})
    if policy is not None:
        cfg["contrary_position_policy"] = policy
    return {
        "id": bot_id,
        "symbol": symbol,
        "allocation": allocation,
        "status": "RUNNING",
        "config": cfg,
        "total_pnl": 0.0,
    }


def _fleet_entry(bot_id, symbol, size, policy="block", status="RUNNING"):
    return {
        "bot_id": bot_id,
        "symbol": symbol,
        "status": status,
        "config": {"contrary_position_policy": policy},
        "position_size": size,
    }


# ── Phase 4: resolve_contrary_policy ───────────────────────────────────────


def test_policy_defaults_to_allow():
    assert resolve_contrary_policy({}) == "allow"
    assert resolve_contrary_policy(None) == "allow"


def test_policy_accepts_block_and_net():
    assert resolve_contrary_policy({"contrary_position_policy": "block"}) == "block"
    assert resolve_contrary_policy({"contrary_position_policy": "NET"}) == "net"


def test_policy_garbage_falls_back_to_allow():
    assert resolve_contrary_policy({"contrary_position_policy": "yolo"}) == "allow"


# ── Phase 4: check_contrary_position ───────────────────────────────────────


def test_entering_allow_policy_never_checks():
    bot = _bot(policy="allow")
    fleet = [_fleet_entry("b2", "AAPL", -5.0)]
    assert check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet) is None


def test_block_requires_both_bots_opted_in():
    bot = _bot(policy="block")
    # Holder still on default "allow" — must NOT block.
    fleet = [_fleet_entry("b2", "AAPL", -5.0, policy="allow")]
    assert check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet) is None


def test_block_blocks_opposite_side_entry():
    bot = _bot(policy="block")
    fleet = [_fleet_entry("b2", "AAPL", -5.0, policy="block")]
    decision = check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet)
    assert decision is not None
    assert not decision.allowed
    assert "b2" in decision.reason
    assert "blocked" in decision.reason


def test_block_ignores_same_side_holder():
    bot = _bot(policy="block")
    fleet = [_fleet_entry("b2", "AAPL", +5.0, policy="block")]  # long, same side as BUY
    assert check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet) is None


def test_block_ignores_non_running_holder():
    bot = _bot(policy="block")
    fleet = [_fleet_entry("b2", "AAPL", -5.0, policy="block", status="PAUSED")]
    assert check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet) is None


def test_block_ignores_other_symbol_and_self():
    bot = _bot(policy="block")
    fleet = [
        _fleet_entry("b2", "MSFT", -5.0, policy="block"),
        _fleet_entry("b1", "AAPL", -5.0, policy="block"),  # self excluded
    ]
    assert check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=fleet) is None


def test_block_sell_side_mirror():
    bot = _bot(policy="block")
    fleet = [_fleet_entry("b2", "AAPL", +5.0, policy="net")]  # long vs short entry
    decision = check_contrary_position(bot=bot, side="SELL", quantity=2.0, fleet=fleet)
    assert decision is not None and not decision.allowed


def test_net_reduces_quantity_by_opposing_size():
    bot = _bot(policy="net")
    fleet = [_fleet_entry("b2", "AAPL", -1.5, policy="block")]
    decision = check_contrary_position(bot=bot, side="BUY", quantity=4.0, fleet=fleet)
    assert decision is not None
    assert decision.allowed
    assert decision.quantity == 2.5


def test_net_fully_netted_blocks():
    bot = _bot(policy="net")
    fleet = [
        _fleet_entry("b2", "AAPL", -3.0, policy="net"),
        _fleet_entry("b3", "AAPL", -1.5, policy="block"),
    ]
    decision = check_contrary_position(bot=bot, side="BUY", quantity=4.0, fleet=fleet)
    assert decision is not None
    assert not decision.allowed
    assert "fully netted" in decision.reason


def test_holder_config_json_string_parsed():
    bot = _bot(policy="block")
    entry = _fleet_entry("b2", "AAPL", -5.0)
    entry["config"] = '{"contrary_position_policy": "block"}'
    decision = check_contrary_position(bot=bot, side="BUY", quantity=2.0, fleet=[entry])
    assert decision is not None and not decision.allowed


# ── Phase 5: evaluate_dd_ladder ────────────────────────────────────────────


def test_ladder_disabled_by_default():
    assert evaluate_dd_ladder(_bot()) is None
    assert evaluate_dd_ladder(_bot(cfg_extra={"dd_budget_pct": 0})) is None


def test_ladder_tier0_below_50pct():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -40.0  # budget $100 → 40% consumed
    ladder = evaluate_dd_ladder(bot)
    assert ladder["tier"] == 0
    assert ladder["consumed_pct"] == 40.0
    assert ladder["size_mult"] == 1.0
    assert not ladder["freeze_entries"] and not ladder["stop"]


def test_ladder_tier1_halves_at_50pct():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -50.0  # exactly 50% consumed
    ladder = evaluate_dd_ladder(bot)
    assert ladder["tier"] == 1
    assert ladder["size_mult"] == 0.5
    assert "halved" in ladder["reason"]


def test_ladder_tier2_freezes_and_flattens_at_80pct():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -80.0
    ladder = evaluate_dd_ladder(bot)
    assert ladder["tier"] == 2
    assert ladder["freeze_entries"] and ladder["flatten"]
    assert not ladder["stop"]
    assert "frozen" in ladder["reason"]


def test_ladder_tier3_stops_at_100pct():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -120.0  # 120% of budget
    ladder = evaluate_dd_ladder(bot)
    assert ladder["tier"] == 3
    assert ladder["stop"] and ladder["freeze_entries"]
    assert "stopping bot" in ladder["reason"]


def test_ladder_positive_pnl_tier0():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = 25.0
    ladder = evaluate_dd_ladder(bot)
    assert ladder["tier"] == 0
    assert ladder["consumed_pct"] == 0.0


def test_ladder_size_mult_config_override_and_clamp():
    bot = _bot(cfg_extra={"dd_budget_pct": 10, "dd_budget_size_mult": 0.25})
    bot["total_pnl"] = -60.0
    assert evaluate_dd_ladder(bot)["size_mult"] == 0.25
    bot2 = _bot(cfg_extra={"dd_budget_pct": 10, "dd_budget_size_mult": 7})
    bot2["total_pnl"] = -60.0
    assert evaluate_dd_ladder(bot2)["size_mult"] == 1.0


def test_ladder_zero_allocation_disabled():
    bot = _bot(allocation=0, cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -100.0
    assert evaluate_dd_ladder(bot) is None


def test_ladder_explicit_total_pnl_wins():
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = 0.0
    ladder = evaluate_dd_ladder(bot, total_pnl=-85.0)
    assert ladder["tier"] == 2


def test_entry_hold_surfaces_dd_budget_freeze(monkeypatch):
    # Streak/cooloff plumbing must not mask the budget freeze.
    monkeypatch.setattr(
        "app.services.bots.analytics.get_recent_consecutive_losses", lambda _bid: 0
    )
    monkeypatch.setattr(
        "app.services.bots.analytics.last_exit_timestamp", lambda _bid: None
    )
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -90.0
    hold = get_bot_entry_hold(bot)
    assert hold is not None
    assert hold["kind"] == "dd_budget"
    assert hold["tier"] == 2
    assert "DD budget" in hold["reason"]


def test_entry_hold_ignores_ladder_below_freeze(monkeypatch):
    monkeypatch.setattr(
        "app.services.bots.analytics.get_recent_consecutive_losses", lambda _bid: 0
    )
    monkeypatch.setattr(
        "app.services.bots.analytics.last_exit_timestamp", lambda _bid: None
    )
    bot = _bot(cfg_extra={"dd_budget_pct": 10})
    bot["total_pnl"] = -60.0  # tier 1 — no entry freeze
    assert get_bot_entry_hold(bot) is None


# ── Manager: tier-2 flatten helper ─────────────────────────────────────────


def test_flatten_bot_for_ladder_routes_exit_order():
    from app.services.bots.manager import BotManagerService

    mgr = BotManagerService.__new__(BotManagerService)
    calls = []

    async def _fake_execute(bot, side, qty, price, signal_data, **kwargs):
        calls.append({"side": side, "qty": qty, "is_exit": kwargs.get("is_exit"),
                      "reasons": signal_data.get("reasons"),
                      "config": bot.get("config")})

    mgr._execute_order = _fake_execute
    bot = _bot()
    asyncio.run(mgr._flatten_bot_for_ladder(bot, 3.0, 100.0, "ladder tier 2"))
    assert len(calls) == 1
    assert calls[0]["side"] == "SELL"  # long position → sell to flatten
    assert calls[0]["qty"] == 3.0
    assert calls[0]["is_exit"] is True
    assert "ladder tier 2" in calls[0]["reasons"][0]


def test_flatten_bot_for_ladder_short_side_and_noop():
    from app.services.bots.manager import BotManagerService

    mgr = BotManagerService.__new__(BotManagerService)
    calls = []

    async def _fake_execute(bot, side, qty, price, signal_data, **kwargs):
        calls.append(side)

    mgr._execute_order = _fake_execute
    bot = _bot()
    asyncio.run(mgr._flatten_bot_for_ladder(bot, -2.0, 100.0, "r"))
    assert calls == ["BUY"]  # short → buy to cover
    asyncio.run(mgr._flatten_bot_for_ladder(bot, 0.0, 100.0, "r"))
    assert calls == ["BUY"]  # zero size is a no-op


def test_flatten_bot_for_ladder_forces_single_shot():
    """A risk-driven flatten must not be VWAP/POV-sliced or paced."""
    from app.services.bots.manager import BotManagerService

    mgr = BotManagerService.__new__(BotManagerService)
    captured = {}

    async def _fake_execute(bot, side, qty, price, signal_data, **kwargs):
        captured["config"] = bot.get("config")

    mgr._execute_order = _fake_execute
    bot = _bot(cfg_extra={"execution_algo": "vwap", "execution_adaptive": True})
    asyncio.run(mgr._flatten_bot_for_ladder(bot, 3.0, 100.0, "r"))
    assert captured["config"]["execution_algo"] == "single"
    # Original bot config untouched (clone, not mutate).
    assert bot["config"]["execution_algo"] == "vwap"


def test_stop_bot_prunes_dd_ladder_tier_state():
    from app.services.bots.manager import BotManagerService

    mgr = BotManagerService.__new__(BotManagerService)
    mgr.active_bots = {"b1": _bot()}
    mgr._dd_ladder_tiers = {"b1": 2, "b2": 1}

    async def _noop(*a, **k):
        return None

    mgr.log_bot_event = _noop
    asyncio.run(mgr.stop_bot("b1"))
    assert "b1" not in mgr._dd_ladder_tiers
    assert mgr._dd_ladder_tiers == {"b2": 1}
    assert "b1" not in mgr.active_bots


# ── Sliced-order fallback (adopt_partial_sliced_result) ────────────────────


def test_adopt_partial_none_when_no_sliced_attempt():
    from app.services.bots.execution_algos import adopt_partial_sliced_result

    assert adopt_partial_sliced_result(None, fallback_id="x") is None


def test_adopt_partial_none_when_nothing_filled():
    from app.services.bots.execution_algos import adopt_partial_sliced_result

    sr = {"total_filled": 0.0, "order_ids": [], "avg_fill_price": 0.0}
    assert adopt_partial_sliced_result(sr, fallback_id="x") is None


def test_adopt_partial_uses_fallback_id_when_filled_without_ids():
    from app.services.bots.execution_algos import adopt_partial_sliced_result

    sr = {"total_filled": 2.5, "order_ids": [], "avg_fill_price": 101.25}
    out = adopt_partial_sliced_result(sr, fallback_id="sliced-b1:1:SELL")
    assert out["status"] == "success"
    assert out["order_id"] == "sliced-b1:1:SELL"
    assert out["average_fill_price"] == 101.25
    assert out["filled_quantity"] == 2.5


def test_adopt_partial_joins_real_broker_ids():
    from app.services.bots.execution_algos import adopt_partial_sliced_result

    sr = {"total_filled": 4.0, "order_ids": ["o1", "o2"], "avg_fill_price": 99.5}
    out = adopt_partial_sliced_result(sr, fallback_id="x")
    assert out["order_id"] == "o1,o2"
