"""Shared early-stopping helpers for deep ML trainers."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EARLY_STOP_PATIENCE = 10


def early_stop_patience(config: dict | None, default: int = DEFAULT_EARLY_STOP_PATIENCE) -> int:
    """Max epochs without val improvement before stopping (1–100).

    Only reads ``early_stop_patience`` — do not alias bare ``patience``
    (that name collides with ReduceLROnPlateau / other scheduler knobs).
    """
    cfg = config or {}
    raw = cfg.get("early_stop_patience", default)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = default
    return max(1, min(100, n))


def mark_early_stop(
    *,
    epoch_1based: int,
    epochs_budget: int,
    patience: int,
    progress_path: str | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Log + progress-file note when early stopping fires. Returns metrics fields."""
    detail = (
        f"early stop @ {epoch_1based}/{epochs_budget} "
        f"(no val improvement for {patience} epochs)"
    )
    logger.info("%s%s", f"{strategy}: " if strategy else "", detail)
    if progress_path:
        try:
            from app.services.bots.ml_job_progress import write_ml_progress

            write_ml_progress(
                progress_path,
                pct=min(20 + int((epoch_1based / max(epochs_budget, 1)) * 70), 90),
                phase="early_stop",
                detail=detail,
            )
        except Exception:
            logger.debug("early_stop progress write failed", exc_info=True)
    return {
        "early_stopped": True,
        "epochs_trained": int(epoch_1based),
        "epochs_budget": int(epochs_budget),
        "early_stop_patience": int(patience),
        "early_stop_reason": detail,
    }
