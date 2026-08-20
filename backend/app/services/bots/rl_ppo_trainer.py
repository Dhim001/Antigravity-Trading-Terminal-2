"""PPO (Proximal Policy Optimization) trainer for the DRL Trading Agent.

Self-contained PPO implementation using PyTorch — no external RL library needed.
Trains an Actor-Critic network on episodes from TradingEnv and exports the
policy network to ONNX for fast inference.

Dependencies (optional):
    pip install torch>=2.3.0 onnxruntime>=1.18.0 onnxscript onnx
    # Windows/CPU tip: pip install torch --index-url https://download.pytorch.org/whl/cpu
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.config import BASE_DIR
from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES, SIGNAL_FEATURE_VERSION
from app.services.bots.rl_trading_env import OBS_DIM, N_ACTIONS, TradingEnv

logger = logging.getLogger(__name__)

PPO_MODEL_DIR = os.path.join(BASE_DIR, "data", "rl_ppo_models")


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(PPO_MODEL_DIR, model_storage_key(symbol, timeframe))


def _onnx_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "ppo_policy.onnx")


def _metadata_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "metadata.json")


def _scaler_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "scaler.json")


def _checkpoint_path(symbol: str, timeframe: str | None = None) -> str:
    """Trainable actor-critic state_dict sidecar (cross-asset transfer donor)."""
    return os.path.join(_model_dir(symbol, timeframe), "policy.pt")


def _get_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is required for PPO training (pip install torch>=2.3.0)"
        ) from exc


def _export_policy_onnx(symbol: str, model, *, timeframe: str | None = None) -> str:
    """Export PPO policy to a single-file ONNX, safe for Windows re-exports."""
    torch, _nn = _get_torch()
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    model.eval()
    tf = timeframe
    return export_onnx_single_file(
        model,
        torch.randn(1, OBS_DIM),
        _onnx_path(symbol, timeframe),
        input_names=["observation"],
        output_names=["action_logits"],
        dynamic_axes={
            "observation": {0: "batch"},
            "action_logits": {0: "batch"},
        },
        opset_version=18,
        invalidate=lambda: get_ppo_store().invalidate(symbol, timeframe=tf),
    )


# ── Actor-Critic Network ─────────────────────────────────────────────────


def _build_actor_critic(obs_dim: int = OBS_DIM, act_dim: int = N_ACTIONS,
                        hidden_dim: int = 128):
    """Build the PPO Actor-Critic network."""
    torch, nn = _get_torch()

    class ActorCritic(nn.Module):
        def __init__(self):
            super().__init__()
            self.shared = nn.Sequential(
                nn.Linear(obs_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
            )
            self.actor = nn.Linear(hidden_dim, act_dim)
            self.critic = nn.Linear(hidden_dim, 1)

        def forward(self, x):
            features = self.shared(x)
            return self.actor(features)  # policy logits only (for ONNX export)

        def policy(self, x):
            features = self.shared(x)
            logits = self.actor(features)
            value = self.critic(features)
            return logits, value

        def get_action(self, obs_np: np.ndarray) -> tuple[int, float, float]:
            """Sample action from policy, return (action, log_prob, value)."""
            device = next(self.parameters()).device
            x = torch.tensor(obs_np, dtype=torch.float32, device=device).unsqueeze(0)
            with torch.no_grad():
                logits, value = self.policy(x)
                dist = torch.distributions.Categorical(logits=logits)
                action = dist.sample()
                log_prob = dist.log_prob(action)
            return int(action.item()), float(log_prob.item()), float(value.item())

    return ActorCritic()


# ── GAE (Generalized Advantage Estimation) ────────────────────────────────


def compute_gae(
    rewards: list[float],
    values: list[float],
    dones: list[bool],
    next_value: float,
    *,
    gamma: float = 0.99,
    gae_lambda: float = 0.95,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute advantages and returns using GAE.

    Returns
    -------
    advantages : np.ndarray
    returns : np.ndarray (advantages + values = returns)
    """
    n = len(rewards)
    advantages = np.zeros(n, dtype=np.float32)
    last_gae = 0.0

    for t in reversed(range(n)):
        if t == n - 1:
            next_val = next_value
        else:
            next_val = values[t + 1]

        next_non_terminal = 0.0 if dones[t] else 1.0
        delta = rewards[t] + gamma * next_val * next_non_terminal - values[t]
        advantages[t] = last_gae = delta + gamma * gae_lambda * next_non_terminal * last_gae

    returns = advantages + np.array(values, dtype=np.float32)
    return advantages, returns


# ── Rollout buffer ────────────────────────────────────────────────────────


class RolloutBuffer:
    """Stores episode data for PPO updates."""

    def __init__(self):
        self.obs: list[np.ndarray] = []
        self.actions: list[int] = []
        self.rewards: list[float] = []
        self.dones: list[bool] = []
        self.log_probs: list[float] = []
        self.values: list[float] = []

    def add(self, obs, action, reward, done, log_prob, value):
        self.obs.append(obs)
        self.actions.append(action)
        self.rewards.append(reward)
        self.dones.append(done)
        self.log_probs.append(log_prob)
        self.values.append(value)

    def __len__(self):
        return len(self.obs)

    def clear(self):
        self.obs.clear()
        self.actions.clear()
        self.rewards.clear()
        self.dones.clear()
        self.log_probs.clear()
        self.values.clear()

    def get_batches(self, batch_size: int = 64):
        """Yield minibatch indices for PPO updates."""
        n = len(self.obs)
        indices = np.random.permutation(n)
        for start in range(0, n, batch_size):
            yield indices[start:start + batch_size]


# ── PPO Training ──────────────────────────────────────────────────────────


def train_ppo_agent(
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    total_timesteps: int = 200_000,
) -> dict[str, Any]:
    """Train a PPO agent on a simulated trading environment.

    Parameters
    ----------
    symbol : str
        Trading symbol.
    candles : list[dict]
        OHLCV bars with indicators. Sorted oldest-first.
    config : dict, optional
        Strategy config overrides.
    total_timesteps : int
        Total environment steps to train for.

    Returns
    -------
    dict with ``ok``, ``metrics``, etc.
    """
    torch, nn = _get_torch()

    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("RL_PPO_AGENT", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_training_window import apply_champion_train_overrides

    from app.services.bots.rl_risk import (
        MIN_ENT_COEF,
        PREFERRED_TRAIN_TIMEFRAME,
        clamp_ent_coef,
        resolve_rl_costs,
    )

    raw_tf = cfg.get("timeframe") or raw_cfg.get("timeframe")
    tf = normalize_model_timeframe(raw_tf or PREFERRED_TRAIN_TIMEFRAME)
    cfg["timeframe"] = tf
    fee_bps, slip_bps = resolve_rl_costs(cfg)
    cfg["fee_bps"] = fee_bps
    cfg["slippage_bps"] = slip_bps
    cfg = apply_champion_train_overrides(cfg, raw_cfg)
    wf_mode = bool(cfg.get("_wf_mode") or cfg.get("wf_mode"))
    if cfg.get("champion_train"):
        wf_mode = False

    # ── Cross-asset donor warm-start ──────────────────────────────
    # WF/PBO folds always train from scratch (honest OOS); the donor path
    # only applies to live/champion trains. The transferred model still
    # registers as a challenger and must pass the usual gates.
    donor_cfg = cfg.get("donor") if isinstance(cfg.get("donor"), dict) else None
    donor_info: dict[str, Any] | None = None
    donor_state: dict[str, Any] | None = None
    scaler_strategy = "recompute"
    if donor_cfg and donor_cfg.get("symbol") and not wf_mode:
        from app.services.bots import model_transfer as _mt

        if _mt.transfer_enabled():
            donor = _mt.resolve_donor(
                "RL_PPO_AGENT", str(donor_cfg["symbol"]), tf,
                donor_cfg.get("version_id"),
            )
            compat = (
                _mt.check_compatibility(donor["metadata"], "RL_PPO_AGENT", tf)
                if donor else ["donor not found"]
            )
            ckpt = _mt.donor_checkpoint_path(donor, "RL_PPO_AGENT") if donor else None
            if donor and not compat and ckpt:
                try:
                    donor_state = torch.load(ckpt, map_location="cpu")
                    donor_info = donor
                    scaler_strategy = str(donor_cfg.get("scaler_strategy") or "recompute")
                    if scaler_strategy not in _mt.SCALER_STRATEGIES:
                        scaler_strategy = "recompute"
                except Exception:
                    logger.warning(
                        "PPO donor checkpoint load failed for %s",
                        donor_cfg.get("symbol"), exc_info=True,
                    )
                    donor_state = None
            else:
                logger.warning(
                    "PPO donor %s unusable (compat=%s, checkpoint=%s) — training from scratch",
                    donor_cfg.get("symbol"), compat or ["no checkpoint"], bool(ckpt),
                )

    # Interactive WF/PBO calls trainer without total_timesteps — lean only
    # when capacity parity is off. Otherwise keep the function-arg budget
    # (Lab Train default 200k) or an explicit config override above.
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    if cfg.get("total_timesteps") is not None:
        total_timesteps = int(cfg["total_timesteps"])
    elif wf_mode and not wf_parity:
        total_timesteps = 2048

    # Episode horizon: without a cap, one episode walks the full candle series
    # (Apply & Retrain ≈ 50k bars) and Optuna budgets (8k–65k) finish at ep=0.
    try:
        max_ep = int(cfg.get("max_episode_steps") or 0)
    except (TypeError, ValueError):
        max_ep = 0
    if max_ep <= 0:
        max_ep = 2048
    cfg["max_episode_steps"] = max_ep
    # Guarantee the step budget can finish several full episodes.
    min_eps = 20 if cfg.get("champion_train") else 5
    min_steps = max_ep * min_eps
    if total_timesteps < min_steps:
        total_timesteps = min_steps

    # Donor fine-tune runs a reduced budget — the policy is already trained.
    if donor_state is not None:
        from app.config import RL_TRANSFER_TIMESTEPS_FRACTION

        frac = float(cfg.get("transfer_timesteps_fraction") or RL_TRANSFER_TIMESTEPS_FRACTION)
        frac = min(1.0, max(0.05, frac))
        total_timesteps = max(min_steps, int(total_timesteps * frac))

    gamma = float(cfg.get("gamma", 0.99))
    gae_lambda = float(cfg.get("gae_lambda", 0.95))
    clip_epsilon = float(cfg.get("clip_epsilon", 0.2))
    ppo_epochs = int(cfg.get("ppo_epochs", 2 if (wf_mode and not wf_parity) else 10))
    n_steps = int(cfg.get("n_steps", 512 if (wf_mode and not wf_parity) else 2048))
    # Fast WF clamps steps so Lab Validate finishes; capacity parity keeps
    # production-scale rollouts for accurate OOS returns.
    if wf_mode and not wf_parity:
        ppo_epochs = min(ppo_epochs, 4)
        n_steps = min(n_steps, 1024)
        total_timesteps = min(total_timesteps, max(n_steps, 8192))
    hidden_dim = int(cfg.get(
        "hidden_dim",
        64 if (wf_mode and not wf_parity) else 256,
    ))
    lr = float(cfg.get("learning_rate", 3e-4))
    if donor_state is not None:
        from app.config import RL_TRANSFER_LR_FACTOR

        lr_factor = float(cfg.get("transfer_lr_factor") or RL_TRANSFER_LR_FACTOR)
        lr *= min(1.0, max(0.01, lr_factor))
    vf_coef = float(cfg.get("vf_coef", 0.5))
    ent_coef = clamp_ent_coef(cfg.get("ent_coef", MIN_ENT_COEF))
    cfg["ent_coef"] = ent_coef
    max_grad_norm = float(cfg.get("max_grad_norm", 0.5))
    from app.services.bots.ml_torch_device import (
        device_info,
        ensure_cuda_ready,
        resolve_torch_device,
        resolve_wf_torch_device,
        suggest_batch_size,
    )

    device = resolve_wf_torch_device(cfg) if wf_mode else resolve_torch_device(cfg)
    batch_size = suggest_batch_size(
        cfg, 128 if getattr(device, "type", None) == "cuda" else 64, device=device,
    )
    ensure_cuda_ready(device)

    min_candles = 200
    if len(candles) < min_candles:
        return {
            "ok": False,
            "error": f"insufficient candles ({len(candles)} < {min_candles})",
            "symbol": symbol,
        }

    # Create environment (numpy / CPU) — heartbeat so large feature builds
    # don't sit at a frozen 5% while the pool worker is alive.
    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        progress_path_from_config as _ppfc,
    )

    cfg.setdefault("symbol", symbol)
    _ppo_progress_path = _ppfc(cfg)
    try:
        env = TradingEnv(candles, config=cfg, progress_path=_ppo_progress_path)
    except InterruptedError:
        return cancelled_train_result(symbol, "RL_PPO_AGENT")

    # Scaler strategy: default recomputes feat_mean/std from target candles
    # (already done by the env); "carry" adopts the donor's scaler verbatim.
    if donor_info is not None and scaler_strategy == "carry":
        from app.services.bots import model_transfer as _mt

        d_scaler = _mt.donor_scaler(donor_info)
        if d_scaler and d_scaler.get("feat_mean") and d_scaler.get("feat_std"):
            env.set_feature_scaler(d_scaler["feat_mean"], d_scaler["feat_std"])
            logger.info("PPO donor scaler carried from %s", donor_info.get("symbol"))

    # Build model on train device
    model = _build_actor_critic(
        obs_dim=OBS_DIM, act_dim=N_ACTIONS, hidden_dim=hidden_dim,
    ).to(device)
    if donor_state is not None:
        try:
            model.load_state_dict(donor_state)
            model.to(device)
            logger.info(
                "PPO warm-start: loaded donor %s weights (scaler=%s)",
                donor_info.get("symbol"), scaler_strategy,
            )
        except Exception:
            logger.warning(
                "PPO donor weight load failed for %s (shape mismatch?) — training from scratch",
                donor_info.get("symbol") if donor_info else "?", exc_info=True,
            )
            donor_state = None
            donor_info = None
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, eps=1e-5)

    # Training loop
    buffer = RolloutBuffer()
    obs = env.reset()
    total_steps = 0
    episode_count = 0
    episode_returns: list[float] = []
    episode_trades: list[int] = []

    best_mean_return = -float("inf")

    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    progress_path = progress_path_from_config(cfg)
    _last_progress_t = 0.0

    # Mark step-loop entry so the UI shows training actually began (a job that
    # never leaves phase "train"/"env" at 5% is stuck in setup, not training).
    if progress_path:
        write_ml_progress(
            progress_path,
            pct=5,
            phase="ppo",
            detail=f"step 0/{total_timesteps} · ep 0",
        )

    while total_steps < total_timesteps:
        if ml_cancel_requested(progress_path):
            return {
                "ok": False,
                "cancelled": True,
                "error": "cancelled",
                "symbol": symbol,
                "strategy": "RL_PPO_AGENT",
            }

        now = time.time()
        if now - _last_progress_t >= 2.0 or total_steps == 0:
            _last_progress_t = now
            pct = int(min(95, 5 + (total_steps / max(1, total_timesteps)) * 90))
            write_ml_progress(
                progress_path,
                pct=pct,
                phase="ppo",
                detail=f"step {total_steps}/{total_timesteps} · ep {episode_count}",
            )

        # ── Collect rollout ───────────────────────────────────────
        buffer.clear()
        model.eval()

        for _ in range(n_steps):
            action, log_prob, value = model.get_action(obs)
            next_obs, reward, done, info = env.step(action)

            buffer.add(obs, action, reward, done, log_prob, value)
            obs = next_obs
            total_steps += 1

            if done:
                stats = env.episode_stats()
                episode_returns.append(stats["return_pct"])
                episode_trades.append(stats["total_trades"])
                episode_count += 1
                obs = env.reset()

            if total_steps >= total_timesteps:
                break

        if len(buffer) == 0:
            break

        # ── Compute advantages ────────────────────────────────────
        with torch.no_grad():
            _, next_value = model.policy(
                torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
            )
            next_value = float(next_value.item())

        advantages, returns = compute_gae(
            buffer.rewards, buffer.values, buffer.dones,
            next_value, gamma=gamma, gae_lambda=gae_lambda,
        )

        # Normalize advantages
        adv_mean = advantages.mean()
        adv_std = advantages.std()
        if adv_std > 1e-8:
            advantages = (advantages - adv_mean) / adv_std

        # Convert to tensors on train device
        obs_t = torch.tensor(np.stack(buffer.obs), dtype=torch.float32, device=device)
        actions_t = torch.tensor(buffer.actions, dtype=torch.long, device=device)
        old_log_probs_t = torch.tensor(buffer.log_probs, dtype=torch.float32, device=device)
        advantages_t = torch.tensor(advantages, dtype=torch.float32, device=device)
        returns_t = torch.tensor(returns, dtype=torch.float32, device=device)

        # ── PPO update ────────────────────────────────────────────
        model.train()
        for _ in range(ppo_epochs):
            for batch_idx in buffer.get_batches(batch_size):
                b_obs = obs_t[batch_idx]
                b_actions = actions_t[batch_idx]
                b_old_lp = old_log_probs_t[batch_idx]
                b_adv = advantages_t[batch_idx]
                b_ret = returns_t[batch_idx]

                logits, values = model.policy(b_obs)
                dist = torch.distributions.Categorical(logits=logits)
                new_log_probs = dist.log_prob(b_actions)
                entropy = dist.entropy().mean()

                # Policy loss (clipped surrogate)
                ratio = torch.exp(new_log_probs - b_old_lp)
                surr1 = ratio * b_adv
                surr2 = torch.clamp(ratio, 1.0 - clip_epsilon, 1.0 + clip_epsilon) * b_adv
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value loss
                value_loss = nn.functional.mse_loss(values.squeeze(-1), b_ret)

                # Total loss
                loss = policy_loss + vf_coef * value_loss - ent_coef * entropy

                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
                optimizer.step()

        # Track progress
        if episode_returns:
            recent = episode_returns[-10:]
            mean_ret = sum(recent) / len(recent)
            if mean_ret > best_mean_return:
                best_mean_return = mean_ret

    # ── Transfer KL guard ─────────────────────────────────────────
    # KL(donor ‖ fine-tuned) on target states from the final rollout; when
    # the fine-tune moved the policy too far, restore the donor weights and
    # flag the result — the deploy gates arbitrate from there.
    transfer_meta: dict[str, Any] | None = None
    if donor_state is not None and donor_info is not None:
        from app.config import RL_TRANSFER_MAX_KL

        kl_val: float | None = None
        transfer_rejected = False
        try:
            if len(buffer) > 0:
                val_obs = torch.tensor(
                    np.stack(buffer.obs[:1024]), dtype=torch.float32, device=device,
                )
                donor_model = _build_actor_critic(
                    obs_dim=OBS_DIM, act_dim=N_ACTIONS, hidden_dim=hidden_dim,
                ).to(device)
                donor_model.load_state_dict(donor_state)
                donor_model.eval()
                model.eval()
                with torch.no_grad():
                    old_logits, _ = donor_model.policy(val_obs)
                    old_dist = torch.distributions.Categorical(logits=old_logits)
                    new_logits, _ = model.policy(val_obs)
                    new_dist = torch.distributions.Categorical(logits=new_logits)
                    kl_val = float(
                        torch.distributions.kl_divergence(old_dist, new_dist).mean().item()
                    )
                if kl_val > RL_TRANSFER_MAX_KL:
                    model.load_state_dict(donor_state)
                    model.to(device)
                    transfer_rejected = True
                    logger.warning(
                        "PPO transfer REJECTED for %s: KL=%.4f > %.4f — donor weights restored",
                        symbol, kl_val, RL_TRANSFER_MAX_KL,
                    )
        except Exception:
            logger.debug("PPO transfer KL guard skipped for %s", symbol, exc_info=True)

        jumpstart = None
        if episode_returns:
            head = episode_returns[:3]
            jumpstart = round(sum(head) / len(head), 4)
        breakeven = None
        for i, r in enumerate(episode_returns):
            if r > 0:
                breakeven = i + 1
                break
        transfer_meta = {
            "donor_symbol": donor_info.get("symbol"),
            "donor_version_id": donor_info.get("version_id"),
            "scaler_strategy": scaler_strategy,
            "kl_divergence": round(kl_val, 6) if kl_val is not None else None,
            "transfer_rejected": transfer_rejected,
            "jumpstart_return": jumpstart,
            "episodes_to_breakeven": breakeven,
        }

    train_device_meta = device_info(device)

    # Continual fine-tuning (AI-FT-PTL-001 §3.2): after the candle-episode
    # training, apply a KL-constrained update from the live replay buffer.
    # Skip for WF/PBO folds — replay adapts the live champion only.
    replay_meta: dict[str, Any] = {"applied": False}
    if not wf_mode:
        try:
            replay_meta = finetune_from_replay(
                model,
                optimizer,
                symbol,
                device=device,
                clip_epsilon=clip_epsilon,
                vf_coef=vf_coef,
                ent_coef=ent_coef,
                max_grad_norm=max_grad_norm,
                batch_size=batch_size,
            )
        except Exception:
            logger.debug("replay fine-tune skipped for %s", symbol, exc_info=True)

    # Never emit ±inf/NaN — Starlette JSONResponse raises ValueError and the
    # Lab UI gets HTTP 500 on GET /ml/jobs/{id}, which looks like a hung train
    # (poll_err / "server busy") even though the job already finished.
    safe_best = (
        round(best_mean_return, 4)
        if episode_returns and math.isfinite(best_mean_return)
        else None
    )
    if episode_count < 1:
        return {
            "ok": False,
            "error": (
                f"rejected train: episodes=0 after {total_steps} steps "
                f"(need completed episodes; raise total_timesteps or lower max_episode_steps)"
            ),
            "symbol": symbol,
            "timeframe": tf,
            "metrics": {
                "total_timesteps": total_steps,
                "episodes": 0,
                "ent_coef": ent_coef,
            },
        }

    metrics = {
        "total_timesteps": total_steps,
        "episodes": episode_count,
        "mean_return_pct": round(sum(episode_returns) / max(1, len(episode_returns)), 4) if episode_returns else 0.0,
        "best_mean_return": safe_best,
        "mean_trades_per_episode": round(sum(episode_trades) / max(1, len(episode_trades)), 1) if episode_trades else 0,
        "last_10_returns": [round(r, 4) for r in episode_returns[-10:]],
        "hidden_dim": hidden_dim,
        "ent_coef": ent_coef,
        "fee_bps": fee_bps,
        "slippage_bps": slip_bps,
        "train_device": train_device_meta.get("device"),
        "replay_finetune": replay_meta,
    }
    if transfer_meta is not None:
        metrics["transfer"] = transfer_meta

    train_history = [
        {"episode": i + 1, "return_pct": round(r, 4)}
        for i, r in enumerate(episode_returns[-50:])
    ]

    scaler = {
        "feat_mean": env._feat_mean.tolist(),
        "feat_std": env._feat_std.tolist(),
    }
    # In-memory bundle for WF/PBO OOS — avoids triple-barrier accuracy and
    # lets folds skip clobbering the live champion ONNX.
    wf_bundle = {
        "strategy": "RL_PPO_AGENT",
        "model": model,
        "scaler": scaler,
        "hidden_dim": hidden_dim,
        "device": str(getattr(device, "type", device)),
    }

    from app.services.bots.ml_training_window import skip_live_artifact_writes

    skip_persist = bool(
        skip_live_artifact_writes(cfg)
        or cfg.get("skip_onnx_export")
        or cfg.get("skip_persist")
    )
    if cfg.get("champion_train"):
        skip_persist = False
        cfg["skip_snapshot"] = False
        cfg.pop("_wf_mode", None)
        cfg.pop("wf_mode", None)

    metadata = {
        "symbol": symbol,
        "timeframe": tf,
        "model_type": "rl_ppo",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "feature_names": list(SIGNAL_FEATURE_NAMES),
        "obs_dim": OBS_DIM,
        "n_actions": N_ACTIONS,
        "action_map": {"0": "HOLD", "1": "BUY", "2": "SELL", "3": "CLOSE"},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "train_history": train_history,
        "loss_history": [
            {"epoch": h["episode"], "train_loss": -h["return_pct"], "val_loss": -h["return_pct"]}
            for h in train_history
        ],
        "config": {
            "gamma": gamma,
            "gae_lambda": gae_lambda,
            "clip_epsilon": clip_epsilon,
            "ppo_epochs": ppo_epochs,
            "n_steps": n_steps,
            "hidden_dim": hidden_dim,
            "learning_rate": lr,
            "ent_coef": ent_coef,
            "timeframe": tf,
            "fee_bps": fee_bps,
            "slippage_bps": slip_bps,
            "atr_stop_mult": cfg.get("atr_stop_mult"),
            "take_profit_r": cfg.get("take_profit_r"),
            "train_device": train_device_meta,
        },
        "model_version": None,
        "train_device": train_device_meta,
    }

    if transfer_meta is not None and donor_info is not None:
        from app.services.bots import model_transfer as _mt

        metadata["transfer"] = _mt.build_lineage(
            donor_info,
            method=_mt.METHOD_WEIGHT_WARM_START,
            scaler_strategy=scaler_strategy,
            finetune_budget={
                "total_timesteps": int(total_timesteps),
                "learning_rate": lr,
                "transfer_rejected": bool(transfer_meta.get("transfer_rejected")),
            },
        )

    if skip_persist:
        logger.info(
            "PPO fold train for %s @ %s skipped live ONNX write (WF/PBO mode; steps=%d)",
            symbol, tf, total_steps,
        )
        return {
            "ok": True,
            "symbol": symbol,
            "timeframe": tf,
            "_wf_bundle": wf_bundle,
            **metadata,
        }

    # ── Export to ONNX (single-file; invalidate ORT mmap before rewrite) ──
    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    _export_policy_onnx(symbol, model, timeframe=tf)

    # Persist the trainable checkpoint so other assets can warm-start from it.
    try:
        torch.save(model.state_dict(), _checkpoint_path(symbol, tf))
    except Exception:
        logger.debug("PPO policy.pt save failed for %s", symbol, exc_info=True)

    with open(_scaler_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)

    with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    # Invalidate model cache so the next OOS eval reloads this artifact.
    _ppo_model_store.invalidate(symbol, timeframe=tf)

    # Walk-forward / interactive validate sets skip_snapshot to avoid copying
    # ONNX while ORT may still hold Windows file mappings across folds.
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="RL_PPO_AGENT")
            if snap:
                pinned = snap.get("version_id") or metadata.get("trained_at")
                metadata["version_id"] = pinned
                metadata["model_version"] = pinned
                metadata["version_path"] = snap.get("path")
                with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
                    json.dump(metadata, fh, indent=2)
        except Exception:
            logger.exception("Failed to snapshot PPO version for %s", symbol)

    if not metadata.get("model_version"):
        metadata["model_version"] = metadata.get("trained_at")
        metadata.setdefault("version_id", metadata["model_version"])

    logger.info(
        "PPO agent trained for %s @ %s (steps=%d, episodes=%d, mean_return=%.2f%%)",
        symbol, tf, total_steps, episode_count, metrics["mean_return_pct"],
    )
    out = {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}
    if wf_mode:
        out["_wf_bundle"] = wf_bundle
    return out


# ── Replay-based continual fine-tuning (AI-FT-PTL-001 §3.2, P1 #4) ──────────


def finetune_from_replay(
    model,
    optimizer,
    symbol: str,
    *,
    device,
    clip_epsilon: float = 0.2,
    vf_coef: float = 0.5,
    ent_coef: float = 0.0,
    max_grad_norm: float = 0.5,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Run KL-constrained PPO epochs on replay-buffer mini-batches.

    Samples stored live transitions, runs ``RL_REPLAY_FINETUNE_EPOCHS`` update
    epochs, and rejects the update when the mean KL divergence between the
    pre-update and post-update policy exceeds ``RL_REPLAY_MAX_KL`` (the old
    weights are restored in that case). Returns a metrics dict; ``applied`` is
    False when the buffer is too small or the KL guard rejected the update.
    """
    torch, nn = _get_torch()
    from app.config import (
        RL_REPLAY_FINETUNE_EPOCHS,
        RL_REPLAY_MAX_KL,
        RL_REPLAY_MIN_FOR_FINETUNE,
    )
    from app.services.bots.rl_replay_store import count_transitions, load_transitions

    n_available = count_transitions(symbol)
    if n_available < RL_REPLAY_MIN_FOR_FINETUNE:
        return {
            "applied": False,
            "reason": f"replay buffer too small ({n_available} < {RL_REPLAY_MIN_FOR_FINETUNE})",
            "transitions": n_available,
        }

    transitions = load_transitions(symbol)
    if not transitions:
        return {"applied": False, "reason": "replay buffer empty", "transitions": 0}

    obs_t = torch.tensor(
        np.stack([t["obs"] for t in transitions]), dtype=torch.float32, device=device,
    )
    actions_t = torch.tensor([t["action"] for t in transitions], dtype=torch.long, device=device)
    rewards_t = torch.tensor([t["reward"] for t in transitions], dtype=torch.float32, device=device)

    # Freeze the pre-update policy for the KL constraint.
    old_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    model.eval()
    with torch.no_grad():
        old_logits, _ = model.policy(obs_t)
        old_dist = torch.distributions.Categorical(logits=old_logits)
        old_log_probs = old_dist.log_prob(actions_t)

    # Advantage proxy: center rewards (replay has no value bootstrapping).
    adv = rewards_t - rewards_t.mean()
    if adv.std() > 1e-8:
        adv = adv / adv.std()

    model.train()
    n = len(transitions)
    epochs = max(1, int(RL_REPLAY_FINETUNE_EPOCHS))
    for _ in range(epochs):
        perm = torch.randperm(n, device=device)
        for start in range(0, n, batch_size):
            idx = perm[start:start + batch_size]
            b_obs, b_act = obs_t[idx], actions_t[idx]
            b_old_lp, b_adv, b_ret = old_log_probs[idx], adv[idx], rewards_t[idx]

            logits, values = model.policy(b_obs)
            dist = torch.distributions.Categorical(logits=logits)
            new_lp = dist.log_prob(b_act)
            entropy = dist.entropy().mean()

            ratio = torch.exp(new_lp - b_old_lp)
            surr1 = ratio * b_adv
            surr2 = torch.clamp(ratio, 1.0 - clip_epsilon, 1.0 + clip_epsilon) * b_adv
            policy_loss = -torch.min(surr1, surr2).mean()
            value_loss = nn.functional.mse_loss(values.squeeze(-1), b_ret)
            loss = policy_loss + vf_coef * value_loss - ent_coef * entropy

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
            optimizer.step()

    # KL(old || new) on the replay states; reject when the policy moved too far.
    model.eval()
    with torch.no_grad():
        new_logits, _ = model.policy(obs_t)
        new_dist = torch.distributions.Categorical(logits=new_logits)
        kl = float(torch.distributions.kl_divergence(old_dist, new_dist).mean().item())

    if kl > RL_REPLAY_MAX_KL:
        model.load_state_dict(old_state)
        model.to(device)
        logger.warning(
            "PPO replay fine-tune REJECTED for %s: KL=%.4f > %.4f — old policy restored",
            symbol, kl, RL_REPLAY_MAX_KL,
        )
        return {
            "applied": False,
            "reason": f"kl_guard ({kl:.4f} > {RL_REPLAY_MAX_KL})",
            "kl_divergence": round(kl, 6),
            "transitions": n,
        }

    logger.info(
        "PPO replay fine-tune applied for %s: %d transitions, %d epochs, KL=%.4f",
        symbol, n, epochs, kl,
    )
    return {
        "applied": True,
        "kl_divergence": round(kl, 6),
        "transitions": n,
        "epochs": epochs,
    }


# ── Model store ───────────────────────────────────────────────────────────


class PpoModelStore:
    """In-memory cache of ONNX PPO policy sessions — LRU + TTL."""

    def __init__(self) -> None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        self._sessions: dict[str, Any] = {}
        self._metadata: dict[str, dict] = {}
        self._scalers: dict[str, dict] = {}
        self._mtime: dict[str, float] = {}
        self._lru = bind_dict_cache(
            self._sessions, self._metadata, self._scalers, self._mtime,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )

    @staticmethod
    def _cache_key(
        symbol: str,
        model_version: str | None,
        timeframe: str | None = None,
    ) -> str:
        from app.services.bots.ml_model_artifacts import model_storage_key

        return f"{model_storage_key(symbol, timeframe)}|{model_version or 'latest'}"

    def invalidate(self, symbol: str | None = None, *, timeframe: str | None = None) -> None:
        from app.services.bots.ml_model_artifacts import model_storage_key, safe_symbol_key

        if symbol:
            if timeframe is not None:
                sk = model_storage_key(symbol, timeframe)
                prefixes = (sk + "|", sk)
            else:
                sk = safe_symbol_key(symbol)
                prefixes = (sk + "|", sk + "__")
            for p in prefixes:
                self._lru.discard_prefix(p)
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if any(k == p.rstrip("|") or k.startswith(p) for p in prefixes):
                        d.pop(k, None)
        else:
            self._lru.clear()
            self._sessions.clear()
            self._metadata.clear()
            self._scalers.clear()
            self._mtime.clear()

    def get_metadata(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ) -> dict | None:
        self._ensure_loaded(symbol, model_version=model_version, timeframe=timeframe)
        return self._metadata.get(self._cache_key(symbol, model_version, timeframe))

    def predict_action(
        self,
        symbol: str,
        obs: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
    ) -> tuple[int, float] | None:
        """Run ONNX inference to get best action and confidence.

        Returns (action_idx, confidence) or None.
        """
        session = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if session is None:
            return None

        try:
            logits = session.run(
                None, {"observation": obs.astype(np.float32).reshape(1, -1)}
            )[0][0]
            # Softmax for confidence
            x = logits - logits.max()
            proba = np.exp(x) / np.exp(x).sum()
            action = int(np.argmax(proba))
            confidence = float(proba[action])
            return action, confidence
        except Exception as exc:
            logger.warning("PPO predict failed for %s: %s", symbol, exc)
            return None

    def get_scaler(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ) -> dict | None:
        self._ensure_loaded(symbol, model_version=model_version, timeframe=timeframe)
        return self._scalers.get(self._cache_key(symbol, model_version, timeframe))

    def _ensure_loaded(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version, timeframe)
        load_dir = resolve_model_dir(_model_dir(symbol, timeframe), model_version)
        onnx_path = os.path.join(load_dir, "ppo_policy.onnx")
        meta_path = os.path.join(load_dir, "metadata.json")

        if not os.path.isfile(onnx_path) or not os.path.isfile(meta_path):
            return None

        mtime = os.path.getmtime(onnx_path)
        if key in self._sessions and self._mtime.get(key) == mtime:
            self._lru.touch(key)
            return self._sessions[key]

        try:
            import onnxruntime as ort
        except ImportError:
            return None

        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            from app.services.bots.ml_feature_engineering import is_compatible_feature_schema
            if not is_compatible_feature_schema(int(meta.get("feature_schema_version", 0))):
                logger.warning("PPO model schema mismatch for %s", key)
                return None

            from app.services.bots.ml_onnx_runtime import create_inference_session

            session = create_inference_session(onnx_path, research=False)

            scaler = None
            scaler_p = os.path.join(load_dir, "scaler.json")
            if os.path.isfile(scaler_p):
                with open(scaler_p, encoding="utf-8") as fh:
                    scaler = json.load(fh)
        except Exception as exc:
            logger.warning("PPO model load failed for %s: %s", key, exc)
            return None

        self._sessions[key] = session
        self._metadata[key] = meta
        self._scalers[key] = scaler or {}
        self._mtime[key] = mtime
        self._lru.touch(key)
        return session


_ppo_model_store = PpoModelStore()


def get_ppo_store() -> PpoModelStore:
    return _ppo_model_store
