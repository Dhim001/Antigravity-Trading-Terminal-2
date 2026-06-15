/** @typedef {'light' | 'dark'} ResolvedTheme */

/** Chart canvas surfaces per resolved theme */
export const CHART_SURFACE_PRESETS = {
  dark: {
    background: '#080d14',
    gridColor: 'rgba(255,255,255,0.03)',
    crosshairColor: '#3b82f6',
    axisLabelColor: '#9ca3af',
  },
  light: {
    background: '#f8fafc',
    gridColor: 'rgba(15,23,42,0.07)',
    crosshairColor: '#2563eb',
    axisLabelColor: '#64748b',
  },
};

/** Shared trading color defaults (theme-independent) */
export const APPEARANCE_DEFAULTS = {
  accentColor: '#2563eb',
  bullishColor: '#10b981',
  bearishColor: '#ef4444',
};

/**
 * @param {import('./defaults').TerminalSettings} settings
 * @param {ResolvedTheme} resolvedTheme
 */
export function resolveChartSurfaces(settings, resolvedTheme) {
  const preset = CHART_SURFACE_PRESETS[resolvedTheme] || CHART_SURFACE_PRESETS.dark;

  if (settings.syncChartToTheme === false) {
    return settings.chart;
  }

  return {
    ...settings.chart,
    background: preset.background,
    gridColor: preset.gridColor,
    crosshairColor: settings.chart.crosshairColor || preset.crosshairColor,
  };
}

/**
 * @param {import('./defaults').TerminalSettings} settings
 * @param {ResolvedTheme} resolvedTheme
 * @returns {import('./defaults').TerminalSettings}
 */
export function getEffectiveSettings(settings, resolvedTheme = 'dark') {
  const theme = resolvedTheme === 'light' ? 'light' : 'dark';
  const preset = CHART_SURFACE_PRESETS[theme];

  return {
    ...settings,
    chart: resolveChartSurfaces(settings, theme),
    _resolvedTheme: theme,
    _axisLabelColor: preset.axisLabelColor,
  };
}

/**
 * Chart surfaces for a theme preset (used when switching theme or resetting).
 * @param {ResolvedTheme} resolvedTheme
 */
export function themeChartDefaults(resolvedTheme = 'dark') {
  const preset = CHART_SURFACE_PRESETS[resolvedTheme] || CHART_SURFACE_PRESETS.dark;
  return {
    background: preset.background,
    gridColor: preset.gridColor,
    crosshairColor: preset.crosshairColor,
    bullishColor: APPEARANCE_DEFAULTS.bullishColor,
    bearishColor: APPEARANCE_DEFAULTS.bearishColor,
  };
}
