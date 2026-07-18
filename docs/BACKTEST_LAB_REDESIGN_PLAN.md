# Tab-Specific Backtest / Optimizer Lab Redesign

> **Status**: Complete (Phases 1–4 + follow-ups). Category-aware Optimizer Lab, Results visualizations, Model Training dock, ML objectives / model pin / versioned artifacts, RL episode replay, and GNN train API are shipped. See [BACKTEST_LAB_VERIFICATION_CHECKLIST.md](./BACKTEST_LAB_VERIFICATION_CHECKLIST.md) for the browser walkthrough.

Redesign the Backtest Lab (Results, Optimizer, Jobs) and the Algo deploy panel so that each bot category tab — **Normal**, **ML / AI**, and **Agentic** — surfaces purpose-built features, parameters, and visualizations instead of the current one-size-fits-all layout.

---

## Background & Problem

Today, [BacktestLabSheet.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx) renders three content panels — **Results** (`BacktestResultsPanel`), **Optimizer** (`BacktestSweepPanel`), and **Jobs** (`BacktestJobHistory`) — identically regardless of whether the selected strategy is a traditional TA indicator bot (MACD_RSI, SUPERTREND_ADX …), an ML/DL/RL bot (ML_SIGNAL_BOOST, LSTM_DIRECTION, RL_PPO_AGENT …), or an agentic bot (CHART_AGENT, ABSORPTION_AGENT).

The Optimizer panel ([BacktestSweepPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestSweepPanel.jsx)) fetches sweep-eligible parameters from [botConfigDisplay.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js) which only defines TA indicator fields (RSI period, MACD fast/slow, Bollinger σ, etc.) and generic risk fields. ML/RL strategies have radically different parameter spaces, and agentic bots need entirely different optimization surfaces.

The strategy catalog ([strategies.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.js)) already classifies strategies by `style` (`'ml'`, `'agent'`, or TA styles). The [AlgoPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/dock/AlgoPanel.jsx) already has a `botCategoryTab` state (`'normal'` | `'ml'` | `'agentic'`) that filters strategy templates. We need to propagate this category awareness into the Backtest Lab itself.

---

## Resolved Design Decisions

### Decision 1: Training Controls → Separate Dock Tab

Model training controls (trigger retrain, select training window, view training curves, monitor convergence) will live in a **new "Model Training Dashboard" dock tab**, not inside the ML Optimizer Lab. The ML Optimizer panel will focus exclusively on **hyperparameter sweep + validation** — keeping it parallel to the TA Optimizer's scope. The Training Dashboard is a distinct concern (long-running GPU jobs, dataset management, model versioning) that warrants its own dedicated workspace.

### Decision 2: RL Episode Replay — shipped

The backend emits per-step RL observations during backtest (`rl_data.episode_steps`, plus action distribution / position trajectory). The frontend **Episode Replay** scrubber (`RlEpisodeReplay.jsx`) is shipped in Results and the ML optimizer footer — not a placeholder.

### Decision 3: Category Inference via `STRATEGY_CATALOG`

The strategy category will be **inferred from `STRATEGY_CATALOG[strategy].style`** rather than lifting `botCategoryTab` to the Zustand store. Rationale:

- **Pure derived state** — no manual sync needed; changes automatically when `botStrategy` changes
- **Single source of truth** — the catalog already encodes the classification
- **BacktestLabSheet already has `botStrategy`** in scope (via `useStore` and `backtestResults.meta.strategy`)
- **No store pollution** — avoids adding redundant state that must stay in sync with AlgoPanel's UI tab

The `getStrategyCategory(strategy)` helper function (added to `strategies.js`) handles the derivation.

---

## Research Summary

Based on online research into best practices for each bot type's backtesting/optimization features:

### What's Essential for Each Tab

| Feature Area | Normal (TA) Bots | ML / AI Bots | Agentic Bots |
|:---|:---|:---|:---|
| **Parameter Grid** | Indicator periods, thresholds (RSI, MACD, BB) | Hyperparameters: lookback window, confidence threshold, feature set selection | Agent guardrails: min_confidence, min_score, confirm_timeframe, calibration gate |
| **Optimization Modes** | Grid, Random, LHS, Bayesian sweep (already built) | Walk-forward retraining windows, cross-validation fold config, feature importance ranking | Signal gate tuning, regime routing thresholds, calibration bucket optimization |
| **Key Metrics** | Sharpe, Max DD, Win Rate, Profit Factor, Calmar | **+ Overfitting score (PBO)**, alpha decay rate, IS vs OOS performance gap, confusion matrix, AUC-ROC | **+ Agent reasoning quality**, confidence calibration, signal rejection rate analysis |
| **Unique Visualizations** | Equity curve, parameter heatmap, trade log | Feature importance bar chart, IS/OOS performance decay chart, confusion matrix, action distribution | Decision reasoning log, confidence calibration plot, filter rejection funnel |
| **Validation** | Walk-forward, PBO/CSCV audit | **Purged k-fold CV**, walk-forward retraining, triple-barrier label quality, alpha decay monitoring | Live parity check, calibration gate accuracy, meta-label shadow analysis |
| **Deploy Flow** | Apply best config → deploy | Apply best config **+ model artifact version** → deploy with model pinning | Apply tuned thresholds → deploy with LLM config |
| **Training Controls** | N/A | **Separate dock tab** (Model Training Dashboard) | N/A |

---

## Proposed Changes

### 1. Strategy Category Detection Layer

#### [MODIFY] [strategies.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.js)

Add `getStrategyCategory()` and `getMLSubtype()` helpers that derive category from the existing `style` field. This is the **single source of truth** for category — consumed by BacktestLabSheet, BacktestSweepPanel, and BacktestResultsPanel.

```js
/**
 * Derive strategy category from STRATEGY_CATALOG style.
 * @param {string} strategy
 * @returns {'normal' | 'ml' | 'agent'}
 */
export function getStrategyCategory(strategy) {
  const meta = getStrategyMeta(strategy);
  if (meta.style === 'ml') return 'ml';
  if (meta.style === 'agent') return 'agent';
  return 'normal';
}

/**
 * For ML strategies, get the sub-type for conditional UI sections.
 * @param {string} strategy
 * @returns {'supervised' | 'rl' | 'unsupervised'}
 */
export function getMLSubtype(strategy) {
  const key = String(strategy).toUpperCase();
  if (key === 'RL_PPO_AGENT') return 'rl';
  if (key === 'VAE_REGIME_DETECTOR') return 'unsupervised';
  return 'supervised'; // XGBoost, LSTM, TCN, Transformer, GNN
}
```

---

### 2. ML/RL-Specific Parameter Definitions

#### [MODIFY] [botConfigDisplay.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js)

Add new `FIELD_META` entries for ML/RL strategy parameters and update `getEditableConfigFields` + `getSweepEligibleFields` to return category-appropriate fields.

**New `GROUP_ORDER` and `GROUP_LABELS` additions:**

```js
export const GROUP_ORDER = [
  'risk', 'agent', 'indicators', 'tick',
  'ml_model', 'rl_policy',   // ← new
  'agent_gate', 'agent_llm', // ← new (refine agent groups)
  'other',
];

export const GROUP_LABELS = {
  ...existing,
  ml_model: 'ML model',
  rl_policy: 'RL policy',
  agent_gate: 'Agent gates',
  agent_llm: 'LLM settings',
};
```

**New ML fields:**

| Field Key | Label | Group | Kind | Purpose |
|:---|:---|:---|:---|:---|
| `lookback_bars` | Lookback window | `ml_model` | `integer` | Sliding input window size (e.g., 60 bars) |
| `confidence_threshold` | Signal threshold | `ml_model` | `confidence` | Min probability to emit BUY/SELL (e.g., 0.6) |
| `feature_set` | Feature set | `ml_model` | `select` | Options: `basic`, `extended`, `orderbook`, `all` |
| `min_return_threshold` | Min return % | `ml_model` | `decimal` | TCN: minimum forecast magnitude to fire signal |
| `horizon_agreement` | Horizon agreement | `ml_model` | `integer` | TCN: require N-of-M horizons to agree on direction |

**New RL fields:**

| Field Key | Label | Group | Kind | Purpose |
|:---|:---|:---|:---|:---|
| `position_threshold` | Position threshold | `rl_policy` | `decimal` | Action magnitude to trigger entry (e.g., 0.3) |
| `reward_function` | Reward function | `rl_policy` | `select` | Options: `sharpe`, `pnl_minus_dd`, `calmar` |
| `discount_factor` | Discount factor | `rl_policy` | `decimal` | Gamma for reward discounting (0–1) |
| `transaction_cost_penalty` | Txn cost penalty | `rl_policy` | `decimal` | Weight of transaction costs in reward |

**New Agent LLM fields (extending existing agent group):**

| Field Key | Label | Group | Kind | Purpose |
|:---|:---|:---|:---|:---|
| `llm_temperature` | LLM temperature | `agent_llm` | `decimal` | Reasoning temperature (0.0–1.0) |
| `max_reasoning_tokens` | Max reasoning tokens | `agent_llm` | `integer` | Cap LLM response length |
| `require_multi_domain` | Multi-domain confirm | `agent_gate` | `integer` | Require ≥ N sub-report domains to agree |

**Updated `getSweepEligibleFields(strategy, config)` logic:**

```js
export function getSweepEligibleFields(strategy, config) {
  const category = getStrategyCategory(strategy);

  if (category === 'ml') {
    // Return ML/RL-specific fields + shared risk fields
    // Hide all TA indicator fields (RSI period, MACD fast/slow, etc.)
    return [...ML_FIELDS, ...SHARED_RISK_FIELDS];
  }
  if (category === 'agent') {
    // Return agent gate/config fields + shared risk fields
    // Hide TA indicator internals
    return [...AGENT_FIELDS, ...SHARED_RISK_FIELDS];
  }
  // Normal: existing behavior — all TA indicator fields + risk
  return existingLogic(strategy, config);
}
```

---

### 3. Backtest Lab — Category-Aware Tab Rendering

#### [MODIFY] [BacktestLabSheet.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx)

- Derive `strategyCategory` from the strategy using `getStrategyCategory()`:
  ```js
  const strategyCategory = useMemo(
    () => getStrategyCategory(backtestResults?.meta?.strategy ?? botStrategy),
    [backtestResults?.meta?.strategy, botStrategy],
  );
  ```
- Pass `strategyCategory` down to `BacktestResultsPanel`, `BacktestSweepPanel`, and `BacktestJobHistory`.
- Update the sheet description dynamically:
  - Normal: *"Strategy replay report — equity, trades, optimizer, and run history"*
  - ML: *"ML model backtest — predictions, feature importance, walk-forward validation"*
  - Agent: *"Agent backtest — reasoning analysis, gate tuning, confidence calibration"*

---

### 4. Optimizer Panel Split — Three Specialized Sub-Panels

#### [MODIFY] [BacktestSweepPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestSweepPanel.jsx)

Convert the monolithic sweep panel into a **dispatcher** that renders a category-specific sub-panel:

```jsx
import { getStrategyCategory } from '../config/strategies';
import { lazyImport } from '../lib/lazyImport';

const TaOptimizerPanel = lazyImport(() => import('./TaOptimizerPanel'), 'ta-optimizer');
const MlOptimizerPanel = lazyImport(() => import('./MlOptimizerPanel'), 'ml-optimizer');
const AgentOptimizerPanel = lazyImport(() => import('./AgentOptimizerPanel'), 'agent-optimizer');

export default function BacktestSweepPanel({ strategyCategory, ...props }) {
  const category = strategyCategory ?? getStrategyCategory(props.strategy);

  if (category === 'ml')    return <MlOptimizerPanel {...props} />;
  if (category === 'agent') return <AgentOptimizerPanel {...props} />;
  return <TaOptimizerPanel {...props} />;
}
```

---

#### [NEW] `TaOptimizerPanel.jsx`

Refactor the current `BacktestSweepPanel` content into this file — essentially a **rename + cleanup**. It retains all existing functionality:

- TA indicator parameter sweep grid (RSI, MACD, Bollinger, SuperTrend, etc.)
- Grid / Random / LHS / Bayesian sweep modes
- Walk-forward & validation section (full featured)
- Optimizer heatmap
- Trial leaderboard
- Optimization history
- Deploy section (Apply best config / Deploy optimized)

No behavior changes — pure extraction.

---

#### [NEW] `MlOptimizerPanel.jsx`

Purpose-built optimizer for ML/DL/RL strategies. **Does NOT include training controls** — those live in the separate Model Training Dashboard dock tab.

**Layout sections (in order):**

1. **Model Status Hero**
   - Model type badge (XGBoost / LSTM / PPO / TCN / Transformer / VAE / GNN)
   - Last training date, model version, validation score
   - Quick link: "Open Model Training Dashboard →" (navigates to new dock tab)

2. **Hyperparameter Sweep Grid** — ML-relevant parameters only:
   - **Supervised ML**: `lookback_bars`, `confidence_threshold`, `feature_set`, `min_return_threshold`, `horizon_agreement`
   - **RL**: `position_threshold`, `reward_function`, `discount_factor`, `transaction_cost_penalty`
   - **Shared**: `trailing_stop_percent`, `stop_loss_percent`, `take_profit_percent`, `direction_mode`
   - **Hides**: All TA indicator fields (RSI period, MACD fast/slow, Bollinger σ, etc.)

3. **ML-Specific Objective Functions** — Additional sweep objectives beyond the standard set:
   - `auc_roc` — AUC-ROC score (classification models)
   - `log_loss` — Log loss (probability calibration quality)
   - `alpha_decay_half_life` — Model edge longevity
   - `oos_is_ratio` — OOS/IS performance ratio (closer to 1.0 = less overfit)

4. **Walk-Forward & Validation** — Focused on ML validation concerns:
   - Walk-forward mode (rolling / anchored)
   - Purged k-fold cross-validation toggle
   - **IS vs OOS Gap Warning**: Prominent alert when IS >> OOS (overfitting detector)
   - PBO/CSCV audit toggle

5. **Feature Importance Panel** — Post-sweep visualization:
   - `FeatureImportanceChart` component (horizontal bar chart)
   - Top-N features ranked by importance (SHAP values for XGBoost, permutation importance for neural nets)
   - Color-coded by feature category (price, volume, indicator, sentiment)

6. **Confusion Matrix** — For classification models (ML_SIGNAL_BOOST, LSTM_DIRECTION, TCN, Transformer):
   - `ConfusionMatrixGrid` component (3×3 grid: BUY/SELL/NONE)
   - Precision / Recall / F1 per class displayed below the grid

7. **RL Action Distribution** — Conditional on `getMLSubtype() === 'rl'`:
   - Action distribution histogram (how often the agent goes long vs short vs flat)
   - Position trajectory sparkline (agent's position over time during backtest)
   - Reward accumulation mini-chart
   - Full episode replay scrubber (`RlEpisodeReplay`) when `rl_data.episode_steps` is present

8. **Alpha Decay Monitor**:
   - Rolling Sharpe ratio line chart over time
   - Highlight zones where the model's edge is decaying
   - Half-life metric displayed as a stat card

9. **Trial Leaderboard** — Same table as TA, but with ML-specific columns:
   - Config, PnL/Sharpe (objective), Trades, Win%, **AUC**, **OOS/IS Ratio**, **Feature Count**

10. **Deploy Section**:
    - "Apply best config" button
    - **Model version selector** — dropdown to pin which `.onnx` / `.pt` / `.json` model artifact to deploy with
    - "Deploy optimized" with model version confirmation

---

#### [NEW] `AgentOptimizerPanel.jsx`

Purpose-built optimizer for agentic (LLM-hybrid) bots.

**Layout sections:**

1. **Agent Config Hero**
   - Agent type badge (CHART_AGENT / ABSORPTION_AGENT)
   - LLM availability indicator
   - Current threshold summary: min_confidence, min_score, meta-label mode

2. **Agent-Specific Sweep Grid** — Agent-relevant parameters only:
   - **Signal gates**: `min_confidence`, `min_score`, `confirm_timeframe`
   - **Calibration**: `calibration_gate_enabled`, `calibration_min_samples`, `calibration_min_wilson`
   - **Meta-label**: `meta_label_model_mode`, `meta_label_min_prob`, `meta_label_min_train_samples`, `meta_label_shadow_mode`
   - **Regime routing**: `regime_routing_enabled`, `elevated_min_confidence`, `elevated_min_score`, `elevated_block_entries`, `compressed_min_confidence`
   - **Sizing**: `use_vol_sizing`, `use_confidence_sizing`, `use_meta_label_sizing`
   - **Shared risk**: `trailing_stop_percent`, `stop_loss_percent`, `take_profit_percent`, `direction_mode`
   - **LLM** (if available): `llm_temperature`, `max_reasoning_tokens`
   - **Hides**: RSI period, MACD fast/slow, Bollinger σ, etc. (irrelevant for agents)

3. **Signal Gate Funnel Visualization** — `SignalGateFunnel` component:
   - Horizontal waterfall: Raw Signals → Confidence Filter → Score Filter → Trend Gate → Regime Gate → Calibration Gate → Meta-Label Gate → **Executed Trades**
   - Each stage shows count passed + count rejected
   - Color intensity maps to rejection severity

4. **Confidence Calibration Chart** — `ConfidenceCalibrationChart` component:
   - X-axis: predicted confidence buckets (0.5–0.6, 0.6–0.7, …, 0.9–1.0)
   - Y-axis: actual win rate in each bucket
   - Perfect calibration = diagonal reference line
   - Helps tune `min_confidence` threshold

5. **Regime Performance Matrix** — Table:
   - Rows: Normal / Elevated / Compressed volatility regimes
   - Columns: Win Rate, Avg PnL, Trade Count, Sharpe, Signals Blocked
   - Highlights the regime where the strategy performs best
   - Informs `regime_routing_enabled` and `elevated_block_entries` settings

6. **Reasoning Quality Section** — Visible when LLM is available:
   - Sample reasoning excerpts from backtest trades (top 3 wins, top 3 losses)
   - Reasoning length distribution histogram
   - Confidence vs actual outcome scatter plot

7. **Walk-Forward & Validation** — Simplified for agents:
   - Rolling WF mode only (agents rarely need anchored)
   - PBO audit toggle
   - Regime-specific optimization dropdown
   - Auto-deploy on OOS pass

8. **Trial Leaderboard** — Tailored columns:
   - Config, Objective, Trades, Win%, **Confidence Gate Blocks**, **Score Gate Blocks**, **Regime Blocks**

9. **Deploy Section**:
   - "Apply best config" with LLM settings preview
   - "Deploy optimized" with agent config confirmation

---

### 5. Results Panel — Category-Aware Sections

#### [MODIFY] [BacktestResultsPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx)

Add `strategyCategory` prop and render conditional sections:

**For ML strategies (`category === 'ml'`), add:**
- **Model Prediction Quality** stat card row: Accuracy, AUC-ROC, Precision, Recall, F1
- **IS vs OOS Performance Comparison** — split stat cards showing in-sample vs out-of-sample Sharpe/PnL/Win%
- **Feature Importance Summary** — compact horizontal bar chart (top 5 features)
- **Confidence Distribution** — histogram of signal confidence levels
- **RL Action Distribution** — (if RL subtype) histogram of agent actions
- **Hide**: `StrategySuggestPanel` (irrelevant — ML bots don't switch strategies)

**For Agent strategies (`category === 'agent'`), add:**
- **Agent Decision Breakdown** stat card row: Signals Generated, Filtered Out, Executed, Success Rate
- **Confidence Calibration Mini-Chart** — inline compact version
- **Regime Performance Summary** — compact 3-column cards (Normal / Elevated / Compressed)
- **Reasoning Panel** — promote `BacktestReasoningPanel` to be more prominent (move higher in layout)

**For TA strategies (`category === 'normal'`):**
- **No changes** — keep current layout exactly as-is

---

### 6. Optimizer Defaults — Category-Aware

#### [MODIFY] [optimizerDefaults.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js)

Update `defaultSweepEnabled()` and default objectives to be category-aware:

```js
import { getStrategyCategory } from '../config/strategies';

export function getDefaultObjective(strategy) {
  const category = getStrategyCategory(strategy);
  if (category === 'ml') return 'robust_score';   // Sharpe × √trades — penalizes overfit
  if (category === 'agent') return 'calmar_ratio'; // unchanged
  return 'calmar_ratio';                           // TA default unchanged
}

export function getDefaultMinTrades(strategy) {
  const category = getStrategyCategory(strategy);
  if (category === 'ml') return 5;    // ML models need more trades to be meaningful
  if (category === 'agent') return 3; // agents are more selective, fewer trades expected
  return 1;                           // TA default unchanged
}
```

---

### 7. New Shared Visualization Components

All four components use the existing terminal design system (CSS classes, color tokens, font sizes).

#### [NEW] `FeatureImportanceChart.jsx`

Horizontal bar chart displaying feature importance rankings from sweep/training results.

```ts
interface Props {
  features: Array<{ name: string; importance: number; category: 'price' | 'volume' | 'indicator' | 'sentiment' }>;
  maxBars?: number;  // default 10
}
```

- Uses CSS custom properties for bar colors (`--color-feature-price`, `--color-feature-volume`, etc.)
- Sorted descending by importance
- Compact mode for inline use in Results panel

#### [NEW] `ConfusionMatrixGrid.jsx`

3×3 CSS grid showing classification confusion matrix with color intensity mapping.

```ts
interface Props {
  matrix: number[][];  // 3×3 array: rows = actual, cols = predicted [BUY, SELL, NONE]
  labels?: string[];   // default ['BUY', 'SELL', 'NONE']
}
```

- Color intensity proportional to cell value (green diagonal = correct, red off-diagonal = errors)
- Per-class Precision/Recall/F1 displayed below
- Overall Accuracy as a stat badge

#### [NEW] `ConfidenceCalibrationChart.jsx`

Scatter/line chart comparing predicted confidence buckets to actual win rates.

```ts
interface Props {
  calibration: Array<{ bucket: string; predicted: number; actual: number; count: number }>;
}
```

- Diagonal reference line (perfect calibration)
- Bucket size annotations
- Above-diagonal = overconfident; below-diagonal = underconfident

#### [NEW] `SignalGateFunnel.jsx`

Horizontal funnel/waterfall visualization showing signal rejection at each pipeline stage.

```ts
interface Props {
  stages: Array<{ name: string; passed: number; rejected: number }>;
}
```

- Horizontal bar segments with pass (green) and reject (red) portions
- Stacked or waterfall layout
- Total conversion rate displayed at the end

---

### 8. Model Training Dashboard — Dock Tab

#### [NEW] `ModelTrainingDashboard.jsx`

Dock tab (registered in [ResizableDock.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/ResizableDock.jsx)) for model training:

- Model inventory list (trained / not trained per ML strategy + symbol)
- Trigger retrain + walk-forward / PBO validation
- Training window selector
- Status, metrics chips, loss history chart
- Dataset & versions browser (sample counts, labels, on-disk version index)

---

### 9. Store Integration

#### [MODIFY] [useResearchStore.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/store/useResearchStore.js)

Ensure `backtestResults` can carry ML-specific result fields from the backend:

```js
// New fields in backtestResults shape:
{
  ...existingFields,

  // ML-specific (populated when strategy category is 'ml')
  ml_metrics: {
    accuracy: number,
    auc_roc: number,
    precision: { BUY: number, SELL: number, NONE: number },
    recall: { BUY: number, SELL: number, NONE: number },
    f1: { BUY: number, SELL: number, NONE: number },
    confusion_matrix: number[][],        // 3×3
    feature_importance: Array<{ name, importance, category }>,
    confidence_distribution: Array<{ bucket, count }>,
    alpha_decay: { half_life_days, rolling_sharpe: number[] },
    is_vs_oos: { is_sharpe, oos_sharpe, is_pnl, oos_pnl },
  },

  // RL-specific (populated when strategy is RL_PPO_AGENT)
  rl_data: {
    action_distribution: { long: number, short: number, flat: number },
    position_trajectory: number[],       // position at each bar
    reward_accumulation: number[],       // cumulative reward
    episode_steps: Array<{               // full replay scrubber input
      bar_index, observation, action, reward, position, info
    }>,
  },

  // Agent-specific (populated when strategy category is 'agent')
  agent_metrics: {
    signals_generated: number,
    signals_filtered: number,
    signals_executed: number,
    gate_funnel: Array<{ name, passed, rejected }>,
    confidence_calibration: Array<{ bucket, predicted, actual, count }>,
    regime_performance: Array<{ regime, win_rate, avg_pnl, trades, sharpe }>,
  },
}
```

No new Zustand state atoms needed — `strategyCategory` is derived via `getStrategyCategory()` at render time.

---

### 10. Backend — RL Observation Emission (Data Pipeline)

#### [MODIFY] Backend backtester (Python) — **Data contract only**

> [!IMPORTANT]
> This section defines the **data contract** the frontend expects. The actual backend implementation is a separate task, but the RL backtest runner should be designed to emit these fields from the start.

When running a backtest for an RL strategy (`RL_PPO_AGENT`), the backtester should include in the result payload:

```python
# In backtest result dict:
{
    "rl_data": {
        "action_distribution": {"long": 142, "short": 98, "flat": 760},
        "position_trajectory": [0, 0, 0.3, 0.5, 0.5, -0.2, ...],  # per-bar
        "reward_accumulation": [0, 0.01, 0.02, 0.018, ...],         # cumulative
        "episode_steps": [  # replay scrubber (trimmed on the wire if large)
            {
                "bar_index": 0,
                "observation": [...],   # flattened obs vector
                "action": [0.3],        # agent output
                "reward": 0.01,
                "position": 0.3,
                "info": {}
            },
            ...
        ]
    }
}
```

Populate `action_distribution`, `position_trajectory`, `reward_accumulation`, and `episode_steps` for RL backtests.

---

## File Change Summary

| Action | File | Description |
|:---|:---|:---|
| MODIFY | [strategies.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/config/strategies.js) | Add `getStrategyCategory()`, `getMLSubtype()` helpers |
| MODIFY | [botConfigDisplay.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/botConfigDisplay.js) | Add ML/RL/Agent field definitions, update `getSweepEligibleFields` |
| MODIFY | [BacktestLabSheet.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestLabSheet.jsx) | Derive & pass `strategyCategory` to child panels |
| MODIFY | [BacktestSweepPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestSweepPanel.jsx) | Convert to dispatcher for category-specific sub-panels |
| NEW | `TaOptimizerPanel.jsx` | Extracted existing TA optimizer logic (rename + cleanup) |
| NEW | `MlOptimizerPanel.jsx` | ML/DL/RL optimizer with hyperparams, feature importance, confusion matrix |
| NEW | `AgentOptimizerPanel.jsx` | Agent optimizer with gate funnel, calibration, regime matrix |
| MODIFY | [BacktestResultsPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestResultsPanel.jsx) | Add conditional ML/Agent result sections |
| MODIFY | [optimizerDefaults.js](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/lib/optimizerDefaults.js) | Category-aware default objectives & sweep presets |
| NEW | `FeatureImportanceChart.jsx` | Shared bar chart for ML feature importance |
| NEW | `ConfusionMatrixGrid.jsx` | 3×3 classification confusion matrix |
| NEW | `ConfidenceCalibrationChart.jsx` | Confidence vs win-rate calibration chart |
| NEW | `SignalGateFunnel.jsx` | Signal rejection funnel visualization |
| NEW | `ModelTrainingDashboard.jsx` | Dock tab: train / validate / inventory / dataset & versions |
| MODIFY | `useResearchStore.js` | Carry ML/RL/Agent-specific result fields in backtest state |
| MODIFY | `index.css` | Styles for new panels and visualization components |

---

## Implementation Phases

### Phase 1 — Core Refactor ✅
- Strategy category detection (`strategies.js`)
- Field definitions for ML/RL/Agent (`botConfigDisplay.js`)
- Optimizer panel split: dispatcher + `TaOptimizerPanel` + `MlOptimizerPanel` + `AgentOptimizerPanel`
- `BacktestLabSheet` category awareness
- Model Training Dashboard dock tab

### Phase 2 — Visualization Components ✅
- `FeatureImportanceChart`, `ConfusionMatrixGrid`, `ConfidenceCalibrationChart`, `SignalGateFunnel`
- `BacktestResultsPanel` category-conditional sections

### Phase 3 — Backend Data Pipeline + RL ✅
- ML / RL / agent metrics in backtest results
- RL episode steps pipeline + action distribution charts
- Alpha decay monitor hooks

### Phase 4 — Polish + Episode Replay ✅
- Full RL episode replay viewer
- Model Training Dashboard (train, validate, loss curves, dataset & versions)
- Model pin + on-disk versioned artifacts
- Cross-tab / category unit tests
- Verification checklist: [BACKTEST_LAB_VERIFICATION_CHECKLIST.md](./BACKTEST_LAB_VERIFICATION_CHECKLIST.md)

---

## Verification Plan

### Automated Tests
- Frontend: `strategies.test.js`, `botConfigDisplay.test.js`, cross-category replay tests
- Backend: `test_ml_model_artifacts.py`, ML/RL strategy tests

### Manual Verification
Use [BACKTEST_LAB_VERIFICATION_CHECKLIST.md](./BACKTEST_LAB_VERIFICATION_CHECKLIST.md). Highlights:

1. **Normal (TA)** — Optimizer shows indicator params only; no model pin.
2. **ML** — ML hyperparameters + ML objectives; Results ML section; pin version dropdown.
3. **RL** — RL fields; action distribution + episode replay (not a stub).
4. **Agentic** — Agent params; gate funnel / calibration / reasoning.
5. **Model Training** — Inventory, retrain, dataset & versions after a successful train.
6. **Deploy gate** — Blocks missing ML models; warns on stale / mismatched pin.

### Visual Demo
- Optional: record a session switching tabs and running a short sweep per category.
