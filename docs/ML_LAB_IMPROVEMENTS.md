# ML Lab (Model Training) — Improvement Plan

Scope: the **Model Training dock panel** (`frontend/src/components/dock/ModelTrainingDashboard.jsx`),
its session store (`frontend/src/lib/mlTrainingSession.js`), and the backend training engine
(`backend/app/services/bots/ml_train_executor.py` + `/api/v1/ml/*` handlers in
`backend/app/api/http/app.py`).

Design rule for every item: **additive only**. New endpoints/JSON fields are optional; the panel
falls back to current behaviour when a field is absent, and no existing route, payload shape, or
trainer signature changes. Each phase ships independently.

---

## Current state (summary)

- Train/validate are **single blocking HTTP requests** (client timeouts 5–20 min).
- The panel progress bar is a **client-side time estimate** (`JobProgressBar` — asymptotic
  ratio of elapsed/timeout), not real progress.
- Jobs run in a `ProcessPoolExecutor` (`ML_TRAIN_MAX_WORKERS=1`) — concurrent requests queue
  **silently**; there is no job id, no cancel, no queue visibility.
- `GET /ml/model-status` (`_ml_status_enrich`) omits `validated_at` / `walk_forward` / `pbo`,
  so the dock cannot show deploy readiness after a refresh even though the deploy gate reads
  those fields from disk.
- `MlRetrainScheduler.get_retrain_history()` / `get_pending()` exist but are not exposed over HTTP.
- No persistent training-run log (only version snapshots on disk).
- Feature importance / confusion matrix visualisations exist only in Backtest Lab
  (`MlOptimizerPanel`), not in the training dock.

---

## Phase 1 — Real jobs: id, progress, cancel (usability + monitoring)

**Problem.** A 15-minute PPO train gives the user a fake bar and no way out. If the request
times out client-side, the job still runs to completion invisibly.

### 1.1 ML job store (backend)

New module `backend/app/services/bots/ml_job_store.py`, modelled on the existing
`backtest_job_store` pattern:

- In-memory dict of jobs: `{job_id, kind: train|validate, strategy, symbol, status:
  queued|running|done|error|cancelled, started_at, finished_at, progress: {pct, phase, detail},
  result_ref}`.
- `submit_train_job` / `submit_validate_job` register the job before dispatch and update it on
  completion. Zero change to their public signatures.

### 1.2 Progress from the worker process

`ProcessPoolExecutor` cannot stream callbacks, so use a **progress file**:

- Parent generates `job_id`, passes `progress_path` (a temp JSON file) inside the existing
  `config` dict (trainers already receive `config`; unknown keys are ignored — non-breaking).
- Trainers that support it (start with PPO episodes and the WF validator's fold loop) overwrite
  the file every N seconds: `{"pct": 34, "phase": "fold 2/3", "detail": "epoch 12/40"}`.
- Parent runs a small asyncio task that polls the file every ~2 s while the job is active,
  updates the job store, and publishes a WS `ml_job_progress` message (new `publish_ml_job_progress`
  in `backend/app/api/outbound.py`, same shape as `backtest_progress`).
- Trainers without instrumentation simply never write the file → UI keeps the estimated bar.

### 1.3 Async submit + endpoints

- `POST /api/v1/ml/train` and `/ml/validate` accept optional `"async": true`. Default (absent)
  keeps today's blocking behaviour — **no client change required**.
- With `async: true` the handler returns `{ok: true, job_id}` immediately.
- New routes (additive, next to the existing `/ml/*` table at `app.py` ~1868):
  - `GET /api/v1/ml/jobs` — recent jobs + queue depth
  - `GET /api/v1/ml/jobs/{job_id}` — status/progress/result
  - `POST /api/v1/ml/jobs/{job_id}/cancel` — cooperative cancel

### 1.4 Cooperative cancel

- Cancel writes a `cancel` flag into the job record and creates `<progress_path>.cancel`.
- Instrumented trainers check the flag at epoch/fold boundaries and return
  `{ok: false, cancelled: true}`. Queued (not yet started) jobs are cancelled via
  `Future.cancel()`.
- No process kill; the pool never needs recycling. Uninstrumented trainers run to completion
  (documented limitation, same as today).

### 1.5 Frontend wiring

- `mlTrainingSession.js`: add `jobId` and `serverProgress` fields (additive).
- `handleTrain` / `handleValidate`: send `async: true`; on `job_id`, poll
  `GET /ml/jobs/{id}` every 3 s (or consume `ml_job_progress` WS via `dispatch.js`, one new
  `MessageType` case) until terminal, then reuse the existing result rendering unchanged.
- `JobProgressBar`: if `serverProgress.pct` exists use it (and the phase text); otherwise keep
  the asymptotic estimate. Add a **Cancel** button that calls the cancel endpoint.
- Benefit: panel remount/page reload can **re-attach** to a running job via `GET /ml/jobs`.

**Risk:** low. Blocking mode remains default; WS message type is new; job store is in-memory.

---

## Phase 2 — Observability & monitoring (small, high value)

### 2.1 Deploy readiness on the panel

- `_ml_status_enrich` (`app.py` ~345–368): include `validated_at`, a compact
  `walk_forward: {ok, mean_oos_accuracy, n_folds, successful_folds}`, and
  `pbo: {pbo, ok}` from the same metadata `persist_ml_validation_metadata` already writes.
- Dock: a small "Deploy readiness" strip under the metric chips — ✓ trained, ✓ walk-forward,
  ✓ PBO, with age. Mirrors what `deploy_gate.py` will decide, so users see *before deploying*
  why the gate would block. Pure display; the gate itself is untouched.

### 2.2 Retrain audit + Run now

- `ml_retrain_status_handler` (~796): add `pending: scheduler.get_pending()` and
  `history: scheduler.get_retrain_history(20)` to the response (additive keys).
- Dock retrain card: show history (when/why/source) and add a **Run now** button per queued
  action that simply calls the existing `POST /ml/train` for that strategy/symbol.

### 2.3 Queue & worker telemetry

- `memory_snapshot.py` already reports `ml_train_process_isolation` / `ml_train_max_workers`;
  add `ml_jobs: {active, queued}` from the job store.
- Dock header badge: "1 running · 2 queued" instead of the vague "busy elsewhere" note.

### 2.4 Persistent training-run history

- New SQLite table `ml_train_runs` (id, kind, strategy, symbol, started_at, duration_ms, ok,
  error, key metrics JSON, config hash, version_id produced). Written by the job store on
  completion — one insert, no hot-path impact.
- `GET /api/v1/ml/runs?symbol=&strategy=&limit=` + a compact table section in the dock under
  Model inventory (virtualized like OptimizationHistory).
- This answers "did last night's retrain work, and how long did it take?" — currently
  unanswerable after the toast disappears.

---

## Phase 3 — Explainability & tuning

### 3.1 Feature importance in the dock

- GBDT metadata already contains `top_features`; render the existing
  `FeatureImportanceChart` (from the Backtest Lab ML viz) in the Dataset & versions card
  instead of the plain text line. Reuse, no new backend work.
- Optional follow-up: `permutation_importance: true` flag on `/ml/validate` for deep models
  (off by default; computed on the last fold only to bound cost).

### 3.2 Validation survives refresh

- Persisted WF/PBO now comes back on `model-status` (2.1), so the "Validation result" card can
  hydrate from status when the session is empty — the result no longer vanishes on tab switch
  or reload.

### 3.3 Tuning drawer (safe knobs only)

- Collapsible "Advanced" section in the controls: `n_folds`, `validate_max_bars`,
  `pbo_segments/max_combos`, and per-family caps (PPO `total_timesteps`, deep `max epochs`).
  All already accepted via `config` — the UI just stops hard-coding them. Defaults identical
  to today's values, so no behavioural change unless the user opts in.

### 3.4 Champion / challenger surface (stretch)

- `ml_champion_challenger.py` exists but is unwired. Minimal step: after a successful
  validate, if the new snapshot beats the active version's stored OOS accuracy, show a
  "Challenger beats champion — Activate?" hint that uses the **existing**
  `/ml/activate-version` endpoint. No new backend mutation path.

---

## Sequencing & test plan

| Order | Item | Touches | Test |
|-------|------|---------|------|
| 1 | 2.1 status enrich + readiness strip | `app.py`, dashboard | unit: enrich adds fields; UI renders with/without them |
| 2 | 2.2 retrain audit + Run now | `app.py`, dashboard | handler returns additive keys; old clients unaffected |
| 3 | 1.x job store + progress + cancel | executor, new module, outbound, dashboard, session | pytest: job lifecycle, cancel flag honoured, blocking mode unchanged; vitest: progress fallback |
| 4 | 2.3 telemetry, 2.4 run history | snapshot, new table/route, dashboard | insert-on-complete; list endpoint |
| 5 | 3.x explainability + tuning drawer | dashboard, validate flag | chart reuse renders from `top_features` |

Backward-compatibility checks per phase: hit old endpoints with old payloads (no `async`,
no new query params) and confirm byte-identical behaviour paths; load the dock against a
backend without the new fields and confirm no rendering regressions.
