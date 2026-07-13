# Scanner Auto-Deploy Agent Implementation Plan

This plan outlines the architecture and tasks for implementing the **Scanner Auto-Deploy Agent** (`SCANNER_DEPLOY`), which autonomously hunts for high-probability setups and deploys capital.

## Architecture

### 1. `backend/app/services/bots/scanner_deploy.py`
Create the new `ScannerDeployAgent` class.
- **Initialization**: Accepts `bot_manager`, `agent_event_bus`.
- **Evaluation Loop (`evaluate`)**:
  - Run the `bot_manager.screener.scan` on a predefined watchlist (e.g., top 20 crypto pairs).
  - Filter results using the existing `rank_scan_rows` with strict thresholds (`min_confidence >= 0.65`, `min_score >= 3`).
  - **Capital & Concurrent Bots Gate**: Ensure total deployed capital across all running bots does not exceed a `MAX_PORTFOLIO_ALLOCATION` (e.g., $10,000) AND max concurrent auto-deployed bots is <= `MAX_CONCURRENT_AUTO_BOTS` (e.g., 5).
  - **Correlation Gate**: Reject deployment if the asset has a correlation `> 0.6` with existing active positions (leveraging the existing correlation utilities).
  - **Backtest Validation Gate**: For surviving candidates, run a quick 7-day historical backtest via `bot_manager.backtester`. Only proceed if out-of-sample PnL > 0 and Win Rate > 50%.
  - **Dynamic Allocation & Deployment**: Compute an allocation amount scaled by confidence, inject risk guardrails (e.g., hard stop loss at 5% drawdown), and call `bot_manager.create_bot`.
  - **AgentReasoning**: Formulate an `AgentReasoning` chain documenting the confidence, checks passed, and publish a `BOT_DEPLOYED` event to the `AgentEventBus`.

### 2. `backend/app/services/bots/runtime.py`
- Wire the agent into the background loop ecosystem.
- Add `scanner_deploy_loop(bot_manager)` similar to `regime_rotation_loop`, running continuously on a configurable interval (e.g., 15 minutes).
- Add the config toggles in `backend/app/config.py` to control the agent.
