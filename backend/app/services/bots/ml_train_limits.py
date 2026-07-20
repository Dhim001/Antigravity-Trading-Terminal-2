"""Apply soft RSS / address-space ceilings inside ML train workers (MEMORY #27)."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def apply_ml_train_rss_limit() -> dict:
    """ProcessPoolExecutor initializer — constrain worker heap growth.

    Returns a small status dict (also useful for tests).
    """
    from app.config import ML_TRAIN_RSS_LIMIT_MB

    limit_mb = max(0, int(ML_TRAIN_RSS_LIMIT_MB or 0))
    if limit_mb <= 0:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    limit_bytes = limit_mb * 1024 * 1024
    status: dict = {"ok": False, "limit_mb": limit_mb, "platform": sys.platform}

    if sys.platform != "win32":
        try:
            import resource

            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            new_soft = limit_bytes
            new_hard = hard if hard > 0 else limit_bytes
            if hard > 0:
                new_soft = min(limit_bytes, hard)
                new_hard = hard
            resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
            status["ok"] = True
            status["method"] = "RLIMIT_AS"
            logger.info("ML train worker RLIMIT_AS soft=%d MB", limit_mb)
            return status
        except Exception as exc:
            status["error"] = str(exc)
            logger.warning("ML train RLIMIT_AS failed: %s", exc)

    # Windows / fallback: record limit for observability; hard Job Objects need
    # elevated privileges — skip silent failure and rely on process isolation.
    status["ok"] = True
    status["method"] = "advisory"
    status["note"] = "RSS ceiling is advisory on this platform; process isolation still applies"
    logger.info("ML train worker RSS advisory limit=%d MB", limit_mb)
    return status
