/**
 * Client-side memory budgets — aligned with pro-terminal tiered buffers.
 * Hot: visible chart window; warm: per-symbol LRU; cold: server archive on pan.
 */

/** In-memory 1m buffer cap per symbol (warm tier). */
export const CANDLE_BUFFER_MAX_BARS = 3000;

/** Max 1m bars retained after archive prepend in the browser. */
export const CANDLE_ARCHIVE_MAX_BARS = 5000;

/** Max distinct symbols with 1m (+ HT) buffers in the tab. */
export const CANDLE_LRU_MAX_SYMBOLS = 4;

/** Native HT buffer cap per symbol|timeframe. */
export const HT_BUFFER_MAX_BARS = 600;

/** Default visible chart bars (first paint). */
export const CHART_DISPLAY_BARS_DEFAULT = 600;

/** Max bars rendered after repeated scroll-left loads. */
export const CHART_DISPLAY_MAX_BARS = 2500;
