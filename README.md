# Antigravity Trading Terminal

A full-stack, real-time trading terminal with a Python WebSocket backend and a React + Vite frontend. The app supports simulated and live market data, order execution, portfolio tracking, algorithmic strategies, and a professional charting workspace — styled with **shadcn/ui** and **Tailwind CSS v4**.

---

## Current Progress

| Area | Status |
|------|--------|
| Simulated market feed (SBBS + yfinance cache) | Done |
| Live feeds: Alpaca, Binance, eToro | Done |
| OMS: market/limit orders, SL/TP, FIFO P&L | Done |
| 25 symbols (15 equities/ETFs + 10 crypto) | Done |
| Charting (ECharts) + 9 overlays + signal badge | Done |
| Multi-chart grid view | Done |
| Bottom dock: positions, orders, balances, algo, history, equity | Done |
| Algo bot engine (4 strategies + backtester) | Done |
| Bot risk gates, pause/resume, analytics | Done |
| Distributed runtime (Redis worker split) | Done |
| PostgreSQL + custom strategy plugins | Done |
| Docker Compose (Redis/Postgres) + header bot controls | Done |
| Admin / simulation controls | Done |
| shadcn/ui design system migration | Done |
| Symbol command palette (⌘K) | Done |

---

## Architecture

```mermaid
graph TD
    subgraph Frontend [React + Vite + shadcn/ui]
        UI[Dashboard] <--> Store[Zustand Store]
        Store <--> WS_Client[WebSocket Client]
        UI --> Chart[ECharts]
        UI --> Dock[Resizable Dock]
        UI --> Palette[Command Palette]
    end

    subgraph Backend [Python WebSocket Server]
        WS_Server[server.py] <--> WS_Client
        WS_Server --> Mode{TERMINAL_MODE}
        Mode -->|SIMULATED| SimFeed[sim_feed + synthetic_data]
        Mode -->|LIVE_ALPACA| Alpaca[alpaca_feed / alpaca_oms]
        Mode -->|LIVE_BINANCE| Binance[binance_feed / binance_oms]
        Mode -->|LIVE_ETORO| Etoro[etoro_feed / etoro_oms]
        WS_Server --> Bots[Bot Manager + Screener + Backtester]
        WS_Server --> OMS[Order Management]
        OMS --> DB[(SQLite trading.db)]
    end
```

---

## Features

### Trading & portfolio
- **Market and limit orders** with pre-trade risk limits
- **Stop-loss / take-profit** on open positions
- **FIFO realized P&L** and live unrealized P&L
- **Order book** and **balance** views in the resizable bottom dock
- **Trade history** blotter with filters, sorting, CSV export, and full-screen Sheet view
- **Equity curve** tab with cumulative P&L and drawdown (ECharts)

### Market data & charts
- **Single-chart** and **multi-chart grid** layouts (⌘1 / ⌘2)
- **Timeframes**: 1m, 5m, 15m, 1H, 4H, 1D
- **Technical overlays**: EMA 9/21/50, Bollinger Bands, VWAP, Volume, RSI, MACD, ATR
- **Signal badge** with rule-based analysis popover (BUY / SELL / NEUTRAL)
- **Market overview strip** with scrolling tickers
- **Watchlist** with category filters (Crypto / Equity / ETF), search, and sparklines

### Simulation engine
- **Stationary Bootstrap (SBBS)** synthetic candles seeded from 7-day 1m yfinance history
- Parquet cache in `backend/data/` (auto-fetched, gitignored)
- Admin controls: tick speed, volatility, directional bias, balance seeding, emergency stop, full reset

### Algorithmic trading
- **Bot manager** persists bots and logs to SQLite
- **Market screener** computes indicators via `pandas-ta`
- **Four built-in strategies**:
  - `MACD_RSI` — MACD crossover + RSI filter
  - `BRS_SCALPING` — Bollinger + RSI + Stochastic
  - `SUPERTREND_ADX` — SuperTrend flip + ADX confirmation
  - `VWAP_PULLBACK` — VWAP mean-reversion entries
- **Backtester** service for offline strategy evaluation
- Dock **Algo Bot** tab: strategy templates, capital allocation, live bot logs, backtest equity curve, deploy confirmation
- **Risk gates**: allocation cap, daily loss halt, signal cooldown, pause/resume/stop-all
- **Bot analytics**: per-bot trades, snapshots, detail panel, chart trade markers
- **Config-driven strategies**: indicator periods wired from bot `config`
- **Optional `CUSTOM` strategy plugins** in `backend/strategies/` (`ALLOW_CUSTOM_STRATEGIES=true`)

### Distributed runtime (Phase 6)

By default everything runs in one process (`TERMINAL_ROLE=all`). For scale, split the WebSocket server from the bot engine via Redis:

| Role | Command | Responsibility |
|------|---------|----------------|
| `all` (default) | `python main.py` | WS + feed + bot engine |
| `server` | `python main.py` | WS + feed; publishes bar-close events |
| `worker` | `python worker.py` | Bot engine only; consumes Redis events |

Requires `REDIS_URL`. Optional `DATABASE_URL` for PostgreSQL instead of SQLite. See `backend/.env.example`.

### Live integrations
Set `TERMINAL_MODE` in `.env` to switch backends:

| Mode | Feed | Symbols | Notes |
|------|------|---------|-------|
| `SIMULATED` (default) | SBBS simulator | Equities + crypto | No API keys required |
| `LIVE_ALPACA` | Alpaca WebSocket | US equities & ETFs | Paper or live via `ALPACA_BASE_URL` |
| `LIVE_BINANCE` | Binance streams | Crypto USDT pairs | Requires API keys |
| `LIVE_ETORO` | REST poll (`/market-data/instruments/rates`) | Equities + crypto | Bearer **or** API-key pair (never both); demo/real env auto-probe |

---

## Frontend UI

Built on **React 19**, **Vite 8**, **Zustand**, **ECharts**, and **shadcn/ui** (Radix + Tailwind v4).

- **`WidgetShell`** — shared widget chrome (header, toolbar, empty states)
- **`StatCard`** — compact metric tiles in history and equity panels
- **`SymbolCommandPalette`** — fuzzy symbol search and view switching
- **Keyboard shortcuts**
  - `⌘K` / `Ctrl+K` — open command palette
  - `⌘1` / `Ctrl+1` — single chart view
  - `⌘2` / `Ctrl+2` — multi-chart view
- Trading-specific button variants: `buy`, `sell`, `live` badges
- **HTTP bootstrap (Phase 4a)** — on mount, parallel REST calls hydrate account, history, bots, and chart candles before WebSocket connects; live ticks still stream over WS. See `frontend/src/api/` and `hooks/useBootstrap.js`. Dev uses Vite proxy (`vite.config.js`); set `VITE_HTTP_BASE_URL` for production builds (`frontend/.env.example`).
- **Unified transport (Phase 4c)** — `sendAction()` in `frontend/src/api/transport.js` tries WebSocket first, then falls back to the matching REST endpoint when WS is offline (orders, bots, admin, SL/TP, etc.).

---

## Project Structure

```
trading-terminal/
├── backend/
│   ├── main.py                 # Entry point (WebSocket server)
│   ├── worker.py               # Bot engine worker (TERMINAL_ROLE=worker)
│   ├── app/
│   │   ├── config.py           # Modes, symbols, API credentials
│   │   ├── database.py         # Schema & helpers (SQLite or Postgres)
│   │   ├── db/connection.py    # DATABASE_URL adapter
│   │   ├── server.py           # WebSocket server & DI wiring
│   │   ├── api/                # Centralized WS action router & protocol
│   │   │   ├── router.py       # Route registry + dispatch
│   │   │   ├── protocol.py     # Action / MessageType enums
│   │   │   ├── outbound.py     # Typed server->client frame builders
│   │   │   ├── http/           # Starlette REST API (Phase 3)
│   │   │   ├── http_server.py  # uvicorn runner
│   │   │   └── handlers/       # Domain handlers (trading, bots, admin, …)
│   │   ├── services/
│   │   │   ├── sim_feed.py     # Simulated feed (SBBS)
│   │   │   ├── synthetic_data.py
│   │   │   ├── alpaca_*.py / binance_*.py / etoro_*.py
│   │   │   ├── events/         # Redis pub/sub (bar_close, bot_reload)
│   │   │   └── bots/           # Screener, strategies, manager, backtester, runtime
│   │   └── websocket/          # Connection manager & message handlers
│   ├── strategies/             # Optional CUSTOM strategy plugins
│   └── data/                   # Cached *.parquet (generated locally)
└── frontend/
    └── src/
        ├── App.jsx             # Layout grid & header
        ├── api/protocol.js     # Action / MessageType constants (mirrors backend)
        ├── store/useStore.js   # Global state
        ├── components/         # Widgets, dock, charts
        └── components/ui/      # shadcn primitives
```

---

## Getting Started

### Prerequisites
- **Python 3.10+**
- **Node.js 18+** and **npm**

### Backend

```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

Server listens on **`ws://127.0.0.1:8765`** and **`http://127.0.0.1:8766`** (REST API, when `HTTP_ENABLED=true`).

On Windows you can also run `backend/start.bat`.

**Full stack with Docker** (backend + frontend + Redis + Postgres):

```bash
docker compose up --build
```

- Frontend: **http://localhost:8080** (nginx proxies `/api` and `/health` to backend)
- Backend WS: **ws://localhost:8765** · HTTP: **http://localhost:8766**

**Distributed mode** (optional — requires Redis):

```bash
# Start Redis + Postgres
docker compose up -d
```

```powershell
# Terminal 2: Server (WS + market feed)
$env:TERMINAL_ROLE="server"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
# Optional Postgres:
# $env:DATABASE_URL="postgresql://trading:trading@127.0.0.1:5432/trading"
python main.py

# Terminal 3: Bot worker
$env:TERMINAL_ROLE="worker"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
python worker.py
```

Or run `backend/worker.bat` for the worker process on Windows.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **`http://localhost:5173`** (or the URL Vite prints).

Production build:

```bash
npm run build
npm run preview
```

### Environment variables

Create a `.env` file in the **repo root** (loaded by `backend/app/config.py`):

```env
# Terminal mode: SIMULATED | LIVE_ALPACA | LIVE_BINANCE | LIVE_ETORO
TERMINAL_MODE=SIMULATED

# Alpaca (LIVE_ALPACA)
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Binance (LIVE_BINANCE)
BINANCE_API_KEY=
BINANCE_SECRET_KEY=

# eToro (LIVE_ETORO) — use Bearer OR key pair, never both
ETORO_ACCESS_TOKEN=
ETORO_API_KEY=
ETORO_USER_KEY=
ETORO_ENV=auto          # demo | real | auto
ETORO_POLL_INTERVAL=1.0
ETORO_EXEC_MIN_INTERVAL=3.0

# Bot engine (optional)
ALLOW_LIVE_BOTS=false
TERMINAL_ROLE=all              # all | server | worker
REDIS_URL=                     # redis://127.0.0.1:6379/0 for server/worker split
DATABASE_URL=                  # postgresql://... or omit for SQLite
ALLOW_CUSTOM_STRATEGIES=false

# HTTP REST API (optional, default on)
HTTP_ENABLED=true
HTTP_HOST=127.0.0.1
HTTP_PORT=8766
```

SQLite database `backend/trading.db` and cached parquet files are created automatically and are **gitignored**.

---

## WebSocket API

Single endpoint: **`ws://127.0.0.1:8765`**. Client requests use JSON with an `action` field; server replies use a `type` field.

### Client -> server (actions)

```json
{ "action": "place_order", "symbol": "AAPL", "side": "buy", "type": "market", "quantity": 10 }
```

| Action | Tags | SIM only | Description |
|--------|------|----------|-------------|
| `place_order` | trading | No | Place market or limit order with optional SL/TP |
| `cancel_order` | trading | No | Cancel a pending limit order by `order_id` |
| `update_position_sl_tp` | trading | No | Update stop-loss / take-profit on an open position |
| `get_account` | account | No | Request account balances and positions snapshot |
| `get_history` | account | No | Request full trade history blotter |
| `subscribe_symbol` | market | No | Subscribe chart symbol and receive candle history |
| `admin_set_simulation` | admin | No | Adjust sim tick speed, volatility, and symbol bias |
| `admin_seed_balance` | admin | Yes | Add balance to an asset account (SIM only) |
| `admin_reset_system` | admin | Yes | Wipe DB to defaults — orders, positions, bots (SIM only) |
| `admin_emergency_stop` | admin | No | Cancel all orders, halt all bots |
| `admin_get_stats` | admin | No | Database and simulation engine statistics |
| `bot_create` | bots | No | Deploy a new algo bot from strategy template |
| `bot_stop` | bots | No | Stop a single bot by `bot_id` |
| `bot_pause` | bots | No | Pause a running bot |
| `bot_resume` | bots | No | Resume a paused bot |
| `bot_stop_all` | bots | No | Stop every active bot |
| `bot_get_detail` | bots | No | Fetch trades, snapshots, and config for one bot |
| `bot_get_all` | bots | No | List all bots (active and stopped) |
| `run_backtest` | bots | No | Run offline backtest for symbol + strategy |

### Server -> client (message types)

```json
{ "type": "account_update", "data": { ... } }
```

| Type | Description |
|------|-------------|
| `terminal_config` | Handshake — mode, symbols, feature flags |
| `order_result` | Order/admin/bot command result |
| `account_update` | Balances, positions, unrealized P&L |
| `trade_history` | Full or incremental trade log |
| `history_update` | Candle history for subscribed symbol |
| `market_update` | Live tick / quote broadcast |
| `orderbook_update` | Simulated order book depth (SIM mode) |
| `system_stats` | DB row counts and sim engine settings |
| `bots_update` | Bot registry list |
| `bot_detail` | Single bot analytics payload |
| `bot_log` | Streaming bot log line |
| `bot_logs_history` | Recent bot logs on connect |
| `backtest_result` | Backtest metrics and equity curve |
| `error` | Unknown action or handler exception |

**Registry:** `backend/app/api/router.py` · **Frontend constants:** `frontend/src/api/protocol.js`

Regenerate the action table from the live route registry:

```bash
python backend/scripts/export_api_docs.py
```

### Adding a new action

1. Add enum in `backend/app/api/protocol.py` and `frontend/src/api/protocol.js`
2. Register handler with `@route(...)` in `backend/app/api/handlers/`
3. Handle outbound `type` in `frontend/src/api/dispatch.js` if needed
4. Optionally add a binding in `backend/app/api/http/bindings.py` (auto-routed by `http/app.py`)

---

## HTTP REST API (Phases 3 & 5)

Runs alongside WebSocket on **`http://127.0.0.1:8766`** by default (`HTTP_ENABLED=true`). Uses the same centralized action router — HTTP handlers invoke `dispatch()` via a reply collector.

| Method | Path | WS action | Description |
|--------|------|-----------|-------------|
| `GET` | `/health` | — | Liveness + mode, role, WS client count |
| `GET` | `/api/v1/routes` | — | List all registered WebSocket actions |
| `GET` | `/api/v1/openapi.json` | — | OpenAPI 3.1 spec (Phase 9) |
| `GET` | `/api/v1/account` | `get_account` | Account balances and positions |
| `GET` | `/api/v1/history` | `get_history` | Trade history blotter |
| `GET` | `/api/v1/market/{symbol}/candles` | `subscribe_symbol` | Historical candles for symbol |
| `POST` | `/api/v1/orders` | `place_order` | Place market/limit order |
| `DELETE` | `/api/v1/orders/{order_id}` | `cancel_order` | Cancel open order |
| `PATCH` | `/api/v1/positions/{symbol}/sl-tp` | `update_position_sl_tp` | Update SL/TP on position |
| `GET` | `/api/v1/bots` | `bot_get_all` | List all bots |
| `POST` | `/api/v1/bots` | `bot_create` | Deploy bot (`strategy`, `symbol`, `allocation`, …) |
| `GET` | `/api/v1/bots/{bot_id}` | `bot_get_detail` | Single bot analytics |
| `POST` | `/api/v1/bots/{bot_id}/stop` | `bot_stop` | Stop a bot |
| `POST` | `/api/v1/bots/{bot_id}/pause` | `bot_pause` | Pause a bot |
| `POST` | `/api/v1/bots/{bot_id}/resume` | `bot_resume` | Resume a bot |
| `POST` | `/api/v1/bots/stop-all` | `bot_stop_all` | Stop all bots |
| `POST` | `/api/v1/backtest` | `run_backtest` | Run strategy backtest |
| `GET` | `/api/v1/admin/stats` | `admin_get_stats` | System stats (sim only) |
| `POST` | `/api/v1/admin/simulation` | `admin_set_simulation` | Toggle simulation mode |
| `POST` | `/api/v1/admin/seed-balance` | `admin_seed_balance` | Seed paper balance |
| `POST` | `/api/v1/admin/reset` | `admin_reset_system` | Reset sim state |
| `POST` | `/api/v1/admin/emergency-stop` | `admin_emergency_stop` | Emergency halt |

Example:

```bash
curl http://127.0.0.1:8766/health
curl http://127.0.0.1:8766/api/v1/openapi.json
curl http://127.0.0.1:8766/api/v1/account
curl -X POST http://127.0.0.1:8766/api/v1/bots/bot-1/stop
curl -X POST http://127.0.0.1:8766/api/v1/bots \
  -H "Content-Type: application/json" \
  -d '{"strategy":"MACD_RSI","symbol":"AAPL","allocation":1000}'
```

Export OpenAPI to disk:

```bash
python backend/scripts/export_openapi.py
```

Response envelope:

```json
{ "ok": true, "type": "account_update", "data": { ... }, "messages": [ ... ] }
```

Disable HTTP: `HTTP_ENABLED=false` in `.env`.

Optional HTTP auth: set `HTTP_API_KEY` in backend `.env` and `VITE_HTTP_API_KEY` in frontend build env. `/health` stays public; all other REST routes require `X-API-Key`.

### CI & testing

GitHub Actions (`.github/workflows/ci.yml`) runs on push/PR:

- Backend unit tests (router, protocol sync, wire guard, HTTP)
- Frontend production build
- Playwright E2E smoke (`frontend/e2e/smoke.spec.js`)
- eToro smoke (`backend/scripts/etoro_smoke_test.py`) — skips without secrets; runs when `ETORO_API_KEY` / `ETORO_USER_KEY` or `ETORO_ACCESS_TOKEN` GitHub secrets are set

Local E2E:

```bash
# Terminal 1
cd backend && python main.py

# Terminal 2
cd frontend && npm run build && npm run preview
npm run test:e2e
```

### Performance & stress testing

Medium-tier load tests for WebSocket throughput, HTTP bursts, and frontend interaction budgets. Requires the backend running on **8765/8766**.

**Backend only** — concurrent WebSocket clients, action round-trips, HTTP latency/burst, symbol-switch stress:

```bash
cd backend
python scripts/perf_stress_test.py --clients 10 --duration 25
```

Optional flags: `--action-rounds`, `--http-samples`, `--http-burst`, `--subscribe-burst`, `--ws-url`, `--http-url`. Prints JSON metrics and a PASS/FAIL summary against built-in thresholds.

Longer WebSocket soak (60s, single client):

```bash
cd backend
python scripts/soak_test.py
```

**Frontend only** — Playwright perf suite (load time, dock tab thrash, watchlist switching, multi-chart, sidebar collapse, heap):

```bash
# Terminal 1 — backend
cd backend && python main.py

# Terminal 2 — production preview (default E2E base URL :4173)
cd frontend && npm run build && npm run preview

# Terminal 3
cd frontend
npm run test:perf
```

Set `E2E_BASE_URL` if the app is served elsewhere (e.g. `http://127.0.0.1:5173` for `npm run dev`).

**Both suites** (Windows):

```powershell
.\scripts\run-perf-tests.ps1
```

Starts preview on `:4173` automatically if `E2E_BASE_URL` is unset.

| Script | Location |
|--------|----------|
| Backend perf/stress | `backend/scripts/perf_stress_test.py` |
| Backend soak | `backend/scripts/soak_test.py` |
| Frontend perf | `frontend/e2e/performance.spec.js` |
| Combined runner | `scripts/run-perf-tests.ps1` |

### Router middleware (Phase 6)

`backend/app/api/middleware.py` wraps every dispatched action with:

- Structured request logging (action, tags, client key)
- Centralized exception → `error` frame conversion
- Trade-action rate limit (0.5 s minimum between `place_order` / `cancel_order` per client)

### AppState bootstrap (Phase 7)

`backend/app/bootstrap.py` builds a shared `AppState` (OMS, feed, bot manager, screener, event bus) injected into WebSocket handlers, HTTP app, and background loops.

### Outbound message catalog (Phase 4)

Server pushes use typed builders in `backend/app/api/outbound.py` instead of raw `{ "type": "..." }` dicts. Handlers use `app/api/responses.py`; feeds and OMS services use `publish_*` helpers.

### Wire protocol CI guard (Phase 10)

`backend/tests/test_wire_guard.py` fails if raw wire `type` strings appear outside `outbound.py` and `protocol.py`.

### UI follow-ups (Phases 11–12)

- **Bot detail drawer** — `frontend/src/components/BotDetailDrawer.jsx`: click a bot row in the Algo dock to open stats, config, trade table, pause/resume/stop.
- **Backtest trade list** — backtester returns `trades[]`; Algo panel shows a trade table after running a backtest.

---

## Tech Stack

**Backend:** Python, `websockets`, `starlette`, `uvicorn`, `pandas`, `pandas-ta-openbb`, `yfinance`, `arch`, `pyarrow`, `requests`, `redis`, `psycopg`

**Frontend:** React 19, Vite 8, Zustand, ECharts, lightweight-charts, shadcn/ui, Tailwind CSS v4, Lucide icons, cmdk, Sonner toasts
