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

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    wf_mode = bool(cfg.get("_wf_mode") or cfg.get("wf_mode"))

    # Interactive WF/PBO calls trainer(symbol, candles, config=cfg) without
    # total_timesteps — keep those runs short so validate finishes in-UI.
    if cfg.get("total_timesteps") is not None:
        total_timesteps = int(cfg["total_timesteps"])
    elif wf_mode:
        total_timesteps = 2048

    gamma = float(cfg.get("gamma", 0.99))
    gae_lambda = float(cfg.get("gae_lambda", 0.95))
    clip_epsilon = float(cfg.get("clip_epsilon", 0.2))
    ppo_epochs = int(cfg.get("ppo_epochs", 2 if wf_mode else 10))
    n_steps = int(cfg.get("n_steps", 512 if wf_mode else 2048))
    if wf_mode:
        ppo_epochs = min(ppo_epochs, 2)
        n_steps = min(n_steps, 512)
        total_timesteps = min(total_timesteps, max(n_steps, 2048))
    hidden_dim = int(cfg.get("hidden_dim", 64 if wf_mode else 256))
    lr = float(cfg.get("learning_rate", 3e-4))
    vf_coef = float(cfg.get("vf_coef", 0.5))
    ent_coef = float(cfg.get("ent_coef", 0.01))
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

    # Create environment (numpy / CPU)
    env = TradingEnv(candles, config=cfg)

    # Build model on train device
    model = _build_actor_critic(
        obs_dim=OBS_DIM, act_dim=N_ACTIONS, hidden_dim=hidden_dim,
    ).to(device)
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

    # ── Export to ONNX (single-file; invalidate ORT mmap before rewrite) ──
    train_device_meta = device_info(device)
    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    _export_policy_onnx(symbol, model, timeframe=tf)

    # Save environment scaler
    scaler = {
        "feat_mean": env._feat_mean.tolist(),
        "feat_std": env._feat_std.tolist(),
    }
    with open(_scaler_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)

    # Metrics
    metrics = {
        "total_timesteps": total_steps,
        "episodes": episode_count,
        "mean_return_pct": round(sum(episode_returns) / max(1, len(episode_returns)), 4) if episode_returns else 0.0,
        "best_mean_return": round(best_mean_return, 4),
        "mean_trades_per_episode": round(sum(episode_trades) / max(1, len(episode_trades)), 1) if episode_trades else 0,
        "last_10_returns": [round(r, 4) for r in episode_returns[-10:]],
        "hidden_dim": hidden_dim,
        "train_device": train_device_meta.get("device"),
    }

    train_history = [
        {"episode": i + 1, "return_pct": round(r, 4)}
        for i, r in enumerate(episode_returns[-50:])
    ]

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
            "timeframe": tf,
            "train_device": train_device_meta,
        },
        "train_device": train_device_meta,
    }
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
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot PPO version for %s", symbol)

    logger.info(
        "PPO agent trained for %s @ %s (steps=%d, episodes=%d, mean_return=%.2f%%)",
        symbol, tf, total_steps, episode_count, metrics["mean_return_pct"],
    )
    return {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}


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
            if int(meta.get("feature_schema_version", 0)) != SIGNAL_FEATURE_VERSION:
                logger.warning("PPO model schema mismatch for %s", key)
                return None

            session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])

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
