# Alternative Data Integration

How corporate actions and market calendar data flow from Massive/Polygon (and Alpaca fallback) into bot performance, backtests, and risk controls.

Related: [MARKET_ARCHIVE.md](./MARKET_ARCHIVE.md) for OHLCV archive tiers.

---

## Overview

| Data | Source table | Primary use (after integration) |
|------|--------------|-------------------------------|
| Dividends / splits | `corporate_events` | Entry blackouts, split-adjusted backtests, CHART_AGENT event domain |
| Market holidays | `economic_events` | RTH/session gates for equity bots |
| Sentiment (separate) | `sentiment_events` | CHART_AGENT sentiment domain (existing) |

**Collection:** `altdata_refresh_loop()` → `massive_provider.refresh_altdata()` (hourly by default).

**Post-trade context:** Trade Explain still uses ±24h event lookup (unchanged).

---

## Architecture

```
Massive/Alpaca REST refresh
        ↓
corporate_events + economic_events
        ↓
┌─────────────────────────────────────────────┐
│  altdata/event_policy.py  (unified API)     │
│  • check_entry_gates()                      │
│  • get_upcoming_events()                    │
│  • backtest_event_manifest()                │
└─────────────────────────────────────────────┘
        ↓              ↓                ↓
  risk_gate.py    backtester.py    archive/resolve.py
  manager.py      deploy_gate.py   rule_engine.py
                  data_quality     strategy_advisor.py
```

Supporting modules:

| Module | Role |
|--------|------|
| `altdata/calendar.py` | US equity RTH, weekends, stored holidays |
| `altdata/adjustments.py` | Backward split (and optional dividend) price adjustment |
| `altdata/event_policy.py` | Policy parsing + gate orchestration |

---

## Phase 1 — Calendar gates (P0)

**Goal:** Equity bots do not enter outside regular session or on exchange holidays.

**Live path:**

- `risk_gate.validate_trade()` → `check_entry_gates()` with current time
- `BotManager` signal loop → early block with `event_gate` log + metric

**Backtest path:**

- `backtester._try_entry` / `_try_short_entry` → `_record_blocked("calendar", ...)`

**Crypto:** Exempt (`BTCUSDT`, etc.).

**Config:**

| Variable | Default |
|----------|---------|
| `CALENDAR_GATES_ENABLED` | `true` |

Per-bot override in `config.event_policy.calendar_gate`.

---

## Phase 2 — Corporate event blackouts (P1)

**Goal:** Risk avoidance around splits (and optional ex-dividend windows)—not dividend-capture alpha.

| Event | Default policy |
|-------|----------------|
| Stock split | Block entries ±1 calendar day (`CORP_BLACKOUT_SPLIT_DAYS=1`) |
| Ex-dividend | Awareness only (`CORP_BLACKOUT_EX_DIV_DAYS=0`); set to `1` to block |

**Config:**

| Variable | Default |
|----------|---------|
| `CORP_EVENT_GATES_ENABLED` | `true` |
| `CORP_BLACKOUT_SPLIT_DAYS` | `1` |
| `CORP_BLACKOUT_EX_DIV_DAYS` | `0` |

Per-bot `config.event_policy`:

```json
{
  "event_policy": {
    "calendar_gate": true,
    "corp_split_blackout_days": 1,
    "corp_ex_div_blackout_days": 0,
    "crypto_exempt": true
  }
}
```

---

## Phase 3 — Split-adjusted backtests (P1)

**Goal:** Backtest PnL is not distorted by unhandled split discontinuities.

- Raw OHLC stays in `market_bars_1m`
- Adjustments applied at read time in `resolve_candles_for_range()`
- Default: `BACKTEST_PRICE_ADJUST=split_only`

| Mode | Use |
|------|-----|
| `raw` | Matches exchange prints (live sim) |
| `split_only` | **Default** — intraday/short TF bots |
| `total_return` | Long-horizon research (splits + dividends) |

Backtest metadata includes `event_manifest` (splits in range, adjust mode).

---

## Phase 4 — Agent awareness (P2)

**CHART_AGENT (`rule_engine.py`):**

- New **events** sub-report with upcoming corporate/holiday rows
- Event domain score (−1 near split window) with low regime weight
- Included in adaptive composite score

**Strategy advisor:** `upcoming_events` + `event_policy` in LLM context.

---

## Phase 5 — Deploy gate & data quality (P2)

**Deploy gate:** Warns when linked backtest spans splits but `price_adjust=raw`.

**Data quality monitor:** Flags `split_jump_symbols` when archive shows >35% bar-to-bar close moves (likely unadjusted splits).

**Time controls status:** Exposes `calendar_gates_enabled`, `market_holiday_today`, `equity_rth_open`.

---

## Industry alignment

Research-backed design choices:

1. **Separate adjustments table / read-time apply** — preserves raw bars (Quasar/Zipline pattern).
2. **Split-adjust for intraday backtests; raw for execution** — standard quant practice.
3. **Event blackouts for risk, not ex-div capture** — retail/systematic bots avoid microstructure noise around corporate dates.
4. **Exchange calendar for session gates** — complements static `RISK_NO_TRADE_WINDOWS` (open/close micro-windows).

---

## What still uses alt-data only for explanation

Trade Explain (`trade_explain.py`) continues to attach ±24h corporate/macro context for human/LLM narrative. Gates and adjustments are additive.

---

## Configuration reference

```env
ALTDATA_ENABLED=true
ALTDATA_REFRESH_INTERVAL_SEC=3600
CALENDAR_GATES_ENABLED=true
CORP_EVENT_GATES_ENABLED=true
CORP_BLACKOUT_SPLIT_DAYS=1
CORP_BLACKOUT_EX_DIV_DAYS=0
BACKTEST_PRICE_ADJUST=split_only
```

---

## Key source files

| Area | Path |
|------|------|
| Ingestion | `backend/app/services/altdata/massive_provider.py`, `loop.py` |
| Storage | `backend/app/services/altdata/store.py`, `archive/schema.py` |
| Calendar | `backend/app/services/altdata/calendar.py` |
| Gates | `backend/app/services/altdata/event_policy.py` |
| Adjustments | `backend/app/services/altdata/adjustments.py` |
| Live risk | `backend/app/services/bots/risk_gate.py`, `manager.py` |
| Backtest | `backend/app/services/bots/backtester.py`, `archive/resolve.py` |
| Agent | `backend/app/services/agent/rule_engine.py` |
| Deploy | `backend/app/services/bots/deploy_gate.py` |
| Quality | `backend/app/services/data_quality/monitor.py` |
| Tests | `backend/tests/test_altdata_event_policy.py` |

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Equity bot blocked mid-day | `time_controls_status` → `equity_rth_reason`; holiday row in `economic_events` |
| Backtest fewer trades on equities | Expected — calendar/corp gates mirror live; check `blocked_events` |
| Deploy warns on splits | Set `BACKTEST_PRICE_ADJUST=split_only` and re-run backtest |
| `split_jump_symbols` in diagnostics | Run backfill; enable split adjustment; verify Massive split feed |

---

## Summary

Corporate actions and market calendar are no longer display-only: they gate live and backtest entries, adjust historical prices for honest PnL, inform CHART_AGENT scoring, and surface warnings at deploy time. Sentiment remains the other alt-data path directly wired into signals.

**P0 roadmap (macro + crypto derivatives):** see [ALTDATA_ROADMAP.md](./ALTDATA_ROADMAP.md).
