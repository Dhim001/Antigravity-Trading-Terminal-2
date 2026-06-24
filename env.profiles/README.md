# Dual-instance environment profiles

Use these when running **simulated**, **IB**, and **Massive** terminals side by side (see `scripts/start-sim.ps1`, `scripts/start-ib.ps1`, `scripts/start-massive.ps1`).

## How loading works

1. `backend/app/config.py` loads repo-root `.env` (optional, gitignored — shared secrets).
2. If `TERMINAL_PROFILE` is set (`sim`, `ib`, or `massive`), loads `env.profiles/{profile}.env` **on top** (profile wins).

Launch scripts set `TERMINAL_PROFILE` for you. Your existing repo-root `.env` is not overwritten.

## Files

| File | Purpose |
|------|---------|
| `sim.env` | `TERMINAL_MODE=SIMULATED`, ports 8765/8766, `trading-sim.db` |
| `ib.env` | `TERMINAL_MODE=LIVE_IB`, ports 8775/8776, `trading-ib.db`, IB Gateway settings |
| `massive.env` | `TERMINAL_MODE=LIVE_MASSIVE`, ports 8785/8786, `trading-massive.db`, stocks + crypto WS |

Frontend Vite profiles live in `frontend/env.profiles/` (dev ports 5173 / 5174 / 5175).

## Customize

- **IB port / client ID:** edit `ib.env` (`IB_PORT`, `IB_CLIENT_ID` — use a different client ID than other IB apps).
- **Separate portfolios:** `SQLITE_DB_PATH` per profile (already set).
- **Shared API keys:** keep in repo-root `.env`; profiles only override mode/ports/DB.

## Notes

- `LIVE_IB` is **feed-only** by default (`IB_OMS_ENABLED=false`). Set `IB_OMS_ENABLED=true` for real IB paper/live orders.
- `IB_BROADCAST_INTERVAL_SEC` (default 1.5s) controls how often the IB backend pushes quotes to the UI over WebSocket.
- IB instance serves **equities only**; crypto symbols are sim-only.
- **Massive** instance is feed-only (simulated OMS); equities via `/stocks` (AM/T/Q), crypto 24/7 via `/crypto` (XA/XT/XQ). Terminal crypto symbols map to Massive `BTC-USD` style pairs. REST poll fallback activates when WebSocket auth fails or `MASSIVE_WS_ENABLED=false`.
- **Massive bots:** `ALLOW_LIVE_BOTS=true` in `massive.env` runs paper bots on live quotes with **simulated fills** (no real broker routing).
- Stop backends with Ctrl+C so IB disconnects cleanly (`feed.stop()`).
