# Code Audit — Bugs Found & Fixed

Audited all files from the backtest & agent bot upgrade. Found and fixed **12 bugs** across 8 files.

---

## Critical Bugs Fixed

### 1. Risk Gate — Config JSON String Crash
**File:** [risk_gate.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/risk_gate.py)
**Severity:** 🔴 Critical (would crash in production)

Both `_check_max_drawdown()` and `_check_symbol_concentration()` called `.get()` on `bot["config"]`, but the config can be stored as a JSON **string** in the DB (not a dict). Calling `.get()` on a string throws `AttributeError`.

**Fix:** Added `_parse_bot_config()` static method that safely handles both dict and JSON string configs. Both methods now use it.

---

### 2. ML Explain — Wrong Model Lookup for Backtest Sessions
**File:** [meta_label_model.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/meta_label_model.py)
**Severity:** 🔴 Critical (silent failure during backtest)

`explain_prediction()` only checked `store._models` (persistent models) but not `_backtest_session_models`. During walk-forward backtests, the model is stored in the session cache, so `store._models.get(bot_id)` returns `None` and the function silently returns empty contributions.

**Fix:** Now checks `get_backtest_session_model(bot_id)` first, matching the same lookup order used by `predict_proba()`.

---

### 3. ML Refit — Stale Class Weight Ratio
**File:** [meta_label_model.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/meta_label_model.py)
**Severity:** 🟡 Medium (incorrect model training)

The production refit used `class_weight_ratio` computed from the **training split** (`y_train`), not the **full dataset** (`y`). The class distribution can differ between the 80% train split and the full 100% dataset.

**Fix:** Recompute `full_class_ratio` from the full `y` array before the production refit. Also now applies class balance on top of risk-adjusted weights when both are active.

---

### 4. Strategy SDK — Memory Leak in Long Backtests
**File:** [strategy_sdk.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/strategy_sdk.py)
**Severity:** 🟡 Medium (OOM in long backtests)

`StrategyV2Adapter._history` accumulated every bar without limit. In a 10,000+ bar backtest, this grows to hundreds of MB. While `BarWindow` clips to 250, the underlying list kept growing.

**Fix:** Cap `_history` at 250 bars by trimming when it exceeds 300 entries.

---

### 5. Custom Loader — Unhandled User Module Errors
**File:** [custom_loader.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/custom_loader.py)
**Severity:** 🟡 Medium (unhandled exception)

`spec.loader.exec_module(mod)` was called without try/except. If a user's strategy module has a syntax error, import error, or runtime error at module level, this would crash the bot manager's initialization.

**Fix:** Wrapped in try/except with error logging, returns `None` on failure.

---

## Performance Bugs Fixed

### 6. Fold Heatmap — O(N²) Recomputation
**File:** [BacktestWalkForwardPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestWalkForwardPanel.jsx)

`maxAbs` (max absolute PnL across all folds) was recomputed inside every fold's `.map()` iteration. For N folds, this was O(N²).

**Fix:** Precompute `maxAbs` once outside the loop using an IIFE.

---

### 7. Backtest Library — O(N² log N) Sort
**File:** [BacktestJobHistory.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestJobHistory.jsx)

`extractMetrics()` was called for every pair comparison in `.sort()`, making it O(N² log N) for N jobs.

**Fix:** Pre-compute metrics into a `Map` before sorting, reducing to O(N log N).

---

## Minor Fixes

### 8. Unused Import — `import pandas as pd`
**File:** [backtester.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/backtester.py)

`cache_candles()` had an unused `import pandas as pd`.

### 9. Misplaced Import — `import math`
**File:** [strategy_sdk.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/strategy_sdk.py)

`import math` was placed at line 286 (after class definitions). Moved to top of file with other imports.

### 10. Unused Import — `asdict`
**File:** [strategy_sdk.py](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/backend/app/services/bots/strategy_sdk.py)

`from dataclasses import ..., asdict` was imported but never used.

### 11. Negative PnL Formatting
**File:** [BacktestWalkForwardPanel.jsx](file:///c:/Users/Dhimeji01/.gemini/antigravity/scratch/trading-terminal/frontend/src/components/BacktestWalkForwardPanel.jsx)

Fold heatmap showed `$-500` for negative PnL. Now properly formats as `-$500` and `-$1.5k`.

---

## Verified Not Bugs (Investigated but Correct)

| Concern | Finding |
|---------|---------|
| `_adaptive_score` normalization `raw / w_sum * 5.0` | Correct — normalizes to equal-weight scale regardless of weight sum |
| `_risk_report` always returns `score: 0` | By design — risk domain provides regime info, not directional score |
| Portfolio equity curve double-counting | Correct — `total_capital + Σ(pnl_i)` is the right formula |
| Cache key doesn't normalize strategy name casing | Both cache and lookup use the same raw name consistently |

---

## Verification

| Check | Result |
|-------|--------|
| Backend tests | **555 passed**, 3 skipped ✅ |
| Frontend build | Clean (vite build) ✅ |
| No regressions | All existing tests unchanged ✅ |
