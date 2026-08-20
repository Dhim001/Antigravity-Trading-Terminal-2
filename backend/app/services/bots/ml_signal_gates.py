"""Shared entry gates for ML / DL / RL strategies.

Ensures ML signals receive the same meta-label / calibration scrutiny as TA
when ``calibration_gate_enabled`` is on (Training & Validation Philosophy §5).
"""

from __future__ import annotations

from typing import Any


def apply_ml_meta_label_gate(
    result: dict[str, Any] | None,
    df_row: Any,
    config: dict | None,
) -> dict[str, Any]:
    """Run meta-label / Wilson gate on BUY/SELL ML entries.

    No-op when:
    - result is not an actionable entry
    - walk-forward OOS eval (``_wf_mode`` / ``skip_meta_label_gate``)
    - calibration gate disabled (same as chart agent / TA)
    """
    if not isinstance(result, dict):
        return {"signal": "NONE"}

    cfg = config if isinstance(config, dict) else {}
    signal = str(result.get("signal") or "NONE").upper()
    if signal not in ("BUY", "SELL"):
        return result
    if cfg.get("_wf_mode") or cfg.get("skip_meta_label_gate"):
        return result
    if not cfg.get("calibration_gate_enabled"):
        return result

    row = df_row if isinstance(df_row, dict) else {}
    symbol = str(
        cfg.get("model_symbol")
        or cfg.get("symbol")
        or row.get("_symbol")
        or result.get("symbol")
        or ""
    ).upper()
    timeframe = str(cfg.get("timeframe") or row.get("timeframe") or "1m")
    bot_id = cfg.get("_bot_id") or cfg.get("bot_id")

    insight = {
        "confidence": result.get("confidence"),
        "bar_time": row.get("time") or row.get("bar_time"),
        "time": row.get("time") or row.get("bar_time"),
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": signal,
        "model_type": result.get("model_type"),
        "raw_signal": result.get("raw_signal") or signal,
        "primary_side": 1.0 if signal == "BUY" else -1.0,
        "primary_confidence": result.get("confidence"),
        "features_available": 0.0,
    }
    sf = result.get("signal_features") if isinstance(result.get("signal_features"), dict) else None
    if sf is None:
        from app.services.bots.meta_label_model import COMPACT_SIGNAL_SLICE

        compact = {}
        for name in COMPACT_SIGNAL_SLICE:
            if name in row:
                compact[name] = row.get(name)
        if compact:
            sf = compact
        else:
            try:
                from app.services.bots.ml_feature_engineering import bar_to_signal_features

                hist = row.get("_lookback") if isinstance(row.get("_lookback"), list) else []
                sf = bar_to_signal_features(row, lookback_rows=hist, symbol=symbol)
            except Exception:
                sf = {}
    if sf:
        insight["signal_features"] = sf
        insight["features_available"] = 1.0

    try:
        from app.services.bots.calibration import check_meta_label_gate

        # Cross-strategy transfer (AI-FT-PTL-001 §4.4): a symbol-wide
        # REGIME_WARNING raises the entry confidence floor for every bot on
        # the symbol until the warning expires.
        gate_cfg = cfg
        try:
            from app.config import REGIME_WARNING_CONFIDENCE_BUMP
            from app.services.bots.agent_event_subscribers import regime_warning_active

            if symbol and regime_warning_active(symbol):
                gate_cfg = dict(cfg)
                cur = float(gate_cfg.get("min_confidence") or 0.55)
                gate_cfg["min_confidence"] = round(
                    min(0.95, cur + float(REGIME_WARNING_CONFIDENCE_BUMP)), 4
                )
        except Exception:
            gate_cfg = cfg

        reject = check_meta_label_gate(
            insight,
            gate_cfg,
            symbol=symbol,
            timeframe=timeframe,
            signal=signal,
            bot_id=str(bot_id) if bot_id else None,
        )
    except Exception:
        return result

    if not reject:
        # Phase 1.2: chain into the conformal prediction-set gate. Opt-in via
        # ``conformal_gate_enabled``; no-op otherwise. Kept inside the
        # calibration_gate_enabled branch so conformal can't run when the
        # operator has explicitly disabled post-hoc gating.
        try:
            from app.services.bots.conformal_gate import apply_conformal_gate
            return apply_conformal_gate(result, cfg)
        except Exception:
            return result

    out = dict(result)
    out["signal"] = "NONE"
    out["raw_signal"] = signal
    out["reject_reason"] = "meta_label_gate"
    out["reject_detail"] = str(reject)
    return out


def apply_ml_conformal_gate(
    result: dict[str, Any] | None,
    config: dict | None,
) -> dict[str, Any]:
    """Conformal prediction-set gate (Phase 1.2).

    Thin wrapper around ``conformal_gate.apply_conformal_gate`` kept here so
    ML strategies can import a single ``ml_signal_gates`` module for all
    post-hoc gates. Opt-in via ``conformal_gate_enabled``.
    """
    try:
        from app.services.bots.conformal_gate import apply_conformal_gate
    except Exception:
        return result if isinstance(result, dict) else {"signal": "NONE"}
    return apply_conformal_gate(result, config)
