# ML / Deep Learning / Reinforcement Learning Signal Generation Proposal

> **Goal**: Introduce data-driven signal generators that sit alongside (or replace) the current indicator-based strategies, using the same `BaseStrategy.evaluate()` interface so they plug directly into the bot manager, backtester, and risk pipeline with zero breaking changes.

---

## 1. Current Architecture Recap

All signal generation today flows through a single contract:

```python
class BaseStrategy:
    def evaluate(self, df_row) -> dict:
        return {"signal": "BUY"|"SELL"|"CLOSE"|"NONE", ...}
```

Every built-in strategy (`MACD_RSI`, `BRS_SCALPING`, `SUPERTREND_ADX`, `VWAP_PULLBACK`, `ICT_SMC`, `DONCHIAN_BREAKOUT`, `MARKET_MAKING`, `CVD_DIVERGENCE`, `WYCKOFF_SPRING`, `VPOC_REVERSION`, `ORDERFLOW_IMBALANCE`, `ABSORPTION_AGENT`, `CHART_AGENT`) plus the `StrategyV2` SDK all produce signals from **hand-crafted indicator rules** — threshold crossovers on RSI, MACD histograms, Bollinger band touches, Supertrend direction flips, etc.

The **meta-label model** (`meta_label_model.py`) already uses gradient-boosted trees (XGBoost) but only as a **gate** on existing TA signals (predict P(win) to filter entries). It does not generate signals itself.

### What's Missing

| Capability | Current State |
|---|---|
| Learn non-linear patterns from raw price data | ❌ Hardcoded thresholds only |
| Adapt to regime shifts without manual tuning | ❌ Regime rotation agent swaps *whole strategies* |
| Discover features the developer hasn't imagined | ❌ Feature set is fixed at design time |
| Optimize the full trade lifecycle (entry → sizing → exit) jointly | ❌ Entry, sizing, exit are separate rule stages |
| Exploit tick-level or orderbook microstructure beyond simple heuristics | ❌ Tick strategies use rolling z-scores |

---

## 2. Proposed ML/DL/RL Strategy Methods

### 2.1 — LSTM / GRU Price-Direction Classifier

> **Category**: Supervised Deep Learning · Time-Series Classification

**What it does**: A recurrent neural network (LSTM or GRU) consumes a sliding window of N bars (e.g. 60 × 1m candles) with features `[open, high, low, close, volume, RSI, ATR, MACD]` and outputs a 3-class probability: `{UP, DOWN, FLAT}`.

**Signal mapping**:
- `P(UP) > threshold` → `BUY`
- `P(DOWN) > threshold` → `SELL`
- Otherwise → `NONE`

**Why it works here**:
- LSTM/GRU captures temporal dependencies that static indicator crossovers miss (e.g. "RSI was oversold 5 bars ago, recovered, then volume surged — that sequence pattern is predictive").
- Lightweight enough to run inference in < 5 ms per bar on CPU (no GPU required at inference time).
- Training can reuse the existing backtest candle archive (60-day rolling 1m bars per symbol).

**Integration sketch**:
```python
class LstmDirectionStrategy(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.model = load_onnx_model("models/lstm_direction.onnx")
        self.window = deque(maxlen=config.get("lookback", 60))

    def evaluate(self, df_row) -> dict:
        self.window.append(self._extract_features(df_row))
        if len(self.window) < self.window.maxlen:
            return {"signal": "NONE"}
        probs = self.model.run(np.array(self.window).reshape(1, -1, N_FEATURES))
        if probs[0] > self.config.get("threshold", 0.6):
            return {"signal": "BUY", "confidence": float(probs[0]),
                    "stop_loss_distance": df_row.get("ATR_14", 0) * 1.5}
        if probs[1] > self.config.get("threshold", 0.6):
            return {"signal": "SELL", "confidence": float(probs[1]),
                    "stop_loss_distance": df_row.get("ATR_14", 0) * 1.5}
        return {"signal": "NONE"}
```

**Training pipeline**:
- Label: Triple-barrier labelling (touch upper barrier = UP, lower = DOWN, time expiry = FLAT).
- Framework: PyTorch → export to ONNX for lightweight inference.
- Walk-forward retraining: retrain monthly on rolling 6-month windows using `backtest_walk_forward.py`.

---

### 2.2 — Temporal Convolutional Network (TCN) Multi-Horizon Forecaster

> **Category**: Supervised Deep Learning · Regression / Classification

**What it does**: A dilated causal CNN (WaveNet-style) that processes variable-length bar sequences without the sequential bottleneck of RNNs. Outputs multi-horizon forecasts: `{5-bar return, 15-bar return, 60-bar return}`.

**Why it's better than LSTM for this use case**:
- **Parallelizable**: Training is ~10× faster because convolutions don't require sequential processing.
- **Multi-horizon**: One model predicts short- and medium-term moves simultaneously, enabling strategy logic like "short-term bearish but medium-term bullish → NONE (wait for pullback)".
- **Receptive field control**: Dilated convolutions can look back hundreds of bars efficiently.

**Signal mapping**:
- If all horizons agree on direction with magnitude > `min_return_threshold` → strong signal.
- Mixed signals across horizons → `NONE` (avoids whipsaw).

**Integration**: Same `BaseStrategy` wrapper as LSTM, but with multi-output interpretation logic.

---

### 2.3 — Transformer Attention-Based Signal Generator

> **Category**: Supervised Deep Learning · Attention Mechanism

**What it does**: A lightweight Transformer encoder (4-6 layers, ~500K params — not GPT-scale) attends to a sequence of bars + orderbook snapshots. The self-attention mechanism learns which historical bars are most relevant to the current decision — similar to how the `CHART_AGENT` uses LLM reasoning, but as a trained numerical model rather than an LLM API call.

**Key advantages**:
- **Interpretable attention weights**: You can visualize which past bars the model is "looking at" when generating a signal — directly displayable in the chart overlay.
- **Multi-modal input**: Can fuse candle data, orderbook depth, and sentiment scores in a single forward pass (the existing `sub_reports` structure from `chart_analyst.py` maps naturally to input channels).
- **No recurrence**: Faster inference than LSTM; scales better with sequence length.

**Signal mapping**:
- Output: `{action: BUY/SELL/NONE, confidence: 0-1, stop_distance_atr_mult: float, tp_distance_atr_mult: float}`
- The model learns entry *and* exit parameters jointly.

---

### 2.4 — XGBoost / LightGBM Signal Classifier (Expanding Meta-Label into Primary Signal)

> **Category**: Supervised Machine Learning · Gradient Boosting

**What it does**: Extends the existing `meta_label_model.py` from a binary gate (win/lose on existing TA signals) into a **primary 3-class signal generator** (BUY / SELL / NONE). This is the lowest-risk, fastest-to-deploy option because the infrastructure already exists.

**Why this is compelling**:
- **Already half-built**: The `insight_to_features()` function, `FEATURE_NAMES`, training pipeline, and walk-forward validation in `meta_label_walk_forward.py` all exist.
- **Feature engineering freedom**: Can incorporate the full `sub_reports` (trend, momentum, volume, sentiment, risk), cyclical time features, cross-symbol correlation from `correlation.py`, and regime labels from `regime_rotation.py`.
- **Battle-tested in production quant shops**: Gradient-boosted trees are the workhorse of most live systematic trading firms (Two Sigma, Citadel, Jump Trading).
- **No GPU required**: Trains in seconds, infers in microseconds.

**Extension plan**:
1. Expand `FEATURE_NAMES` to include raw OHLCV ratios, rolling volatility, cross-asset momentum.
2. Change target from binary `{win, lose}` to ternary `{BUY, SELL, NONE}` using triple-barrier labels.
3. Register as `ML_SIGNAL_BOOST` in the strategy catalog.
4. Reuse `meta_label_walk_forward.py` for purged k-fold cross-validation (prevents leakage).

**Integration**:
```python
class MlSignalBoostStrategy(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.model = load_xgb_model(config)

    def evaluate(self, df_row) -> dict:
        features = extract_features(df_row, self.config)
        pred_class, confidence = self.model.predict_with_confidence(features)
        if pred_class == "BUY" and confidence > self.config.get("min_confidence", 0.55):
            return {"signal": "BUY", "confidence": confidence,
                    "stop_loss_distance": df_row.get("ATR_14") * 1.5}
        elif pred_class == "SELL" and confidence > self.config.get("min_confidence", 0.55):
            return {"signal": "SELL", "confidence": confidence,
                    "stop_loss_distance": df_row.get("ATR_14") * 1.5}
        return {"signal": "NONE"}
```

---

### 2.5 — Deep Reinforcement Learning (DRL) Trading Agent

> **Category**: Reinforcement Learning · PPO / SAC

**What it does**: An RL agent observes market state (OHLCV window + indicators + portfolio state) and outputs continuous actions: `{position_target: [-1, +1], stop_distance: [0.5, 3.0] × ATR}`. Instead of predicting price direction, it directly learns the **policy** that maximizes risk-adjusted PnL (Sharpe ratio as reward).

**Why RL is fundamentally different**:

| Aspect | Supervised ML | Reinforcement Learning |
|---|---|---|
| Learns from | Historical labels | Simulated trading experience |
| Optimizes | Prediction accuracy | Cumulative reward (PnL, Sharpe) |
| Handles | Pattern recognition | Full decision lifecycle (entry + sizing + exit) |
| Adapts to | Static historical distribution | Dynamic interaction with market |

**Architecture**: PPO (Proximal Policy Optimization) with:
- **Observation space**: 60-bar OHLCV + RSI + ATR + MACD + current position + unrealized PnL + portfolio heat.
- **Action space**: Continuous `[-1, +1]` (target position fraction of allocation).
- **Reward function**: `reward = sharpe_increment - transaction_cost_penalty - drawdown_penalty`.

**Training environment**: The existing backtester (`backtester.py`) is already a nearly-complete RL environment — it steps through bars, applies positions, tracks PnL. Wrapping it as a Gymnasium `env` requires ~200 lines.

**Integration**:
```python
class DrlTradingAgent(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.policy = load_ppo_policy("models/drl_agent.pt")
        self.obs_buffer = deque(maxlen=60)

    def evaluate(self, df_row) -> dict:
        self.obs_buffer.append(self._obs(df_row))
        if len(self.obs_buffer) < 60:
            return {"signal": "NONE"}
        action = self.policy.predict(np.array(self.obs_buffer))
        position_target = float(action[0])  # -1 to +1
        if position_target > 0.3:
            return {"signal": "BUY", "confidence": abs(position_target),
                    "size_factor": abs(position_target)}
        elif position_target < -0.3:
            return {"signal": "SELL", "confidence": abs(position_target),
                    "size_factor": abs(position_target)}
        return {"signal": "CLOSE" if abs(position_target) < 0.05 else "NONE"}
```

---

### 2.6 — Autoencoder Anomaly-Driven Regime Change Detector

> **Category**: Unsupervised Deep Learning · Anomaly Detection

**What it does**: A variational autoencoder (VAE) learns the "normal" distribution of market microstructure (price movements, volume patterns, spread behavior). When reconstruction error spikes, it signals a **regime change** — not a direction, but a "something unusual is happening" flag.

**How it generates signals**:
- High reconstruction error + directional momentum → amplify existing strategy signals.
- High reconstruction error + no direction → suppress entries (the regime is unstable).
- This works as a **meta-strategy layer** that modulates other strategies' output.

**Integration point**: Feeds into the existing `PreTradeIntel` gate as an additional veto/confirm signal, or modulates the `RegimeRotationAgent` to trigger faster strategy swaps.

---

### 2.7 — Graph Neural Network (GNN) Cross-Asset Signal Propagation

> **Category**: Deep Learning · Relational Learning

**What it does**: Models the crypto/equity universe as a graph where symbols are nodes and edges represent correlation strength (already computed by `correlation.py`). A GNN propagates information across related assets: if BTC breaks out and ETH hasn't moved yet, the GNN learns that ETH is likely to follow.

**Why it's powerful**:
- The existing `summarize_basket_correlation()` function already computes pairwise correlations — this is the adjacency matrix.
- Captures lead-lag relationships across assets (e.g., institutional flows hit BTC first, then alts).
- Can incorporate the `portfolio_risk.py` exposure data as node features.

**Signal mapping**:
- Node-level output: per-symbol directional forecast influenced by the entire watchlist.
- Particularly valuable for the `SCANNER` flow — rank symbols by "likelihood of imminent move" propagated from already-moving assets.

---

## 3. Recommended Implementation Priority

| Priority | Strategy | Effort | Risk | Expected Edge |
|---|---|---|---|---|
| **P0** | 2.4 — XGBoost Signal Classifier | 1-2 weeks | Low (extend existing infra) | Moderate — learns feature interactions humans miss |
| **P1** | 2.1 — LSTM Direction Classifier | 2-3 weeks | Medium | High — captures temporal patterns |
| **P2** | 2.5 — DRL Trading Agent (PPO) | 4-6 weeks | High (reward shaping is hard) | Very High — jointly optimizes full lifecycle |
| **P3** | 2.2 — TCN Multi-Horizon | 3-4 weeks | Medium | High — multi-timeframe without multi-model |
| **P4** | 2.6 — VAE Regime Detector | 2-3 weeks | Low | Moderate — improves existing regime rotation |
| **P5** | 2.3 — Transformer Signal Gen | 4-5 weeks | Medium | High — interpretable attention |
| **P6** | 2.7 — GNN Cross-Asset | 5-6 weeks | High | High for multi-symbol trading |

---

## 4. Integration Architecture

All ML strategies plug into the **existing bot pipeline** with zero changes to the runtime:

```
                    ┌─────────────────────┐
                    │   Strategy Catalog   │
                    │  strategy_catalog.py │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌──────▼──────┐ ┌───────▼───────┐
     │  TA Strategies │ │ ML Strategies│ │ RL Strategies  │
     │  (existing)    │ │  (new)       │ │  (new)         │
     │  MACD_RSI      │ │  ML_BOOST    │ │  DRL_AGENT     │
     │  SUPERTREND    │ │  LSTM_DIR    │ │                │
     │  ICT_SMC  ...  │ │  TCN_MULTI   │ │                │
     └────────┬──────┘ └──────┬──────┘ └───────┬───────┘
              │                │                │
              └────────────────┼────────────────┘
                               │
                    evaluate(df_row) → dict
                               │
                    ┌──────────▼──────────┐
                    │   Bot Manager /      │
                    │   Backtester          │
                    │   (unchanged)        │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌──────▼──────┐ ┌───────▼───────┐
     │ PreTrade Intel │ │ Risk Gate    │ │ Meta-Label    │
     │  (gate)        │ │  (sizing)    │ │  (filter)     │
     └───────────────┘ └─────────────┘ └───────────────┘
```

### New Files Required

| File | Purpose |
|---|---|
| `strategies_ml.py` | `MlSignalBoostStrategy`, `LstmDirectionStrategy`, `TcnMultiHorizonStrategy` |
| `strategies_rl.py` | `DrlTradingAgent` |
| `ml_training/train_xgb_signal.py` | XGBoost signal classifier training pipeline |
| `ml_training/train_lstm.py` | LSTM training + ONNX export |
| `ml_training/train_drl.py` | PPO/SAC training with backtester-as-environment |
| `ml_training/feature_engineering.py` | Shared feature extraction (extends `insight_to_features`) |
| `ml_training/triple_barrier.py` | Label generation for supervised methods |
| `models/` directory | Serialized model artifacts (.onnx, .pt, .json) |

### Dependencies to Add

```
# For ML strategies (add to backend requirements)
onnxruntime >= 1.18        # ONNX inference (LSTM, TCN, Transformer)
torch >= 2.3               # Training only (not needed at inference if using ONNX)
stable-baselines3 >= 2.3   # PPO/SAC for DRL agent
gymnasium >= 1.0            # RL environment interface
```

---

## 5. Training & Validation Philosophy

> [!IMPORTANT]
> The #1 failure mode of ML in trading is **overfitting to historical patterns that don't persist**. Every method below must pass walk-forward validation before live deployment.

### Anti-Overfitting Safeguards

1. **Purged k-fold CV** (already built in `backtest_purged_cv.py`): Embargo period between train/test folds to prevent look-ahead leakage.
2. **Walk-forward retraining**: Models retrain on rolling windows (e.g. train on months 1-6, test on month 7, then train on 2-7, test on 8...). Reuse `backtest_walk_forward.py`.
3. **Combinatorial Purged Cross-Validation (CPCV)**: Already in `backtest_pbo.py` — use it to measure Probability of Backtest Overfitting.
4. **Alpha decay monitoring**: The existing `alpha_decay.py` agent monitors live performance vs backtest expectations — apply it to ML strategies too.
5. **Meta-label gate on ML signals**: Even ML signals pass through the meta-label P(win) filter before execution — ML and TA signals get the same scrutiny.

### Labelling Strategy: Triple-Barrier Method

Instead of predicting raw price direction (noisy), we label each bar using the triple-barrier method from *Advances in Financial Machine Learning* (de Prado):

- **Upper barrier**: price touches `+k × ATR` → label = **UP**
- **Lower barrier**: price touches `-k × ATR` → label = **DOWN**
- **Time barrier**: neither barrier hit within N bars → label = **FLAT**

This naturally handles the asymmetry between profitable and unprofitable trades and produces cleaner labels than simple `close[t+1] > close[t]`.

### Implementation status (shipped)

| Safeguard | Status |
|---|---|
| Purge + **post-test embargo** on ML walk-forward folds | ✅ `ml_walk_forward_validator.py` |
| Walk-forward required before ML deploy | ✅ `deploy_gate` blocks unless `validated_at` / `walk_forward.ok` (escape: `ml_skip_validation_gate`) |
| PBO / CSCV | ✅ `ml_pbo_validator.py`; validate persists `pbo` into metadata; high PBO blocks deploy; missing PBO warns (`ml_require_pbo` to block) |
| Alpha decay (staleness + accuracy drift) | ✅ `alpha_decay.py` |
| Meta-label gate on ML/DL/RL entries | ✅ `ml_signal_gates.apply_ml_meta_label_gate` when `calibration_gate_enabled` |
| Triple-barrier labels | ✅ XGB / LSTM / Transformer / GNN; TCN uses multi-horizon returns; VAE/RL use their own objectives |
---

## 6. Frontend Integration

### New UI elements needed:

1. **Strategy Picker**: Add `"ML / Deep Learning"` and `"Reinforcement Learning"` category tabs to the bot config panel.
2. **Model Training Dashboard** (new dock tab): Show training progress, validation curves, feature importance plots.
3. **Attention Heatmap** (for Transformer strategy): Overlay on the chart showing which historical bars the model is attending to for the current signal.
4. **RL Episode Replay**: Visualize the RL agent's decisions across a backtest episode.
5. **Confidence Calibration Plot**: Show how well the model's confidence scores map to actual win rates (already partially built in `calibration.py`).

---

## 7. Hybrid Ensemble: The Best of Both Worlds

> [!TIP]
> The highest-performing approach is typically an **ensemble** — combine TA signals, ML signals, and RL signals via a voting/stacking mechanism.

### Proposed Ensemble Architecture

```python
class EnsembleStrategy(BaseStrategy):
    """Combines TA + ML + RL signals via weighted voting."""

    def __init__(self, config):
        super().__init__(config)
        self.ta_strategy = get_strategy(config.get("ta_strategy", "MACD_RSI"), config)
        self.ml_strategy = MlSignalBoostStrategy(config)
        self.rl_strategy = DrlTradingAgent(config)

    def evaluate(self, df_row) -> dict:
        ta = self.ta_strategy.evaluate(df_row)
        ml = self.ml_strategy.evaluate(df_row)
        rl = self.rl_strategy.evaluate(df_row)

        # Weighted vote (configurable)
        votes = {"BUY": 0, "SELL": 0, "NONE": 0}
        for result, weight in [(ta, 0.3), (ml, 0.4), (rl, 0.3)]:
            signal = result.get("signal", "NONE")
            conf = result.get("confidence", 0.5)
            votes[signal] += weight * conf

        best = max(votes, key=votes.get)
        if best != "NONE" and votes[best] > config.get("ensemble_threshold", 0.5):
            return {"signal": best, "confidence": votes[best],
                    "stop_loss_distance": df_row.get("ATR_14", 0) * 1.5}
        return {"signal": "NONE"}
```

This ensemble approach means:
- If TA and ML agree but RL disagrees → signal fires (2 out of 3).
- If only one method fires → signal suppressed (high bar reduces false signals).
- Weights are adaptive — set `ensemble_adaptive_weights` on bot config (e.g. `{ta, ml, rl}`) to override static weights; post-trade learner can write this blob later.

### Implementation status (shipped)

| Piece | Status |
|---|---|
| `HybridEnsembleStrategy` weighted vote | ✅ `strategies_ensemble.py` (`HYBRID_ENSEMBLE`) |
| Configurable TA / ML / RL legs + weights + threshold | ✅ `indicators.STRATEGY_DEFAULTS` + bot config fields |
| Optional ≥2-component agreement | ✅ `ensemble_require_agreement` |
| Meta-label gate on ensemble entries | ✅ via `apply_ml_meta_label_gate` |
| Deploy gate checks **component** ML/RL models + ML WF | ✅ `deploy_gate` ensemble branch |
| Catalog + frontend picker | ✅ strategy catalog / `strategies.js` (not in Model Training list — train legs separately) |
| Alpha decay uses ML leg artifact | ✅ `alpha_decay.py` |
---

## 8. Risk & Limitations

> [!WARNING]
> ML trading models are powerful but carry specific risks that don't apply to rule-based TA strategies.

| Risk | Mitigation |
|---|---|
| **Overfitting** | Walk-forward validation, CPCV, alpha decay monitoring |
| **Regime sensitivity** | Retrain regularly; VAE anomaly detector flags distribution shifts |
| **Black-box decisions** | Feature importance (XGBoost), attention maps (Transformer), SHAP values |
| **Training data quality** | Use the existing candle archive; validate for gaps and outliers |
| **Computational cost** | ONNX Runtime for inference (~1ms/bar); GPU only needed for training |
| **Reward hacking (RL)** | Careful reward shaping; include transaction costs and drawdown penalties |

---

## 9. Summary

This proposal introduces **7 ML/DL/RL methods** that generate tradable signals as direct alternatives to the existing TA strategies:

1. **LSTM Direction Classifier** — temporal pattern learning
2. **TCN Multi-Horizon Forecaster** — parallel multi-timeframe predictions
3. **Transformer Attention Signal** — interpretable attention-based decisions
4. **XGBoost Signal Classifier** — fastest to deploy, extends existing meta-label infra
5. **DRL Trading Agent (PPO)** — learns full entry→sizing→exit policy
6. **VAE Regime Detector** — unsupervised anomaly detection for regime gates
7. **GNN Cross-Asset Propagation** — learns lead-lag relationships across symbols

All integrate through the same `BaseStrategy.evaluate()` contract and slot into the existing bot manager, backtester, risk gate, and meta-label pipeline without breaking changes.

**Recommended starting point**: Method 2.4 (XGBoost) because the infrastructure is 70% built — extend `meta_label_model.py` from a gate into a primary signal source in 1-2 weeks.
