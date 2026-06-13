import {
  ArrowDownToLine,
  ChartCandlestick,
  Cpu,
  Target,
  TrendingUp,
} from 'lucide-react';

/** Visual + copy metadata for built-in bot strategies (mirrors backend strategy keys). */
export const STRATEGY_CATALOG = Object.freeze({
  MACD_RSI: {
    label: 'MACD + RSI',
    shortLabel: 'MACD',
    icon: ChartCandlestick,
    color: '#34d399',
    tagline: 'MACD crossover with RSI filter',
    style: 'momentum',
  },
  SUPERTREND_ADX: {
    label: 'SuperTrend + ADX',
    shortLabel: 'Trend',
    icon: TrendingUp,
    color: '#3b82f6',
    tagline: 'SuperTrend flip confirmed by ADX',
    style: 'trend',
  },
  BRS_SCALPING: {
    label: 'BRS Scalping',
    shortLabel: 'BRS',
    icon: Target,
    color: '#f59e0b',
    tagline: 'Bollinger + RSI + Stochastic scalps',
    style: 'mean-reversion',
  },
  VWAP_PULLBACK: {
    label: 'VWAP Pullback',
    shortLabel: 'VWAP',
    icon: ArrowDownToLine,
    color: '#ec4899',
    tagline: 'Pullback entries to VWAP',
    style: 'mean-reversion',
  },
});

const FALLBACK = Object.freeze({
  label: 'Custom',
  shortLabel: 'Custom',
  icon: Cpu,
  color: '#64748b',
  tagline: 'User-defined strategy',
  style: 'custom',
});

/** @param {string | undefined} strategy */
export function getStrategyMeta(strategy) {
  if (strategy && STRATEGY_CATALOG[strategy]) {
    return STRATEGY_CATALOG[strategy];
  }
  if (strategy) {
    return {
      ...FALLBACK,
      label: strategy,
      shortLabel: strategy.length > 8 ? `${strategy.slice(0, 8)}…` : strategy,
      tagline: `${strategy} strategy`,
    };
  }
  return FALLBACK;
}
