import {
  Activity,
  ArrowDownToLine,
  Bot,
  BrainCircuit,
  Braces,
  ChartCandlestick,
  Cpu,
  Gamepad2,
  Layers,
  Network,
  Sparkles,
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

  /* ── ML / DL / RL strategies ───────────────────────────────── */
  ML_SIGNAL_BOOST: {
    label: 'ML Signal Boost',
    shortLabel: 'GBDT',
    icon: BrainCircuit,
    color: '#22d3ee',
    tagline: 'Gradient-boosted tree signal classifier',
    style: 'ml',
  },
  LSTM_DIRECTION: {
    label: 'LSTM Direction',
    shortLabel: 'LSTM',
    icon: Braces,
    color: '#a78bfa',
    tagline: 'LSTM temporal sequence classifier',
    style: 'ml',
  },
  RL_PPO_AGENT: {
    label: 'RL Trading Agent',
    shortLabel: 'PPO',
    icon: Gamepad2,
    color: '#f472b6',
    tagline: 'PPO reinforcement learning agent',
    style: 'ml',
  },
  TCN_MULTI_HORIZON: {
    label: 'TCN Multi-Horizon',
    shortLabel: 'TCN',
    icon: Layers,
    color: '#fb923c',
    tagline: 'Multi-horizon causal CNN forecaster',
    style: 'ml',
  },
  VAE_REGIME_DETECTOR: {
    label: 'VAE Regime Detector',
    shortLabel: 'VAE',
    icon: Activity,
    color: '#4ade80',
    tagline: 'Anomaly meta-layer — suppress / amplify / gate entries',
    style: 'ml',
  },
  TRANSFORMER_SIGNAL: {
    label: 'Transformer Signal',
    shortLabel: 'Attn',
    icon: Sparkles,
    color: '#facc15',
    tagline: 'Transformer attention-based signal',
    style: 'ml',
  },
  GNN_CROSS_ASSET: {
    label: 'GNN Cross-Asset',
    shortLabel: 'GNN',
    icon: Network,
    color: '#38bdf8',
    tagline: 'Graph neural network cross-asset',
    style: 'ml',
  },
});

/** Canonical ML strategy ids — single source for badge, training, deploy gates. */
export const ML_STRATEGY_IDS = Object.freeze([
  'ML_SIGNAL_BOOST',
  'LSTM_DIRECTION',
  'RL_PPO_AGENT',
  'TCN_MULTI_HORIZON',
  'VAE_REGIME_DETECTOR',
  'TRANSFORMER_SIGNAL',
  'GNN_CROSS_ASSET',
]);

const ML_STRATEGY_SET = new Set(ML_STRATEGY_IDS);

/** Torch / RL jobs that need longer client timeouts than GBDT. */
export const DEEP_ML_STRATEGY_IDS = Object.freeze(
  ML_STRATEGY_IDS.filter((id) => id !== 'ML_SIGNAL_BOOST'),
);

const DEEP_ML_STRATEGY_SET = new Set(DEEP_ML_STRATEGY_IDS);

/** Suggested paper allocation ($) when catalog omits one. */
export const STRATEGY_ALLOCATION_DEFAULTS = Object.freeze({
  MACD_RSI: 2000,
  SUPERTREND_ADX: 5000,
  BRS_SCALPING: 1000,
  VWAP_PULLBACK: 1500,
  TICK_MOMENTUM: 1000,
  TICK_MEAN_REVERT: 1000,
  TICK_BREAKOUT: 1000,
  CHART_AGENT: 2000,
  ABSORPTION_AGENT: 2000,
  ICT_SMC: 2000,
  DONCHIAN_BREAKOUT: 3000,
  MARKET_MAKING: 5000,
  CVD_DIVERGENCE: 2000,
  WYCKOFF_SPRING: 2000,
  VPOC_REVERSION: 1500,
  ORDERFLOW_IMBALANCE: 2000,
  ML_SIGNAL_BOOST: 2000,
  LSTM_DIRECTION: 2000,
  RL_PPO_AGENT: 3000,
  TCN_MULTI_HORIZON: 2000,
  VAE_REGIME_DETECTOR: 2000,
  TRANSFORMER_SIGNAL: 2000,
  GNN_CROSS_ASSET: 2500,
});

export function isMlStrategy(strategy) {
  return ML_STRATEGY_SET.has(String(strategy || '').toUpperCase());
}

export function isDeepMlStrategy(strategy) {
  return DEEP_ML_STRATEGY_SET.has(String(strategy || '').toUpperCase());
}

export function defaultAllocationFor(strategy) {
  const key = String(strategy || '').toUpperCase();
  return STRATEGY_ALLOCATION_DEFAULTS[key] ?? 1000;
}

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

/**
 * Derive strategy category from STRATEGY_CATALOG style.
 * @param {string | undefined} strategy
 * @returns {'normal' | 'ml' | 'agent'}
 */
export function getStrategyCategory(strategy) {
  const meta = getStrategyMeta(strategy);
  if (meta.style === 'ml') return 'ml';
  if (meta.style === 'agent') return 'agent';
  return 'normal';
}

/**
 * For ML strategies, get the sub-type for conditional UI sections.
 * @param {string | undefined} strategy
 * @returns {'supervised' | 'rl' | 'unsupervised'}
 */
export function getMLSubtype(strategy) {
  const key = String(strategy || '').toUpperCase();
  if (key === 'RL_PPO_AGENT') return 'rl';
  if (key === 'VAE_REGIME_DETECTOR') return 'unsupervised';
  return 'supervised';
}
