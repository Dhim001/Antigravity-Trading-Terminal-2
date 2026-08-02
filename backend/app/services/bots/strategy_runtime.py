"""Shared execution kernel — parity gates, sizing, and execution-chain logging.

Used by the bar-close backtester and live BotManager so both paths share one
gate vocabulary for filters that decide whether a trade is taken or sized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.services.bots.backtest_parity import htf_bias_at_time
from app.services.bots.strategies_chart_agent import classify_filter_reject


@dataclass
class ParityBlock:
    kind: str
    reason: str
    side: str | None = None
    signal: str | None = None
    bucket: str | None = None


@dataclass
class ParityGateOutcome:
    signal: str | None
    block: ParityBlock | None = None
    signal_data: dict | None = None


def apply_indicator_parity_gates(
    signal: str | None,
    *,
    row: dict,
    bar_time,
    live_parity: bool,
    strat_key: str,
    confirm_tf: str,
    htf_bias_lookup: list[tuple[int, str]],
    strat_filter,
) -> ParityGateOutcome:
    """Mirror live HTF + filter gates for non-CHART_AGENT strategies."""
    if not live_parity or strat_key == "CHART_AGENT" or signal not in ("BUY", "SELL"):
        return ParityGateOutcome(signal=signal)

    if confirm_tf and htf_bias_lookup:
        bias = htf_bias_at_time(htf_bias_lookup, bar_time)
        if signal == "BUY" and bias == "BEAR":
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_htf",
                    reason=f"HTF bias {bias} blocks BUY",
                    side="BUY",
                    signal="BUY",
                ),
            )
        if signal == "SELL" and bias == "BULL":
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_htf",
                    reason=f"HTF bias {bias} blocks SELL",
                    side="SELL",
                    signal="SELL",
                ),
            )

    if signal and strat_filter:
        allowed, reject_reason = strat_filter.evaluate_gate(row, signal)
        if not allowed:
            return ParityGateOutcome(
                signal=None,
                block=ParityBlock(
                    kind="parity_filter",
                    reason=reject_reason or "Strategy filter blocked entry",
                    signal=signal,
                ),
            )

    return ParityGateOutcome(signal=signal)


def apply_vae_regime_meta_gate(
    signal: str | None,
    *,
    row: dict,
    symbol: str,
    bot_config: dict | None,
    lookback_rows: list[dict] | None = None,
) -> ParityGateOutcome:
    """Block BUY/SELL when VAE meta-layer reports unstable regime.

    Opt-in via `vae_regime_gate_enabled` or `filter_strategy=VAE_REGIME_DETECTOR`.
    Soft-fails open when no model is available. Skips when a VAE REGIME_GATE
    filter is already configured (avoids double-eval).
    """
    if signal not in ("BUY", "SELL"):
        return ParityGateOutcome(signal=signal)

    cfg = bot_config or {}
    from app.services.bots.strategies_vae_regime import (
        assess_vae_regime_for_meta,
        vae_regime_gate_enabled,
    )

    if not vae_regime_gate_enabled(cfg):
        return ParityGateOutcome(signal=signal)

    # Filter already applies REGIME_GATE when filter_strategy is VAE.
    filt = str(cfg.get("filter_strategy") or "").strip().upper()
    mode = str(cfg.get("filter_mode") or "").strip().upper()
    if filt == "VAE_REGIME_DETECTOR" and (not mode or mode == "REGIME_GATE"):
        return ParityGateOutcome(signal=signal)

    try:
        assessment = assess_vae_regime_for_meta(
            symbol,
            row,
            lookback_rows=lookback_rows,
            config=cfg,
        )
    except Exception:
        return ParityGateOutcome(signal=signal)

    if assessment.regime_action == "suppress":
        return ParityGateOutcome(
            signal=None,
            block=ParityBlock(
                kind="vae_regime_gate",
                reason=assessment.reason or "VAE regime gate suppressed entry",
                signal=signal,
            ),
        )
    return ParityGateOutcome(signal=signal)


def apply_shared_signal_gates(
    signal: str | None,
    signal_data: dict | None,
    *,
    bot_config: dict | None,
    recent_features: Any = None,
) -> ParityGateOutcome:
    """Shared post-strategy gates: conformal (hard) + HMM regime (soft scale).

    Applied by both live BotManager and the backtester so Phase 1.2 / 2.5
    behave identically when the corresponding config flags are on.
    """
    cfg = bot_config or {}
    data = dict(signal_data or {}) if isinstance(signal_data, dict) else {}
    sig = str(signal or data.get("signal") or "NONE").upper()
    if sig not in ("BUY", "SELL"):
        return ParityGateOutcome(signal=signal, signal_data=data)

    data["signal"] = sig

    # ── Conformal prediction-set gate (Phase 1.2) ─────────────────────────
    try:
        from app.services.bots.conformal_gate import apply_conformal_gate

        gated = apply_conformal_gate(data, cfg)
        if isinstance(gated, dict):
            data = gated
            new_sig = str(data.get("signal") or "NONE").upper()
            if new_sig not in ("BUY", "SELL"):
                return ParityGateOutcome(
                    signal=None,
                    signal_data=data,
                    block=ParityBlock(
                        kind="conformal_gate",
                        reason=str(data.get("reject_detail") or data.get("reject_reason") or "conformal rejected"),
                        signal=sig,
                    ),
                )
            sig = new_sig
    except Exception:
        pass

    # ── HMM soft regime scale (Phase 2.5) ─────────────────────────────────
    try:
        from app.services.bots.hmm_regime import apply_hmm_regime_gate

        data = apply_hmm_regime_gate(data, cfg, recent_features=recent_features)
        sig = str(data.get("signal") or sig).upper()
    except Exception:
        pass

    return ParityGateOutcome(signal=sig if sig in ("BUY", "SELL") else None, signal_data=data)


def scale_entry_quantity(
    quantity: float,
    *,
    signal_data: dict | None,
    bot_config: dict | None,
    bot_id: str | None = None,
    recent_closed_pnls: Sequence[float] | None = None,
) -> float:
    """Shared live/backtest entry sizing overlays.

    Applies (in order, each opt-in / soft-fail):
    1. Volatility / signal ``size_factor`` is assumed already baked into qty
    2. Confidence sizing (+ temperature scaling when calibration present)
    3. Fractional-Kelly overlay
    4. Meta-label probability sizing
    5. Regime drawdown halving (3 consecutive losses)
    """
    qty = float(quantity or 0.0)
    if qty <= 0:
        return qty
    cfg = bot_config if isinstance(bot_config, dict) else {}
    data = signal_data if isinstance(signal_data, dict) else {}

    # Confidence sizing (default on — matches live manager).
    if cfg.get("use_confidence_sizing", True):
        conf = float(data.get("confidence") or 0.55)
        try:
            from app.services.bots.calibration_fitter import (
                calibrate_probability,
                get_bot_calibration,
            )

            if bot_id:
                cal = get_bot_calibration(str(bot_id))
                conf = calibrate_probability(conf, cal.temperature)
        except Exception:
            pass
        conf_scale = 0.7 + (conf * 0.6)
        conf_scale = max(0.5, min(1.5, conf_scale))
        qty *= conf_scale

    # Fractional Kelly (opt-in).
    if cfg.get("use_kelly_sizing", False) and bot_id:
        try:
            from app.services.bots.calibration_fitter import (
                calibrate_probability,
                get_bot_calibration,
                kelly_size_scale,
            )

            cal = get_bot_calibration(str(bot_id))
            conf = calibrate_probability(
                float(data.get("confidence") or 0.55),
                cal.temperature,
            )
            b = (cal.avg_win / abs(cal.avg_loss)) if cal.avg_loss else 1.0
            kelly_frac = float(cfg.get("kelly_fraction") or cal.kelly_fraction)
            qty *= kelly_size_scale(
                conf,
                b,
                fraction=kelly_frac,
                min_p=float(cfg.get("kelly_min_p", 0.50)),
            )
        except Exception:
            pass

    # Meta-label probability sizing (opt-in).
    if cfg.get("use_meta_label_sizing") and bot_id:
        try:
            from app.services.bots.meta_label_model import predict_meta_label_prob

            snap = data.get("insight_snapshot") or {
                "score": data.get("score"),
                "confidence": data.get("confidence"),
                "sub_reports": data.get("sub_reports"),
            }
            prob = predict_meta_label_prob(
                str(bot_id),
                snap,
                symbol=str(cfg.get("symbol") or data.get("symbol") or ""),
                side=str(data.get("signal") or "BUY"),
                timeframe=str(cfg.get("timeframe") or "1m"),
                bar_time=data.get("time") or data.get("bar_time"),
            )
            if prob is not None:
                ml_scale = 0.7 + (0.6 * float(prob))
                ml_scale = max(0.5, min(1.5, ml_scale))
                qty *= ml_scale
        except Exception:
            pass

    # Regime-adaptive sizing — halve after 3 consecutive closed losses.
    if cfg.get("use_regime_sizing", True) and recent_closed_pnls is not None:
        pnls = [float(p) for p in recent_closed_pnls]
        if len(pnls) >= 3 and all(p < 0 for p in pnls[-3:]):
            qty *= 0.5

    return qty


def evaluate_parity_pretrade(
    *,
    side: str,
    symbol: str,
    bar_time,
    bot_config: dict | None,
    recent_exit_pnls: Sequence[float] | None = None,
    recent_exit_times: Sequence[float | int | None] | None = None,
    anomaly: dict | None = None,
    sentiment: dict | None = None,
    reduce_size_factor: float | None = None,
    setup_fail_limit: int | None = None,
    sentiment_threshold: float | None = None,
    sentiment_min_mentions: int | None = None,
    gap_veto_pct: float | None = None,
    lookback_hours: float | None = None,
) -> dict[str, Any]:
    """Deterministic PreTradeIntel subset shared by live parity / backtest.

    Covers the checks that do not need a live OMS feed or DB:
    - recent failure streak (from closed trade pnls, bar-time lookback)
    - bar anomaly / gap veto
    - sentiment divergence

    Event gates and VAE are applied elsewhere (shared already). Correlation
    exposure stays live-only (needs portfolio state).

    Pass ``recent_exit_times`` (unix sec, aligned with ``recent_exit_pnls``) and
    ``bar_time`` so streak VETO uses a real lookback window instead of latching
    forever across multi-day live_aligned runs.
    """
    from app.config import (
        PRETRADE_GAP_VETO_PCT,
        PRETRADE_REDUCE_SIZE_FACTOR,
        PRETRADE_SENTIMENT_MIN_MENTIONS,
        PRETRADE_SENTIMENT_THRESHOLD,
        PRETRADE_SETUP_FAIL_LIMIT,
    )
    from app.services.bots.pretrade_context import apply_failures_streak

    cfg = bot_config or {}
    verdict = "CONFIRM"
    vetoes: list[str] = []
    size_multiplier = 1.0
    reduce = float(
        reduce_size_factor
        if reduce_size_factor is not None
        else PRETRADE_REDUCE_SIZE_FACTOR
    )
    fail_limit = int(
        setup_fail_limit if setup_fail_limit is not None else PRETRADE_SETUP_FAIL_LIMIT
    )
    sent_thresh = float(
        sentiment_threshold
        if sentiment_threshold is not None
        else PRETRADE_SENTIMENT_THRESHOLD
    )
    sent_mentions = int(
        sentiment_min_mentions
        if sentiment_min_mentions is not None
        else PRETRADE_SENTIMENT_MIN_MENTIONS
    )
    gap_pct_limit = float(
        gap_veto_pct if gap_veto_pct is not None else PRETRADE_GAP_VETO_PCT
    )

    streak_action = None
    # Failure streak from recent closed exits (newest last), bar-time lookback.
    now_ts = None
    if bar_time is not None:
        try:
            now_ts = float(bar_time)
        except (TypeError, ValueError):
            now_ts = None
    if recent_exit_pnls is not None and fail_limit > 0:
        streak_action = apply_failures_streak(
            recent_exit_pnls,
            bot_config=cfg,
            setup_fail_limit=fail_limit,
            newest_first=False,
            lookback_hours=lookback_hours,
            exit_times=recent_exit_times,
            now_ts=now_ts,
        )
        if streak_action:
            vetoes.extend(streak_action.get("vetoes") or [])
            if streak_action["verdict"] == "VETO":
                verdict = "VETO"
                size_multiplier = 0.0
            else:
                verdict = "REDUCE_SIZE"
                size_multiplier = min(
                    size_multiplier, float(streak_action.get("size_multiplier") or reduce)
                )

    # Sentiment divergence (optional — primed cache or live store).
    # Accept both store keys (aggregate_score/mention_count) and legacy
    # aliases (score/mentions) used by older callers/tests.
    if sentiment and verdict != "VETO":
        try:
            mentions = int(
                sentiment.get("mentions")
                if sentiment.get("mentions") is not None
                else (sentiment.get("mention_count") or 0)
            )
            score = float(
                sentiment.get("score")
                if sentiment.get("score") is not None
                else (sentiment.get("aggregate_score") or 0.0)
            )
            if mentions >= sent_mentions:
                side_u = str(side or "").upper()
                if (side_u == "BUY" and score <= -sent_thresh) or (
                    side_u == "SELL" and score >= sent_thresh
                ):
                    verdict = "REDUCE_SIZE"
                    vetoes.append(f"sentiment_divergence: score={score:+.2f}")
                    size_multiplier = min(size_multiplier, reduce)
        except (TypeError, ValueError):
            pass

    # Price / volatility anomaly on the current bar.
    if anomaly and isinstance(anomaly, dict) and anomaly.get("is_anomaly"):
        kinds = anomaly.get("kinds") or []
        gap_val = anomaly.get("gap_pct")
        try:
            gap_f = float(gap_val) if gap_val is not None else None
        except (TypeError, ValueError):
            gap_f = None
        if gap_f is not None and gap_f >= gap_pct_limit:
            verdict = "VETO"
            vetoes.append(f"price_gap_anomaly: {gap_f:.2f}% gap")
        elif any(k in kinds for k in ("price_gap", "return_spike", "volume_spike")):
            verdict = "VETO"
            vetoes.append(f"market_anomaly: {', '.join(str(k) for k in kinds)}")

    if verdict == "VETO":
        size_multiplier = 0.0

    _ = symbol
    streak_veto = bool(
        streak_action
        and streak_action.get("verdict") == "VETO"
        and any(str(v).startswith("failures_streak") for v in vetoes)
    )

    return {
        "verdict": verdict,
        "vetoes": vetoes,
        "size_multiplier": size_multiplier,
        "reasoning": "; ".join(vetoes) if vetoes else "Confirmation passed.",
        "streak_action": streak_action,
        "streak_veto": streak_veto,
    }


async def maybe_apply_llm_debate(
    signal: str | None,
    signal_data: dict | None,
    *,
    bot_config: dict | None,
) -> ParityGateOutcome:
    """Live-path LLM Bull/Bear/Judge debate with deterministic firewall.

    Opt-in via ``llm_debate_enabled``. Soft-fails open when the LLM is
    unavailable (same as backtest skip) so disabling LLM does not change
    baseline behaviour.
    """
    cfg = bot_config or {}
    data = dict(signal_data or {}) if isinstance(signal_data, dict) else {}
    sig = str(signal or data.get("signal") or "NONE").upper()
    if sig not in ("BUY", "SELL") or not cfg.get("llm_debate_enabled"):
        return ParityGateOutcome(signal=signal, signal_data=data)

    try:
        from app.services.agent.debate import run_debate

        insight = data.get("insight_snapshot") or data
        if isinstance(insight, dict):
            insight = dict(insight)
            pt = data.get("pretrade_context") or data.get("trade_state")
            if isinstance(pt, dict):
                insight.setdefault("pretrade_context", pt)
        verdict = await run_debate(sig, insight if isinstance(insight, dict) else {}, config=cfg)
    except Exception:
        return ParityGateOutcome(signal=sig, signal_data=data)

    if verdict is None:
        # Pre-check skipped debate (LLM down / disabled mid-flight) — pass through.
        return ParityGateOutcome(signal=sig, signal_data=data)

    data["llm_debate"] = verdict.to_dict()
    if verdict.firewall_vetoed or str(verdict.signal).upper() == "NONE":
        data["signal"] = "NONE"
        data["raw_signal"] = sig
        data["reject_reason"] = "llm_firewall"
        data["reject_detail"] = verdict.firewall_reason or verdict.reasoning
        return ParityGateOutcome(
            signal=None,
            signal_data=data,
            block=ParityBlock(
                kind="llm_firewall",
                reason=verdict.firewall_reason or verdict.reasoning or "LLM debate veto",
                signal=sig,
            ),
        )

    # Accept judge confidence if present (never raise above prior).
    try:
        prior = float(data.get("confidence") or verdict.confidence)
        data["confidence"] = min(prior, float(verdict.confidence))
    except (TypeError, ValueError):
        pass
    data["signal"] = str(verdict.signal).upper()
    return ParityGateOutcome(signal=data["signal"], signal_data=data)


def chart_filter_reject_block(signal_data: dict | None, bar_time=None) -> ParityBlock | None:
    if not signal_data:
        return None
    reason = signal_data.get("reject_reason")
    if not reason:
        return None
    bucket = classify_filter_reject(reason)
    return ParityBlock(
        kind="filter",
        reason=str(reason)[:240],
        signal=(signal_data or {}).get("signal"),
        bucket=bucket,
    )


class ExecutionChain:
    """Event-sourced signal → intent → fill chain for parity diffing."""

    def __init__(self, bar_time) -> None:
        self._bar_time = int(bar_time) if bar_time is not None else 0
        self._events: list[dict[str, Any]] = []

    def record(self, stage: str, *, ok: bool, **detail: Any) -> None:
        payload = {k: v for k, v in detail.items() if v is not None}
        self._events.append({
            "stage": stage,
            "ok": bool(ok),
            "time": self._bar_time,
            **payload,
        })

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._events)
