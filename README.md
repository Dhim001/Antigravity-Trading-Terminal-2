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

Server listens on **`ws://127.0.0.1:8765`**.

On Windows you can also run `backend/start.bat`.

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
```

SQLite database `backend/trading.db` and cached parquet files are created automatically and are **gitignored**.

---

## WebSocket Actions (selected)

| Action | Description |
|--------|-------------|
| `place_order` | Market or limit order |
| `cancel_order` | Cancel pending limit order |
| `update_position_sl_tp` | Set stop-loss / take-profit |
| `subscribe_symbol` | Request candle history for symbol |
| `get_account` / `get_history` | Snapshot account or trade log |
| `bot_create` / `bot_stop` / `bot_pause` / `bot_resume` / `bot_stop_all` | Manage algo bots |
| `bot_get_detail` / `run_backtest` | Bot stats + offline backtest |
| `admin_set_simulation` | Tick speed, volatility, bias |
| `admin_reset_system` | Wipe orders, positions, history |

---

## Tech Stack

**Backend:** Python, `websockets`, `pandas`, `pandas-ta-openbb`, `yfinance`, `arch`, `pyarrow`, `requests`, `redis`, `psycopg`

**Frontend:** React 19, Vite 8, Zustand, ECharts, lightweight-charts, shadcn/ui, Tailwind CSS v4, Lucide icons, cmdk, Sonner toasts
