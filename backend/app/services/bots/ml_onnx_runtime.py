"""Shared ONNX Runtime session helpers for ML inference.

Live deploy defaults to ``CPUExecutionProvider`` (portable, no GPU package).
Research / backtest auto-prefers CUDA when the CUDA EP is installed
(``BACKTEST_INFERENCE_DEVICE=auto|cuda`` or config ``backtest_use_gpu=true``);
falls back to CPU when unavailable. Live paths never request CUDA.

Thread knobs (single-run matmul parallelism):
  ``ORT_INTRA_OP_THREADS``, ``ORT_INTER_OP_THREADS``
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_LIVE_PROVIDERS = ("CPUExecutionProvider",)
_cuda_ep_cached: bool | None = None
# Set True only after this process creates a CUDA InferenceSession.
# Used by backtest ProcessPool ``auto`` to avoid spawn+CUDA hazards without
# blocking process parallelism merely because onnxruntime-gpu is installed.
_cuda_session_created: bool = False


def cuda_session_loaded_in_process() -> bool:
    """True if this process has already constructed a CUDA ORT session."""
    return bool(_cuda_session_created)


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def cuda_ep_available() -> bool:
    """True when onnxruntime lists ``CUDAExecutionProvider``."""
    global _cuda_ep_cached
    if _cuda_ep_cached is not None:
        return _cuda_ep_cached
    try:
        import onnxruntime as ort

        _cuda_ep_cached = "CUDAExecutionProvider" in set(ort.get_available_providers())
    except Exception:
        _cuda_ep_cached = False
    return _cuda_ep_cached


def backtest_wants_gpu(config: dict | None = None) -> bool:
    """True when research/backtest path should try CUDA EP.

    ``BACKTEST_INFERENCE_DEVICE``: ``auto`` (default) prefers CUDA when EP
    exists; ``cuda``/``gpu`` force try; ``cpu`` forces CPU.
    """
    cfg = config if isinstance(config, dict) else {}
    if bool(cfg.get("backtest_use_gpu")):
        return True
    if cfg.get("backtest_use_gpu") is False:
        return False
    device = (
        str(
            cfg.get("backtest_inference_device")
            or os.environ.get("BACKTEST_INFERENCE_DEVICE")
            or "auto"
        )
        .strip()
        .lower()
    )
    if device in ("cpu", "none", "off"):
        return False
    if device in ("cuda", "gpu", "cuda:0"):
        return True
    # auto / empty — prefer CUDA when the EP is actually available
    return cuda_ep_available()


def backtest_research_inference(config: dict | None = None) -> bool:
    """True when batch ONNX may use research/CUDA sessions.

    ``live_aligned`` (default) must stay on CPU sessions to match live
    ``evaluate()``. Only ``research`` / ``research_fast`` sim modes opt in.
    """
    cfg = config if isinstance(config, dict) else {}
    mode = str(cfg.get("sim_mode") or "live_aligned").strip().lower()
    return mode in ("research", "research_fast")


def ort_provider_cache_tag(*, research: bool = False, config: dict | None = None) -> str:
    """Cache-key suffix so CUDA research sessions never alias live CPU sessions.

    Live / default: ``\"\"``. Research + GPU: ``\"|cuda\"``.
    """
    if research and backtest_wants_gpu(config):
        return "|cuda"
    return ""


def resolve_ort_providers(
    *,
    research: bool = False,
    config: dict | None = None,
) -> list[str]:
    """Provider list for InferenceSession.

    Live / default: CPU only. Research + GPU want: CUDA then CPU fallback.
    """
    if not research:
        return list(_LIVE_PROVIDERS)
    if not backtest_wants_gpu(config):
        logger.info("Research ONNX inference using CPU (GPU not requested / disabled)")
        return list(_LIVE_PROVIDERS)

    try:
        import onnxruntime as ort

        available = set(ort.get_available_providers())
    except Exception:
        available = set()
    if "CUDAExecutionProvider" in available:
        global _cuda_ep_cached
        _cuda_ep_cached = True
        logger.info("Research ONNX inference using CUDAExecutionProvider")
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    _cuda_ep_cached = False
    logger.info(
        "Research ONNX wanted CUDA but CUDA EP unavailable — using CPU",
    )
    return list(_LIVE_PROVIDERS)


def make_session_options() -> Any:
    """ORT SessionOptions with optional intra/inter-op thread caps from env."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    intra = _env_int("ORT_INTRA_OP_THREADS")
    inter = _env_int("ORT_INTER_OP_THREADS")
    if intra is not None:
        opts.intra_op_num_threads = intra
    if inter is not None:
        opts.inter_op_num_threads = inter
    return opts


def create_inference_session(
    path: str,
    *,
    research: bool = False,
    config: dict | None = None,
):
    """Create an InferenceSession with shared providers + thread options."""
    global _cuda_session_created
    import onnxruntime as ort

    providers = resolve_ort_providers(research=research, config=config)
    opts = make_session_options()
    session = ort.InferenceSession(path, sess_options=opts, providers=providers)
    # Only mark after a successful construct — failed CUDA loads must not
    # permanently force ThreadPool auto-backend for this process.
    if providers and providers[0] == "CUDAExecutionProvider":
        _cuda_session_created = True
    return session
