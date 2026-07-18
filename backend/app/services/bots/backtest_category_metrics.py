"""Build ml_metrics / rl_data / agent_metrics for backtest result payloads."""

from __future__ import annotations

import math
from typing import Any

# Cap episode steps retained in results (further downsampled on the wire).
MAX_EPISODE_STEPS = 2000
MAX_OBS_DIMS = 24

_LABEL_TO_IDX = {"BUY": 0, "SELL": 1, "NONE": 2}
_IDX_TO_LABEL = {0: "BUY", 1: "SELL", 2: "NONE"}

_AGENT_FUNNEL_ORDER = (
    "confidence",
    "min_score",
    "trend",
    "htf",
    "vol",
    "calibration",
    "sentiment",
    "other",
)

_ML_STRATEGIES = frozenset({
    "ML_SIGNAL_BOOST",
    "LSTM_DIRECTION",
    "RL_PPO_AGENT",
    "TCN_MULTI_HORIZON",
    "VAE_REGIME_DETECTOR",
    "TRANSFORMER_SIGNAL",
    "GNN_CROSS_ASSET",
})

_AGENT_STRATEGIES = frozenset({
    "CHART_AGENT",
    "ABSORPTION_AGENT",
})


def is_ml_strategy_key(strategy: str) -> bool:
    return str(strategy or "").upper() in _ML_STRATEGIES


def is_rl_strategy_key(strategy: str) -> bool:
    return str(strategy or "").upper() == "RL_PPO_AGENT"


def is_agent_strategy_key(strategy: str) -> bool:
    return str(strategy or "").upper() in _AGENT_STRATEGIES


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def future_direction_label(close_now: float, close_future: float, deadband: float = 0.0005) -> str:
    if close_now <= 0:
        return "NONE"
    ret = (close_future - close_now) / close_now
    if ret > deadband:
        return "BUY"
    if ret < -deadband:
        return "SELL"
    return "NONE"


def compute_alpha_decay(
    equity_curve: list[dict] | None,
    *,
    window: int = 50,
    max_points: int = 80,
) -> dict[str, Any] | None:
    """Estimate half-life of edge from rolling Sharpe on the equity curve."""
    if not equity_curve or len(equity_curve) < window + 5:
        return None
    rets: list[float] = []
    times: list[float] = []
    for j in range(1, len(equity_curve)):
        prev_eq = _safe_float(equity_curve[j - 1].get("equity"), 0.0)
        curr_eq = _safe_float(equity_curve[j].get("equity"), 0.0)
        if prev_eq <= 0:
            continue
        rets.append((curr_eq - prev_eq) / prev_eq)
        t = equity_curve[j].get("time")
        times.append(_safe_float(t, float(j)) if t is not None else float(j))
    if len(rets) < window:
        return None

    rolling: list[float] = []
    for i in range(window, len(rets) + 1):
        chunk = rets[i - window : i]
        mean = sum(chunk) / window
        var = sum((x - mean) ** 2 for x in chunk) / max(window - 1, 1)
        std = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mean / std) * math.sqrt(252) if std > 1e-12 else 0.0
        rolling.append(round(sharpe, 4))

    if len(rolling) < 4:
        return None

    early_n = max(1, len(rolling) // 4)
    late_n = max(1, len(rolling) // 4)
    early = sum(rolling[:early_n]) / early_n
    late = sum(rolling[-late_n:]) / late_n
    peak = max(rolling[: max(early_n * 2, 1)])
    half = peak * 0.5

    half_life_days = None
    if peak > 0.05 and times:
        # Index into equity timestamps when rolling first drops to half of early peak
        for ri, s in enumerate(rolling):
            if s <= half and ri > early_n:
                # Map rolling index → absolute bar index in equity_curve
                bar_i = min(window + ri, len(equity_curve) - 1)
                t0 = _safe_float(equity_curve[0].get("time"), 0.0)
                t1 = _safe_float(equity_curve[bar_i].get("time"), 0.0)
                if t1 > t0 > 0:
                    half_life_days = round((t1 - t0) / 86_400.0, 2)
                else:
                    # Fallback: bars as proxy days (1m bars ≈ minutes)
                    half_life_days = round(bar_i / max(window, 1), 2)
                break
        if half_life_days is None and late < peak * 0.5:
            t0 = _safe_float(equity_curve[0].get("time"), 0.0)
            t1 = _safe_float(equity_curve[-1].get("time"), 0.0)
            if t1 > t0 > 0:
                half_life_days = round((t1 - t0) / 86_400.0, 2)

    stride = max(1, len(rolling) // max_points) if len(rolling) > max_points else 1
    return {
        "half_life_days": half_life_days,
        "rolling_sharpe": rolling[::stride],
        "early_sharpe": round(early, 4),
        "late_sharpe": round(late, 4),
        "window_bars": window,
    }


class CategoryMetricsCollector:
    """Accumulates per-bar telemetry during a backtest run."""

    def __init__(self, strategy: str):
        self.strategy = str(strategy or "").upper()
        self.collect_ml = is_ml_strategy_key(self.strategy)
        self.collect_rl = is_rl_strategy_key(self.strategy)
        self.collect_agent = is_agent_strategy_key(self.strategy)

        self._pred_labels: list[int] = []
        self._true_labels: list[int] = []
        self._confidences: list[float] = []
        self._signals_generated = 0
        self._signals_filtered = 0
        self._signals_executed = 0
        self._filter_rejects: dict[str, int] = {}
        self._regime_stats: dict[str, dict[str, float]] = {}

        self._action_counts = {"long": 0, "short": 0, "flat": 0}
        self._position_traj: list[float] = []
        self._reward_cum: list[float] = []
        self._episode_steps: list[dict[str, Any]] = []
        self._reward_sum = 0.0
        self._calib_buckets: dict[str, dict[str, float]] = {}

    def record_bar(
        self,
        *,
        bar_index: int,
        signal_data: dict | None,
        signal: str | None,
        close: float,
        future_close: float | None,
        position_side: str | None,
        executed: bool,
        filter_bucket: str | None = None,
        regime: str | None = None,
        trade_pnl: float | None = None,
    ) -> None:
        sd = signal_data or {}
        raw_signal = str(sd.get("signal") or signal or "NONE").upper()
        conf = _safe_float(sd.get("confidence"), 0.0)

        if self.collect_ml or self.collect_agent:
            if raw_signal in ("BUY", "SELL") or sd.get("reject_reason"):
                self._signals_generated += 1
            if sd.get("reject_reason") or filter_bucket:
                self._signals_filtered += 1
                bucket = filter_bucket or "other"
                self._filter_rejects[bucket] = self._filter_rejects.get(bucket, 0) + 1
            if executed:
                self._signals_executed += 1

        if self.collect_ml and conf > 0:
            self._confidences.append(conf)

        if self.collect_ml and not self.collect_rl:
            pred = raw_signal if raw_signal in _LABEL_TO_IDX else "NONE"
            if future_close is not None:
                true = future_direction_label(close, future_close)
                self._pred_labels.append(_LABEL_TO_IDX[pred])
                self._true_labels.append(_LABEL_TO_IDX[true])

        if self.collect_agent and conf > 0 and executed and trade_pnl is not None:
            # deferred — win outcome recorded via record_closed_trade
            pass

        if self.collect_agent and regime:
            st = self._regime_stats.setdefault(
                regime,
                {"wins": 0.0, "pnl": 0.0, "trades": 0.0, "blocked": 0.0},
            )
            if filter_bucket:
                st["blocked"] += 1

        if self.collect_rl:
            self._record_rl_step(bar_index, sd, position_side)

    def record_closed_trade(
        self,
        *,
        confidence: float | None,
        regime: str | None,
        pnl: float,
    ) -> None:
        if not self.collect_agent:
            return
        if confidence is not None and confidence > 0:
            bucket = f"{math.floor(confidence * 10) / 10:.1f}-{math.floor(confidence * 10) / 10 + 0.1:.1f}"
            cell = self._calib_buckets.setdefault(
                bucket,
                {"predicted": 0.0, "wins": 0.0, "count": 0.0},
            )
            cell["predicted"] += confidence
            cell["count"] += 1
            if pnl > 0:
                cell["wins"] += 1
        if regime:
            st = self._regime_stats.setdefault(
                regime,
                {"wins": 0.0, "pnl": 0.0, "trades": 0.0, "blocked": 0.0},
            )
            st["trades"] += 1
            st["pnl"] += pnl
            if pnl > 0:
                st["wins"] += 1

    def _record_rl_step(self, bar_index: int, sd: dict, position_side: str | None) -> None:
        step = sd.get("rl_step") if isinstance(sd.get("rl_step"), dict) else None
        pos = 0.0
        if position_side == "BUY":
            pos = 1.0
        elif position_side == "SELL":
            pos = -1.0
        elif step and step.get("position") is not None:
            pos = _safe_float(step.get("position"), 0.0)

        action_raw = step.get("action") if step else None
        action_id = None
        if isinstance(action_raw, list) and action_raw:
            action_id = int(_safe_float(action_raw[0], 0))
        elif isinstance(action_raw, (int, float)):
            action_id = int(action_raw)

        # Action counts: BUY→long attempts, SELL→short, else flat/hold
        sig = str(sd.get("signal") or "NONE").upper()
        if sig == "BUY" or action_id == 1:
            self._action_counts["long"] += 1
        elif sig == "SELL" or action_id == 2:
            self._action_counts["short"] += 1
        else:
            self._action_counts["flat"] += 1

        reward = _safe_float(step.get("reward") if step else 0.0, 0.0)
        self._reward_sum += reward
        self._position_traj.append(round(pos, 4))
        self._reward_cum.append(round(self._reward_sum, 6))

        if len(self._episode_steps) < MAX_EPISODE_STEPS:
            obs = []
            if step and isinstance(step.get("observation"), list):
                obs = [round(_safe_float(x), 5) for x in step["observation"][:MAX_OBS_DIMS]]
            self._episode_steps.append({
                "bar_index": int(bar_index),
                "observation": obs,
                "action": action_raw if action_raw is not None else [0],
                "reward": round(reward, 6),
                "position": round(pos, 4),
                "info": {
                    "confidence": round(_safe_float(sd.get("confidence"), 0.0), 4),
                    "signal": sig,
                },
            })

    def finalize(
        self,
        *,
        summary: dict | None = None,
        filter_rejects: dict | None = None,
        feature_importance: list | None = None,
        oos_summary: dict | None = None,
        equity_curve: list[dict] | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        alpha = compute_alpha_decay(equity_curve) if (self.collect_ml or self.collect_rl) else None
        if self.collect_ml and not self.collect_rl:
            metrics = self._build_ml_metrics(feature_importance, oos_summary, summary)
            if alpha:
                metrics["alpha_decay"] = alpha
            out["ml_metrics"] = metrics
        if self.collect_rl:
            out["rl_data"] = self._build_rl_data()
            # RL also gets lightweight ml_metrics stub from confidence if any
            if self._confidences or alpha:
                ml_stub: dict[str, Any] = {}
                if self._confidences:
                    ml_stub["confidence_distribution"] = self._confidence_hist()
                is_oos = self._is_vs_oos(oos_summary, summary)
                if is_oos and any(v is not None for v in is_oos.values()):
                    ml_stub["is_vs_oos"] = is_oos
                if alpha:
                    ml_stub["alpha_decay"] = alpha
                if ml_stub:
                    out["ml_metrics"] = ml_stub
        if self.collect_agent:
            fr = filter_rejects or self._filter_rejects
            out["agent_metrics"] = self._build_agent_metrics(fr, summary)
        return out

    def _confidence_hist(self) -> list[dict[str, Any]]:
        buckets: dict[str, int] = {}
        for c in self._confidences:
            key = f"{math.floor(c * 10) / 10:.1f}"
            buckets[key] = buckets.get(key, 0) + 1
        return [{"bucket": k, "count": v} for k, v in sorted(buckets.items())]

    def _confusion_matrix(self) -> list[list[int]]:
        m = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        for t, p in zip(self._true_labels, self._pred_labels):
            if 0 <= t < 3 and 0 <= p < 3:
                m[t][p] += 1
        return m

    def _class_prf(self, matrix: list[list[int]]) -> tuple[dict, dict, dict]:
        precision: dict[str, float] = {}
        recall: dict[str, float] = {}
        f1: dict[str, float] = {}
        for idx, label in _IDX_TO_LABEL.items():
            tp = matrix[idx][idx]
            fp = sum(matrix[i][idx] for i in range(3) if i != idx)
            fn = sum(matrix[idx][j] for j in range(3) if j != idx)
            p = tp / (tp + fp) if (tp + fp) else 0.0
            r = tp / (tp + fn) if (tp + fn) else 0.0
            precision[label] = round(p, 4)
            recall[label] = round(r, 4)
            f1[label] = round((2 * p * r / (p + r)) if (p + r) else 0.0, 4)
        return precision, recall, f1

    def _is_vs_oos(self, oos_summary: dict | None, summary: dict | None) -> dict | None:
        """Build IS/OOS block only when a real OOS side exists (walk-forward).

        Plain backtests pass the full-run summary as ``summary`` with no OOS
        fields — do not mislabel that run as in-sample with empty OOS.
        """
        if not oos_summary and not summary:
            return None
        s = summary or {}
        o = oos_summary or {}
        oos_sharpe = o.get("oos_sharpe", s.get("oos_sharpe"))
        oos_pnl = o.get("oos_pnl", s.get("oos_pnl"))
        if oos_sharpe is None and oos_pnl is None:
            return None
        return {
            "is_sharpe": o.get("is_sharpe", s.get("sharpe_ratio")),
            "oos_sharpe": oos_sharpe,
            "is_pnl": o.get("is_pnl", s.get("total_pnl")),
            "oos_pnl": oos_pnl,
        }

    def _approx_log_loss(self) -> float | None:
        """Approximate multi-class log-loss from predicted confidence vs truth."""
        if not self._confidences or not self._pred_labels or not self._true_labels:
            return None
        n = min(len(self._confidences), len(self._pred_labels), len(self._true_labels))
        if n < 1:
            return None
        total = 0.0
        for i in range(n):
            conf = min(max(_safe_float(self._confidences[i], 0.5), 1e-6), 1.0 - 1e-6)
            true_i = self._true_labels[i]
            pred_i = self._pred_labels[i]
            # Mass `conf` on predicted class; remainder split across other two.
            p_true = conf if true_i == pred_i else (1.0 - conf) / 2.0
            p_true = min(max(p_true, 1e-6), 1.0)
            total += -math.log(p_true)
        return round(total / n, 6)

    def _build_ml_metrics(
        self,
        feature_importance: list | None,
        oos_summary: dict | None,
        summary: dict | None,
    ) -> dict[str, Any]:
        matrix = self._confusion_matrix()
        total = sum(sum(row) for row in matrix)
        correct = matrix[0][0] + matrix[1][1] + matrix[2][2]
        accuracy = correct / total if total else 0.0
        precision, recall, f1 = self._class_prf(matrix)
        # Mean recall is a multi-class proxy — NOT ROC-AUC. Keep auc_roc key for
        # sweep compat but also expose an honest name + prediction mass.
        mean_recall = sum(recall.values()) / 3.0 if recall else 0.0
        pred_buy = matrix[0][0] + matrix[1][0] + matrix[2][0]
        pred_sell = matrix[0][1] + matrix[1][1] + matrix[2][1]
        pred_none = matrix[0][2] + matrix[1][2] + matrix[2][2]
        true_counts = [sum(matrix[r]) for r in range(3)]
        majority_n = max(true_counts) if true_counts else 0
        majority_baseline = majority_n / total if total else 0.0
        directional = pred_buy + pred_sell
        metrics: dict[str, Any] = {
            "accuracy": round(accuracy, 4),
            # Deprecated alias — UI should prefer mean_recall / hide when all-NONE.
            "auc_roc": round(mean_recall, 4),
            "mean_recall": round(mean_recall, 4),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "confusion_matrix": matrix,
            "prediction_counts": {
                "BUY": int(pred_buy),
                "SELL": int(pred_sell),
                "NONE": int(pred_none),
            },
            "directional_predictions": int(directional),
            "majority_class_baseline": round(majority_baseline, 4),
            "label_definition": "next_bar_return_deadband_5bps",
            "confidence_distribution": self._confidence_hist(),
        }
        if directional == 0 and total > 0:
            metrics["prediction_warning"] = (
                "All gated predictions were NONE — accuracy equals the flat-bar "
                "majority baseline and does not reflect tradeable model skill."
            )
        log_loss = self._approx_log_loss()
        if log_loss is not None:
            metrics["log_loss"] = log_loss
        if feature_importance:
            metrics["feature_importance"] = feature_importance
        is_oos = self._is_vs_oos(oos_summary, summary)
        if is_oos and any(v is not None for v in is_oos.values()):
            metrics["is_vs_oos"] = is_oos
        return metrics

    def _build_rl_data(self) -> dict[str, Any]:
        stride = max(1, len(self._position_traj) // 1000) if len(self._position_traj) > 1000 else 1
        return {
            "action_distribution": dict(self._action_counts),
            "position_trajectory": self._position_traj[::stride],
            "reward_accumulation": self._reward_cum[::stride],
            "episode_steps": self._episode_steps,
        }

    def _build_agent_metrics(self, filter_rejects: dict, summary: dict | None) -> dict[str, Any]:
        generated = max(self._signals_generated, sum(filter_rejects.values()) + self._signals_executed)
        filtered = sum(filter_rejects.values()) if filter_rejects else self._signals_filtered
        executed = self._signals_executed or int((summary or {}).get("trade_count") or 0)
        success = None
        wr = (summary or {}).get("win_rate")
        if wr is not None:
            success = float(wr) / 100.0 if float(wr) > 1 else float(wr)
        elif generated > 0:
            success = executed / generated

        # Funnel: start from generated, subtract rejects stage by stage
        remaining = generated
        funnel = [{"name": "Raw signals", "passed": generated, "rejected": 0}]
        for key in _AGENT_FUNNEL_ORDER:
            rejected = int(filter_rejects.get(key) or 0)
            if rejected <= 0:
                continue
            passed = max(0, remaining - rejected)
            funnel.append({
                "name": key.replace("_", " ").title(),
                "passed": passed,
                "rejected": rejected,
            })
            remaining = passed
        funnel.append({
            "name": "Executed",
            "passed": executed,
            "rejected": max(0, remaining - executed),
        })

        calibration = []
        for bucket, cell in sorted(self._calib_buckets.items()):
            n = cell["count"] or 1
            calibration.append({
                "bucket": bucket,
                "predicted": round(cell["predicted"] / n, 4),
                "actual": round(cell["wins"] / n, 4),
                "count": int(n),
            })

        regime_perf = []
        for regime, st in self._regime_stats.items():
            trades = st["trades"] or 0
            regime_perf.append({
                "regime": regime,
                "win_rate": round(st["wins"] / trades, 4) if trades else 0.0,
                "avg_pnl": round(st["pnl"] / trades, 4) if trades else 0.0,
                "trades": int(trades),
                "sharpe": None,
                "signals_blocked": int(st["blocked"]),
            })

        return {
            "signals_generated": int(generated),
            "signals_filtered": int(filtered),
            "signals_executed": int(executed),
            "success_rate": round(success, 4) if success is not None else None,
            "gate_funnel": funnel,
            "confidence_calibration": calibration,
            "regime_performance": regime_perf,
        }


def load_ml_feature_importance(strategy: str, symbol: str) -> list[dict[str, Any]] | None:
    """Best-effort load of feature importance from trained model metadata."""
    try:
        import json
        import os
        from app.config import BASE_DIR

        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (symbol or "").upper())
        subdirs = {
            "ML_SIGNAL_BOOST": "ml_signal_models",
            "LSTM_DIRECTION": "lstm_signal_models",
            "TCN_MULTI_HORIZON": "tcn_signal_models",
            "TRANSFORMER_SIGNAL": "transformer_signal_models",
            "VAE_REGIME_DETECTOR": "vae_regime_models",
            "GNN_CROSS_ASSET": "gnn_signal_models",
            "RL_PPO_AGENT": "rl_ppo_models",
        }
        sub = subdirs.get(str(strategy or "").upper())
        if not sub or not safe:
            return None
        path = os.path.join(BASE_DIR, "data", sub, safe, "metadata.json")
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        fi = (
            meta.get("feature_importance")
            or meta.get("top_features")
            or meta.get("metrics", {}).get("feature_importance")
            or meta.get("metrics", {}).get("top_features")
        )
        if isinstance(fi, list) and fi:
            # Normalize trainer shapes ({name, importance} or bare strings)
            out: list[dict[str, Any]] = []
            for item in fi[:20]:
                if isinstance(item, dict) and item.get("name") is not None:
                    row = {
                        "name": str(item["name"]),
                        "importance": float(item.get("importance") or item.get("weight") or 0),
                    }
                    if item.get("category"):
                        row["category"] = item["category"]
                    else:
                        row["category"] = "indicator"
                    out.append(row)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append({
                        "name": str(item[0]),
                        "importance": float(item[1]),
                        "category": "indicator",
                    })
            return out or None
        # sklearn-style dict → list
        if isinstance(fi, dict):
            return [
                {"name": k, "importance": float(v), "category": "indicator"}
                for k, v in sorted(fi.items(), key=lambda kv: -float(kv[1]))
            ][:20]
    except Exception:
        return None
    return None


def is_vs_oos_from_windows(
    in_sample: dict | None,
    out_of_sample: dict | None,
) -> dict[str, Any] | None:
    """Build ``is_vs_oos`` block from walk-forward IS / OOS window snapshots."""
    is_block = in_sample or {}
    oos_block = out_of_sample or {}
    is_sum = is_block.get("summary") if isinstance(is_block.get("summary"), dict) else {}
    oos_sum = oos_block.get("summary") if isinstance(oos_block.get("summary"), dict) else {}
    out = {
        "is_sharpe": is_sum.get("sharpe_ratio"),
        "oos_sharpe": oos_sum.get("sharpe_ratio"),
        "is_pnl": is_block.get("total_pnl", is_sum.get("total_pnl")),
        "oos_pnl": oos_block.get("total_pnl", oos_sum.get("total_pnl")),
    }
    if not any(v is not None for v in out.values()):
        return None
    return out


def attach_is_vs_oos(result: dict | None, is_vs_oos: dict | None) -> dict | None:
    """Ensure ``result['ml_metrics']['is_vs_oos']`` is present when comparable."""
    if not isinstance(result, dict) or not isinstance(is_vs_oos, dict):
        return result
    if not any(v is not None for v in is_vs_oos.values()):
        return result
    ml = result.get("ml_metrics")
    if not isinstance(ml, dict):
        ml = {}
        result["ml_metrics"] = ml
    ml["is_vs_oos"] = is_vs_oos
    return result
