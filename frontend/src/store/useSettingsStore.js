import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import {
  SETTINGS_STORAGE_KEY,
  DEFAULT_TERMINAL_SETTINGS,
  CHART_LAYOUT_STORAGE_KEYS,
  CHART_LAYOUT_RESET_EVENT,
  migrateSettings,
} from '../settings/defaults';
import { applySettingsToDOM } from '../settings/applySettings';
import { getEffectiveSettings, themeChartDefaults, APPEARANCE_DEFAULTS } from '../settings/themePresets';

export const SETTINGS_THEME_CHANGE_EVENT = 'terminal:theme-change';

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) return migrateSettings(null);
    return migrateSettings(JSON.parse(raw));
  } catch (_) {
    return migrateSettings(null);
  }
}

function persistSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch (_) {}
}

function dispatchThemeChange(resolvedTheme) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(SETTINGS_THEME_CHANGE_EVENT, {
    detail: { resolvedTheme },
  }));
}

const initialSettings = loadSettings();

export const useSettingsStore = create(subscribeWithSelector((set, get) => ({
  settings: initialSettings,
  resolvedTheme: 'dark',
  panelOpen: false,
  /** Deep-link tab when opening preferences */
  panelTab: 'appearance',

  setPanelOpen: (open, tab) => set({
    panelOpen: open,
    ...(tab ? { panelTab: tab } : {}),
  }),

  setResolvedTheme: (resolvedTheme) => {
    const theme = resolvedTheme === 'light' ? 'light' : 'dark';
    const prev = get().resolvedTheme;
    if (prev === theme) return;

    const { settings } = get();
    let nextSettings = settings;

    if (settings.syncChartToTheme !== false) {
      nextSettings = {
        ...settings,
        chart: {
          ...settings.chart,
          ...themeChartDefaults(theme),
        },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(nextSettings);
    }

    set({ resolvedTheme: theme, settings: nextSettings });
    applySettingsToDOM(nextSettings, theme);
    dispatchThemeChange(theme);
  },

  applyToDOM: () => {
    const { settings, resolvedTheme } = get();
    applySettingsToDOM(settings, resolvedTheme);
  },

  /** Effective settings merged with active theme surfaces */
  getEffective: () => getEffectiveSettings(get().settings, get().resolvedTheme),

  /** @param {Partial<import('../settings/defaults').TerminalSettings>} patch */
  updateSettings: (patch) => {
    set((state) => {
      const next = {
        ...state.settings,
        ...patch,
        chart: { ...state.settings.chart, ...(patch.chart || {}) },
        chartLayout: { ...state.settings.chartLayout, ...(patch.chartLayout || {}) },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
    const { settings, resolvedTheme } = get();
    applySettingsToDOM(settings, resolvedTheme);
    dispatchThemeChange(resolvedTheme);
  },

  /**
   * Set theme mode and optionally refresh chart surfaces from preset.
   * @param {'light' | 'dark' | 'system'} themeMode
   */
  setThemeMode: (themeMode) => {
    const { settings, resolvedTheme } = get();
    const nextResolved = themeMode === 'light'
      ? 'light'
      : themeMode === 'dark'
        ? 'dark'
        : resolvedTheme;

    if (themeMode === 'light' || themeMode === 'dark') {
      set({ resolvedTheme: themeMode });
    }

    const patch = { theme: themeMode };
    if (settings.syncChartToTheme !== false) {
      patch.chart = {
        ...settings.chart,
        ...themeChartDefaults(nextResolved),
      };
    }
    get().updateSettings(patch);
  },

  /** @param {Partial<import('../settings/defaults').ChartLayoutSettings>} patch */
  updateChartLayout: (patch) => {
    set((state) => {
      const next = {
        ...state.settings,
        chartLayout: {
          ...state.settings.chartLayout,
          ...patch,
          activeIndicators: patch.activeIndicators
            ? { ...state.settings.chartLayout.activeIndicators, ...patch.activeIndicators }
            : state.settings.chartLayout.activeIndicators,
        },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
  },

  resetAppearance: () => {
    const { theme, chartLayout, resolvedTheme, syncChartToTheme } = get().settings;
    get().updateSettings({
      accentColor: APPEARANCE_DEFAULTS.accentColor,
      bullishColor: APPEARANCE_DEFAULTS.bullishColor,
      bearishColor: APPEARANCE_DEFAULTS.bearishColor,
      chart: {
        ...(syncChartToTheme !== false
          ? themeChartDefaults(resolvedTheme)
          : DEFAULT_TERMINAL_SETTINGS.chart),
        bullishColor: APPEARANCE_DEFAULTS.bullishColor,
        bearishColor: APPEARANCE_DEFAULTS.bearishColor,
      },
      theme,
      chartLayout,
    });
  },

  resetChartLayout: () => {
    for (const key of CHART_LAYOUT_STORAGE_KEYS) {
      try { localStorage.removeItem(key); } catch (_) {}
    }

    const chartLayout = { ...DEFAULT_TERMINAL_SETTINGS.chartLayout };

    set((state) => {
      const next = {
        ...state.settings,
        chartLayout,
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });

    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(CHART_LAYOUT_RESET_EVENT, {
        detail: { chartLayout },
      }));
    }
  },
})));

if (typeof document !== 'undefined') {
  applySettingsToDOM(initialSettings, 'dark');
}
