# Whole-App Scan Report — Dead Code, Wiring & Broken Features

Date: 2026-07-20. Scope: full backend (`backend/app/**`) and frontend (`frontend/src/**`).
Method: four parallel deep scans (API layer, services layer, frontend, FE↔BE contract), with
top findings re-verified by direct grep. Status labels: **CONFIRMED** (zero references /
reproducible break) vs **SUSPECT** (wired but questionable).

---

## P0 — Broken user-facing features (confirmed)

### 1. Risk Settings + basket correlation UI throw immediately — **FIXED 2026-07-20**
- **Where:** `frontend/src/api/transport.js` `HTTP_ROUTES` vs `backend/app/api/bindings.py`
- **What:** Backend exposes `GET /api/v1/risk/config`, `POST /api/v1/risk/preview`,
  `POST /api/v1/risk/basket-correlation`. `RiskSettingsSection.jsx` and
  `PortfolioBacktestPicker.jsx` call `invokeHttpAction(Action.RISK_*)`, but `HTTP_ROUTES`
  has **no RISK_ entries** (verified: only `ADMIN_RESET_RISK_KILL_SWITCH` matches "RISK").
  `invokeHttpAction` throws `No HTTP route for action: risk_*` before any network call.
- **Fix applied:** Added the three RISK routes to `HTTP_ROUTES`. In `dispatch.js`, skip
  `setOrderResult` when the payload carries `risk_config` / `risk_preview` /
  `basket_correlation` so OrderEntryWidget does not toast risk replies.

### 2. Copilot "Clear chat" never clears the server session — **FIXED 2026-07-20**
- **Where:** `frontend/src/api/endpoints.js` vs `backend/app/api/http/app.py`
- **What:** FE did `POST /api/v1/copilot/clear`; backend only registers
  `DELETE /api/v1/copilot/history/{session_id}`. The 404 was swallowed in `CopilotTab.jsx`,
  so the UI cleared locally but the server session/history persisted.
- **Fix applied:** `clearCopilotSession` now `DELETE`s `/api/v1/copilot/history/{session_id}`.

### 3. RQ backtest sweep path raises ImportError — **FIXED 2026-07-20**
- **Where:** `backend/app/api/handlers/bots.py` (RQ notify branch)
- **What:** Local `from app.api.outbound import send_order_result` — that function lives in
  `app.api.responses`, not `outbound`. Any sweep run with `REDIS_URL` set (RQ path) crashed
  at notify time. The correct import already exists at the top of the module.
- **Fix applied:** Removed the bogus local re-import; uses module-level `send_order_result`.

### 4. Scanner WS fallback always times out — **FIXED 2026-07-20**
- **Where:** `frontend/src/api/transport.js` `waitForScanResults`
- **What:** Read `scanResults` from `useResearchStore` but subscribed to `useStore`, so the
  promise only ever resolved via the initial check, otherwise timed out.
- **Fix applied:** Subscribe to `useResearchStore`; dropped unused `useStore` import.

---

## P0 — Features that silently do nothing (confirmed, backend)

### 5. Alpha-decay "ML auto-retrain" is a no-op — **FIXED 2026-07-20**
- **Where:** `backend/app/services/bots/alpha_decay.py`
- **What:** On ML/ensemble decay it called `record_retrain` (cooldown only) and logged
  "will be retrained" without ever training.
- **Fix applied:** Calls `request_retrain` instead (queues for drain / Run now) with an
  honest bot-log message. Does **not** start the cooldown until a train actually succeeds
  (`record_retrain` remains in `submit_train_job` success path).

### 6. Retrain pending queue is never drained — **FIXED 2026-07-20**
- **Where:** `backend/app/services/bots/ml_retrain_scheduler.py` + `server.py`
- **What:** `request_retrain()` filled `_pending` with nothing dequeuing it.
- **Fix applied:** `pop_next_pending` + `ml_retrain_drain_loop` submits real
  `submit_train_job`s (gated by `ML_RETRAIN_AUTO_DRAIN`, interval
  `ML_RETRAIN_DRAIN_INTERVAL_SEC`). UI label: "Queued (auto-drain or Run now)".

### 7. Feature-drift monitoring can never fire — **FIXED 2026-07-20**
- **Where:** `backend/app/services/bots/ml_feature_drift.py` (~195–218)
- **What:** `FeatureDriftMonitor.record_inference` had **zero callers**, so the PSI buffer
  never reached the 30-vector minimum and `check_drift` always returned `None`. The
  alpha-decay "Metric 8" branch that depends on it was unreachable.
- **Fix applied:** `record_ml_inference_features()` helper; called from ML strategy
  `evaluate()` / VAE assess paths (`ML_SIGNAL_BOOST`, `LSTM_DIRECTION`,
  `TCN_MULTI_HORIZON`, `TRANSFORMER_SIGNAL`, `GNN_CROSS_ASSET`, `RL_PPO_AGENT`,
  `VAE_REGIME_DETECTOR`) with live feature vectors.

### 8. Walk-forward validate can overwrite live deep-learning models — **FIXED 2026-07-20**
- **Where:** trainers `ml_lstm_trainer.py`, `ml_tcn_trainer.py`, `ml_vae_regime.py`,
  `ml_transformer_trainer.py`, `ml_gnn_trainer.py`
- **What:** Deep trainers ignored `skip_snapshot` / `_wf_mode` and snapshotted a version
  on every WF fold (version history pollution). Live ONNX still written per fold so OOS
  eval can load the fold model — same pattern as GBM/PPO.
- **Fix applied:** All five deep trainers honor `skip_snapshot` (defaulting from
  `_wf_mode`), matching `rl_ppo_trainer.py`.

---

## P1 — Incomplete FlexLayout migration (frontend) — **FIXED 2026-07-20**

The `WorkspaceGrid` migration left the old shell half-attached — cleaned up:

| # | Finding | Fix applied |
|---|---------|-------------|
| 9 | Dead `TradingPanel` / `ResizableDock` / `ResizableWatchlistSidebar` imports in App | Removed; `AutomationStudio` imports `AlgoTab` from `dock/AlgoPanel` |
| 10–12 | `sidebar-toggle` / `sidebar-expand` / `trading-panel-expand` / `dock-group` had no live listeners | `WorkspaceGrid` focuses FlexLayout tabs (`watchlist`↔`chart`, `order-entry`, dock-group first tab) |
| 13 | Footprint unreachable | Mounted as FlexLayout **Footprint** tab + `chart/FootprintPanel.jsx` |
| 14 | Stale dock/sidebar CSS vars & resize handlers in App | Stripped; root no longer sets `--dock-h` / `--sidebar-w` |

Legacy shell files kept on disk with deprecation headers (not mounted).

---

## P1 — Backend dead/unwired modules (confirmed) — **FIXED 2026-07-20**

| # | Finding | Fix applied |
|---|---------|-------------|
| 15 | `ChampionChallengerGate` unwired | Deleted `ml_champion_challenger.py` (manual activate via dock remains) |
| 16 | No prod `subscribe` handlers | Documented history/poll usage on `AgentEventBus`; kept publishes |
| 17 | Dead outbound publishers | Removed `publish_orderbook_update`, `publish_agent_insight`, `publish_ml_job_progress` (builders unchanged) |
| 18 | `shutdown_ml_train_pool` unused | Called from `graceful_shutdown` |
| 19 | Dead helpers | Deleted `run_portfolio_config_eval`, `get_ml_job_future` |
| 20 | Legacy `OrderManager` | Deleted `services/oms.py` |
| 21 | Unused / unwired config | Deleted `USE_LIVE_FEEDS`, `PRETRADE_MACRO_WINDOW_MIN`, `PRETRADE_PEER_DIVERGE_PCT`; wired `SCANNER_DEPLOY_MAX_ALLOCATION`, `MAX_PER_CYCLE`, `MAX_PORTFOLIO_PCT` in `scanner_deploy.py` |

---

## P1 — Protocol / contract drift — **FIXED 2026-07-20**

| # | Finding | Fix applied |
|---|---------|-------------|
| 22 | Orphan `COPILOT_*` Actions (no `@route`; FE omitted them) | REST-only: removed Actions from `protocol.py`; dedicated `/api/v1/copilot/*` handlers remain |
| 23 | Duplicate copilot `HTTP_BINDINGS` unreachable | Removed copilot rows from `bindings.py` |
| 24 | Raw `agent_insights_list` WS type | Deleted unused `get_agent_insights` helper; REST `GET /api/v1/agent/insights/{symbol}` already covers list |
| 25 | Empty `ORDER_PREVIEW` WS case | Documented as intentional noop (HTTP `previewOrder` reads envelope) |
| 26 | `_pick_primary_message` incomplete priority | Added `JOURNAL_ENTRY`, `JOURNAL_DELETED`, `CHART_DRAWINGS`, `ML_JOB_PROGRESS`, `TICKS_UPDATE`, `COPILOT_AGENT_MESSAGE` |
| 27 | Unused FE Actions `ADMIN_ARCHIVE_IMPORT`, `NOTIFY_PUSH_LIST` | Wired: System Control → Import user file; Notifications shows push subscription count |

---

## P2 — Consistency / hygiene — **FIXED 2026-07-20** (#28–#33, #39–#40)

| # | Finding | Fix applied |
|---|---------|-------------|
| 28 | ML cancel only in PPO/WF folds | Epoch-boundary `ml_cancel_requested` in LSTM/TCN/VAE/Transformer/GNN; GBM checks before fits |
| 29 | Worker missing calibration/scanner loops | Started `calibration_refresh_loop` + `scanner_deploy_loop` in `worker.py` |
| 30 | Uniform `min_confidence` bump | Strategy-aware bump + `PARAM_BOUNDS` for TCN/RL vs prob families |
| 31 | Split meta-label flags | `effective_meta_label_mode()`; UI warning when mode set but gate off |
| 32 | Dual “alpha decay” names | Renamed helper → `compute_equity_edge_half_life` (+ legacy alias); emit `edge_half_life` |
| 33 | Silent `save_optimization_run` | Log + `optimization_save_error` on sweep results |
| 34–38 | (frontend hygiene) | **FIXED** earlier |
| 39 | PBO 0.5 boundary drift | Shared `pbo_policy.pbo_passes` / `pbo_is_block` (≥0.5 blocks) |
| 40 | Async ML candle pinning | `ML_ASYNC_MAX_INFLIGHT` slot reserve (429 when full) |

---

## Verified healthy (no action)

- ML Lab HTTP/WS contract: train/validate/jobs/cancel/runs/retrain-status/model-status/
  activate/delete all shape-match; `pbo: null` and skipped-PBO render safely.
- `MessageType` enums in sync FE↔BE; `ml_job_progress` payload matches dispatch.
- No orphan SQLite tables; archive writer/WAL/rollups, data-quality loops, memory snapshot,
  deploy gate, tick screener, post-trade learner all wired.
- No orphaned frontend component/lib files (deadness is unmounted shells + unused exports).

---

## Suggested fix sequence

1. **P0 quick wins — DONE 2026-07-20:** #1–#4 (risk routes + toast spin-off, copilot clear,
   RQ import, scanner subscribe).
2. **P0 model safety — DONE 2026-07-20:** #8 `skip_snapshot` on five deep trainers; #5/#6
   alpha-decay queues + auto-drain loop.
3. **P1 migration cleanup — DONE 2026-07-20:** #9–#14 FlexLayout shell removal + event rewiring.
4. **P1 dead code sweep — DONE 2026-07-20:** #15–#21 backend, #34–#38 frontend.
5. **P1 protocol tidy — DONE 2026-07-20:** #22–#27 (REST-only copilot; primary-message + unused Actions).
6. **P2 consistency — DONE 2026-07-20:** #28–#33, #39–#40.
7. **P0 feature-drift wire — DONE 2026-07-20:** #7 `record_ml_inference_features` from ML evaluate paths.
