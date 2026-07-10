# New Agentic Bot Strategies — Microstructure & Imbalance Focus

## Current Architecture Analysis

Your terminal currently has **10 built-in strategies** across two execution modes:

| Mode | Strategies | Signal Method |
|---|---|---|
| **BAR_CLOSE** | MACD_RSI, BRS_SCALPING, SUPERTREND_ADX, VWAP_PULLBACK, CHART_AGENT, ICT_SMC, DONCHIAN_BREAKOUT, MARKET_MAKING | Technical indicators on OHLCV bars |
| **TICK** | TICK_MOMENTUM, TICK_MEAN_REVERT, TICK_BREAKOUT | Raw price ticks |

The **CHART_AGENT** (your only agentic bot) uses a **rule engine** with 7 scoring domains:

```
Trend (EMA alignment) → Momentum (RSI zones + MACD position) → Volume (surge/weak) 
→ Risk (ATR regime) → Sentiment (news) → Events → Derivatives
```

All signals pass through a **confidence sigmoid** and **regime-adaptive weighting** system.

> [!IMPORTANT]
> The key gap: **None of the current strategies analyze order flow, bid/ask imbalances, or volume microstructure.** The ICT_SMC strategy detects FVGs and OBs from OHLCV data alone — it doesn't use actual order book depth or trade-by-trade flow.

---

## Proposed New Strategies

### 🟣 Strategy 1: **Order Flow Imbalance Agent** (`ORDERFLOW_IMBALANCE`)

**Category:** `microstructure` · **Execution:** `BAR_CLOSE` · **Data:** OHLCV + Order Book snapshots

**What it does:** Detects when aggressive buying/selling overwhelms passive liquidity at the best bid/ask levels — a leading indicator of short-term price moves that pure OHLCV indicators miss entirely.

**Signal Logic:**
```
1. Compute Bid-Ask Imbalance Ratio (BAIR):
   BAIR = (best_bid_volume - best_ask_volume) / (best_bid_volume + best_ask_volume)
   
2. Compute Multi-Level Order Flow Imbalance (MLOFI):
   For levels 1-5 of the order book, weight volume by 1/level
   MLOFI = Σ (bid_vol_i - ask_vol_i) × (1/i)  /  Σ (bid_vol_i + ask_vol_i) × (1/i)

3. BUY signal when:
   - BAIR > +0.3 (bid dominance at top of book)
   - MLOFI > +0.2 (deeper book confirms)
   - Volume surge > 1.3× 20-bar avg
   - RSI < 65 (not overbought)
   
4. SELL signal when:
   - BAIR < -0.3 (ask dominance at top of book)
   - MLOFI < -0.2 (deeper book confirms)
   - Volume surge > 1.3× 20-bar avg
   - RSI > 35 (not oversold)
```

**Why it works:** When you see heavy buying pressure in the order book that hasn't yet moved the price, smart money is likely loading. The price move follows 1-5 bars later.

**What makes it different from existing strategies:** Your current strategies only see *after* price has moved (lagging). This strategy sees the *cause* (order flow) before the *effect* (price change).

---

### 🔵 Strategy 2: **CVD Divergence Agent** (`CVD_DIVERGENCE`)

**Category:** `microstructure` · **Execution:** `BAR_CLOSE` · **Data:** OHLCV + simulated trade classification

**What it does:** Tracks **Cumulative Volume Delta** (running total of buy-volume minus sell-volume) and fires signals when price and CVD diverge — the classic "hidden hand" detector.

**Signal Logic:**
```
1. Classify each bar's volume using the "Close Location Value" method:
   buy_pct = (close - low) / (high - low)
   buy_vol = volume × buy_pct
   sell_vol = volume × (1 - buy_pct)
   delta = buy_vol - sell_vol
   CVD = running_sum(delta)

2. Detect swing pivots in both price and CVD (using ZigZag or N-bar highs/lows)

3. BULLISH DIVERGENCE (BUY):
   - Price makes a LOWER LOW
   - CVD makes a HIGHER LOW  
   - Volume on the latest low is declining (exhaustion)
   → Sellers are exhausted; hidden buying absorbing supply

4. BEARISH DIVERGENCE (SELL):
   - Price makes a HIGHER HIGH
   - CVD makes a LOWER HIGH
   - Volume on the latest high is declining
   → Buyers are exhausted; hidden selling distributing
   
5. Confirmation filter: ADX < 40 (works best in range-to-trend transitions)
```

**Why it works:** CVD divergence is one of the most powerful concepts in modern order flow trading. When price moves up but cumulative buying pressure declines, it means the move is being *distributed into* — institutions are selling to retail buyers who chase the move.

**Key advantage:** This can be computed from standard OHLCV data (no Level 2 needed) using the Close Location Value approximation, making it deployable in your simulated environment immediately.

---

### 🟢 Strategy 3: **Wyckoff Spring/Upthrust Detector** (`WYCKOFF_SPRING`)

**Category:** `smc` · **Execution:** `BAR_CLOSE` · **Data:** OHLCV

**What it does:** Algorithmically detects Wyckoff accumulation "Springs" (false breakdowns) and distribution "Upthrusts" (false breakouts) — the exact moments where smart money traps retail traders.

**Signal Logic:**
```
1. Identify Trading Range:
   - Detect sideways consolidation (ATR/close ratio compressed < threshold)
   - Establish range_high (resistance) and range_low (support) from N-bar lookback
   
2. SPRING Detection (BUY):
   - Bar low pierces below range_low (false breakdown)
   - Bar closes BACK INSIDE the range (close > range_low)
   - Volume on the spring bar is > 1.5× average (climactic absorption)
   - Next bar's close > spring bar's close (follow-through confirmation)
   - Stop loss: below the spring bar's low
   
3. UPTHRUST Detection (SELL):
   - Bar high pierces above range_high (false breakout)
   - Bar closes BACK INSIDE the range (close < range_high)
   - Volume is elevated (distribution)
   - Next bar's close < upthrust bar's close (confirmation)
   - Stop loss: above the upthrust bar's high

4. Quality filter: Require ≥ 15 bars of range-bound action before the spring/upthrust
   (prevents false signals during trending markets)
```

**Why it works:** Springs and Upthrusts exploit the most predictable behavior in markets — retail stop-loss clusters sitting just beyond range boundaries. When price sweeps those stops and reverses, it creates a violent, high-probability move back through the range. This is mathematically similar to your ICT liquidity sweep logic, but with Wyckoff's volume confirmation layer.

---

### 🟡 Strategy 4: **Volume Profile POC Reversion** (`VPOC_REVERSION`)

**Category:** `intraday` · **Execution:** `BAR_CLOSE` · **Data:** OHLCV

**What it does:** Builds a real-time Volume Profile and trades mean-reversion toward the **Point of Control** (POC = price level with the most volume) and **Value Area** boundaries.

**Signal Logic:**
```
1. Build rolling Volume Profile from last N bars (default: 100):
   - Divide price range into bins (bin_size = ATR / 10)
   - Assign each bar's volume to bins proportionally
   - POC = bin with highest cumulative volume
   - VA_High / VA_Low = boundaries of 70% of total volume
   
2. BUY when:
   - Price drops below VA_Low (outside value area, below fair value)
   - RSI < 40 (confirming oversold)
   - Volume declining (exhaustion, not new selling)
   - Target: POC (mean reversion target)
   - Stop: 1.5 × ATR below VA_Low

3. SELL when:
   - Price rises above VA_High (outside value area, above fair value)
   - RSI > 60 (confirming overbought)
   - Volume declining
   - Target: POC
   - Stop: 1.5 × ATR above VA_High

4. TREND FILTER: Skip signal if ADX > 35 (strong trends override mean reversion)
```

**Why it works:** Volume Profile is the "X-ray" of price action — it shows *where* the most trading happened, not just *when*. 70% of the time, price oscillates within the Value Area. Trading from the edges back to POC has a high win rate in range-bound conditions.

**Key advantage:** Unlike VWAP_PULLBACK (which only considers current session VWAP), VPOC looks at multi-session volume distribution — it's a structurally deeper view of fair value.

---

### 🔴 Strategy 5: **Absorption & Exhaustion Agent** (`ABSORPTION_AGENT`)

**Category:** `microstructure` · **Execution:** `BAR_CLOSE` · **Data:** OHLCV + Order Book

**What it does:** An **agentic** strategy (like CHART_AGENT) that uses a multi-domain scoring system to detect institutional absorption (large passive orders absorbing aggressive flow) and exhaustion (declining conviction at swing extremes).

**Signal Architecture (multi-domain scoring, like CHART_AGENT):**
```
Domain 1: ABSORPTION DETECTION (weight: 2.0)
  +2: Large volume bar with tiny body (high/low range > 3× body = absorption candle)
  +1: Volume > 2× avg with same-direction close (aggressive flow being absorbed)
  -1/-2: Inverse patterns
  
Domain 2: EXHAUSTION DETECTION (weight: 1.5)
  +2: 3+ consecutive same-direction bars with declining volume (bearish exhaustion → BUY)
  +1: Volume declining on a move approaching key level
  -1/-2: Inverse patterns

Domain 3: ORDERBOOK DEPTH (weight: 1.5)
  +1: Bid wall detected (bid depth > 3× ask depth at best levels)
  -1: Ask wall detected
  0: Balanced or insufficient data

Domain 4: STRUCTURE (weight: 1.0)
  ±1: Price at or near S/R level (rolling high/low)
  ±1: Inside bar pattern after absorption = coiling for breakout
  
Domain 5: TREND CONTEXT (weight: 0.5)  
  Reuse existing EMA trend scoring from rule_engine.py

Composite score ≥ 3 → BUY  |  ≤ -3 → SELL  |  else → NONE
Confidence: sigmoid mapping identical to CHART_AGENT
```

**Why it's agentic:** Like your CHART_AGENT, this uses a **multi-domain scoring engine** with regime-adaptive weights rather than simple if/else rules. It can be extended with LLM reasoning overlay later.

---

## Implementation Priority

| Priority | Strategy | Difficulty | Data Needed | Value Add |
|---|---|---|---|---|
| **1st** | CVD_DIVERGENCE | 🟢 Easy | OHLCV only | Immediate — works with existing data |
| **2nd** | WYCKOFF_SPRING | 🟢 Easy | OHLCV only | High edge — traps + volume |
| **3rd** | VPOC_REVERSION | 🟡 Medium | OHLCV only | Unique — volume profile is powerful |
| **4th** | ORDERFLOW_IMBALANCE | 🟡 Medium | OHLCV + OrderBook | Needs order book wiring |
| **5th** | ABSORPTION_AGENT | 🔴 Complex | OHLCV + OrderBook | Full agentic architecture |

> [!TIP]
> Strategies 1-3 require **zero infrastructure changes** — they compute everything from existing OHLCV bar data using the `BaseStrategy.evaluate(df_row)` interface. The CVD approximation using Close Location Value is well-established in academic literature and widely used by platforms like Bookmap and ATAS.

> [!NOTE]
> Strategies 4-5 benefit from order book data, which your app already streams via the `orderbook` field in `MARKET_UPDATE` messages. The wiring from the frontend store → backend strategy would need a lightweight bridge.

## Architecture Fit

All strategies slot into your existing system with **zero breaking changes**:

```
strategies.py::get_strategy()          → add new keys
strategy_catalog.py::_BAR_BUILTIN      → add catalog entries  
indicators.py                          → add CVD, VPOC helper columns
New files: strategies_microstructure.py → CVD, VPOC, Wyckoff, OrderFlow, Absorption
```

The `StrategyV2` SDK (`strategy_sdk.py`) already supports `BarWindow` history access, `Signal` output, and persistent state — all 5 strategies can use it directly.
