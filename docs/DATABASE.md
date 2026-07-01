# Database layer

How persistence works in the trading terminal backend and how to extend it safely.

## Architecture

| Component | Path | Role |
|-----------|------|------|
| Connection factory | `app/db/connection.py` | Pooled SQLite or PostgreSQL via `DATABASE_URL` |
| Schema bootstrap | `app/database.py` | `init_db()` + idempotent `_safe_alter()` |
| Migration tracking | `app/db/migrations.py` | `schema_migrations` table, baseline `001_baseline` |
| Call sites | ~50 modules | `get_connection()` or `db_session()` |

**Default:** SQLite file (`DB_PATH` / `SQLITE_DB_PATH`). **Docker scale-out:** set `DATABASE_URL=postgresql://…`.

## Concurrency (SQLite)

- **WAL mode** is enabled on every pooled connection (`PRAGMA journal_mode=WAL`).
- **busy_timeout** 5000 ms, **synchronous=NORMAL**, **foreign_keys=ON**.
- **Lock retries:** `_CursorWrapper.execute` retries `database is locked` with exponential backoff (`DB_LOCK_RETRIES`, `DB_LOCK_RETRY_BASE_SEC`).
- **Pool:** `_SqlitePool` — size `DB_SQLITE_POOL_SIZE` (default `min(DB_POOL_SIZE, 5)`).

## PostgreSQL

- Uses **psycopg3** + **psycopg_pool** when installed (`psycopg[binary,pool]` in requirements).
- Set `DB_REQUIRE_POOL=1` in Docker to fail fast if the pool package is missing.
- Env: `DB_POOL_SIZE`, `DB_POOL_MIN`, `DB_POOL_TIMEOUT`.

## API

```python
from app.db.connection import db_session, check_db_health, warm_pool

# Preferred for new code — always returns connection to pool
with db_session() as conn:
    cur = conn.cursor()
    cur.execute("SELECT 1")

# Startup (server.py)
warm_pool()   # retry with backoff
init_db()

# Health
check_db_health()  # exposed on GET /health as `database`
```

## Health & observability

`GET /health` includes:

```json
{
  "database": {
    "ok": true,
    "driver": "sqlite",
    "latency_ms": 1.2,
    "journal_mode": "wal",
    "pool": { "initialized": true, "driver": "sqlite", "max_size": 5, "lock_retries": 0 }
  }
}
```

## Migrations (current → Alembic)

1. **Today:** `init_db()` + `_safe_alter()` apply schema; `record_baseline_if_needed()` writes `001_baseline` to `schema_migrations`.
2. **Docker:** `ALEMBIC_AUTO_UPGRADE=1` runs `alembic upgrade head` after `init_db()` (no-op until revision 002+ exists).
3. **New changes:** Add `alembic/versions/00N_description.py` — do **not** add new `_safe_alter()` calls.
4. **Cutover checklist:**
   - Generate revision: `cd backend && alembic revision -m "add_foo_column"`
   - Implement `upgrade()` / `downgrade()` with driver-aware SQL if needed
   - Test on fresh SQLite + Postgres
   - Deploy with `ALEMBIC_AUTO_UPGRADE=1`

## Modules using `db_session()` (preferred)

Store / runtime layer migrated from manual `get_connection()` + `close()`:

- `app/services/bots/signal_ledger.py`
- `app/services/bots/backtest_store.py`
- `app/services/bots/risk_state_store.py`
- `app/services/bots/portfolio_risk.py`
- `app/services/altdata/store.py`
- `app/services/sim_state.py`
- `app/services/journal/store.py`
- `app/services/runtime/system_state.py`
- `app/services/reconciliation.py`
- `app/services/notifications/digest.py`
- `app/services/data_quality/monitor.py`
- `app/services/analytics/exposure.py`
- `app/db/migrations.py`

Remaining hot paths (`sim_oms.py`, `manager.py`, `positions.py`, …) still use `get_connection()` — migrate incrementally.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (empty → SQLite) | Postgres connection URL |
| `SQLITE_DB_PATH` / `DB_PATH` | `trading-sim.db` | SQLite file |
| `DB_POOL_SIZE` | `10` | Postgres max pool size |
| `DB_SQLITE_POOL_SIZE` | `min(DB_POOL_SIZE, 5)` | SQLite pool size |
| `DB_POOL_MIN` | `1` | Postgres min pool size |
| `DB_POOL_TIMEOUT` | `30` | Postgres pool acquire timeout (seconds) |
| `DB_REQUIRE_POOL` | `0` | Require psycopg_pool for Postgres |
| `DB_STARTUP_RETRIES` | `5` | Startup connectivity attempts |
| `DB_STARTUP_RETRY_BASE_SEC` | `0.5` | Exponential backoff base |
| `DB_LOCK_RETRIES` | `8` | Per-query lock / transient retries |
| `DB_LOCK_RETRY_BASE_SEC` | `0.025` | Lock retry backoff base |
| `ALEMBIC_AUTO_UPGRADE` | `0` | Run `alembic upgrade head` after init_db |

## Audit notes (Phase 0)

- Raw `sqlite3.connect()` outside the pool: only `inspect_db.py` (CLI) and `connection.py`.
- Prefer `db_session()` over manual `get_connection()` + `close()` in new code.
- Hot paths (`sim_oms.py`, `manager.py`, `signal_ledger.py`) already use `try/finally: conn.close()`.

## Tests

```bash
python -m unittest tests.test_db_connection tests.test_db_concurrency
```
