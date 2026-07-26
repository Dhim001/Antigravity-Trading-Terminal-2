# Terminal Optimizer Engine — Deep Upgrade Plan

## Problem Statement

The optimizer is currently a **collection of decoupled modules** (grid/random/LHS/Bayesian sweep, walk-forward, param stability, multi-objective Pareto) that only optimizes **backtest trading parameters** (SL, TP, confidence, allocation). It has several critical gaps:

1. **No ML Training Hyperparameter Auto-Tune** — ML model `learning_rate`, `hidden_dim`, `epochs`, `batch_size`, `gbm_max_depth` etc. are hard-coded defaults in `indicators.py`. Users must manually guess values.
2. **No Optimizer → Deploy Pipeline** — After finding best params, user must manually copy/paste config to a bot. No "apply best" button or auto-deploy.
3. **No Optimizer → Retrain Trigger** — Alpha decay detects degradation, retrain scheduler checks age, but neither feeds optimized hyperparameters back into the retrain cycle.
4. **Weak UI Feedback** — Heatmap only shows 2 axes, no convergence plot for Bayesian trials, no hyperparameter importance ranking, no "what to tune next" guidance.
5. **No Multi-Fidelity Optimization** — Every trial runs a full-length backtest; no mechanism to quickly screen bad params on a data subset first.
6. **Missing Optimizer ↔ WF Integration for ML** — Walk-forward re-trains the model inside each fold but uses the **same** hyperparameters every fold. There's no inner optimization loop.

---

## Research Summary

Modern trading optimizer best practices (2024-2025) emphasize:

| Principle | Implementation |
|:---|:---|
| **Bayesian TPE** | Already have (Optuna). Need to extend to ML hyperparams. |
| **Multi-Fidelity / ASHA** | Screen on 30% data first, promote winners to full backtest. |
| **Walk-Forward Inner Loop** | Optimize hyperparams within each WF fold's IS window. |
| **Sensitivity Analysis** | Already have basic CV-based sensitivity. Need SHAP-style hyperparameter importance. |
| **Auto-Apply / Hot-Swap** | "Apply best config" button → updates live bot config via API. |
| **Performance-Triggered Retrain** | Alpha decay score → auto-queue retrain with last-best hyperparams. |
| **Convergence Monitoring** | Real-time chart of trial scores vs. trial number. |

---

## Proposed Changes

### Phase 1: ML Hyperparameter Auto-Tune Engine (Backend)

> [!IMPORTANT]
> This is the highest-impact change. Currently ML model hyperparams are **frozen defaults** — users cannot sweep them during training.

#### [NEW] [ml_hyperparam_sweep.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/ml_hyperparam_sweep.py)

New Optuna-based ML hyperparameter optimizer that wraps existing trainers:

- **Search spaces** for each ML strategy:
  - `ML_SIGNAL_BOOST`: `gbm_max_depth` ∈ [2, 8], `gbm_learning_rate` ∈ [0.01, 0.2], `gbm_max_iter` ∈ [100, 500], `gbm_l2_reg` ∈ [0, 5.0], `val_fraction` ∈ [0.15, 0.3], `triple_barrier_atr_mult` ∈ [1.0, 4.0]
  - `LSTM_DIRECTION` / `TRANSFORMER_SIGNAL` / `TCN_MULTI_HORIZON`: `learning_rate` ∈ [1e-4, 5e-3], `hidden_dim` ∈ {64, 128, 256}, `epochs` ∈ [30, 150], `batch_size` ∈ {32, 64, 128, 256}, `lookback` ∈ [30, 180], `num_layers` ∈ {1, 2, 3, 4}
  - `RL_PPO_AGENT`: `learning_rate` ∈ [1e-4, 1e-3], `clip_epsilon` ∈ [0.1, 0.3], `ent_coef` ∈ [0.001, 0.05], `n_steps` ∈ {512, 1024, 2048, 4096}
  - `VAE_REGIME_DETECTOR`: `latent_dim` ∈ {8, 16, 32, 64}, `anomaly_threshold` ∈ [1.0, 4.0], `hidden_dim` ∈ {64, 128, 256}
- **Objective function**: Wraps `run_train_job()` → extracts validation metrics (OOS accuracy, Sharpe from WF fold, log-loss). Uses **purged cross-validation** score as the objective, not IS accuracy.
- **Budget controls**: `max_trials` (default 20), `time_budget_sec` (default 600), early stopping on plateau (patience 8).
- **Multi-fidelity screen**: First trials train on 40% of data with reduced epochs (⅓), promote top-k to full training.
- Returns `best_hyperparams`, `trial_history`, `importance_ranking`.

#### [MODIFY] [ml_train_executor.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/ml_train_executor.py)

- Add `run_hyperparam_sweep_job()` — async wrapper that runs `ml_hyperparam_sweep.run_sweep()` in the existing process pool.
- Reports progress via `write_ml_progress()` (trial N of M, current best score).

#### [MODIFY] [strategies_ml.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/strategies_ml.py)

- Expose a `train_ml_signal_model_with_config(symbol, candles, hyperparams)` that accepts an explicit hyperparam dict instead of reading from defaults. The sweep engine calls this.

---

### Phase 2: Optimizer → Deploy Pipeline ("Apply Best Config")

> [!IMPORTANT]
> After a sweep finds the best params, users currently have no way to apply them. This adds a one-click "Apply to Bot" action.

#### [MODIFY] [bots.py (API handler)](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/api/handlers/bots.py)

- New endpoint: `POST /api/v1/bots/{bot_id}/apply-optimized-config`
  - Accepts `{ optimization_run_id, config_source: "best" | "centroid" | "manual", overrides: {} }`
  - Loads best/centroid config from `optimization_store`
  - Validates against deploy gate
  - Merges into the bot's live config via `manager.update_bot_config()`
  - Returns `{ applied: true, config_diff: {...}, deploy_gate_result: {...} }`

#### [MODIFY] [optimization_store.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/optimization_store.py)

- Add `get_best_config(run_id, source="best"|"centroid")` helper.
- Add `link_optimization_to_bot(run_id, bot_id)` to track which optimization was applied where.

---

### Phase 3: Optimizer → Retrain Feedback Loop

#### [MODIFY] [ml_retrain_scheduler.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/ml_retrain_scheduler.py)

- When alpha decay triggers a retrain, check `optimization_store` for the last successful sweep for that symbol+strategy.
- If found, pass the `best_hyperparams` into the retrain config instead of using defaults.
- Add config flag `retrain_use_optimized_hyperparams: bool` (default `true`).

#### [MODIFY] [alpha_decay.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/alpha_decay.py)

- When circuit breaker fires, emit a suggestion: "Consider running hyperparameter sweep before retrain — model may need architectural changes."
- Surface this in the UI decay panel.

---

### Phase 4: Backtest Optimizer Enhancements

#### [MODIFY] [backtest_bayesian.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtest_bayesian.py)

- Add **Optuna pruning** (MedianPruner) — if mid-backtest equity is in the bottom 30% of trials at 50% completion, abort the trial early.
- Add **warm-start**: Load previous study trials from `optimization_store` to seed the TPE sampler with prior knowledge.
- Return `hyperparameter_importance` (via `optuna.importance.get_param_importances()`).

#### [MODIFY] [backtest_multi_objective.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtest_multi_objective.py)

- Add `NSGA-II` multi-objective sampler option alongside current TPE (user selects in sweep config).
- Expose `crowding_distance` ranking on Pareto frontier rows.

#### [MODIFY] [backtest_sweep.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtest_sweep.py)

- Add `"sobol"` sweep mode — Sobol quasi-random sequences for better space coverage than LHS with same trial count.
- Increase `MAX_SWEEP_COMBOS_EXTENDED` from 100 → 200 for Bayesian/Sobol modes.

---

### Phase 5: Frontend Optimizer UI Upgrade

> [!IMPORTANT]
> The UI currently gives very little insight into **why** a config was chosen as best, and no tools to trigger or monitor ML hyperparameter sweeps.

#### [NEW] [MlAutoTunePanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/MlAutoTunePanel.jsx)

New panel in Model Training Dashboard for ML hyperparameter auto-tune:
- **Start Sweep** button with configurable budget (max trials, time limit)
- **Live convergence chart** — trial score vs. trial number (sparkline)
- **Hyperparameter importance** — horizontal bar chart showing which params matter most (from Optuna `get_param_importances()`)
- **Best config comparison table** — default vs. best found, with diff highlights
- **"Apply & Retrain"** button that sends best hyperparams to `ml_train_executor`

#### [MODIFY] [MlOptimizerPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/MlOptimizerPanel.jsx)

- Add "ML training hyperparams" section exposing sweepable training params (learning_rate, hidden_dim, epochs, etc.) alongside the existing trading params (SL, TP, confidence).
- Add toggle: "Include training hyperparams in sweep" (default off for TA, on for ML).

#### [MODIFY] [TaOptimizerPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/TaOptimizerPanel.jsx)

- Add **convergence chart** for Bayesian sweeps — plot objective value vs trial number.
- Add **"Apply Best to Bot"** button that calls the new apply-optimized-config endpoint.
- Add **hyperparameter importance ranking** below heatmap (Optuna importance data).
- Show **"Optimization Health"** badge: ✅ Converged (plateau), ⚠️ Still exploring, ❌ Budget exhausted without convergence.

#### [MODIFY] [OptimizerHeatmap.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/OptimizerHeatmap.jsx)

- Support 3D heatmap (3+ swept params) via dropdown axis selector.
- Add tooltip with full trial config on hover.

#### [MODIFY] [BacktestWalkForwardPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestWalkForwardPanel.jsx)

- Show per-fold hyperparameter drift (if ML sweep was used within folds).
- Add "Parameter Stability" badge per fold.

#### [MODIFY] [ModelTrainingDashboard.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/dock/ModelTrainingDashboard.jsx)

- Add "Auto-Tune Hyperparams" tab/section that renders `MlAutoTunePanel`.
- Show last sweep result summary with "Apply" action.

---

### Phase 6: API Endpoints

#### [MODIFY] [bots.py (API handler)](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/api/handlers/bots.py)

New endpoints:
- `POST /api/v1/ml/hyperparam-sweep` — Start an ML hyperparameter sweep job
  - Body: `{ symbol, strategy, timeframe, candle_days, max_trials, time_budget_sec, custom_search_space }`
  - Returns: `{ job_id }` (async, progress via WS)
- `GET /api/v1/ml/hyperparam-sweep/{job_id}` — Get sweep status/results
- `POST /api/v1/bots/{bot_id}/apply-optimized-config` — Apply optimization results to a bot
- `GET /api/v1/optimization/param-importance/{run_id}` — Get hyperparameter importance

---

## Open Questions

> [!IMPORTANT]
> **Search Space Boundaries**: Should the user be able to customize the search space ranges (e.g., set their own min/max for `learning_rate`), or should we use research-backed defaults only? I recommend **defaults with optional user overrides** in an "Advanced" collapse.

> [!IMPORTANT]
> **Multi-Fidelity vs. Full Sweep**: Multi-fidelity (screen on 40% data first) saves ~60% compute but adds complexity. Should we implement it in Phase 1 or defer to a later iteration? I recommend **including it** since ML training is the most expensive operation.

> [!WARNING]
> **Apply to Bot Safety**: When applying optimized config to a running bot, should we require the bot to be paused first, or hot-swap with a "previous config" rollback option?

---

## Recommended Phasing

I recommend starting with **Phase 1 + 5 + 6** (ML auto-tune engine + UI + API), then **Phase 2** (apply-to-bot), then **Phase 4** (backtest enhancements), then **Phase 3** (retrain feedback loop).

| Phase | Effort | Impact | Priority |
|:---|:---|:---|:---|
| **Phase 1**: ML Hyperparam Auto-Tune | High | 🟢 Very High | 1st |
| **Phase 5**: Frontend UI Upgrade | Medium | 🟢 Very High | 1st |
| **Phase 6**: API Endpoints | Medium | 🟢 High | 1st |
| **Phase 2**: Apply Best to Bot | Low | 🟡 Medium | 2nd |
| **Phase 4**: Backtest Enhancements | Medium | 🟡 Medium | 3rd |
| **Phase 3**: Retrain Feedback Loop | Low | 🟡 Medium | 4th |

---

## Verification Plan

### Automated Tests
- Unit tests for `ml_hyperparam_sweep.py` — search space generation, objective wrapping, budget enforcement.
- Unit tests for apply-optimized-config endpoint — config merging, deploy gate validation.
- Integration test: run a 5-trial sweep on `ML_SIGNAL_BOOST` with synthetic data, verify best_hyperparams differ from defaults.
- Existing test suite (`npm run test`, `pytest`) must remain green.

### Manual Verification
- Run ML hyperparameter sweep from UI, verify convergence chart updates in real-time.
- Apply best config to a bot, verify bot config updates correctly.
- Run backtest with Bayesian mode, verify hyperparameter importance appears.
- Verify "Optimization Health" badge shows correct convergence state.
