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
    }

    try:
        from app.services.bots.calibration import check_meta_label_gate

        reject = check_meta_label_gate(
            insight,
            cfg,
            symbol=symbol,
            timeframe=timeframe,
            signal=signal,
            bot_id=str(bot_id) if bot_id else None,
        )
    except Exception:
        return result

    if not reject:
        return result

    out = dict(result)
    out["signal"] = "NONE"
    out["raw_signal"] = signal
    out["reject_reason"] = "meta_label_gate"
    out["reject_detail"] = str(reject)
    return out
