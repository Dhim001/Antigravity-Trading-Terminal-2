# Trading Bots — User Manual

**Audience:** operators and researchers using the Algo dock, Model Training, and Backtest Lab  
**Last updated:** 2026-07-19  
**Related:** [OPTIMIZATION_ENGINE.md](./OPTIMIZATION_ENGINE.md) · [META_LABEL_MODEL.md](./META_LABEL_MODEL.md) · [ML_DL_RL_SIGNAL_GENERATION_PROPOSAL.md](./ML_DL_RL_SIGNAL_GENERATION_PROPOSAL.md) · [BACKTEST_LAB_REDESIGN_PLAN.md](./BACKTEST_LAB_REDESIGN_PLAN.md)

---

## 1. Quick start (recommended path)

```text
1. Pick strategy template in Algo (⌘B / Ctrl+B)
2. Set symbol, timeframe, allocation, risk exits
3. Run a baseline backtest (Backtest Lab → Results)
4. Optimize 2–3 params (Lab → Optimizer) — exploratory only
5. Walk-forward validate (required before capital deploy)
6. Pass Deploy Gate → Deploy Bot
7. For ML: Train + Validate in Model Training first, then backtest/deploy
```

**Do not** deploy from a sweep-only (in-sample) winner. The deploy gate blocks that path.

---

## 2. Where things live in the UI

| Surface | Shortcut / location | Role |
|---------|---------------------|------|
| **Algo** | `⌘B` / Ctrl+B · Automation dock | Templates, config, live bots, deploy, open Lab |
| **Backtest Lab** | From Algo or Lab sheet | Results · Optimizer · Jobs |
| **Model Training** | Intelligence dock · ML Training | Train / WF+PBO validate / activate versions |
| **Bot detail** | Click bot row in Algo | Pause, stats, trades, config |

**Algo category tabs**

| Tab | Strategies |
|-----|------------|
| **Normal** | TA, SMC, microstructure, market making, tick |
| **ML / AI** | GBDT, LSTM, TCN, Transformer, GNN, VAE, RL, Hybrid Ensemble |
| **Agentic** | Chart Analyst, Absorption Agent |

**Execution modes:** `BAR_CLOSE` (default) vs `TICK` (tick strategies only — switch mode, stay on Normal).

---

## 3. Shared bot concepts

### Risk & exits (almost every bot)

| Parameter | What it does | Typical focus |
|-----------|--------------|---------------|
| `allocation` | Paper/live size budget ($) | Start small; match `STRATEGY_ALLOCATION_DEFAULTS` |
| `trailing_stop_percent` | Exit on retrace from peak | **Always** in sweep shortlist |
| `take_profit_percent` / `tp_mode` | Fixed % TP, strategy TP, or none | Scalps: tighter; trends: wider or trailing-only |
| `direction_mode` | `LONG_ONLY` / `SHORT_ONLY` / `BOTH` | Crypto often BOTH; equity often LONG_ONLY |
| `stop_loss_percent` | Hard stop (when used) | Widen only with evidence (MAE/MFE) |

### Optional meta-layers

| Feature | Where | Effect |
|---------|-------|--------|
| `filter_strategy` | Several TA bots | Secondary strategy must agree / bias |
| `vae_regime_gate_enabled` | TA + VAE | Suppress entries when VAE says unstable |
| `calibration_gate_enabled` | Agents + ensemble | Block weak historical setup buckets |
| Meta-label (GBM/Wilson) | Chart Agent primarily | Block low P(win) — see META_LABEL_MODEL.md |
| `model_version` | ML bots | Pin a trained snapshot (or leave Latest) |
| `model_symbol` | ML bots | Artifact key if different from bot symbol |

### Models on disk

Trained ML/RL artifacts live under `backend/data/{ml_signal_models|lstm_…|rl_ppo_…}/SYMBOL/`. They **survive** server recycle. Status badge reads `/api/v1/ml/model-status`.

---

## 4. Strategy catalog (what each bot does)

### 4.1 Trend / mean-reversion / breakout (TA)

| ID | Name | How it trades | Focus parameters |
|----|------|---------------|------------------|
| `MACD_RSI` | MACD + RSI | MACD hist crossover filtered by RSI; ATR-aware stops | `rsi_length`, `macd_slow`, trailing |
| `SUPERTREND_ADX` | Supertrend + ADX | Follow Supertrend when ADX strong | `adx_threshold`, `st_multiplier`, `block_elevated_vol` |
| `BRS_SCALPING` | Bollinger RSI Stoch | Fade bands with RSI/Stoch confirm | `bb_std`, `rsi_oversold`, take-profit % |
| `VWAP_PULLBACK` | VWAP Pullback | Buy/sell pullbacks to session VWAP | `rsi_overbought_gate` / oversold, trailing |
| `DONCHIAN_BREAKOUT` | Donchian Breakout | Channel break + ATR expansion | `breakout_length`, `exit_length`, `atr_confirm_mult` |
| `ICT_SMC` | ICT Smart Money | Order blocks, FVGs, liquidity sweeps | `sweep_lookback`, `fvg_min_gap_pct`, `ob_lookback` |
| `MARKET_MAKING` | Spread capture | Quote both sides; manage inventory skew | `spread_pct`, `max_skew`, `vol_shutdown_mult` (`tp_mode: none`) |

**How to use (TA):** pick template → set TF (often 5m–1h) → baseline backtest → sweep 2–3 params → walk-forward → deploy.

---

### 4.2 Microstructure / volume structure

| ID | Name | How it trades | Focus parameters |
|----|------|---------------|------------------|
| `CVD_DIVERGENCE` | CVD Divergence | Price vs cumulative volume delta pivots | `pivot_lookback`, ADX filter |
| `WYCKOFF_SPRING` | Wyckoff Spring/Upthrust | False break + volume absorption | `range_lookback`, `volume_surge_mult` |
| `VPOC_REVERSION` | Volume POC Reversion | Revert to POC from outside value area | `profile_lookback`, `value_area_pct`, ADX trend filter |
| `ORDERFLOW_IMBALANCE` | Order Flow Imbalance | Aggressive book pressure (BAIR/MLOFI) | `bair_threshold`, `mlofi_threshold`, `book_levels` |

**Notes:** Order-flow quality depends on L2/book availability; candle proxy is a degraded fallback. Prefer liquid symbols and shorter TFs for CVD/orderflow.

---

### 4.3 Tick bots (`execution_mode: TICK`)

| ID | Name | How it trades | Focus parameters |
|----|------|---------------|------------------|
| `TICK_MOMENTUM` | Tick Momentum | Short momentum burst → reverse exit | `lookback_ticks`, `tick_cooldown_sec` |
| `TICK_MEAN_REVERT` | Tick Mean Reversion | Fade z-score spikes | lookback, cooldown |
| `TICK_BREAKOUT` | Tick Breakout | Break recent tick range + cooldown | lookback, cooldown |

**How to use:** Algo → Normal → set execution **Tick** → pick template. Backtest tick strategies only if you have tick/archive coverage for that symbol.

---

### 4.4 Agentic bots

| ID | Name | How it trades | Focus parameters |
|----|------|---------------|------------------|
| `CHART_AGENT` | Chart Analyst Agent | Multi-domain chart score + optional LLM; confidence gate | `min_confidence`, `require_trend_alignment`, calibration / meta-label, trailing |
| `ABSORPTION_AGENT` | Absorption Agent | Scores absorption/exhaustion across domains | `min_confidence`, `min_score`, trailing |

**How to use:** raise `min_confidence` until trade count is sane; enable calibration/meta-label after you have closed-trade history. LLM improves narration more than raw edge — keep it optional for latency/cost.

---

### 4.5 ML / DL / RL (+ ensemble)

Train in **Model Training**; Algo only deploys inference configs.

| ID | Name | Signal idea | Train first? | Focus (inference / sweep) |
|----|------|-------------|--------------|---------------------------|
| `ML_SIGNAL_BOOST` | ML Signal Boost | HistGradientBoosting on triple-barrier labels | Yes | `min_confidence`, `triple_barrier_atr_mult` (retrain), `gbm_*`, trailing |
| `LSTM_DIRECTION` | LSTM Direction | Sequence classifier over lookback bars | Yes | `lookback`, `min_confidence`, trailing |
| `TCN_MULTI_HORIZON` | TCN Multi-Horizon | 5/15/60-bar return forecasts; fire when aligned | Yes | `lookback`, `min_return`, `min_confidence` |
| `TRANSFORMER_SIGNAL` | Transformer Signal | Attention over bar window | Yes | `lookback`, `min_confidence`, trailing |
| `GNN_CROSS_ASSET` | GNN Cross-Asset | Lead-lag across correlated basket | Yes | `min_corr`, `min_confidence`, basket |
| `RL_PPO_AGENT` | RL Trading Agent | PPO policy (entry/hold/exit) | Yes | `min_confidence` (~0.28 default), `gamma` (retrain), trailing |
| `VAE_REGIME_DETECTOR` | VAE Regime | Anomaly score: amplify / suppress / gate | Yes | `anomaly_threshold`, `suppress_threshold` |
| `HYBRID_ENSEMBLE` | Hybrid Ensemble | Weighted vote TA + ML + RL | Train **legs**, not ensemble | `ensemble_threshold`, weights, `ensemble_require_agreement` |

**ML workflow**

```text
Model Training → Train (symbol + strategy)
              → Validate (walk-forward + optional PBO)
              → Activate version if needed
Algo / Lab    → Backtest with same symbol
              → Optimizer (inference + risk params)
              → Deploy Gate (requires WF on model metadata)
              → Deploy
```

**Ensemble specifics**

- Default legs: TA `MACD_RSI`, ML `ML_SIGNAL_BOOST`, RL `RL_PPO_AGENT` (weights 0.3 / 0.4 / 0.3).
- Deploy gate checks **component** models + ML-leg walk-forward — not a fake `HYBRID_ENSEMBLE` artifact.
- Optional `ensemble_require_agreement`: need ≥2 legs on the same side.

---

## 5. Backtest Lab — how to use it

Open from Algo (**Open Lab**) or the Lab sheet. Tabs: **Results · Optimizer · Jobs**.

### 5.1 Baseline (Results)

1. Symbol, days, timeframe, strategy, config.
2. Optional: **portfolio** basket (multi-symbol) — contribution % is share of |PnL|; sign is on per-symbol PnL.
3. Modes: research vs live-aligned parity (HTF confirm / filters).
4. Read: total PnL, trades, Sharpe, max drawdown, equity curve, trade list.

**Baseline criteria (sanity):** enough trades for the horizon; DD tolerable; not a single lucky trade.

### 5.2 Optimizer (sweep)

Category-aware panels (TA / ML / Agent). Defaults:

| Category | Default objective | Min trades (leaderboard) | Pre-enabled params |
|----------|-------------------|--------------------------|--------------------|
| TA / Normal | **Calmar** | 1 | 2–3 strategy-specific |
| Agent | Calmar | 3 | confidence, score, trailing |
| ML | **robust_score** | 5 | confidence / lookback / trailing (+ GBM for XGB) |

**Search modes:** grid · random · LHS · Bayesian (Optuna TPE). Keep trials modest; enable **sensitivity** view — flag CV > 0.3 and outlier “best” configs.

**Exploratory vs validated:** sweep without walk-forward is **research only**. Deploy gate blocks sweep-only.

### 5.3 Walk-forward

- Rolling or anchored IS → OOS folds with purge/embargo.
- Aggregate OOS PnL, WFE, stability, optional DSR / PBO / holdout.
- For ML models, also use Model Training **Validate** (persists `validated_at` / `walk_forward` / `pbo` into `metadata.json`).

### 5.4 Jobs

Long sweeps and WF run as deferred jobs. Track status and reopen results from **Jobs**.

---

## 6. What to optimize (cheat sheet)

Sweep **few** levers that change edge; leave architecture for Model Training retrain.

| Strategy family | Sweep these first | Retrain / leave alone |
|-----------------|-------------------|------------------------|
| MACD / Supertrend / Donchian | Indicator periods, trailing | — |
| BRS scalp | Band σ, RSI gates, TP% | — |
| ICT / Wyckoff / VPOC | Lookbacks, surge/gap thresholds | — |
| Market making | Spread, skew, vol shutdown | — |
| Agents | `min_confidence`, trailing, (score) | Meta-label after enough exits |
| ML_SIGNAL_BOOST | `min_confidence`, trailing; optionally `gbm_*` then **retrain** | Barrier / features via retrain |
| LSTM / Transformer / TCN | lookback, confidence / min_return, trailing | Epochs, width → Model Training |
| RL | confidence, trailing; gamma via retrain | PPO timesteps → Training |
| VAE | anomaly / suppress thresholds | Latent size → Training |
| Ensemble | threshold, ML weight, trailing | Component models |

**Rule of thumb:** if changing a param requires a new `.onnx` / `.joblib`, train it in Model Training; if it only gates inference, sweep it in Lab.

---

## 7. Evaluation criteria (how to judge results)

### 7.1 Primary metrics

| Metric | Prefer | Red flag |
|--------|--------|----------|
| **Calmar** (PnL / max DD) | Default TA objective | High PnL with catastrophic DD |
| **Sharpe** | Stable risk-adjusted return | High Sharpe on &lt;10 trades |
| **robust_score** | ML default: Sharpe × √trades × stability | Peak score on fragile neighborhood |
| **Max drawdown %** | Gate warns ~25%+ | Strategy unusable live at that DD |
| **Trade count** | Enough for OOS folds | “Curve” with 2–3 trades |

### 7.2 Validation metrics (required for serious deploy)

| Metric | Meaning | Typical gate |
|--------|---------|--------------|
| **WFE** (walk-forward efficiency) | OOS vs IS quality | Block if &lt; **0.5** |
| **Stability** | Consistency across folds | Block if &lt; **0.5** (when enough folds) |
| **DSR** (deflated Sharpe) | Corrects for multiple testing | Warn if weak |
| **PBO** | Prob. of backtest overfitting | Block if ≥ **0.5**; warn ≥ 0.35 |
| **Final holdout** | Never used in optimization | Should not collapse vs OOS |

### 7.3 ML-specific

| Check | Pass looks like |
|-------|-----------------|
| Model exists for symbol | Green status badge |
| Walk-forward validate | `walk_forward.ok`, recommendation not REJECT |
| PBO on metadata | Low PBO; missing PBO = warn (or block if `ml_require_pbo`) |
| Model age | Retrain if stale (default max age ~168h) |
| Feature drift (PSI) | PSI &gt; 0.25 → decay / retrain signal |
| Capacity parity | WF used same capacity as production (`wf_capacity_parity`) |

### 7.4 Deploy gate (summary)

Before **Deploy Bot**, the gate typically checks:

- Linked backtest / WF OOS path (not exploratory sweep alone)
- Trades, PnL, WFE, stability, DD warn
- Config fingerprint matches results
- **ML:** model on disk, WF validated, PBO, pinned version resolves
- **Ensemble:** ML + RL leg models + ML-leg WF

**Force deploy** exists for paper/debug only — do not use for live capital.

---

## 8. Live / paper operations

1. Prefer **paper** until WF + gate are green.
2. Live bots require server `ALLOW_LIVE_BOTS`.
3. Watch Algo logs, cooloff / streak / drawdown holds.
4. Alpha decay + retrain coordinator may queue retrains (cooldown / dedup).
5. Ambiguous live orders show under reconciliation — do not blindly re-send.

---

## 9. Suggested paper allocations

| Style | Example strategies | Paper $ (defaults) |
|-------|--------------------|--------------------|
| Scalp / tick | BRS, tick bots | ~1,000 |
| Intraday TA | VWAP, MACD, VPOC | ~1,500–2,000 |
| Trend / breakout | Supertrend, Donchian | ~3,000–5,000 |
| Market making | MM | ~5,000 |
| Agents | Chart / Absorption | ~2,000 |
| ML / ensemble / RL | ML stack, Hybrid | ~2,000–3,000 |

---

## 10. Common pitfalls

| Pitfall | Fix |
|---------|-----|
| Badge says “Train” but you trained earlier | Recycle backend after code fixes; confirm symbol matches folder (`BTCUSDT` ≠ `BTC`) |
| Optimizing ML architecture in Lab only | Retrain in Model Training so artifacts update |
| Deploying sweep winner without WF | Run walk-forward; gate will block otherwise |
| Ensemble deploy with no component models | Train + validate ML_SIGNAL_BOOST and RL_PPO for that symbol |
| Too many sweep dimensions | Stick to 2–3 params; check sensitivity outliers |
| Tick bot on bar-only data | Need tick feed/archive |
| Ignoring DD for high Calmar | Size down or tighten risk |

---

## 11. Decision tree (pick a bot)

```text
Have L2 / CVD / tick data? ──yes──► Orderflow / CVD / Tick bots
         │
         no
         ▼
Want rules you can explain? ──yes──► TA (MACD, Supertrend, Donchian, VWAP, ICT)
         │
         no / want adaptive
         ▼
Have GPU/time to train? ──yes──► ML_SIGNAL_BOOST first → then LSTM/TCN/Transformer
         │                         → RL only after supervised baseline
         │                         → Ensemble when TA+ML+RL legs are healthy
         no
         ▼
Chart Agent / Absorption (confidence + calibration) for discretionary automation
```

---

## 12. API & file map (operators)

| Concern | Location |
|---------|----------|
| Strategy list | `GET /api/v1/strategies` · `strategy_catalog.py` |
| Defaults | `indicators.py` · `tick_strategies.py` |
| Deploy gate | `deploy_gate.py` · UI `deployGate.js` |
| ML train/validate | `/api/v1/ml/*` · Model Training dock |
| Artifacts | `backend/data/*_models/{SYMBOL}/` |
| Optimizer defaults | `frontend/src/lib/optimizerDefaults.js` |

---

*This manual describes shipped product behavior. Design history and deeper ML theory live in the proposal docs linked above.*
