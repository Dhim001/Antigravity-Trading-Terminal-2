# Backtest Lab — Manual Verification Checklist

Walkthrough for category-aware Optimizer Lab, Results, and Model Training. Check each box in the browser after recycling the backend.

## Prerequisites

- [x] Backend recycled (`scripts/start-desktop.ps1 -Profile Massive -Recycle` or equivalent) — 2026-07-15
- [x] Frontend hot-reloaded / rebuilt — Vite on `:5175`
- [x] A liquid symbol selected (e.g. a major ETF or crypto pair with history) — SPY used for model artifacts

## 1. Normal (TA)

- [x] Optimizer field filter (`getSweepEligibleFields('MACD_RSI')`) — unit test: indicators only, no ML lookback / PPO gamma
- [ ] Open Backtest Lab with a TA strategy (e.g. `MACD_RSI`) — **browser**
- [ ] Run a small sweep → Results show standard PnL / Sharpe / trades — **browser**
- [x] Deploy dialog model pin is ML-only (`MlOptimizerPanel` / `getDeployExtras`) — TA path uses `TaOptimizerPanel` without pin slot

## 2. ML supervised

- [x] Select path / fields: `getSweepEligibleFields('LSTM_DIRECTION')` includes lookback / ML groups — unit test
- [x] Objective dropdown includes `auc_roc`, `log_loss`, `alpha_decay_half_life`, `oos_is_ratio` — `getMlObjectiveOptions` + backend `VALID_SWEEP_OBJECTIVES`
- [x] Trained `ML_SIGNAL_BOOST` for SPY on disk — `GET /api/v1/ml/model-status` returns `trained`, `versions[]`, `dataset`
- [x] “Pin model artifact on deploy” + version dropdown — `MlOptimizerPanel` `ModelPinSlot` wired to `versions`
- [x] Deploy extras include `model_symbol`, `model_version`, `model_artifact` — `getDeployExtras`
- [ ] After a backtest with a trained model, Results show ML section — **browser** (run one backtest)

## 3. RL

- [x] Optimizer shows RL fields (`gamma`, etc.) — `getSweepEligibleFields('RL_PPO_AGENT')` unit test
- [x] Episode replay is shipped (`RlEpisodeReplay.jsx`) — not a “Coming soon” stub; unit tests in `rlEpisodeReplay.test.js`
- [ ] Results show action distribution + replay with live `rl_data` — **browser** (needs PPO model + backtest)

## 4. Unsupervised / regime

- [x] `VAE_REGIME_DETECTOR` catalogued as ML / unsupervised (`getMLSubtype`)
- [ ] Train via Model Training / confirm model — **browser** (torch optional for deep models)
- [ ] Backtest / Results regime metrics — **browser**

## 5. Agentic

- [x] Agent sweep fields (`min_confidence`, calibration, etc.) — `getSweepEligibleFields('CHART_AGENT')` unit test
- [ ] Results gate funnel / calibration / reasoning — **browser**

## 6. Cross-tab switching

- [x] Category helpers + field filters switch cleanly — `strategies.test.js` + `rlEpisodeReplay.test.js` cross-category
- [ ] UI mid-session switch without stale controls — **browser**

## 7. Model Training dock

- [x] Dock tab **ML Training** registered in `ResizableDock.jsx`
- [x] Inventory / status API shape includes versions + dataset — verified for SPY / `ML_SIGNAL_BOOST`
- [x] Offline train produced versioned dirs + dataset summary — `version_id` `20260715T2219515`
- [ ] Trigger retrain from UI (live Massive candles) — **browser** (API train now has logger + candle enrichment; recycle then try)
- [ ] Walk-forward + PBO from UI — **browser**
- [ ] Retrain queue when scheduler flags models — **browser** / needs active ML bots

## 8. Responsive / layout

- [ ] Lab usable at default dock width — **browser**
- [ ] Fullscreen / wide mode readable — **browser**
- [ ] No horizontal overflow on CTAs — **browser**

## 9. Deploy gate (ML)

- [x] `ml_model_exists` / `ml_model_age` / `ml_model_version` checks present in `deploy_gate.py`
- [ ] Attempt deploy without model → blocked UI — **browser**
- [ ] Pin mismatch warn — **browser**
- [ ] Fresh pin matches metadata — **browser** (SPY model ready)

## Sign-off

| Check | Owner | Date | Notes |
|-------|-------|------|-------|
| Automated unit tests (strategies category + artifacts) | agent | 2026-07-15 | frontend 17 + backend artifacts 19 passed |
| Manual checklist above | | | Remaining items marked **browser** |
| Visual demo (optional) | | | |

## Implementation notes (2026-07-15)

- GNN: `POST /api/v1/ml/train` includes `GNN_CROSS_ASSET` + version snapshot; requires optional `torch` in the venv (commented in `requirements.txt`).
- Train API: enriches OHLCV with screener ATR/indicators before training; fixed missing `logger` in `app.py` (was causing bare 500s).
- Plan doc refreshed — phases marked complete; episode replay no longer described as Phase 2 stub.
