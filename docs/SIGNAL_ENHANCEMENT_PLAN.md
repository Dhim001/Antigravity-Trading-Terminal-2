# Agentic Bot Signal Enhancement Plan

## Problem

Agentic bots underperform. Root causes found in code:
1. HTF gate uses crude 2-bar heuristic (`manager._get_htf_bias` L1017)
2. Strategy filter fails open on errors (`strategy_filter.py` L72)
3. Calibration / meta-label off by default (`indicators.py` L75)
4. Duplicate PreTradeIntel calls (`manager.py` L1207 + L1284)
5. Hardcoded thresholds (score ±2, ADX 40, SL 2%, RL 0.28 vs ML 0.55)
6. RL shadow position drift (`strategies_rl.py` L240)
7. ABSORPTION_AGENT has no confidence/reject
8. TCN/RL silent NONE without reject_reason

## Rollout discipline

Every enhancement in this plan is **off by default** and opt-in via per-bot config
flags. A bot whose config is untouched behaves exactly as before. "Operator" = the
person managing bots through the app UI / API. To turn a feature on, set the
relevant key on the bot's config JSON — e.g.:

```json
{
  "calibration_gate_enabled": true,
  "conformal_gate_enabled": true,
  "conformal_alpha": 0.10,
  "use_kelly_sizing": true,
  "kelly_fraction": 0.25,
  "hmm_regime_gate_enabled": true,
  "ensemble_combination": "stacking"
}
```

Flags are read at signal-evaluation time, so toggling takes effect on the next
bar — no restart needed. Defaulting to off keeps existing bots safe and lets
each enhancement be tuned per-symbol / per-strategy before rollout. A global
env-var kill-switch or UI toggle can be layered on later if desired.

## Phases

### Phase 1 — Quick Wins (S effort) — SHIPPED
1. Temperature scaling + fractional-Kelly sizing
2. Conformal prediction gate via MAPIE
3. Square-root market impact + latency-aware fills

### Phase 2 — Core Signal Quality (M effort) — SHIPPED
4. Enable + tune triple-barrier meta-labeling
5. HMM regime gate (soft posterior-weighted)
6. Stacking meta-learner (inverse-MSE → gating network)

### Phase 3 — Alpha Enrichment (M effort) — SHIPPED
7. CVD + VPIN microstructure features
8. Champion-challenger + drift-triggered retraining
9. LLM Bull/Bear/Judge debate + deterministic firewall

### Phase 4 — Execution & Telemetry (M effort) — SHIPPED
10. VWAP/POV execution slicing
11. Reject telemetry for silent NONEs

## Integration Points

| Enhancement | Touches | New files |
|-------------|---------|-----------|
| Temp scaling + Kelly | `strategy_runtime.scale_entry_quantity` (live + BT) | `calibration_fitter.py` |
| Conformal gate | `strategy_runtime.apply_shared_signal_gates` + chart agent | `conformal_gate.py` |
| Sqrt impact | `backtester.py` cost model | — |
| Meta-label enable | `indicators.py` L75 | — |
| HMM regime | `strategy_runtime.apply_shared_signal_gates` | `hmm_regime.py` |
| Stacking | `strategies_ensemble.py` | `stacking_meta_learner.py` |
| CVD/VPIN | `indicators.py` schema | — |
| Champion-challenger | `ml_retrain_scheduler.py` | `model_promotion.py` |
| LLM debate | live `manager` via `maybe_apply_llm_debate`; BT skips LLM (flags `llm_debate_skipped`) | `agent/debate.py` |
| VWAP/POV | live OMS only | `execution_algos.py` |
| Reject telemetry | live manager | `reject_telemetry.py` |
| Time stop / PreTrade parity | `backtester` mirrors `time_stop_bars` + deterministic PreTrade subset | `strategy_runtime.evaluate_parity_pretrade` |

## Sources (2024-2026)
- Calibrated Conviction: https://github.laiyagushi.com/ryonzhang/calibrated-conviction
- IEEE Access 2026 Uncertainty-Aware Forecasting: https://doi.org/10.1109/access.2026.3702666
- MAPIE: https://github.com/scikit-learn-contrib/MAPIE
- Wayland Zhang Backtest-to-Live Gap: https://waylandz.com/blog/backtest-to-live-gap/
- mlfinlab: https://github.com/hudson-thames/mlfinlab
- RegimeForecast HMM: https://regimeforecast.com/blog/hidden-markov-models-market-regimes-python
- Stacked Ensemble IJFS 2025: https://doi.org/10.3390/ijfs13040201
- MEXC VPIN alpha: https://www.mexc.co/news/1002105
- ML4Trading MLOps: https://ml4trading.io/third-edition/chapters/26_mlops_governance/
- APEX Multi-agent: https://github.com/Yasirpyro/Multi-agent-Trading-System
- Talos VWAP vs TWAP: https://www.talos.com/insights/vwap-or-twap-for-crypto-execution-a-market-impact-perspective
