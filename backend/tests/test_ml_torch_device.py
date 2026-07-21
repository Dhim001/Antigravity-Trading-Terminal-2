"""Tests for ML train device selection."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_resolve_torch_device_force_cpu():
    from app.services.bots.ml_torch_device import resolve_torch_device

    device = resolve_torch_device({"force_cpu": True})
    assert str(device) == "cpu"


def test_resolve_torch_device_respects_env_cpu(monkeypatch):
    from app.services.bots.ml_torch_device import resolve_torch_device

    monkeypatch.setenv("ML_TRAIN_DEVICE", "cpu")
    device = resolve_torch_device({})
    assert str(device) == "cpu"


def test_suggest_batch_size_gpu_bumps():
    from app.services.bots.ml_torch_device import suggest_batch_size

    cuda = MagicMock()
    cuda.type = "cuda"
    assert suggest_batch_size({}, 64, device=cuda) == 128
    assert suggest_batch_size({"batch_size": 32}, 64, device=cuda) == 32


def test_device_info_cpu():
    import torch
    from app.services.bots.ml_torch_device import device_info

    info = device_info(torch.device("cpu"))
    assert info["device"] == "cpu"
    assert "cuda_available" in info


def test_resolve_wf_torch_device_opt_out():
    from app.services.bots.ml_torch_device import resolve_wf_torch_device

    device = resolve_wf_torch_device({"wf_use_gpu": False})
    assert str(device) == "cpu"
    device2 = resolve_wf_torch_device({"force_cpu": True})
    assert str(device2) == "cpu"


def test_cap_wf_epochs():
    from app.services.bots.ml_torch_device import cap_wf_epochs

    assert cap_wf_epochs(80, {"_wf_mode": True}, default=8) == 8
    assert cap_wf_epochs(80, {"_wf_mode": True, "wf_epochs": 12}, default=8) == 12
    assert cap_wf_epochs(80, {}, default=8) == 80
    assert cap_wf_epochs(3, {"_wf_mode": True}, default=8) == 3
