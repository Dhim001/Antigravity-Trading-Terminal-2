import { DEFAULT_TERMINAL_SETTINGS } from './defaults';
import { getEffectiveSettings } from './themePresets';

/** CSS custom properties driven by user settings */
const SETTINGS_CSS_VARS = [
  ['--color-up', (s) => s.bullishColor],
  ['--color-down', (s) => s.bearishColor],
  ['--color-accent', (s) => s.accentColor],
  ['--color-accent-light', (s) => s.chart.crosshairColor || s.accentColor],
  ['--chart-bg', (s) => s.chart.background],
  ['--chart-grid', (s) => s.chart.gridColor],
  ['--chart-crosshair', (s) => s.chart.crosshairColor],
  ['--chart-bullish', (s) => s.chart.bullishColor],
  ['--chart-bearish', (s) => s.chart.bearishColor],
];

/**
 * Apply trading + chart tokens to the document root.
 * @param {import('./defaults').TerminalSettings} [settings]
 * @param {'light' | 'dark'} [resolvedTheme]
 */
export function applySettingsToDOM(settings = DEFAULT_TERMINAL_SETTINGS, resolvedTheme = 'dark') {
  if (typeof document === 'undefined') return;

  const effective = getEffectiveSettings(settings, resolvedTheme);
  const root = document.documentElement;

  for (const [varName, getter] of SETTINGS_CSS_VARS) {
    root.style.setProperty(varName, getter(effective));
  }

  root.style.setProperty('--color-up-bg', hexToRgba(effective.bullishColor, 0.12));
  root.style.setProperty('--color-down-bg', hexToRgba(effective.bearishColor, 0.12));
  root.style.setProperty('--color-accent-bg', hexToRgba(effective.accentColor, 0.12));
}

/**
 * Build ECharts option fragments from settings.
 * @param {import('./defaults').TerminalSettings} settings
 * @param {'light' | 'dark'} [resolvedTheme]
 */
export function getChartEchartsTheme(settings, resolvedTheme = 'dark') {
  const effective = getEffectiveSettings(settings, resolvedTheme);
  const grid = effective.chart.gridColor;
  const axisLine = grid.replace(/[\d.]+\)$/, '0.12)');

  return {
    backgroundColor: effective.chart.background,
    gridColor: grid,
    axisLineColor: axisLine,
    axisLabelColor: effective._axisLabelColor || '#9ca3af',
    crosshairLabelBg: effective.chart.crosshairColor,
    bullishColor: effective.chart.bullishColor,
    bearishColor: effective.chart.bearishColor,
    accentColor: effective.accentColor,
    dataZoomFiller: hexToRgba(effective.accentColor, 0.12),
    echartsTheme: resolvedTheme === 'light' ? null : 'dark',
  };
}

/**
 * @param {string} hex
 * @param {number} alpha
 */
export function hexToRgba(hex, alpha) {
  if (!hex || typeof hex !== 'string') return `rgba(37,99,235,${alpha})`;
  if (hex.startsWith('rgba') || hex.startsWith('rgb')) return hex;

  const normalized = hex.replace('#', '');
  if (normalized.length !== 6) return `rgba(37,99,235,${alpha})`;

  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) {
    return `rgba(37,99,235,${alpha})`;
  }
  return `rgba(${r},${g},${b},${alpha})`;
}
