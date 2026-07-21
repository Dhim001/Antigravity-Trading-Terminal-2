"""Torch device selection for ML/RL training.

Training prefers CUDA when available; live inference stays on CPU ONNX
(``CPUExecutionProvider``) so deploy remains portable without ``onnxruntime-gpu``.

Env overrides:
  ``ML_TRAIN_DEVICE=cpu|cuda|cuda:0`` — force a device
  config ``force_cpu=true`` — per-job override (validate / CI)

Keep large train/val feature tensors on CPU and move **batches** to the device
— placing full datasets on CUDA at once can stall or OOM and looks like a hang.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def resolve_torch_device(config: dict | None = None):
    """Return a ``torch.device`` for training.

    Priority: ``config.force_cpu`` → ``ML_TRAIN_DEVICE`` → CUDA if available → CPU.
    """
    import torch

    cfg = config if isinstance(config, dict) else {}
    if bool(cfg.get("force_cpu")):
        return torch.device("cpu")

    raw = (os.environ.get("ML_TRAIN_DEVICE") or cfg.get("device") or "").strip().lower()
    if raw in ("cpu", "cuda") or raw.startswith("cuda:"):
        if raw.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("ML_TRAIN_DEVICE=%s but CUDA unavailable — using CPU", raw)
            return torch.device("cpu")
        return torch.device(raw)

    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def resolve_wf_torch_device(config: dict | None = None):
    """Device for walk-forward fold training.

    Prefer CUDA when available (same as full train). Opt out with
    ``force_cpu=true`` or ``wf_use_gpu=false``.
    """
    import torch

    cfg = config if isinstance(config, dict) else {}
    if bool(cfg.get("force_cpu")) or cfg.get("wf_use_gpu") is False:
        return torch.device("cpu")
    return resolve_torch_device(cfg)


def cap_wf_epochs(epochs: int, config: dict | None, *, default: int = 12) -> int:
    """Clamp epoch count for interactive walk-forward folds."""
    cfg = config if isinstance(config, dict) else {}
    if not bool(cfg.get("_wf_mode") or cfg.get("wf_mode")):
        return max(1, int(epochs))
    try:
        limit = int(cfg.get("wf_epochs", default))
    except (TypeError, ValueError):
        limit = default
    return min(max(1, int(epochs)), max(1, limit))


def device_info(device) -> dict[str, Any]:
    """Compact train-device metadata for model ``metadata.json``."""
    import torch

    name = str(device)
    info: dict[str, Any] = {
        "device": name,
        "cuda_available": bool(torch.cuda.is_available()),
    }
    if device.type == "cuda" and torch.cuda.is_available():
        try:
            idx = int(device.index) if device.index is not None else torch.cuda.current_device()
            info["cuda_device_index"] = idx
            info["cuda_device_name"] = torch.cuda.get_device_name(idx)
        except Exception:
            pass
    return info


def to_device(obj, device):
    """Move a module or tensor to ``device`` (no-op for None)."""
    if obj is None:
        return None
    return obj.to(device)


def ensure_cuda_ready(device) -> None:
    """Prime CUDA context so the first real batch is not a silent multi-second stall."""
    if getattr(device, "type", None) != "cuda":
        return
    try:
        import torch

        torch.zeros(1, device=device)
        if hasattr(torch.cuda, "synchronize"):
            torch.cuda.synchronize(device)
    except Exception:
        logger.debug("ensure_cuda_ready failed", exc_info=True)


def module_cpu_copy(model):
    """Return an eval-mode CPU copy suitable for ONNX export (restores train device).

    Deepcopy of CUDA modules can hang; move to CPU first, copy, then restore.
    """
    import copy

    try:
        orig_device = next(model.parameters()).device
    except StopIteration:
        return copy.deepcopy(model).eval()

    model.to("cpu")
    try:
        cpu_model = copy.deepcopy(model).eval()
    finally:
        model.to(orig_device)
    return cpu_model


def suggest_batch_size(config: dict | None, default: int, *, device=None) -> int:
    """Prefer larger mini-batches on GPU unless the caller set ``batch_size``."""
    cfg = config if isinstance(config, dict) else {}
    if "batch_size" in cfg and cfg.get("batch_size") is not None:
        try:
            return max(1, int(cfg["batch_size"]))
        except (TypeError, ValueError):
            pass
    if device is not None and getattr(device, "type", None) == "cuda":
        return max(int(default), 128)
    return int(default)


def cpu_tensor(data, *, dtype):
    """Build a CPU tensor (pin only when CUDA training will pull batches)."""
    import torch

    return torch.as_tensor(data, dtype=dtype, device="cpu")
