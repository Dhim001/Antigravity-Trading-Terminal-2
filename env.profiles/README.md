# Dual-instance environment profiles

Use these when running **simulated**, **IB**, **Massive**, and **Alpaca** terminals side by side (see `scripts/start-sim.ps1`, `scripts/start-ib.ps1`, `scripts/start-massive.ps1`, `scripts/start-alpaca.ps1`).

## How loading works

1. `backend/app/config.py` loads repo-root `.env` (optional, gitignored — shared secrets).
2. If `TERMINAL_PROFILE` is set (`sim`, `ib`, `massive`, or `alpaca`), loads `env.profiles/{profile}.env` **on top** (profile wins).

Launch scripts set `TERMINAL_PROFILE` for you. Your existing repo-root `.env` is not overwritten.

**Restart stuck dev servers** (kills processes on that profile's WS/HTTP/UI ports, then opens fresh windows):

```powershell
.\scripts\start-alpaca.ps1 -Restart
.\scripts\start-massive.ps1 -Restart
.\scripts\start-sim.ps1 -Restart
.\scripts\start-ib.ps1 -Restart
```

See [docs/MEMORY_16GB.md](../docs/MEMORY_16GB.md) for browser memory caps and 16 GB workstation guidance.

## Files

| File | Purpose |
|------|---------|
| `sim.env` | `TERMINAL_MODE=SIMULATED`, ports 8765/8766, `trading-sim.db` |
| `ib.env` | `TERMINAL_MODE=LIVE_IB`, ports 8775/8776, `trading-ib.db`, IB Gateway settings |
| `massive.env` | `TERMINAL_MODE=LIVE_MASSIVE`, ports 8785/8786, `trading-massive.db`, stocks + crypto WS |
| `alpaca.env` | `TERMINAL_MODE=LIVE_ALPACA`, ports 8795/8796, `trading-alpaca.db`, equities + crypto + options WS |

Frontend Vite profiles live in `frontend/env.profiles/` (dev ports 5173 / 5174 / 5175 / 5176).

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
- **Alpaca** instance uses Alpaca market data. Order routing defaults to app SimulatedOMS on the alpaca profile (`ALPACA_OMS_ENABLED=false`) for safe bot testing; set `ALPACA_OMS_ENABLED=true` to route through Alpaca paper/live REST (`ALPACA_BASE_URL`). Equities via SIP/IEX, crypto via `v1beta3/crypto/us` (terminal `BTCUSDT` → wire `BTC/USD`; watchlist limited to Alpaca US bases), options via `v1beta1/indicative|opra` (streamed for charts/OMS — OCC contracts are **not** listed in the Watchlist sidebar). WS connect sends a full quote snapshot so late joiners / weekend equities are not stuck at `…`. **ML training, backtests, optimize, bots, and archive ingest** resolve history through Alpaca REST on this profile (Massive/Polygon is not used even when `MASSIVE_API_KEY` is present in root `.env`). Only one process may hold the SIP equity stream per Alpaca account.
- **Alpaca bots:** `ALLOW_LIVE_BOTS=true` in `alpaca.env`. With `ALPACA_OMS_ENABLED=false` (profile default), bots fill via SimulatedOMS on live Alpaca quotes; set `ALPACA_OMS_ENABLED=true` + restart for broker OMS.
- **Alpaca UI extras:** left-rail **Movers** tab + `GET /api/v1/market/movers` / `GET /api/v1/news/market`; feed banner via `GET /health/alpaca`.
- Stop backends with Ctrl+C so IB disconnects cleanly (`feed.stop()`).
