# Execution Quality & Portfolio Risk Intelligence Plan

## Problem

Two measurement/coordination gaps remain after the signal, optimizer, and memory programs:

1. **No Transaction Cost Analysis (TCA).** Phase 4.10 shipped VWAP/POV/TWAP execution
   slicing and Phase 1 shipped a square-root impact cost model — but nothing *measures*
   live execution quality. There is no arrival-price benchmark, no implementation
   shortfall (IS) telemetry, and no feedback loop from measured live slippage into the
   backtest cost model. We cannot answer "is POV better than VWAP for ETHUSDT?" or
   "is my backtest slippage assumption right?" — the two questions that close the
   backtest-to-live gap. Grep-proof: no `arrival_price`, `implementation_shortfall`,
   or `slippage_vs` anywhere in `backend/app/services`.

2. **Account-level risk coordination is partial.** `portfolio_risk.py` caps gross and
   correlation-group exposure and `risk_sentinel.py` pauses bots on breach — but there
   is no **contradictory-position blocker** (two bots can be long and short the same
   symbol simultaneously), no **per-bot drawdown budget ladder** (reduce → flatten →
   stop at configurable milestones), and no **account-level daily-loss kill-switch**
   that halts/derisks the whole fleet at once (only per-bot daily-loss blocks exist).

## Research summary (2024–2026)

Implementation shortfall (Perold) is the standard, ungameable execution benchmark:
paper portfolio at decision price vs. real fills, decomposed into **spread + delay +
impact + opportunity** costs. IS is the metric execution algos (VWAP/TWAP/POV/IS) are
formally judged against. Post-trade TCA feeds pre-trade calibration. Adaptive
execution (MPC-style re-solving, or simpler arrival-anchored aggressiveness shifts)
shows 40–50% schedule-shortfall reductions in research; we adopt the *light* version
(aggressiveness adjustment around the arrival benchmark, bounded). On the risk side,
production multi-bot fleets converge on: aggregate exposure caps (have it), portfolio
daily-loss kill-switch (missing), correlation monitoring (have it), per-strategy
drawdown **budgets with milestone actions** (50% → reduce size, 80% → flatten +
freeze entries, 100% → stop), and a **contradictory-position blocker**.

## Rollout discipline

Everything here is **off by default**, opt-in via per-bot config flags or env vars,
exactly like the Signal Enhancement Plan. TCA recording (Phase 1) is read-only
telemetry and may default to ON at low verbosity since it never blocks trades; every
*acting* feature (adaptive execution, budgets, kill-switch) defaults OFF.

## Phases

### Phase 1 — TCA capture: arrival benchmark + IS decomposition (S effort) ✅ SHIPPED
1. Record `arrival_price` (feed mark/mid at order decision) on every live order in
   `manager._execute_order` (and per slice in `execution_algos.execute_sliced_order`).
   ✅ Arrival snapshot (feed mark + bid/ask) captured pre-submission via
   `execution_tca.capture_arrival`; sliced orders record under their resolved algo
   name; signal price doubles as the decision benchmark for delay attribution.
2. On fill, compute IS in bps vs. arrival; decompose into spread / delay / impact /
   opportunity components. Unfilled parents record opportunity cost.
   ✅ `execution_tca.compute_is` — pure decomposition, positive = cost; spread split
   from the arrival quote when known; partial fills price the unfilled remainder
   against the current mark as opportunity cost.
3. New table `execution_quality_log` (bot, symbol, strategy, algo, side, qty,
   arrival, avg_fill, is_bps, spread_bps, delay_bps, impact_bps, opp_bps, fees, ts)
   with retention wiring (same startup pass as reject-log).
   ✅ Portable `_serial_type()` DDL + 3 indexes; `prune_execution_quality_log`
   (30-day window + 200k row cap) wired into the `server.py` startup retention
   pass; `EXEC_QUALITY_LOG_ENABLED` kill-switch. Live orders carry the arrival
   benchmark on `bot_pending_fills` (new `_safe_alter` columns) so reconciled
   broker fills are measured against the submission-time mark.

### Phase 2 — TCA analytics + backtest calibration loop (M effort) ✅ SHIPPED
4. Aggregates by algo × symbol × strategy × regime; `GET /api/v1/execution/quality`
   + Execution Quality section in Bot Detail / Analytics panels (IS trend, algo
   comparison table, worst-fill list).
   ✅ `execution_tca.execution_quality_dashboard` — KPIs + by-algo/symbol/strategy
   breakdowns + daily IS trend + worst-fills (regime grouping deferred: no regime
   column on the log; noted for Phase 3). `GET /api/v1/execution/quality` with
   bot/symbol/strategy/hours filters. `ExecutionQualityPanel.jsx` (KPI strip,
   SVG IS trend, algo table, worst fills, calibration card) mounted as an
   "Execution quality" section in the Bot Detail drawer.
5. **Backtest cost calibration**: measured live slippage per symbol → suggested
   `slippage_bps` / sqrt-impact coefficient for backtests, surfaced as a one-click
   "Calibrate backtest costs from live" action and an automatic nightly suggestion
   (champion-challenger style, operator approves).
   ✅ `execution_calibration.py` — measured exec cost (avg spread+impact) × 1.25
   safety → `suggested_slippage_bps` (clamped, min-10-sample gate); avg delay →
   `suggested_latency_bps` (maps to `CostModel.latency_slippage_bps`). Persisted
   in `execution_cost_calibration` (upsert preserves `applied_at`); recomputed in
   the server startup pass + on-demand. `GET /api/v1/execution/cost-suggestions`
   (merges insufficient-data symbols), `POST .../apply` stamps approval and
   returns the patch; the panel's Apply button writes it into `botConfig`
   (`slippage_bps` + `latency_slippage_bps`) via `updateBotConfig` — the same
   binding the manual "Slip bps" input uses.

### Phase 3 — Adaptive execution, MPC-lite (M effort) ✅ SHIPPED
6. Arrival-anchored aggressiveness: sliced orders speed up on favorable drift and
   slow on adverse drift, bounded to ±50% schedule deviation, per-bot flag
   (`execution_adaptive: true`). Algo choice per order informed by Phase 2 stats
   (e.g. prefer POV when measured impact > X bps).
   ✅ `AdaptivePacer` in `execution_algos.py` — drift vs arrival in cost terms
   (signed per side); ≥±`adaptive_drift_threshold_bps` (15) compresses the gap
   ×0.5 / stretches ×2.0; cumulative deviation clamped to ±50% of the planned
   schedule. Wired into `execute_sliced_order` via `pacer` + `mark_price_fn`
   (feed mark via `execution_tca.capture_arrival`; failures fall back to the
   planned gap). `execution_algo: "adaptive"` resolves per order through
   `choose_adaptive_algo` — measured impact > `adaptive_impact_threshold_bps`
   (10) → POV, else VWAP — and implies adaptive pacing; `execution_adaptive:
   true` enables pacing for explicit vwap/pov too. Defaults + backtest parity
   cache keys registered.
   **Bonus fix:** inter-slice sleeps now index slices by list position instead
   of `sl.index` — POV schedules that skip zero-volume bars (non-sequential
   indices) previously mis-timed or dropped gaps (regression test included).

### Phase 4 — Contradictory-position blocker (S effort) ✅ SHIPPED
7. Fleet-wide side check at entry: another RUNNING bot holding the opposite side of
   the same symbol blocks (or nets down) the new entry — `allow_contrary_positions:
   false` per bot, default false = block only when *both* bots opt in
   (`contrary_position_policy: "block" | "net" | "allow"`).
   **Implementation:** `risk_gate.check_contrary_position` (pure, both-bots-must-opt-in
   semantics — default `allow` bots never interfere); `_execute_order` builds the
   fleet from `active_bots` + signed per-bot positions, blocks or nets the entry,
   and records blocked telemetry. `block` rejects; `net` subtracts opposing size
   and rejects when fully netted.

### Phase 5 — Per-bot drawdown budget ladder (M effort) ✅ SHIPPED
8. Config `dd_budget_pct` + milestone actions: 50% consumed → halve size; 80% →
   flatten + freeze entries; 100% → stop bot (mirrors existing per-bot drawdown-hold
   plumbing in `risk_gate._compute_drawdown_hold`, extended from binary hold to a
   ladder). Telemetry into the existing reject/blocked-event channels.
   **Implementation:** `risk_gate.evaluate_dd_ladder` (pure tier evaluation: tier 1
   ≥50% size_mult, tier 2 ≥80% freeze+flatten, tier 3 ≥100% stop).
   `manager._execute_order` runs the ladder BEFORE `validate_trade` so side effects
   fire: tier 1 halves entry quantity, tier 2 blocks + `_flatten_bot_for_ladder`
   closes the open position once per tier crossing, tier 3 stops the bot (STOPPED).
   Tier state tracked per bot (`_dd_ladder_tiers`) with de-escalation logging on
   recovery. `get_bot_entry_hold` surfaces tier ≥2 as `kind: "dd_budget"` for the
   UI chip and the `_check_streak_and_cooloff` backstop. Live-only, mirroring the
   existing drawdown breaker; defaults + parity cache keys registered.

### Phase 6 — Account daily-loss kill-switch + fleet de-risk flag (M effort)
9. `ACCOUNT_DAILY_LOSS_KILL_PCT` (env): on breach → pause all RUNNING bots, optional
   flatten-all (`ACCOUNT_DAILY_LOSS_FLATTEN=true`), requires manual re-arm — extends
   `risk_sentinel`, which already pauses bots proactively.
10. Fleet OK / DE_RISK / KILL flag driven by the existing HMM/VAE regime +
    correlation-breach signals: DE_RISK scales every bot's budget by
    `derisk_size_mult` (default 0.5), KILL equals the daily-loss kill-switch.

## Integration points

| Enhancement | Touches | New files |
|-------------|---------|-----------|
| Arrival benchmark ✅ | `manager._execute_order` (+ `reconcile_pending_fills`) | — |
| IS decomposition + log ✅ | `analytics.record_pending_fill`, `database.py`, `server.py` retention | `execution_tca.py` ✅, `test_execution_tca.py` ✅ |
| TCA aggregates/API/UI ✅ | `api/http/app.py` (3 routes), Bot Detail drawer | `ExecutionQualityPanel.jsx` ✅ |
| Cost calibration ✅ | `execution_calibration.py` suggestions → `updateBotConfig` patch | `test_execution_calibration.py` ✅ |
| Adaptive execution ✅ | `execution_algos.py` (`AdaptivePacer`, `choose_adaptive_algo`), `manager._execute_order` pacing hook | `test_adaptive_execution.py` ✅ |
| Contradictory blocker ✅ | `risk_gate.check_contrary_position`, `manager._execute_order` fleet build | `test_risk_ladder_contrary.py` ✅ |
| DD budget ladder ✅ | `risk_gate.evaluate_dd_ladder` + `get_bot_entry_hold`, `manager._execute_order` (pre-validate) + `_flatten_bot_for_ladder` | `test_risk_ladder_contrary.py` ✅ |
| Kill-switch + fleet flag | `risk_sentinel.py` | — |

## Verification plan

- Unit tests: IS decomposition math (known fill sequences → known bps), retention
  prune, contradictory-blocker matrix (long/short × block/net/allow), budget ladder
  transitions, kill-switch arm/fire/re-arm, adaptive pacing bounds.
- Integration: paper session on Alpaca demo — force a sliced POV order, verify
  `execution_quality_log` rows and aggregate endpoint; trigger contradictory entry
  and daily-loss kill on synthetic equity.
- Existing suites must stay green (`pytest`, `vitest`); the 6 documented
  pre-existing backend failures remain the only red.

## Sources (2024–2026)

- Perold IS framework + arrival price: https://ryanoconnellfinance.com/implementation-shortfall/
- Institutional TCA, sqrt-impact pre-trade models, IS decomposition: https://www.quodfinancial.com/transaction-cost-analysis-tca-institutional-trading/
- IS cost decomposition (spread/delay/impact/opportunity): https://hftradingbook.com/costs/implicit-costs
- Arrival vs decision benchmark, post-trade algo diagnostics: https://www.cube.exchange/what-is/implementation-shortfall
- MPC adaptive execution (40–50% schedule-shortfall cut): https://arxiv.org/pdf/2603.28898
- Drawdown budget ladder (50/80/100 milestones), joint-tail stress: https://kiploks.com/research/drawdown-budget-allocation-for-a-live-algo-trading-portfolio
- Fleet oversight: aggregate caps, daily-loss kill-switch, correlation watch: https://openclawtraderpro.com/en/strategies/multi-strategy-portfolio/
- Contradictory-position blocker, daily loss limit, re-entry cooldown (production fleet): https://github.com/yakub268/algo-trading-platform
- Regime-aware QP capital allocation, OK/DE_RISK/KILL flags: https://github.com/LORD-ZYTHOZ/regime-aware-strategy-allocator-public
- Correlation-constrained strategy selection, HRP, 10% caps: https://github.com/angel4angelov-glitch/Multi-Strategy-Allocation-Engine
