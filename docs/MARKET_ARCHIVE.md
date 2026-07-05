# Market Archive (DB)

How the trading terminal stores long-term OHLCV history in SQLite/Postgres, what the **Admin → Diagnostics → Market Archive (DB)** counters mean, and why **1h Bars** can be zero while **1m Bars** is healthy.

---

## Overview

The archive is a **two-tier** bar store:

| Table | Purpose | Default retention |
|-------|---------|-------------------|
| `market_bars_1m` | Hot / recent full-resolution history | **90 days** (`ARCHIVE_RETENTION_1M_DAYS`) |
| `market_bars_1h` | Cold / rolled-up compressed history | **~5 years** (`ARCHIVE_RETENTION_1H_DAYS`, default 1825) |

Recent data stays at 1-minute resolution. Data **older than 90 days** is aggregated into 1-hour candles, written to `market_bars_1h`, and the corresponding 1m rows are deleted.

This matches a common platform pattern: fine granularity for recent windows, coarser bars for deep history.

---

## Architecture

```
[Live capture loop]     ──┐
[Seed parquet backfill] ──┼──► market_bars_1m  (≤ 90 days of 1m bars)
[Broker ingest]         ──┘           │
                                      │ rollup job (hourly)
                                      ▼
                              market_bars_1h  (older compressed bars, up to ~5y)
```

### Data sources → `market_bars_1m`

1. **Live capture** — `archive_capture_loop()` records bar closes from the feed (`ArchiveBarHook` + `ArchiveWriter`).
2. **Seed backfill** — Admin **Backfill from seed data** / startup backfill imports `backend/app/data/{SYMBOL}_7d_1m.parquet` and/or the in-memory feed buffer.
3. **Broker ingest** — Admin **Broker ingest** / hourly ingestion fetches 1m bars from Massive, Alpaca, Binance, etc., and repairs gaps.

**None of these write directly to `market_bars_1h`.**

### Rollup → `market_bars_1h`

| Step | Detail |
|------|--------|
| Background task | `archive_rollup_loop()` in `backend/app/services/archive/runtime.py` |
| Interval | `ARCHIVE_ROLLUP_INTERVAL` (default **3600s** = hourly) |
| Logic | `run_rollup_job()` / `rollup_symbol()` in `backend/app/services/archive/rollup.py` |

For each symbol, rollup:

1. Selects 1m bars where `time < now - ARCHIVE_RETENTION_1M_DAYS`.
2. Buckets them by hour and aggregates OHLCV + `bar_count`.
3. Upserts into `market_bars_1h`.
4. Deletes the rolled 1m rows.

Bars older than `ARCHIVE_RETENTION_1H_DAYS` are purged from `market_bars_1h`.

```python
# rollup.py — only 1m bars older than the retention window are eligible
cutoff_1m = now - int(ARCHIVE_RETENTION_1M_DAYS * 86400)
h, m = rollup_symbol(symbol, cutoff_1m)
```

---

## Admin → Diagnostics counters

Stats come from `get_archive_stats()` (`backend/app/services/archive/query.py`), exposed via `get_db_stats()` → `ADMIN_GET_STATS`.

| UI label | DB table | Meaning |
|----------|----------|---------|
| **1m Bars** | `market_bars_1m` | Count of full-minute OHLCV rows (active archive). |
| **1h Bars** | `market_bars_1h` | Count of **rolled-up** hourly rows (compressed older history). |
| **Broker source** | ingestion summary | Which broker API is configured (`massive`, `alpaca`, `binance`, or `none`). |
| **History shortfall** | ingestion summary | Symbols with less than the ingest target depth (default 90 days). |
| **Est. Size** | derived | Rough MB estimate from row counts. |

The subtitle **Rolled-up archive** under 1h Bars is intentional: it is not live-captured 1h data; it is 1m data that has been compressed by the rollup job.

---

## Why 1h Bars can be 0 (and that's normal)

**1h Bars = 0 does not mean the archive is broken** if 1m Bars is non-zero.

Rollup only runs on 1m data **older than 90 days**. Typical situations where 1h stays at zero:

| Situation | Result |
|-----------|--------|
| Fresh install / recent seed backfill (7d parquet) | All data in `market_bars_1m`; nothing old enough to roll up. |
| Broker ingest to ~90 days (`ARCHIVE_INGESTION_DAYS=90`) | Nearly all bars are within the 90-day window → stay in 1m. |
| Live capture for less than 90 days | Same — rollup has nothing to process. |
| Rollup loop hasn't run yet | First cycle is after `ARCHIVE_ROLLUP_INTERVAL`; still 0 until eligible 1m exists. |

**1h becomes non-zero when:**

- Calendar time passes and the oldest 1m bars cross the 90-day cutoff, **or**
- You ingest history with timestamps **more than 90 days in the past** (and rollup has run).

Default broker ingest targets 90 days of **recent** history, which mostly keeps everything in the 1m tier by design.

The test suite mirrors this: rollup only moves data that is **older than the 90-day cutoff** (`backend/tests/test_archive.py`).

---

## How backtests and charts use the archive

`query_market_history()` with `interval=auto` (`backend/app/services/archive/query.py`):

- **Recent window** (within 90 days): reads `market_bars_1m`.
- **Older window** (before cutoff): reads `market_bars_1h`.

For typical backtests (7–90 days), resolution comes from **`market_bars_1m`**. An empty `market_bars_1h` does not block those runs.

Backtest candle resolution (`backend/app/services/archive/resolve.py`) loads from the 1m archive and resamples to the requested timeframe (e.g. 5m).

---

## Configuration reference

| Variable | Default | Role |
|----------|---------|------|
| `ARCHIVE_ENABLED` | `true` | Master switch for capture, rollup, ingestion. |
| `ARCHIVE_RETENTION_1M_DAYS` | `90` | How long 1m bars are kept before rollup eligibility. |
| `ARCHIVE_RETENTION_1H_DAYS` | `1825` | Max age of 1h bars before purge (~5 years). |
| `ARCHIVE_ROLLUP_INTERVAL` | `3600` | Seconds between rollup cycles. |
| `ARCHIVE_FLUSH_INTERVAL` | `60` | Writer flush interval for live capture. |
| `ARCHIVE_BACKFILL_ON_STARTUP` | `true` | Seed parquet/feed on boot. |
| `ARCHIVE_INGESTION_ENABLED` | `true` | Hourly broker ingest loop. |
| `ARCHIVE_INGESTION_ON_STARTUP` | `true` | One-shot broker ingest after startup backfill. |
| `ARCHIVE_INGESTION_DAYS` | `90` | Target 1m depth for backtests / shortfall detection. |
| `ARCHIVE_INGESTION_INTERVAL` | `3600` | Seconds between ingestion cycles. |
| `ARCHIVE_INGESTION_STARTUP_BATCH_SIZE` | `6` | Symbols per startup ingest batch (rest on hourly loop). |
| `ARCHIVE_INGESTION_SYMBOL_DELAY_SEC` | `1.0` | Pause between symbols during broker fetch. |
| `ARCHIVE_PARQUET_ENABLED` | `false` | Optional Parquet export (or `ARCHIVE_BACKEND=both`). |

See `backend/.env.example` for the full list.

---

## Admin actions (Market Archive section)

| Button | Endpoint | Writes to |
|--------|----------|-----------|
| **Backfill from seed data** | `POST /api/v1/admin/archive/backfill` | `market_bars_1m` (parquet + feed) |
| **Broker ingest (Nd)** | `POST /api/v1/admin/archive/ingest` | `market_bars_1m` (+ optional seed pass) |
| **Export Parquet (90d)** | `POST /api/v1/admin/archive/export` | Files on disk (requires `ARCHIVE_PARQUET_ENABLED`) |

Manual backfill uses `force: true` in the UI so existing partial archive does not skip symbols entirely.

Long ingest runs use extended HTTP timeouts; the UI shows a loading spinner and success/error toasts on completion, then refreshes diagnostics stats.

### Operator / admin UI access

Operator controls (System Control Panel, archive buttons) are gated by:

- Build-time: `VITE_OPERATOR_MODE=true` in `frontend/.env.local`
- Runtime: `OPERATOR_MODE=true` on the backend (exposed as `operator_mode` on `GET /api/v1/session`)

---

## Startup sequence

On server boot (`backend/app/server.py`):

1. `archive_startup_pipeline()` — sequential seed backfill, then staggered broker ingest.
2. `archive_capture_loop()` — live bar recording from the feed.
3. `archive_ingestion_loop()` — periodic broker backfill + gap repair.
4. `archive_rollup_loop()` — hourly 1m → 1h rollup + retention purge.

Sequential startup avoids a race where ingestion runs before seed backfill completes.

---

## Troubleshooting

### 1m Bars is 0

- Check `ARCHIVE_ENABLED=true`.
- Run seed backfill or broker ingest from Admin → Diagnostics.
- Confirm broker credentials (`MASSIVE_API_KEY`, Alpaca, etc.) if using ingest.
- Ensure archive loops started (server logs: “Market archive capture loop active”).

### 1m Bars > 0 but 1h Bars = 0

- **Expected** if all archived data is within the last 90 days.
- Wait for data to age past the cutoff, or ingest deeper history with timestamps > 90 days ago.
- Confirm rollup is running (logs: “Market archive rollup loop active”).

### Backfill / ingest appears to do nothing

- **0 rows** can mean: symbols already populated, no seed parquet files, or no broker API.
- Check the toast message after the action (row count or explanation).
- Refresh Diagnostics to update counts.

### History shortfall > 0

- Some symbols have less than `ARCHIVE_INGESTION_DAYS` of 1m data.
- Run broker ingest or wait for the hourly ingestion cycle.
- If broker source is `none`, configure API keys first.

### Broker ingest button disabled

- Tooltip explains: no broker API configured.
- Set `MASSIVE_API_KEY` or Alpaca/Binance credentials matching `TERMINAL_MODE`.

---

## Key source files

| Area | Path |
|------|------|
| Schema | `backend/app/services/archive/schema.py` |
| Live capture | `backend/app/services/archive/runtime.py`, `writer.py`, `bar_hook.py` |
| Seed backfill | `backend/app/services/archive/backfill.py` |
| Broker ingest | `backend/app/services/archive/ingestion.py`, `broker_fetch.py`, `gap_scan.py` |
| Rollup | `backend/app/services/archive/rollup.py` |
| Queries / stats | `backend/app/services/archive/query.py` |
| Backtest resolution | `backend/app/services/archive/resolve.py` |
| Admin API | `backend/app/api/handlers/admin.py` |
| Admin UI | `frontend/src/components/SystemControlPanel.jsx` |
| Config | `backend/app/config.py` |
| Tests | `backend/tests/test_archive.py`, `backend/tests/test_archive_ingestion.py` |

Related: [DATABASE.md](./DATABASE.md) for connection pooling, migrations, and general DB layer.

---

## Summary

- **`market_bars_1m`** — day-to-day archive for backtests and recent history (what you care about for 7–90 day runs).
- **`market_bars_1h`** — long-term storage filled only when 1m data is **older than 90 days** and the rollup job has run.
- **0 rolled-up 1h bars with healthy 1m counts is normal** for new or ≤90-day archives — not a defect.
