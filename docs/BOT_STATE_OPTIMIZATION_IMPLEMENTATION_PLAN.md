# Optimize Bot State & Implement Dynamic Win-Rate Feedback

Based on the research suggestions, we will refactor the strategy state buffers for bounded memory efficiency and inject post-trade feedback to scale bot risk autonomously.

## Proposed Changes

### 1. Memory Optimization (`collections.deque`)
**Goal:** Prevent CPU spikes and `O(N)` memory bloat in live bots by replacing standard Python lists with highly optimized C-level double-ended queues.

#### [MODIFY] `strategies_microstructure.py`
- Replace `self.history = []` with `self.history = deque(maxlen=N)`.
- Remove manual `self.history.pop(0)` slice/delete operations (the `deque` automatically drops the oldest element natively when `maxlen` is reached).
- Apply to `CvdDivergenceStrategy` (maxlen=20), `WyckoffStrategy` (maxlen=30), and `VpocReversionStrategy` (maxlen=lookback).

### 2. Time Stops (Time-in-Trade Exits)
**Goal:** Automatically exit trades that stagnate and fail to materialize momentum within N bars, freeing up capital rather than waiting for a full stop-loss hit.

#### [MODIFY] `manager.py` (`_evaluate_bar_close_bots` & `process_price_tick`)
- Before calling `strat.evaluate(eval_row)`, check the bot's current position and `opened_at` timestamp.
- If `bot_config.get("time_stop_bars") > 0`, calculate `bars_elapsed` using the bot's timeframe in milliseconds.
- If `bars_elapsed >= time_stop_bars`, preemptively emit a `CLOSE` signal with the reason `"Time stop reached"` bypassing the strategy's standard evaluation.

### 3. Regime-Adaptive Sizing (Kelly Criterion Sizing)
**Goal:** Bots should automatically halve their trade sizing if they enter a drawdown (e.g., 3 consecutive losses), and restore sizing when they secure a win.

#### [MODIFY] `positions.py`
- Add `get_recent_closed_trades_pnl(bot_id: str, limit: int = 3) -> list[float]` to query the `bot_trades` database table for the PNL of the bot's most recent exited trades.

#### [MODIFY] `manager.py` (`_execute_order`)
- Right before final quantity is calculated, if `bot_config.get("use_regime_sizing", True)` is active, fetch the last 3 PNLs.
- If all 3 are negative (`pnl < 0`), apply a `0.5x` scalar to the `quantity` to aggressively protect capital during unfavorable market regimes.

## Verification Plan

### Automated Tests
- Syntax check `manager.py`, `positions.py`, and `strategies_microstructure.py`.
- Run pytest for core strategy evaluations.

### Manual Verification
- Deploy a bot with `time_stop_bars = 2` and verify it automatically scratches the trade after 2 bars.
- Inspect the logs for Regime Sizing adjustments.
