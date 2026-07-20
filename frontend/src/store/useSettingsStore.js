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

  /** @param {Partial<import('../settings/defaults').WorkspaceSettings>} patch */
  updateWorkspace: (patch) => {
    set((state) => {
      const next = {
        ...state.settings,
        workspace: { ...state.settings.workspace, ...patch },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
  },

  saveWorkspacePreset: (name) => {
    const { settings } = get();
    const id = `ws-${Date.now()}`;
    const preset = {
      id,
      name: name || `Workspace ${settings.workspacePresets.length + 1}`,
      workspace: { ...settings.workspace },
      chartLayout: { ...settings.chartLayout },
    };
    set((state) => {
      const next = {
        ...state.settings,
        workspacePresets: [preset, ...state.settings.workspacePresets].slice(0, 12),
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
    return id;
  },

  loadWorkspacePreset: (id) => {
    const { settings } = get();
    const preset = settings.workspacePresets.find((p) => p.id === id);
    if (!preset) return false;
    set((state) => {
      const next = {
        ...state.settings,
        workspace: { ...preset.workspace },
        chartLayout: {
          ...state.settings.chartLayout,
          ...preset.chartLayout,
          activeIndicators: {
            ...state.settings.chartLayout.activeIndicators,
            ...(preset.chartLayout?.activeIndicators || {}),
          },
        },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('terminal:workspace-loaded', { detail: preset }));
    }
    return true;
  },

  deleteWorkspacePreset: (id) => {
    set((state) => {
      const next = {
        ...state.settings,
        workspacePresets: state.settings.workspacePresets.filter((p) => p.id !== id),
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
  },

  cloudWorkspaces: [],
  cloudWorkspacesLoading: false,

  fetchCloudWorkspaces: async () => {
    set({ cloudWorkspacesLoading: true });
    try {
      const { fetchWorkspaces } = await import('../api/endpoints');
      const res = await fetchWorkspaces();
      if (res.ok) {
        set({ cloudWorkspaces: res.workspaces, cloudWorkspacesLoading: false });
      }
    } catch (err) {
      console.error('Failed to fetch cloud workspaces', err);
      set({ cloudWorkspacesLoading: false });
    }
  },

  saveCloudWorkspace: async (name) => {
    try {
      const { settings } = get();
      const state = {
        workspace: settings.workspace,
        chartLayout: settings.chartLayout,
      };
      const { saveWorkspace } = await import('../api/endpoints');
      const res = await saveWorkspace(null, name, state);
      if (res.ok) {
        await get().fetchCloudWorkspaces();
        return res.workspace_id;
      }
    } catch (err) {
      console.error('Failed to save cloud workspace', err);
    }
    return null;
  },

  deleteCloudWorkspace: async (id) => {
    try {
      const { deleteWorkspace } = await import('../api/endpoints');
      const res = await deleteWorkspace(id);
      if (res.ok) {
        set((state) => ({
          cloudWorkspaces: state.cloudWorkspaces.filter(w => w.id !== id),
        }));
      }
    } catch (err) {
      console.error('Failed to delete cloud workspace', err);
    }
  },

  loadCloudWorkspace: (id) => {
    const { cloudWorkspaces, settings } = get();
    const ws = cloudWorkspaces.find((w) => w.id === id);
    if (!ws || !ws.state) return false;
    
    set((state) => {
      const next = {
        ...state.settings,
        workspace: { ...ws.state.workspace },
        chartLayout: {
          ...state.settings.chartLayout,
          ...ws.state.chartLayout,
          activeIndicators: {
            ...state.settings.chartLayout.activeIndicators,
            ...(ws.state.chartLayout?.activeIndicators || {}),
          },
        },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('terminal:workspace-loaded', { detail: ws.state }));
    }
    return true;
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
          overlays: patch.overlays
            ? { ...state.settings.chartLayout.overlays, ...patch.overlays }
            : state.settings.chartLayout.overlays,
        },
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
  },

  setOnboardingCompleted: (done = true) => {
    set((state) => {
      const next = {
        ...state.settings,
        onboardingCompleted: done,
        updatedAt: new Date().toISOString(),
      };
      persistSettings(next);
      return { settings: next };
    });
  },

  /** @param {import('../settings/defaults').AlertRule[]} alerts */
  setAlerts: (alerts) => {
    set((state) => {
      const next = {
        ...state.settings,
        alerts,
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
