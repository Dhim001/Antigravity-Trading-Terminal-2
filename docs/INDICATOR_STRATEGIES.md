# Indicator strategies — user guide & tuning

How to deploy, backtest, and tune **technical indicator bots** (non–Chart Agent). These strategies evaluate **closed bars** and use pandas-ta indicators computed by the market screener.

## Strategies at a glance

| Strategy | Style | Best for | Default TF |
|----------|-------|----------|------------|
| **MACD_RSI** | Trend / momentum cross | Liquid stocks & crypto with clear swings | 5m–1h |
| **BRS_SCALPING** | Mean reversion | Range-bound sessions, tight bands | 1m–5m |
| **SUPERTREND_ADX** | Trend follow | Directional trends with ADX confirmation | 5m–1h |
| **VWAP_PULLBACK** | Intraday mean revert | Session VWAP crosses (stocks) | 1m–15m |
| **DONCHIAN_BREAKOUT** | Breakout / momentum | Trending markets, crypto | 15m–4h |
| **ICT_SMC** | Structure (SMC) | Volatile symbols with clear sweeps | 5m–15m |
| **MARKET_MAKING** | Spread capture | Wide-spread crypto pairs | 1m–5m |

Deploy from **Algo panel** → pick strategy → set allocation & risk → **Deploy**. Tune parameters in **Bot detail → Config** after deploy (indicator fields appear per strategy).

---

## Shared risk & execution settings

All bar-close bots share these (Algo deploy + Bot config):

| Parameter | What it does | Starting point |
|-----------|--------------|----------------|
| `allocation` | Max notional per trade | 1–5% of account via risk sizing |
| `trailing_stop_percent` | Trailing stop from high/low since entry | 1.5–2.5% crypto, 1–2% stocks |
| `take_profit_percent` | Fixed % target (`tp_mode: percent`) | 2–4% |
| `tp_mode` | `percent` \| `strategy` (BRS only) \| `none` | `percent` for most |
| `direction_mode` | `LONG_ONLY` (default), `SHORT_ONLY`, `BOTH` | `LONG_ONLY` unless you hedge |
| `atr_length` | ATR period for stops & filters | 14 |

**Fees / slippage:** set `fee_bps` and `slippage_bps` in backtest (Algo → Backtest) before trusting PnL.

**Live vs backtest:** `confirm_timeframe` and `filter_strategy` gates apply **live** on ICT/Donchian; backtests do not yet simulate HTF/filter gates for indicator bots — validate those in paper trading.

---

## MACD + RSI

**Logic:** MACD histogram crosses zero **and** RSI is on the “correct” side of 50 (buy: RSI &lt; 50, sell: RSI &gt; 50). Exits via `CLOSE` on opposite MACD cross when in a position.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `macd_fast` / `macd_slow` / `macd_signal` | 12 / 26 / 9 | Faster (8/21/5) = more signals; slower = fewer whipsaws |
| `rsi_length` | 14 | 10 for faster; 21 for smoother |
| `atr_length` | 14 | Stop distance = 1.5 × ATR |

### Tuning tips

- **Too many false entries:** lengthen MACD slow or raise implicit bar requirement (use higher TF).
- **Missing trends:** shorten MACD periods or switch to SUPERTREND_ADX.
- **SHORT_ONLY:** set `direction_mode`; live and backtest both block long entries.
- Backtest sweep: `rsi_length`, `macd_fast`, `macd_slow`, `trailing_stop_percent`.

---

## Bollinger + RSI + Stochastic (BRS_SCALPING)

**Logic:** Buy at lower band with RSI & stoch oversold; sell at upper band with overbought readings. TP can target **mid-band** (`tp_mode: strategy`).

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `bb_length` / `bb_std` | 20 / 2.0 | 2.5 std = fewer, stronger touches |
| `rsi_oversold` / `rsi_overbought` | 30 / 70 | 25/75 = stricter |
| `stoch_oversold` / `stoch_overbought` | 20 / 80 | Align with RSI strictness |
| `stoch_k`, `stoch_d`, `stoch_smooth` | 14 / 3 / 3 | Standard stochastic |

### Tuning tips

- Works best in **ranges**; disable or pause in strong trends (use SUPERTREND filter — see filters below).
- Use `tp_mode: strategy` to exit at BB mid; otherwise set `take_profit_percent` 1.5–3%.
- Tighten `trailing_stop_percent` (1–1.5%) for scalp-style exits.

---

## SuperTrend + ADX

**Logic:** Enter on SuperTrend direction flip when **ADX &gt; threshold** (trend strength). Optional `block_elevated_vol` skips entries when ATR ≥ 1.5× its 20-bar median.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `st_length` / `st_multiplier` | 14 / 3.0 | Higher mult = fewer, later signals |
| `adx_length` / `adx_threshold` | 14 / 25 | Raise threshold to 28–35 for strong trends only |
| `block_elevated_vol` | false | Enable to avoid spike entries |
| `atr_length` | 14 | For vol block |

### Tuning tips

- **Choppy markets:** raise `adx_threshold` or enable `block_elevated_vol`.
- **Late entries:** lower `st_multiplier` slightly (2.5).
- Stops use SuperTrend line (`stop_loss_price`) — ensure `trailing_stop_percent` still acts as backup.

---

## VWAP Pullback

**Logic:** Buy when price crosses **down through** VWAP (pullback to value); sell on cross up. Optional RSI gates block buys when overbought / sells when oversold.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `use_rsi_confirmation` | true | Disable for pure VWAP crosses |
| `rsi_overbought_gate` / `rsi_oversold_gate` | 60 / 40 | Tighter: 55/45 |
| `rsi_length` | 14 | Match session volatility |
| `atr_length` | 14 | Stop = 1.5 × ATR |

### Tuning tips

- Best on **intraday** stock symbols with volume; crypto VWAP is approximate on bar data.
- Combine with `direction_mode: LONG_ONLY` for cash equities.
- If entries cluster at open, use 5m+ bars to reduce noise.

---

## Donchian Breakout

**Logic:** Enter long on break of N-bar high (short on N-bar low) when ATR ≥ `atr_confirm_mult` × median ATR. Exit on shorter-channel break opposite side.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `breakout_length` | 20 | 55 for Turtle-style; 10 for faster |
| `exit_length` | 10 | Must be &lt; `breakout_length`; ½ of entry channel common |
| `atr_confirm_mult` | 1.0 | 1.2+ = only expand-vol breakouts |
| `atr_length` | 14 | ATR filter & stops |
| `confirm_timeframe` | — | Live HTF bias (e.g. `1h`) |
| `filter_strategy` | — | e.g. `SUPERTREND_ADX` trend gate (live) |

### Tuning tips

- **False breakouts:** raise `atr_confirm_mult` or lengthen `breakout_length`.
- **Slow exits:** shorten `exit_length`.
- Wide bars that break both channels are **skipped** (no ambiguous signal).
- Backtest 30–90 days; crypto 15m–1h often works better than 1m.

---

## ICT Smart Money Concepts

**Logic:** Entry when **liquidity sweep** + **order block or FVG** + directional candle on the **same bar** (strict). Exits on opposing OB structure.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `sweep_lookback` | 20 | 10–30; shorter = more sweeps detected |
| `fvg_min_gap_pct` | 0.0005 | Raise on high-priced symbols |
| `ob_lookback` | 10 | Lower = stricter impulse requirement |
| `atr_length` | 14 | Stop = 2 × ATR |
| `filter_strategy` | — | `SUPERTREND_ADX` recommended live |
| `confirm_timeframe` | — | Higher-TF bias live |

### Tuning tips

- Expect **low trade count** — structure alignment on one bar is strict by design.
- Use **filter_strategy: SUPERTREND_ADX** so entries align with higher-level trend.
- Paper trade before live; validate filter/HTF in logs (`Filter gate blocked`, `HTF gate blocked`).

---

## Market Making

**Logic:** Quote-style entries at synthetic bid/ask zones from bar range; inventory skew forces closes when position exceeds `max_skew`. Vol shutdown when ATR &gt; `vol_shutdown_mult` × median.

### Key parameters

| Parameter | Default | Tune |
|-----------|---------|------|
| `spread_pct` | 0.002 (0.2%) | Must be &lt; typical bar range / price |
| `max_skew` | 0.5 | Lower = faster inventory flattening |
| `vol_shutdown_mult` | 2.5 | Lower = stop sooner in volatility |
| `inventory_target` | 0.0 | Bias skew target (0 = neutral) |
| `direction_mode` | LONG_ONLY | Use **BOTH** for two-sided MM |

### Tuning tips

- **Requires `direction_mode: BOTH`** for full bid/ask behavior; `LONG_ONLY` blocks short entries.
- Bar OHLC is a **spread proxy**, not true L2 — treat results as approximate.
- Crypto pairs with wide natural spreads (e.g. alts) suit this better than tight large-caps.
- Keep `trailing_stop_percent` wide enough to avoid stop-outs from noise.

---

## Strategy filters (ICT & Donchian)

Add to bot config (Bot → Config → Other):

```json
{
  "filter_strategy": "SUPERTREND_ADX",
  "filter_mode": "TREND_GATE",
  "filter_config": {
    "adx_threshold": 25,
    "st_length": 14,
    "st_multiplier": 3.0
  }
}
```

**TREND_GATE:** blocks BUY when filter is bearish; blocks SELL when filter is bullish. `CLOSE` always passes.

Filter indicators are computed live alongside the primary strategy. Match `filter_config` periods to your intent.

---

## Backtest & optimization workflow

1. **Baseline backtest** — 14–30 days, same symbol/TF as live.
2. **Open Backtest Lab → Optimizer** — sweep 2–3 parameters (see sweep placeholders in UI).
3. **Compare** win rate, max drawdown, expectancy — not just total PnL.
4. **Paper deploy** with winning config; watch bot logs for blocked signals.
5. **Adjust risk** (`trailing_stop_percent`, `take_profit_percent`) after entry logic is stable.

### Suggested sweep parameters by strategy

| Strategy | Sweep first |
|----------|-------------|
| MACD_RSI | `rsi_length`, `macd_slow`, `trailing_stop_percent` |
| BRS_SCALPING | `bb_std`, `rsi_oversold`, `take_profit_percent` |
| SUPERTREND_ADX | `adx_threshold`, `st_multiplier`, `block_elevated_vol` |
| VWAP_PULLBACK | `rsi_overbought_gate`, `trailing_stop_percent` |
| DONCHIAN | `breakout_length`, `exit_length`, `atr_confirm_mult` |
| ICT_SMC | `sweep_lookback`, `fvg_min_gap_pct`, `ob_lookback` |
| MARKET_MAKING | `spread_pct`, `max_skew`, `vol_shutdown_mult` |

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| No trades | Warm-up (~50 bars), filters too strict | More history; relax thresholds |
| No trades (Donchian/MM) | ATR median missing (fixed in recent builds) | Restart backend; verify `ATR_*_median_20` in logs |
| Filter never blocks | Wrong `filter_strategy` name | Use exact ID: `SUPERTREND_ADX` |
| Backtest ≠ live | HTF / filter live-only | Paper test with gates enabled |
| BRS stops too tight | Low `trailing_stop_percent` on volatile symbol | Widen stop or lower TF |
| MM only buys | `direction_mode: LONG_ONLY` | Set `BOTH` |
| MACD sells in LONG_ONLY | Normal — risk gate blocks shorts | Ignore or use CLOSE exits |

---

## Known limitations

- **VWAP** on multi-day windows may blend sessions (rolling 300-bar live window).
- **ICT** structure detection is simplified vs full SMC coursework (single-bar confluence).
- **Market making** uses bar range as spread proxy, not order book data.
- **Backtest** does not simulate `confirm_timeframe` / `filter_strategy` for indicator bots (live only today).

---

## Related docs

- [META_LABEL_MODEL.md](./META_LABEL_MODEL.md) — Chart Agent ML gate (not used by indicator bots)
- [DATABASE.md](./DATABASE.md) — persistence & bot trade history
