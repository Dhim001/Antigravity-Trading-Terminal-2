/**
 * TradingView-style indicator colors (built-in palette + per-study defaults).
 * @see https://www.tradingview.com/pine-script-docs/visuals/colors/
 */
import { hexToRgba } from './applySettings';

/** TradingView Pine Script built-in color constants */
export const TV_COLORS = {
  aqua: '#00BCD4',
  black: '#363A45',
  blue: '#2196F3',
  fuchsia: '#E040FB',
  gray: '#787B86',
  green: '#4CAF50',
  lime: '#00E676',
  maroon: '#880E4F',
  navy: '#311B92',
  olive: '#808000',
  orange: '#FF9800',
  purple: '#9C27B0',
  red: '#F23645',
  silver: '#B2B5BE',
  teal: '#089981',
  white: '#FFFFFF',
  yellow: '#FDD835',
};

/** @typedef {typeof TRADINGVIEW_INDICATOR_THEMES.dark} IndicatorTheme */

export const TRADINGVIEW_INDICATOR_THEMES = {
  dark: {
    ema9: { line: TV_COLORS.blue, width: 1, opacity: 1 },
    ema21: { line: TV_COLORS.orange, width: 1, opacity: 1 },
    ema50: { line: TV_COLORS.fuchsia, width: 1, opacity: 1 },
    bb: {
      outer: TV_COLORS.blue,
      basis: TV_COLORS.gray,
      fill: 'rgba(33, 150, 243, 0.12)',
      outerOpacity: 0.85,
      basisOpacity: 0.9,
    },
    vwap: { line: TV_COLORS.purple, width: 2, opacity: 1 },
    volume: { up: TV_COLORS.teal, down: TV_COLORS.red, opacity: 0.55 },
    rsi: { line: TV_COLORS.purple, width: 2, guide: TV_COLORS.gray },
    macd: {
      line: TV_COLORS.blue,
      signal: TV_COLORS.orange,
      histUp: TV_COLORS.teal,
      histDown: TV_COLORS.red,
      histOpacity: 0.7,
      zeroLine: TV_COLORS.gray,
      lineWidth: 1.2,
    },
    atr: { line: TV_COLORS.orange, width: 2, opacity: 1 },
  },
  light: {
    ema9: { line: '#1E88E5', width: 1, opacity: 1 },
    ema21: { line: '#EF6C00', width: 1, opacity: 1 },
    ema50: { line: '#C2185B', width: 1, opacity: 1 },
    bb: {
      outer: '#1E88E5',
      basis: TV_COLORS.gray,
      fill: 'rgba(30, 136, 229, 0.10)',
      outerOpacity: 0.9,
      basisOpacity: 0.95,
    },
    vwap: { line: '#7B1FA2', width: 2, opacity: 1 },
    volume: { up: '#00897B', down: '#D32F2F', opacity: 0.55 },
    rsi: { line: '#7B1FA2', width: 2, guide: TV_COLORS.gray },
    macd: {
      line: '#1E88E5',
      signal: '#EF6C00',
      histUp: '#00897B',
      histDown: '#D32F2F',
      histOpacity: 0.7,
      zeroLine: TV_COLORS.gray,
      lineWidth: 1.2,
    },
    atr: { line: '#EF6C00', width: 2, opacity: 1 },
  },
};

/**
 * @param {'light' | 'dark'} [resolvedTheme]
 * @returns {IndicatorTheme}
 */
export function getIndicatorTheme(resolvedTheme = 'dark') {
  return TRADINGVIEW_INDICATOR_THEMES[resolvedTheme === 'light' ? 'light' : 'dark'];
}

/**
 * Toolbar toggle metadata (label + swatch color).
 * @param {IndicatorTheme} theme
 */
export function getIndicatorToolbarMeta(theme) {
  return {
    ema9: { label: 'EMA 9', color: theme.ema9.line },
    ema21: { label: 'EMA 21', color: theme.ema21.line },
    ema50: { label: 'EMA 50', color: theme.ema50.line },
    bb: { label: 'BB 20', color: theme.bb.outer },
    vwap: { label: 'VWAP', color: theme.vwap.line },
    volume: { label: 'Volume', color: theme.volume.up },
    rsi: { label: 'RSI 14', color: theme.rsi.line },
    macd: { label: 'MACD', color: theme.macd.line },
    atr: { label: 'ATR 14', color: theme.atr.line },
  };
}

/**
 * @param {{ open: number, close: number, volume?: number }} bar
 * @param {IndicatorTheme} theme
 */
export function volumeBarEntry(bar, theme) {
  const up = bar.close >= bar.open;
  const base = up ? theme.volume.up : theme.volume.down;
  return {
    value: bar.volume || 0,
    itemStyle: { color: hexToRgba(base, theme.volume.opacity) },
  };
}

/**
 * @param {number} value
 * @param {IndicatorTheme} theme
 */
export function macdHistogramColor(value, theme) {
  const base = value >= 0 ? theme.macd.histUp : theme.macd.histDown;
  return hexToRgba(base, theme.macd.histOpacity);
}

/** @param {IndicatorTheme} theme */
export function rsiMarkLine(theme) {
  return {
    silent: true,
    symbol: ['none', 'none'],
    animation: false,
    lineStyle: { type: 'dashed', color: theme.rsi.guide, width: 1, opacity: 0.55 },
    label: { show: false },
    data: [{ yAxis: 70 }, { yAxis: 50 }, { yAxis: 30 }],
  };
}

/** @param {IndicatorTheme} theme */
export function macdZeroMarkLine(theme) {
  return {
    silent: true,
    symbol: ['none', 'none'],
    animation: false,
    lineStyle: { type: 'dashed', color: theme.macd.zeroLine, width: 1, opacity: 0.45 },
    label: { show: false },
    data: [{ yAxis: 0 }],
  };
}

/**
 * @param {9 | 21 | 50} period
 * @param {IndicatorTheme} theme
 */
export function emaLineStyle(period, theme) {
  const cfg = period === 9 ? theme.ema9 : period === 21 ? theme.ema21 : theme.ema50;
  return { color: cfg.line, width: cfg.width, opacity: cfg.opacity };
}
