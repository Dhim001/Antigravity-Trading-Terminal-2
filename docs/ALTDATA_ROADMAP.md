# Alternative Data Roadmap

Phased plan for alt-data that improves CHART_AGENT signal quality, bot win rate, and LLM advisor context.

Related: [ALTDATA_INTEGRATION.md](./ALTDATA_INTEGRATION.md) (calendar/corp phases 1–5).

---

## Current stack (after P0)

| Data | Table / module | Agent use |
|------|----------------|-----------|
| News sentiment | `sentiment_events` | Sentiment domain |
| Dividends / splits | `corporate_events` | Event domain, gates, backtest adjust |
| Market holidays | `economic_events` (`market_holiday`) | Calendar gates |
| **Macro releases** | `economic_events` (`macro_release`) | **Macro gates**, events sub-report |
| **Crypto funding + OI** | `crypto_derivatives_history` | **Derivatives domain** (crypto only) |

---

## Architecture

```
Finnhub economic calendar ──► economic_events (macro_release)
Binance fapi (public)       ──► crypto_derivatives_history
        ↓                              ↓
   event_policy.py              crypto_derivatives.py
   (macro blackout)             (positioning score)
        ↓                              ↓
   risk_gate / backtester      rule_engine derivatives domain
   manager                      strategy_advisor context
```

---

## Phase P0 — Implemented

### Macro calendar + gates

- **Ingest:** `macro_provider.py` → Finnhub `/calendar/economic`
- **Store:** `economic_events` with `event_type=macro_release`
- **Gate:** `MACRO_GATES_ENABLED` — block entries ±`MACRO_BLACKOUT_MINUTES` (default 30) around high-impact US releases (CPI, FOMC, NFP, GDP, PPI, etc.)
- **Scope:** Applies to **equities and crypto** (BTC reacts to US macro)
- **Diagnostics:** `time_controls_status.upcoming_macro`

### Crypto derivatives positioning

- **Ingest:** `crypto_provider.py` → Binance USD-M `premiumIndex`, `openInterest`, `openInterestHist`
- **Store:** `crypto_derivatives_history` (time series, 30d retention)
- **Score:** OI × funding quadrant → `derivatives` domain in CHART_AGENT
- **Regime weight:** Highest in `elevated_vol` (0.8)

---

## Phase P1 — Next (recommended)

| Dataset | Source | Use |
|---------|--------|-----|
| Earnings calendar + EPS revisions | Finnhub / FMP | Fundamental momentum domain; pre-earnings gate |
| Options unusual flow (scored) | Polygon options / vendor API | Positioning confirmation for equities |
| Cross-asset macro regime | FRED (VIX, yields, DXY) | Global risk-on/off overlay on composite score |

---

## Phase P2 — LLM-heavy

| Dataset | Use |
|---------|-----|
| Earnings call transcripts (RAG) | Strategy advisor, Trade Explain |
| SEC 10-K/Q risk-factor diffs | Slow bearish narrative filter |
| Short interest + borrow rate | Squeeze watchlist (with momentum + flow confluence) |
| Insider Form 4 clusters | Conviction modifier |

---

## Phase P3 — Optional

- Social sentiment (X/Reddit) — contrarian at extremes only
- ETF flows — sector rotation context
- Order-book imbalance domain — microstructure timing (feed already has L2)

---

## Configuration

```env
ALTDATA_ENABLED=true
FINNHUB_API_KEY=your_key          # required for macro calendar
MACRO_GATES_ENABLED=true
MACRO_BLACKOUT_MINUTES=30
MACRO_CALENDAR_ENABLED=true
CRYPTO_DERIVATIVES_ENABLED=true
```

Per-bot `config.event_policy`:

```json
{
  "event_policy": {
    "macro_gate": true,
    "macro_blackout_minutes": 30,
    "calendar_gate": true,
    "crypto_exempt": true
  }
}
```

Note: `crypto_exempt` applies to **calendar/corp** gates only, not macro (BTC is macro-sensitive).

---

## CHART_AGENT domains

| Domain | Equities | Crypto |
|--------|----------|--------|
| trend, momentum, volume, risk | ✓ | ✓ |
| sentiment | ✓ (Finnhub) | ✗ (no news path yet) |
| events | ✓ corp/holidays/macro upcoming | macro upcoming |
| **derivatives** | — | ✓ funding + OI |

---

## Key files

| Area | Path |
|------|------|
| Macro ingest | `backend/app/services/altdata/macro_provider.py` |
| Crypto ingest | `backend/app/services/altdata/crypto_provider.py` |
| Positioning score | `backend/app/services/altdata/crypto_derivatives.py` |
| Gates | `backend/app/services/altdata/event_policy.py` |
| Agent scoring | `backend/app/services/agent/rule_engine.py` |
| Refresh loop | `backend/app/services/altdata/loop.py` |
| Schema | `backend/app/services/archive/schema.py` |
| Tests | `backend/tests/test_altdata_p0.py` |

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No macro events | `FINNHUB_API_KEY` set; `altdata_counts().economic_events` |
| Macro gate not firing | `MACRO_GATES_ENABLED`; event `impact=high` in DB |
| Derivatives score always 0 | `CRYPTO_DERIVATIVES_ENABLED`; Binance reachable; symbol `*USDT` |
| Crypto backtest no deriv context | Historical snapshots only exist after refresh ran — score 0 before first ingest is expected |
