# Backtest Lab Redesign — Implementation Audit Report

> **Audit date**: 15 July 2026  
> **Compared against**: [BACKTEST_LAB_REDESIGN_PLAN.md](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/docs/BACKTEST_LAB_REDESIGN_PLAN.md)

---

## Executive Summary

The implementation is **substantially complete** — Phase 1 (Core Refactor) and Phase 2 (Visualization Components) from the plan are fully built. The remaining gaps are Phase 3 (backend data pipeline) and some Phase 4 items (RL Episode Replay full viewer). The frontend is wired end-to-end and gracefully degrades when backend data fields aren't populated yet.

| Phase | Plan Status | Coverage |
|:---|:---|:---|
| Phase 1 — Core Refactor | ✅ **Complete** | 100% |
| Phase 2 — Visualization Components | ✅ **Complete** | 100% |
| Phase 3 — Backend Data Pipeline + RL | 🔲 **Not started** | 0% (expected — this is backend Python work) |
| Phase 4 — Polish + Episode Replay | 🟡 **Partial** | ~40% (stub built, full replay viewer deferred) |

---

## Detailed Per-Item Checklist

### 1. Strategy Category Detection Layer

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `getStrategyCategory()` added to strategies.js | ✅ Done | [strategies.js:L216–221](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.js#L216-L221) |
| `getMLSubtype()` added to strategies.js | ✅ Done | [strategies.js:L228–233](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.js#L228-L233) |
| Unit tests for both helpers | ✅ Done | [strategies.test.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.test.js) — 45 lines, covers all categories |
| RL episode replay test | ✅ Done | [rlEpisodeReplay.test.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/rlEpisodeReplay.test.js) |

---

### 2. ML/RL-Specific Parameter Definitions

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `ml_model` group in FIELD_META | ✅ Done | [botConfigDisplay.js:L105–126](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L105-L126) — `lookback`, `min_return`, `hidden_dim`, `num_layers`, `learning_rate`, `batch_size`, `d_model`, `nhead`, `latent_dim`, `anomaly_threshold`, `triple_barrier_*`, `min_train_samples`, `val_fraction`, `retrain_interval_hours`, `model_symbol/version/artifact` |
| `rl_policy` group in FIELD_META | ✅ Done | [botConfigDisplay.js:L127–134](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L127-L134) — `gamma`, `gae_lambda`, `clip_epsilon`, `ppo_epochs`, `n_steps`, `total_timesteps`, `vf_coef`, `ent_coef` |
| `agent_llm` group in FIELD_META | ✅ Done | [botConfigDisplay.js:L135–136](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L135-L136) — `llm_temperature`, `max_reasoning_tokens` |
| `agent_gate` group in FIELD_META | ✅ Done | [botConfigDisplay.js:L137](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L137) — `require_multi_domain` |
| `STRATEGY_FIELD_KEYS` per ML strategy | ✅ Done | [botConfigDisplay.js:L154–160](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L154-L160) — ML_SIGNAL_BOOST, LSTM_DIRECTION, RL_PPO_AGENT, TCN_MULTI_HORIZON, VAE_REGIME_DETECTOR, TRANSFORMER_SIGNAL, GNN_CROSS_ASSET |
| `getSweepEligibleFields` category-aware | ✅ Done | [botConfigDisplay.js:L328–343](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L328-L343) — branches on `getStrategyCategory()`, returns ML/RL/Agent-specific fields, hides TA indicators |
| Updated GROUP_ORDER & GROUP_LABELS | ✅ Done | Groups `ml_model`, `rl_policy`, `agent_gate`, `agent_llm` added |
| `inferGroup()` regex updated | ✅ Done | [botConfigDisplay.js:L186–196](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js#L186-L196) |

> [!NOTE]
> The plan specified `lookback_bars`, `confidence_threshold`, `feature_set`, `horizon_agreement`, `position_threshold`, `reward_function`, `transaction_cost_penalty` as new field names. The implementation uses slightly different keys that match the **actual backend config schema** (e.g., `lookback` instead of `lookback_bars`, `gamma` instead of `discount_factor`, `min_return` instead of `min_return_threshold`). This is correct — the plan names were illustrative, the implementation uses the real backend keys.

---

### 3. Backtest Lab — Category-Aware Tab Rendering

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `strategyCategory` derived via `useMemo` | ✅ Done | [BacktestLabSheet.jsx:L96–99](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx#L96-L99) |
| Dynamic `labDescription` per category | ✅ Done | [BacktestLabSheet.jsx:L100](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx#L100) — uses `LAB_DESCRIPTIONS[strategyCategory]` |
| `strategyCategory` passed to BacktestResultsPanel | ✅ Done | [BacktestLabSheet.jsx:L235](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx#L235) |
| `strategyCategory` passed to BacktestSweepPanel | ✅ Done | [BacktestLabSheet.jsx:L259](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx#L259) |

---

### 4. Optimizer Panel Split — Three Sub-Panels

| Plan Item | Status | Evidence |
|:---|:---|:---|
| BacktestSweepPanel → dispatcher | ✅ Done | [BacktestSweepPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestSweepPanel.jsx) — 31 lines, lazy-loads 3 sub-panels with `Suspense` fallback |
| `TaOptimizerPanel.jsx` created | ✅ Done | [TaOptimizerPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/TaOptimizerPanel.jsx) — existing sweep logic extracted |
| `MlOptimizerPanel.jsx` created | ✅ Done | [MlOptimizerPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/MlOptimizerPanel.jsx) — 285 lines |
| `AgentOptimizerPanel.jsx` created | ✅ Done | [AgentOptimizerPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/AgentOptimizerPanel.jsx) — 109 lines |

#### MlOptimizerPanel Section-by-Section

| Planned Section | Status | Notes |
|:---|:---|:---|
| 1. Model Status Hero | ✅ Done | Shows model type badge, subtype label, symbol/strategy/days chip, `MlModelStatusBadge` component |
| 2. Hyperparameter Sweep Grid (ML-only fields) | ✅ Done | Delegates to `TaOptimizerPanel` with ML field set (TA indicators hidden via `getSweepEligibleFields`) |
| 3. ML-Specific Objective Functions | ✅ Done | `getMlObjectiveOptions()` adds `auc_roc`, `log_loss`, `alpha_decay_half_life`, `oos_is_ratio` |
| 4. Walk-Forward & Validation | ✅ Done | Inherited from TaOptimizerPanel with IS vs OOS gap warning |
| 5. Feature Importance Panel | ✅ Done | `FeatureImportanceChart` rendered when `ml_metrics.feature_importance` exists |
| 6. Confusion Matrix | ✅ Done | `ConfusionMatrixGrid` rendered when `ml_metrics.confusion_matrix` exists |
| 7. RL Action Distribution (Phase 1) | ✅ Done | Action counts (long/short/flat) displayed inline |
| 7b. RL Episode Replay (Phase 2) | ✅ Stub done | `RlEpisodeReplay` component exists and is imported; shows "Coming soon" when data empty |
| 8. Alpha Decay Monitor | ✅ Done | `AlphaDecayMonitor` component renders rolling Sharpe + half-life stat |
| 9. Trial Leaderboard | ✅ Done | Inherited from TaOptimizerPanel |
| 10. Deploy with Model Version Pinning | ✅ Done | `ModelPinSlot` component — artifact name, version dropdown, pin checkbox, `getDeployExtras()` callback |
| Link to Model Training dock tab | ✅ Done | "Model Training" button dispatches `dock-tab` event to `'ml-training'` |

#### AgentOptimizerPanel Section-by-Section

| Planned Section | Status | Notes |
|:---|:---|:---|
| 1. Agent Config Hero | ✅ Done | Shows agent type, LLM availability badge, current thresholds summary, signal counts |
| 2. Agent-Specific Sweep Grid | ✅ Done | Delegates to `TaOptimizerPanel` with agent field set |
| 3. Signal Gate Funnel | ✅ Done | `SignalGateFunnel` rendered from `agent_metrics.gate_funnel` |
| 4. Confidence Calibration Chart | ✅ Done | `ConfidenceCalibrationChart` rendered from `agent_metrics.confidence_calibration` |
| 5. Regime Performance Matrix | ✅ Done | `StatCard` grid from `agent_metrics.regime_performance` |
| 6. Reasoning Quality Section | ⚠️ Partial | Reasoning panel exists via `BacktestReasoningPanel` (already in results), but **no dedicated reasoning quality metrics** (length distribution, confidence vs outcome scatter) in the optimizer |
| 7. Walk-Forward & Validation | ✅ Done | Inherited from TaOptimizerPanel |
| 8. Trial Leaderboard | ✅ Done | Inherited from TaOptimizerPanel |
| 9. Deploy Section | ✅ Done | Inherited from TaOptimizerPanel |

---

### 5. Results Panel — Category-Aware Sections

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `strategyCategory` prop accepted | ✅ Done | [BacktestResultsPanel.jsx:L511](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx#L511) |
| `resolvedCategory` derived | ✅ Done | [L535–538](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx#L535-L538) — falls back to `getStrategyCategory()` |
| `BacktestMlInsightsSection` (ML blocks) | ✅ Done | [BacktestMlInsightsSection.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestMlInsightsSection.jsx) — 183 lines, includes Accuracy, AUC-ROC, Precision, Recall, F1 stat cards; IS vs OOS comparison; Alpha Decay Monitor; Feature Importance; Confusion Matrix; Confidence Distribution histogram; RL Action Distribution bars; RL Episode Replay |
| `BacktestAgentInsightsSection` (Agent blocks) | ✅ Done | [BacktestAgentInsightsSection.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestAgentInsightsSection.jsx) — 106 lines, includes Agent Decision Breakdown cards, Signal Gate Funnel, Confidence Calibration chart, Regime Performance cards |
| Conditional rendering in results | ✅ Done | ML section at [L940–941](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx#L940-L941), Agent section at [L946–948](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx#L946-L948), plus compact versions at L1255–1263 |
| Hide StrategySuggestPanel for ML | ✅ Done | `showAdvisor = !isMlCategory` at L539 |

---

### 6. Optimizer Defaults — Category-Aware

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `getDefaultObjective(strategy)` | ✅ Done | [optimizerDefaults.js:L39–43](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L39-L43) — returns `robust_score` for ML |
| `getDefaultMinTrades(strategy)` | ✅ Done | [L49–54](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L49-L54) — ML=5, agent=3, TA=1 |
| `defaultSweepEnabled(strategy)` | ✅ Done | [L59–82](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L59-L82) — uses `getStrategyCategory` for fallback keys |
| `getMlObjectiveOptions()` | ✅ Done | [L92–101](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L92-L101) — adds auc_roc, log_loss, alpha_decay_half_life, oos_is_ratio |
| `getMlSubtypeSweepHint()` | ✅ Done | [L103–108](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L103-L108) |
| Per-strategy sweep defaults for all ML/RL strategies | ✅ Done | [L21–27](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js#L21-L27) |

---

### 7. New Shared Visualization Components

| Component | Status | File | Lines |
|:---|:---|:---|:---|
| `FeatureImportanceChart` | ✅ Done | [FeatureImportanceChart.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/FeatureImportanceChart.jsx) | 76 lines — horizontal bars, category colors, compact mode, empty state |
| `ConfusionMatrixGrid` | ✅ Done | [ConfusionMatrixGrid.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/ConfusionMatrixGrid.jsx) | 132 lines — 3×3 grid, intensity coloring, per-class P/R/F1, accuracy badge, exported `computeConfusionStats` |
| `ConfidenceCalibrationChart` | ✅ Done | [ConfidenceCalibrationChart.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/ConfidenceCalibrationChart.jsx) | 108 lines — SVG scatter chart, diagonal reference, over/underconfident coloring, bucket size via circle radius |
| `SignalGateFunnel` | ✅ Done | [SignalGateFunnel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/SignalGateFunnel.jsx) | 62 lines — horizontal bars, pass/reject segments, conversion rate badge |

**Bonus components not in original plan (implemented anyway):**

| Component | File | Purpose |
|:---|:---|:---|
| `MlModelStatusBadge` | [MlModelStatusBadge.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/MlModelStatusBadge.jsx) | Compact model trained/untrained badge with API fetch |
| `AlphaDecayMonitor` | [AlphaDecayMonitor.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/AlphaDecayMonitor.jsx) | Rolling Sharpe line chart + half-life stat card |
| `RlEpisodeReplay` | [RlEpisodeReplay.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/RlEpisodeReplay.jsx) | Phase 1 stub — position trajectory + reward accumulation sparkline; full replay deferred |

---

### 8. Model Training Dashboard — Dock Tab

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `ModelTrainingDashboard.jsx` created | ✅ Done | [ModelTrainingDashboard.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/dock/ModelTrainingDashboard.jsx) — **585 lines** (far exceeds Phase 1 "stub") |
| Registered in ResizableDock | ✅ Done | Lazy import at L49, tab ID `'ml-training'` at L99, tab config at L306, content at L672 |
| Model inventory list | ✅ Done | Queries `/api/v1/ml/model-status` for all 7 ML strategies per symbol |
| "Trigger Retrain" button | ✅ Done | Posts to `/api/v1/ml/train` with strategy/symbol/training_window |
| Training window selector | ✅ Done | 1/3/6/12 month options |
| Status indicator | ✅ Done | Training / Validating / Ready / Failed / Idle with Loader2 animation |
| Last training date + validation metrics | ✅ Done | `MetricChips` component shows val_accuracy, auc_roc, sharpe, pbo etc. |

**Beyond plan scope (bonus implementations):**
- Walk-forward + PBO validation button (posts to `/api/v1/ml/validate`)
- Training loss curve chart (`LossHistoryChart`)
- Dataset browser with label distribution, feature list, schema version
- Model version history list
- Retrain queue display
- Auto-sync with currently selected bot strategy

> [!TIP]
> The plan called for a "Phase 1 stub" but the actual implementation is a **fully functional dashboard** with training, validation, dataset browsing, and version management. This is ahead of schedule.

---

### 9. Store Integration

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `ml_metrics` in backtest results shape | ✅ Wired | ML components read `results?.ml_metrics` and gracefully show empty state when not present |
| `rl_data` in backtest results shape | ✅ Wired | RL components read `results?.rl_data` with fallback |
| `agent_metrics` in backtest results shape | ✅ Wired | Agent components read `results?.agent_metrics` with fallback to `summary.filter_rejects_total` |
| No new store atoms needed (category is derived) | ✅ Confirmed | `getStrategyCategory()` used at render time; no `activeStrategyCategory` atom |

> [!NOTE]
> The store doesn't explicitly define `ml_metrics` / `rl_data` / `agent_metrics` shape in a schema — it just passes through whatever the backend sends. This is fine for a JS/Zustand store; the components handle missing fields gracefully.

---

### 10. Backend — RL Observation Emission

| Plan Item | Status | Notes |
|:---|:---|:---|
| Backend data contract defined | ✅ Plan documented | The plan defines the expected payload shape |
| Backend Python implementation | 🔲 **Not started** | This is backend work — the frontend is ready to consume it |

---

### CSS Styles

| Plan Item | Status | Evidence |
|:---|:---|:---|
| `.optimizer-panel__*` styles | ✅ Done | [index.css:L5938–5958](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L5938-L5958) |
| `.feature-importance__*` styles | ✅ Done | [index.css:L5960–6018](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L5960-L6018) — including category color variants |
| `.confusion-matrix__*` styles | ✅ Done | [index.css:L6025–6095](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L6025-L6095) — grid, intensity, metrics |
| `.calib-chart__*` styles | ✅ Done | [index.css:L6097–6137](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L6097-L6137) — SVG, diagonal, points |
| `.gate-funnel__*` styles | ✅ Done | [index.css:L6139–6188](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L6139-L6188) — bars, pass/reject colors |
| `.ml-insights__*` styles | ✅ Done | [index.css:L6190+](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/index.css#L6190) — viz grid, histogram, action bars |
| `.agent-insights__*` styles | ✅ Done | Same block — shared grid styles |
| `.ml-training__*` styles | ✅ Done | Confirmed in CSS — inventory, metrics, loss chart, dataset styles |

---

## Gap Analysis

### What's Missing / Needs Attention

| # | Gap | Severity | Phase | Action Required |
|:---|:---|:---|:---|:---|
| 1 | **Backend ML metrics pipeline** — The backend doesn't yet emit `ml_metrics`, `rl_data`, or `agent_metrics` in backtest results | 🔴 High | Phase 3 | Implement in Python backtester to populate these fields |
| 2 | **Backend RL per-step observations** — `rl_data.episode_steps` not emitted | 🟡 Medium | Phase 3 | Backend must record obs/action/reward per step during RL backtest |
| 3 | **Backend ML API endpoints** — `/api/v1/ml/model-status`, `/api/v1/ml/train`, `/api/v1/ml/validate`, `/api/v1/ml/retrain-status` — frontend calls these but backend likely returns 404 | 🔴 High | Phase 3 | Implement backend REST endpoints |
| 4 | **RL Episode Replay full viewer** — The `RlEpisodeReplay` component exists but is a stub showing sparklines; the full per-step observation inspector is not built | 🟢 Low | Phase 4 | Build when `episode_steps` data is available from backend |
| 5 | **Reasoning Quality Section** in AgentOptimizer — Plan called for reasoning length distribution + confidence vs outcome scatter plot; not implemented | 🟢 Low | Phase 4 | Add once reasoning data is richer |
| 6 | **Agent optimizer custom trial leaderboard columns** — Plan called for `Confidence Gate Blocks`, `Score Gate Blocks`, `Regime Blocks` columns; current leaderboard inherits TA columns | 🟢 Low | Phase 4 | Extend TaOptimizerPanel leaderboard to accept custom columns |
| 7 | **ML optimizer custom trial leaderboard columns** — Plan called for `AUC`, `OOS/IS Ratio`, `Feature Count` columns | 🟢 Low | Phase 4 | Same as above |

### What's Been Built Beyond Plan Scope

| # | Extra Implementation | Value |
|:---|:---|:---|
| 1 | Full Model Training Dashboard (585 lines vs planned "stub") with training curves, dataset browser, version management, retrain queue, validation | 🟢 High — ready for immediate use once backend endpoints exist |
| 2 | `AlphaDecayMonitor` dedicated component | 🟢 Useful — reusable across optimizer and results panels |
| 3 | `MlModelStatusBadge` component | 🟢 Nice UX — compact badge showing model status anywhere |
| 4 | `mlVizStats.test.js` — unit tests for ML visualization helpers | 🟢 Quality assurance |
| 5 | Graceful degradation throughout — all ML/Agent sections show helpful empty states when data is missing | 🟢 Excellent UX — users aren't confused when backend data isn't populated yet |

---

## Conclusion

The frontend implementation faithfully follows the plan with all 3 design decisions correctly applied:

1. ✅ **Training controls in separate dock tab** — `ModelTrainingDashboard` is a full dock tab, not embedded in the optimizer
2. ✅ **RL per-step backend prioritized, replay UI deferred** — `RlEpisodeReplay` exists as a stub; action distribution/position trajectory available as Phase 1 charts
3. ✅ **Category inferred from `STRATEGY_CATALOG`** — `getStrategyCategory()` used everywhere, no store state lifted

**Next priority**: Implement the backend Python endpoints (`/api/v1/ml/model-status`, `/api/v1/ml/train`, `/api/v1/ml/validate`) and populate `ml_metrics` / `rl_data` / `agent_metrics` in backtest results to bring the frontend to life with real data.
