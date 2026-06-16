/** @typedef {'light' | 'dark' | 'system'} ThemeMode */
/** @typedef {'candle' | 'line'} ChartType */

/**
 * @typedef {Object} ChartAppearanceSettings
 * @property {string} background
 * @property {string} gridColor
 * @property {string} crosshairColor
 * @property {string} bullishColor
 * @property {string} bearishColor
 */

/**
 * @typedef {Object} ChartLayoutSettings
 * @property {string} timeframe
 * @property {ChartType} chartType
 * @property {Record<string, boolean>} activeIndicators
 * @property {string} multiChartLayoutId
 * @property {string[]} multiChartSymbols
 * @property {ChartOverlaySettings} overlays
 */

/**
 * @typedef {Object} ChartOverlaySettings
 * @property {boolean} trades
 * @property {boolean} positions
 * @property {boolean} agentLevels
 * @property {boolean} botMarkers
 */

/**
 * @typedef {Object} AlertRule
 * @property {string} id
 * @property {string} symbol
 * @property {'price_above' | 'price_below' | 'signal_change'} type
 * @property {number} [threshold]
 * @property {'BUY' | 'SELL' | 'NONE'} [signal]
 * @property {boolean} [enabled]

/**
 * @typedef {Object} TerminalSettings
 * @property {1} version
 * @property {ThemeMode} theme
 * @property {string} accentColor
 * @property {string} bullishColor
 * @property {string} bearishColor
 * @property {ChartAppearanceSettings} chart
 * @property {ChartLayoutSettings} chartLayout
 * @property {WorkspaceSettings} workspace
 * @property {WorkspacePreset[]} workspacePresets
 * @property {AlertRule[]} alerts
 * @property {boolean} onboardingCompleted
 * @property {boolean} [syncChartToTheme] Auto-match chart canvas to light/dark theme
 * @property {string} updatedAt
 */

/**
 * @typedef {Object} WorkspaceSettings
 * @property {number} dockHeight
 * @property {number} sidebarWidth
 * @property {string} dockActiveTab
 * @property {'portfolio' | 'intelligence' | 'automation' | 'data'} dockGroup
 * @property {'single' | 'multi'} viewMode
 * @property {'all' | 'focused'} chartLinkMode
 * @property {'trade' | 'analyze' | 'automate' | 'portfolio'} layoutMode
 * @property {boolean} zenMode
 * @property {boolean} rightPanelCollapsed
 * @property {'trade' | 'book' | 'depth'} rightPanelTab
 * @property {boolean} dockCollapsed
 * @property {'compact' | 'comfortable'} density
 */

/**
 * @typedef {Object} WorkspacePreset
 * @property {string} id
 * @property {string} name
 * @property {WorkspaceSettings} workspace
 * @property {import('./defaults').ChartLayoutSettings} chartLayout
 */

export const SETTINGS_STORAGE_KEY = 'terminal_settings_v1';

export const CHART_LAYOUT_RESET_EVENT = 'terminal:chart-layout-reset';

/** Legacy keys cleared by "Reset chart layout" */
export const CHART_LAYOUT_STORAGE_KEYS = [
  'terminal_tf',
  'terminal_chart_indicators_active',
  'terminal_chart_type',
  'terminal_multi_chart_layout_id',
  'terminal_multi_chart_symbols',
];

/** @type {TerminalSettings} */
export const DEFAULT_TERMINAL_SETTINGS = {
  version: 1,
  theme: 'dark',
  accentColor: '#2563eb',
  bullishColor: '#10b981',
  bearishColor: '#ef4444',
  chart: {
    background: '#080d14',
    gridColor: 'rgba(255,255,255,0.03)',
    crosshairColor: '#3b82f6',
    bullishColor: '#10b981',
    bearishColor: '#ef4444',
  },
  syncChartToTheme: true,
  chartLayout: {
    timeframe: '1m',
    chartType: 'candle',
    activeIndicators: {
      ema9: true,
      ema21: true,
      ema50: false,
      bb: true,
      vwap: false,
      rsi: true,
      macd: true,
      atr: false,
      volume: true,
    },
    multiChartLayoutId: '2x2',
    multiChartSymbols: ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA'],
    overlays: {
      trades: true,
      positions: true,
      agentLevels: true,
      botMarkers: true,
    },
  },
  workspace: {
    dockHeight: 320,
    sidebarWidth: 260,
    dockActiveTab: 'positions',
    dockGroup: 'portfolio',
    viewMode: 'single',
    chartLinkMode: 'all',
    layoutMode: 'trade',
    zenMode: false,
    rightPanelCollapsed: false,
    rightPanelTab: 'trade',
    dockCollapsed: false,
    density: 'compact',
  },
  workspacePresets: [],
  alerts: [],
  onboardingCompleted: false,
  updatedAt: new Date().toISOString(),
};

/** Built-in workspace presets (UX-6) */
export const BUILTIN_WORKSPACE_PRESETS = [
  {
    id: 'builtin-trade',
    name: 'Day Trade',
    workspace: {
      ...DEFAULT_TERMINAL_SETTINGS.workspace,
      layoutMode: 'trade',
      dockActiveTab: 'positions',
      dockGroup: 'portfolio',
      dockHeight: 260,
      rightPanelCollapsed: false,
      rightPanelTab: 'trade',
    },
    chartLayout: { ...DEFAULT_TERMINAL_SETTINGS.chartLayout },
  },
  {
    id: 'builtin-analyze',
    name: 'Research',
    workspace: {
      ...DEFAULT_TERMINAL_SETTINGS.workspace,
      layoutMode: 'analyze',
      dockActiveTab: 'scanner',
      dockGroup: 'intelligence',
      dockHeight: 280,
      rightPanelCollapsed: true,
    },
    chartLayout: { ...DEFAULT_TERMINAL_SETTINGS.chartLayout },
  },
  {
    id: 'builtin-automate',
    name: 'Bot Ops',
    workspace: {
      ...DEFAULT_TERMINAL_SETTINGS.workspace,
      layoutMode: 'automate',
      dockActiveTab: 'algo',
      dockGroup: 'automation',
      dockHeight: 320,
      rightPanelCollapsed: true,
    },
    chartLayout: { ...DEFAULT_TERMINAL_SETTINGS.chartLayout },
  },
];

export function ensureBuiltinPresets(presets) {
  const existing = Array.isArray(presets) ? [...presets] : [];
  const ids = new Set(existing.map((p) => p.id));
  const merged = [...existing];
  for (const builtin of BUILTIN_WORKSPACE_PRESETS) {
    if (!ids.has(builtin.id)) merged.push(builtin);
  }
  return merged;
}

/**
 * @param {unknown} raw
 * @returns {TerminalSettings}
 */
export function migrateSettings(raw) {
  if (!raw || typeof raw !== 'object') {
    return {
      ...DEFAULT_TERMINAL_SETTINGS,
      workspacePresets: ensureBuiltinPresets([]),
      updatedAt: new Date().toISOString(),
    };
  }

  const base = { ...DEFAULT_TERMINAL_SETTINGS };
  const input = /** @type {Partial<TerminalSettings>} */ (raw);

  return {
    version: 1,
    theme: input.theme === 'light' || input.theme === 'system' ? input.theme : 'dark',
    accentColor: input.accentColor || base.accentColor,
    bullishColor: input.bullishColor || base.bullishColor,
    bearishColor: input.bearishColor || base.bearishColor,
    chart: {
      ...base.chart,
      ...(input.chart || {}),
      bullishColor: input.chart?.bullishColor || input.bullishColor || base.chart.bullishColor,
      bearishColor: input.chart?.bearishColor || input.bearishColor || base.chart.bearishColor,
    },
    syncChartToTheme: input.syncChartToTheme !== false,
    chartLayout: {
      ...base.chartLayout,
      ...(input.chartLayout || {}),
      activeIndicators: {
        ...base.chartLayout.activeIndicators,
        ...(input.chartLayout?.activeIndicators || {}),
      },
      multiChartSymbols: Array.isArray(input.chartLayout?.multiChartSymbols)
        ? input.chartLayout.multiChartSymbols
        : base.chartLayout.multiChartSymbols,
      overlays: {
        ...base.chartLayout.overlays,
        ...(input.chartLayout?.overlays || {}),
      },
    },
    workspace: {
      ...base.workspace,
      ...(input.workspace || {}),
      layoutMode: ['trade', 'analyze', 'automate', 'portfolio'].includes(input.workspace?.layoutMode)
        ? input.workspace.layoutMode
        : base.workspace.layoutMode,
      dockGroup: ['portfolio', 'intelligence', 'automation', 'data'].includes(input.workspace?.dockGroup)
        ? input.workspace.dockGroup
        : base.workspace.dockGroup,
      rightPanelTab: ['trade', 'book', 'depth'].includes(input.workspace?.rightPanelTab)
        ? input.workspace.rightPanelTab
        : base.workspace.rightPanelTab,
      density: input.workspace?.density === 'comfortable' ? 'comfortable' : 'compact',
      zenMode: Boolean(input.workspace?.zenMode),
      rightPanelCollapsed: Boolean(input.workspace?.rightPanelCollapsed),
      dockCollapsed: Boolean(input.workspace?.dockCollapsed),
    },
    workspacePresets: ensureBuiltinPresets(input.workspacePresets),
    alerts: Array.isArray(input.alerts) ? input.alerts : [],
    onboardingCompleted: Boolean(input.onboardingCompleted),
    updatedAt: input.updatedAt || new Date().toISOString(),
  };
}
