# Memory budget — 16 GB workstation guide

This terminal runs **three optional profiles** (Sim, IB, Massive) that can each consume hundreds of MB to several GB. On a **16 GB RAM** machine, treat memory as a shared budget across browser tab, Python backends, and other apps.

## Recommended daily setup

| Choice | Why |
|--------|-----|
| **Run Massive only** (`.\scripts\start-massive.ps1`) | Live crypto/stocks + paper OMS; skip Sim/IB unless you need them |
| **One browser tab** on `http://127.0.0.1:5175` | Each tab holds its own candle LRU + ECharts GPU buffers |
| **Hard refresh** after long sessions | Clears stale Vite HMR state |
| **`-Restart` when ports are stuck** | Kills old Vite/Python on profile ports before relaunch |

```powershell
.\scripts\start-massive.ps1 -Restart
```

## Browser caps (client-side)

Defined in `frontend/src/services/memoryBudget.js`:

| Constant | Value | Role |
|----------|-------|------|
| `CANDLE_LRU_MAX_SYMBOLS` | 4 | Max watchlist symbols with warm 1m buffers |
| `CANDLE_BUFFER_MAX_BARS` | 3,000 | 1m bars per symbol in tab memory |
| `CANDLE_ARCHIVE_MAX_BARS` | 5,000 | Max after scroll-left archive prepend |
| `CHART_DISPLAY_BARS_DEFAULT` | 600 | Initial chart window |
| `CHART_DISPLAY_MAX_BARS` | 2,500 | Max rendered after repeated pan-left |
| `HT_BUFFER_MAX_BARS` | 600 | Native higher-TF buffer per symbol\|TF |

Active symbol is **pinned** — switching watchlist evicts the oldest non-active symbol when LRU is full.

## Observability

| Surface | What it shows |
|---------|----------------|
| **Dev badge** (bottom-right, DEV builds only) | JS heap %, buffer symbol/bar counts, WS clients, crypto lag |
| **Settings → System → Memory & buffers** | Full client stats + backend HT cache / lag (all builds) |
| **`GET /health`** | `ws_clients`, `massive.crypto_lag_sec`, `massive.ht_cache_entries` |

### Quick triage

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Tab freezes / OOM | Browser heap + chart GPU | Close extra tabs; check dev badge heap %; reduce scroll-left history |
| Stale chart / wrong backend proxy | Old Vite on :5175 | `start-massive.ps1 -Restart` |
| `ws_clients = 0` with UI open | WS not connected | Hard refresh; confirm Vite maps 5175 → 8785 |
| Heap > 70% sustained | Too many symbols / long session | Switch away from unused symbols; refresh |
| Backend HTTP hangs / health timeouts | Full `/health` + SQLite archive COUNT under load | Prefer `/health/live`; Massive keeps `ARCHIVE_RETENTION_1M_DAYS=14` (90d backtests use broker REST / 1h archive, not local 1m) |

Launch scripts and Docker healthchecks use **`/health/live`**. UI pollers (dev badge, memory settings, IB banner) use live/massive probes — not full `/health`.

## Backend / multi-profile RAM

Each profile runs a separate Python process + SQLite DB:

| Profile | Ports | Typical use |
|---------|-------|-------------|
| Sim | 5173 / 8765–8766 | Paper sim generator |
| IB | 5174 / 8775–8776 | IB Gateway feed |
| Massive | 5175 / 8785–8786 | Live Massive feed + paper OMS |

Running **all three** can exceed 16 GB under load. Prefer **one profile** for live trading.

## Massive HT server cache

Higher-timeframe REST bars are cached server-side (`massive_feed._ht_cache`). Health exposes `massive.ht_cache_entries`, `ht_cache_max_entries`, and `ht_cache_ttl_sec`. Limits are configured via `MASSIVE_HT_LIMIT_*` env vars (see `env.profiles/massive.env` and `backend/app/services/massive_ht_limits.py`).

### Phase 3 tuning (optional)

When running bots + multi-chart + analyst HT fetches on 16 GB:

| Env | Default | Role |
|-----|---------|------|
| `MASSIVE_HT_CACHE_TTL_SEC` | 300 | How long REST HT responses stay warm |
| `MASSIVE_HT_CACHE_MAX_ENTRIES` | 48 | LRU cap on symbol×TF pairs (~26 symbols × few TFs) |
| `MASSIVE_HT_LIMIT_ANALYSIS` | 2000 | Lower to 1200 if server RSS climbs |

Prometheus counters: `massive_ht_cache_hit_total`, `massive_ht_cache_miss_total`. Watch hit ratio in Settings → System → Metrics when switching symbols aggressively.

## Automated soak (optional)

```powershell
cd backend
python scripts/soak_ws_health.py --http http://127.0.0.1:8786 --ws ws://127.0.0.1:8785 --seconds 30
```

Expect `ws_clients >= 1` when the browser is open on the Massive UI.
