import {
  ArrowDownToLine,
  Bot,
  ChartCandlestick,
  Cpu,
  Target,
  TrendingUp,
  Coins,
  Landmark,
  Zap,
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
  CHART_AGENT: {
    label: 'Chart Analyst Agent',
    shortLabel: 'Agent',
    icon: Bot,
    color: '#a78bfa',
    tagline: 'Hybrid rule+LLM chart analysis',
    style: 'agent',
  },
  ICT_SMC: {
    label: 'ICT Smart Money',
    shortLabel: 'ICT',
    icon: Landmark,
    color: '#f43f5e',
    tagline: 'Order blocks, FVGs & liquidity sweeps',
    style: 'smc',
  },
  DONCHIAN_BREAKOUT: {
    label: 'Donchian Breakout',
    shortLabel: 'Donchian',
    icon: Zap,
    color: '#06b6d4',
    tagline: 'Channel breakout with ATR expansion',
    style: 'breakout',
  },
  MARKET_MAKING: {
    label: 'Market Making',
    shortLabel: 'MM',
    icon: Coins,
    color: '#eab308',
    tagline: 'Spread capture with inventory skew',
    style: 'market-making',
  },
  CVD_DIVERGENCE: {
    label: 'CVD Divergence',
    shortLabel: 'CVD',
    icon: TrendingUp,
    color: '#f97316',
    tagline: 'Spot hidden buying/selling',
    style: 'microstructure',
  },
  WYCKOFF_SPRING: {
    label: 'Wyckoff Spring',
    shortLabel: 'Wyckoff',
    icon: Target,
    color: '#10b981',
    tagline: 'Institutional stop-runs & absorption',
    style: 'smc',
  },
  VPOC_REVERSION: {
    label: 'Volume POC Reversion',
    shortLabel: 'VPOC',
    icon: ArrowDownToLine,
    color: '#8b5cf6',
    tagline: 'Mean reversion to Value Area POC',
    style: 'intraday',
  },
  ORDERFLOW_IMBALANCE: {
    label: 'Order Flow Imbalance',
    shortLabel: 'Imbalance',
    icon: Zap,
    color: '#ef4444',
    tagline: 'Bid/Ask pressure at top of book',
    style: 'microstructure',
  },
  ABSORPTION_AGENT: {
    label: 'Absorption Agent',
    shortLabel: 'Absorb',
    icon: Bot,
    color: '#6366f1',
    tagline: 'Multi-domain footprint scoring',
    style: 'agent',
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
