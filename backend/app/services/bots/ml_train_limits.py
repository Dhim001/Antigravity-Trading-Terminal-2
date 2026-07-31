"""Apply soft RSS / address-space ceilings inside ML train workers (MEMORY #27)."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def _apply_windows_job_limit(limit_bytes: int, status: dict, limit_mb: int) -> dict:
    """Best-effort Windows Job Object per-process memory ceiling (MEMORY #41).

    Assigns the current (worker) process to a Job Object with
    ``JOB_OBJECT_LIMIT_PROCESS_MEMORY``. Any failure raises — the caller falls
    back to the advisory path. Assignment persists after the handle closes.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", ctypes.c_byte * 48),  # IO_COUNTERS — 6 × ULONGLONG
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JobObjectExtendedLimitInformation = 9

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit_bytes
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(f"SetInformationJobObject failed (err={ctypes.get_last_error()})")
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            raise OSError(f"AssignProcessToJobObject failed (err={ctypes.get_last_error()})")
    finally:
        kernel32.CloseHandle(job)

    status["ok"] = True
    status["method"] = "JobObject"
    logger.info("ML train worker Job Object process-memory limit=%d MB", limit_mb)
    return status


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

    if sys.platform == "win32":
        try:
            return _apply_windows_job_limit(limit_bytes, status, limit_mb)
        except Exception as exc:
            status["error"] = str(exc)
            logger.warning("ML train Job Object limit failed (advisory fallback): %s", exc)

    # Fallback: record limit for observability; process isolation still applies.
    status["ok"] = True
    status["method"] = "advisory"
    status["note"] = "RSS ceiling is advisory on this platform; process isolation still applies"
    logger.info("ML train worker RSS advisory limit=%d MB", limit_mb)
    return status
