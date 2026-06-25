import React, { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useTheme } from 'next-themes';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { IS_OPERATOR, brokerLabel } from '../lib/operator';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { Badge } from '@/components/ui/badge';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  Palette,
  BarChart3,
  LayoutGrid,
  RotateCcw,
  Sun,
  Moon,
  Monitor,
  ShieldAlert,
  Cpu,
  Wifi,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { themeChartDefaults, getEffectiveSettings } from '../settings/themePresets';
import { getIndicatorTheme, getIndicatorToolbarMeta } from '../settings/indicatorThemes';
import { DEFAULT_TERMINAL_SETTINGS } from '../settings/defaults';
import { fetchHealth, parseMetricsSummary, fetchLlmModels } from '../api/endpoints';
import LlmSettingsSection from './LlmSettingsSection';
import {
  useMemoryObservability,
  MemoryObservabilityBadge,
  MemoryObservabilityBody,
} from './MemoryObservabilitySection';
import { HTTP_BASE_URL } from '../api/config';

const PRESET_SWATCHES = {
  bullish: ['#10b981', '#22c55e', '#00d4aa', '#4ade80'],
  bearish: ['#ef4444', '#f87171', '#ff4757', '#dc2626'],
  accent: ['#2563eb', '#3b82f6', '#6366f1', '#0ea5e9'],
};

const CHART_TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

function ColorField({ id, label, value, onChange, presets = [], onCustomize }) {
  return (
    <div className="settings-color-field">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      <div className="settings-color-field__row">
        <input
          id={id}
          type="color"
          value={value.startsWith('#') ? value : '#2563eb'}
          onChange={(e) => {
            onCustomize?.();
            onChange(e.target.value);
          }}
          className="settings-color-input"
          aria-label={`${label} color picker`}
        />
        <input
          type="text"
          value={value}
          onChange={(e) => {
            onCustomize?.();
            onChange(e.target.value);
          }}
          className="settings-color-text num-mono"
          spellCheck={false}
        />
      </div>
      {presets.length > 0 && (
        <div className="settings-color-presets">
          {presets.map((c) => (
            <button
              key={c}
              type="button"
              className="settings-color-swatch"
              style={{ backgroundColor: c }}
              onClick={() => {
                onCustomize?.();
                onChange(c);
              }}
              title={c}
              aria-label={`Use ${c}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SettingsAccordionSection({ value, title, hint, badge, children }) {
  return (
    <AccordionItem value={value} className="settings-accordion__item">
      <AccordionTrigger className="settings-accordion__trigger">
        <div className="flex min-w-0 flex-1 items-start justify-between gap-2 pr-1">
          <div className="flex min-w-0 flex-col gap-0.5 text-left">
            <span className="settings-accordion__title">{title}</span>
            {hint && <span className="settings-accordion__hint">{hint}</span>}
          </div>
          {badge}
        </div>
      </AccordionTrigger>
      <AccordionContent className="settings-accordion__content">
        <div className="settings-accordion__inner">{children}</div>
      </AccordionContent>
    </AccordionItem>
  );
}

export default function SettingsPanel({ open, onOpenChange, onOpenAdmin }) {
  const { systemTheme: osTheme } = useTheme();
  const settings = useSettingsStore((s) => s.settings);
  const resolvedTheme = useSettingsStore((s) => s.resolvedTheme);
  const panelTab = useSettingsStore((s) => s.panelTab);
  const setPanelOpen = useSettingsStore((s) => s.setPanelOpen);
  const updateSettings = useSettingsStore((s) => s.updateSettings);
  const setThemeMode = useSettingsStore((s) => s.setThemeMode);
  const resetAppearance = useSettingsStore((s) => s.resetAppearance);
  const resetChartLayout = useSettingsStore((s) => s.resetChartLayout);
  const updateWorkspace = useSettingsStore((s) => s.updateWorkspace);
  const saveWorkspacePreset = useSettingsStore((s) => s.saveWorkspacePreset);
  const loadWorkspacePreset = useSettingsStore((s) => s.loadWorkspacePreset);
  const deleteWorkspacePreset = useSettingsStore((s) => s.deleteWorkspacePreset);
  const setAlerts = useSettingsStore((s) => s.setAlerts);
  const updateChartLayout = useSettingsStore((s) => s.updateChartLayout);
  const setOnboardingCompleted = useSettingsStore((s) => s.setOnboardingCompleted);
  const activeSymbol = useStore((s) => s.activeSymbol);

  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);
  const isLive = useStore((s) => s.isLive);
  const terminalMode = useStore((s) => s.terminalMode);
  const terminalRole = useStore((s) => s.terminalRole);
  const distributed = useStore((s) => s.distributed);
  const allowLiveBots = useStore((s) => s.allowLiveBots);
  const allowCustomStrategies = useStore((s) => s.allowCustomStrategies);
  const archiveParquetEnabled = useStore((s) => s.archiveParquetEnabled);
  const archiveBackend = useStore((s) => s.archiveBackend);
  const archiveTicksEnabled = useStore((s) => s.archiveTicksEnabled);
  const botMinCandles = useStore((s) => s.botMinCandles);
  const agentVisionEnabled = useStore((s) => s.agentVisionEnabled);
  const agentEnabled = useStore((s) => s.agentEnabled);
  const scannerEnabled = useStore((s) => s.scannerEnabled);
  const workerAlive = useStore((s) => s.workerAlive);
  const workerHeartbeatAge = useStore((s) => s.workerHeartbeatAge);
  const isBotRunning = useStore((s) => s.isBotRunning);
  const agentLlmEnabled = useStore((s) => s.agentLlmEnabled);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const agentLlmProvider = useStore((s) => s.agentLlmProvider);
  const agentLlmModel = useStore((s) => s.agentLlmModel);
  const agentLlmModels = useStore((s) => s.agentLlmModels);
  const selectedLlmModel = useStore((s) => s.selectedLlmModel);
  const setSelectedLlmModel = useStore((s) => s.setSelectedLlmModel);

  const [activeTab, setActiveTab] = React.useState(panelTab);
  const [presetName, setPresetName] = React.useState('');
  const [obsHealth, setObsHealth] = useState(null);
  const memoryObs = useMemoryObservability();
  const [obsMetrics, setObsMetrics] = useState(null);
  const [alertDraft, setAlertDraft] = useState({
    symbol: activeSymbol,
    type: 'price_above',
    threshold: '',
    signal: 'BUY',
  });
  const [editingAlertId, setEditingAlertId] = useState(null);

  useEffect(() => {
    if (!editingAlertId) {
      setAlertDraft((d) => ({ ...d, symbol: activeSymbol }));
    }
  }, [activeSymbol, editingAlertId]);

  const effectiveChart = useMemo(
    () => getEffectiveSettings(settings, resolvedTheme).chart,
    [settings, resolvedTheme],
  );

  const indicatorTheme = useMemo(
    () => getIndicatorTheme(resolvedTheme),
    [resolvedTheme],
  );
  const indicatorToolbar = useMemo(
    () => getIndicatorToolbarMeta(indicatorTheme),
    [indicatorTheme],
  );

  const chartLayout = settings.chartLayout ?? DEFAULT_TERMINAL_SETTINGS.chartLayout;
  const activeIndicatorKeys = useMemo(
    () => Object.entries(chartLayout.activeIndicators || {})
      .filter(([, on]) => on)
      .map(([k]) => k),
    [chartLayout.activeIndicators],
  );

  useEffect(() => {
    if (open) setActiveTab(panelTab);
  }, [open, panelTab]);

  useEffect(() => {
    if (!open || activeTab !== 'system') return;
    let cancelled = false;
    (async () => {
      try {
        const health = await fetchHealth(useStore.getState());
        if (!cancelled) setObsHealth(health);
      } catch {
        if (!cancelled) setObsHealth(null);
      }
      try {
        const models = await fetchLlmModels(useStore.getState());
        if (!cancelled && models?.ok) {
          useStore.getState().setTerminalConfig({
            agentLlmModels: [...(models.ollama || []), ...(models.openrouter || [])],
            agentLlmModel: models.active_model,
          });
        }
      } catch {
        /* models optional */
      }
      try {
        const base = HTTP_BASE_URL.replace(/\/$/, '');
        const path = base ? `${base}/metrics` : '/metrics';
        const resp = await fetch(path);
        const text = await resp.text();
        if (!cancelled) setObsMetrics(parseMetricsSummary(text));
      } catch {
        if (!cancelled) setObsMetrics(null);
      }
    })();
    return () => { cancelled = true; };
  }, [open, activeTab]);

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    setPanelOpen(true, tab);
  };

  const markChartCustom = () => {
    if (settings.syncChartToTheme !== false) {
      updateSettings({ syncChartToTheme: false });
    }
  };

  const handleResetLayout = () => {
    resetChartLayout();
    toast.success('Chart layout reset', {
      description: 'Indicators, timeframe, and multi-chart layout restored to defaults.',
    });
    onOpenChange(false);
  };

  const connected = connectionStatus === 'connected';
  const resolvedLabel = settings.theme === 'system'
    ? `System → ${resolvedTheme}`
    : settings.theme;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="terminal-sheet settings-panel w-full sm:max-w-lg">
        <SheetHeader className="settings-panel__header terminal-sheet__header">
          <SheetTitle className="settings-panel__title">
            <Palette aria-hidden />
            Preferences
          </SheetTitle>
          <SheetDescription className="settings-panel__description">
            Appearance, charts, layout, and system controls.
          </SheetDescription>
        </SheetHeader>

        <Tabs value={activeTab} onValueChange={handleTabChange} className="terminal-tabs settings-panel__tabs">
          <TabsList variant="line" className="terminal-tabs__list settings-panel__tablist w-full justify-start">
            <TabsTrigger value="appearance" className="gap-1.5 text-xs">
              <Palette size={13} aria-hidden />
              Theme
            </TabsTrigger>
            <TabsTrigger value="chart" className="gap-1.5 text-xs">
              <BarChart3 size={13} aria-hidden />
              Chart
            </TabsTrigger>
            <TabsTrigger value="layout" className="gap-1.5 text-xs">
              <LayoutGrid size={13} aria-hidden />
              Layout
            </TabsTrigger>
            <TabsTrigger value="system" className="gap-1.5 text-xs">
              <Cpu size={13} aria-hidden />
              System
            </TabsTrigger>
          </TabsList>

          <TabsContent value="appearance" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <Accordion type="multiple" defaultValue={['color-mode', 'trading-colors']} className="settings-accordion">
              <SettingsAccordionSection
                value="color-mode"
                title="Color mode"
                badge={(
                  <Badge variant="outline" className="shrink-0 text-xs capitalize">
                    {resolvedLabel}
                  </Badge>
                )}
              >
                <ToggleGroup
                  type="single"
                  value={settings.theme}
                  onValueChange={(v) => v && setThemeMode(v)}
                  className="w-full"
                >
                  <ToggleGroupItem value="dark" className="flex-1 gap-1.5 text-xs">
                    <Moon aria-hidden data-icon="inline-start" />
                    Dark
                  </ToggleGroupItem>
                  <ToggleGroupItem value="light" className="flex-1 gap-1.5 text-xs">
                    <Sun aria-hidden data-icon="inline-start" />
                    Light
                  </ToggleGroupItem>
                  <ToggleGroupItem value="system" className="flex-1 gap-1.5 text-xs">
                    <Monitor aria-hidden data-icon="inline-start" />
                    System
                  </ToggleGroupItem>
                </ToggleGroup>
                {settings.theme === 'system' && (
                  <p className="settings-section__hint">
                    Following OS preference ({osTheme || resolvedTheme}).
                  </p>
                )}
              </SettingsAccordionSection>

              <SettingsAccordionSection value="trading-colors" title="Trading colors">
                <ColorField
                  id="bullish-color"
                  label="Bullish / Up"
                  value={settings.bullishColor}
                  onChange={(v) => updateSettings({
                    bullishColor: v,
                    chart: { ...settings.chart, bullishColor: v },
                  })}
                  presets={PRESET_SWATCHES.bullish}
                />
                <ColorField
                  id="bearish-color"
                  label="Bearish / Down"
                  value={settings.bearishColor}
                  onChange={(v) => updateSettings({
                    bearishColor: v,
                    chart: { ...settings.chart, bearishColor: v },
                  })}
                  presets={PRESET_SWATCHES.bearish}
                />
                <ColorField
                  id="accent-color"
                  label="Accent"
                  value={settings.accentColor}
                  onChange={(v) => updateSettings({ accentColor: v })}
                  presets={PRESET_SWATCHES.accent}
                />
                <div className="flex justify-end pt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 text-xs"
                    onClick={() => {
                      resetAppearance();
                      toast.success('Appearance reset for current theme');
                    }}
                  >
                    <RotateCcw aria-hidden data-icon="inline-start" />
                    Reset appearance
                  </Button>
                </div>
              </SettingsAccordionSection>
            </Accordion>
          </TabsContent>

          <TabsContent value="chart" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <Accordion type="multiple" defaultValue={['chart-controls']} className="settings-accordion">
              <SettingsAccordionSection
                value="chart-controls"
                title="Chart controls"
                hint="Timeframe, chart type, and indicators — synced with the chart toolbar."
              >
                <div>
                  <Label className="mb-1.5 block text-xs text-muted-foreground">Timeframe</Label>
                  <Select
                    value={chartLayout.timeframe}
                    onValueChange={(v) => v && updateChartLayout({ timeframe: v })}
                  >
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue placeholder="Timeframe" />
                    </SelectTrigger>
                    <SelectContent>
                      {CHART_TIMEFRAMES.map((tf) => (
                        <SelectItem key={tf} value={tf} className="text-xs">
                          {tf}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="mb-1.5 block text-xs text-muted-foreground">Chart type</Label>
                  <ToggleGroup
                    type="single"
                    value={chartLayout.chartType}
                    onValueChange={(v) => v && updateChartLayout({ chartType: v })}
                    className="w-full"
                  >
                    <ToggleGroupItem value="candle" className="flex-1 text-xs">Candle</ToggleGroupItem>
                    <ToggleGroupItem value="line" className="flex-1 text-xs">Line</ToggleGroupItem>
                  </ToggleGroup>
                </div>

                <div>
                  <Label className="mb-1.5 block text-xs text-muted-foreground">Indicators</Label>
                  <ToggleGroup
                    type="multiple"
                    value={activeIndicatorKeys}
                    onValueChange={(vals) => {
                      const next = { ...chartLayout.activeIndicators };
                      for (const key of Object.keys(indicatorToolbar)) {
                        next[key] = vals.includes(key);
                      }
                      updateChartLayout({ activeIndicators: next });
                    }}
                    className="flex flex-wrap gap-1"
                    spacing={1}
                  >
                    {Object.entries(indicatorToolbar).map(([key, ind]) => (
                      <ToggleGroupItem
                        key={key}
                        value={key}
                        size="sm"
                        className="gap-1 text-xs font-semibold data-[state=on]:border-[var(--ind-c)] data-[state=on]:bg-[color-mix(in_srgb,var(--ind-c)_14%,transparent)] data-[state=on]:text-[var(--ind-c)]"
                        style={{ '--ind-c': ind.color }}
                      >
                        <span className="size-1.5 shrink-0 rounded-full bg-[var(--ind-c)] opacity-70" />
                        {ind.label}
                      </ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </div>
              </SettingsAccordionSection>

              <SettingsAccordionSection value="chart-canvas" title="Chart canvas">
                <div className="flex items-center justify-between gap-2">
                  <p className="settings-section__hint m-0">
                    When synced, chart background and grid update with Dark / Light / System.
                  </p>
                  <Button
                    variant={settings.syncChartToTheme !== false ? 'secondary' : 'outline'}
                    size="sm"
                    className="shrink-0 text-xs"
                    onClick={() => {
                      const enabling = settings.syncChartToTheme === false;
                      updateSettings({
                        syncChartToTheme: enabling,
                        ...(enabling
                          ? { chart: { ...settings.chart, ...themeChartDefaults(resolvedTheme) } }
                          : {}),
                      });
                    }}
                  >
                    {settings.syncChartToTheme !== false ? 'Synced to theme' : 'Custom colors'}
                  </Button>
                </div>
                <ColorField
                  id="chart-bg"
                  label="Background"
                  value={effectiveChart.background}
                  onChange={(v) => updateSettings({ chart: { ...settings.chart, background: v } })}
                  onCustomize={markChartCustom}
                />
                <ColorField
                  id="chart-grid"
                  label="Grid lines"
                  value={effectiveChart.gridColor}
                  onChange={(v) => updateSettings({ chart: { ...settings.chart, gridColor: v } })}
                  onCustomize={markChartCustom}
                />
                <ColorField
                  id="chart-crosshair"
                  label="Crosshair / focus"
                  value={effectiveChart.crosshairColor}
                  onChange={(v) => updateSettings({ chart: { ...settings.chart, crosshairColor: v } })}
                  presets={PRESET_SWATCHES.accent}
                  onCustomize={markChartCustom}
                />
              </SettingsAccordionSection>

              <SettingsAccordionSection value="candle-colors" title="Candle colors">
                <ColorField
                  id="chart-bullish"
                  label="Bullish candle"
                  value={settings.chart.bullishColor}
                  onChange={(v) => updateSettings({ chart: { ...settings.chart, bullishColor: v } })}
                  presets={PRESET_SWATCHES.bullish}
                />
                <ColorField
                  id="chart-bearish"
                  label="Bearish candle"
                  value={settings.chart.bearishColor}
                  onChange={(v) => updateSettings({ chart: { ...settings.chart, bearishColor: v } })}
                  presets={PRESET_SWATCHES.bearish}
                />
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="chart-overlays"
                title="Chart overlays"
                hint="Toggle trade markers, position lines, and analyst levels on the chart."
              >
                {[
                  ['trades', 'Trade markers'],
                  ['positions', 'Position SL/TP'],
                  ['agentLevels', 'Analyst levels'],
                  ['botMarkers', 'Bot markers'],
                ].map(([key, label]) => (
                  <div key={key} className="flex items-center justify-between gap-2">
                    <Label htmlFor={`overlay-${key}`} className="cursor-pointer text-xs font-normal">
                      {label}
                    </Label>
                    <Checkbox
                      id={`overlay-${key}`}
                      checked={settings.chartLayout?.overlays?.[key] !== false}
                      onCheckedChange={(c) => updateChartLayout({
                        overlays: { ...settings.chartLayout?.overlays, [key]: c === true },
                      })}
                    />
                  </div>
                ))}
              </SettingsAccordionSection>
            </Accordion>
          </TabsContent>

          <TabsContent value="layout" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <Accordion type="multiple" defaultValue={['workspace-presets']} className="settings-accordion">
              <SettingsAccordionSection
                value="chart-layout-reset"
                title="Chart layout"
                hint="Clears saved indicators, chart type, timeframe, and multi-chart grid. Symbol, dock size, and bot settings are preserved."
              >
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="destructive" size="sm" className="gap-1.5 text-xs">
                      <RotateCcw aria-hidden data-icon="inline-start" />
                      Reset chart layout
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent className="sm:max-w-md">
                    <AlertDialogHeader>
                      <AlertDialogTitle>Reset chart layout?</AlertDialogTitle>
                      <AlertDialogDescription>
                        Restores default indicators, timeframe (1m), chart type (candle),
                        and multi-chart grid layout.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        onClick={handleResetLayout}
                      >
                        Reset layout
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="workspace-presets"
                title="Workspace presets"
                hint="Save dock layout, sidebar width, view mode, and chart link mode."
                badge={settings.workspacePresets.length > 0 ? (
                  <Badge variant="secondary" className="shrink-0 text-xs">
                    {settings.workspacePresets.length}
                  </Badge>
                ) : null}
              >
              <div className="flex gap-2">
                <Input
                  className="h-8 text-xs"
                  placeholder="Preset name"
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  className="shrink-0 text-xs"
                  onClick={() => {
                    const id = saveWorkspacePreset(presetName.trim() || undefined);
                    setPresetName('');
                    toast.success('Workspace preset saved', { description: id });
                  }}
                >
                  Save
                </Button>
              </div>
              {settings.workspacePresets.length > 0 ? (
                <ul className="mt-2 flex flex-col gap-1">
                  {settings.workspacePresets.map((p) => (
                    <li key={p.id} className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-2 py-1.5 text-xs">
                      <span className="truncate font-medium">{p.name}</span>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            if (loadWorkspacePreset(p.id)) {
                              toast.success(`Loaded “${p.name}”`);
                              onOpenChange(false);
                            }
                          }}
                        >
                          Load
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-trading-down"
                          onClick={() => {
                            deleteWorkspacePreset(p.id);
                            toast.message(`Deleted “${p.name}”`);
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="settings-section__hint mt-2">No presets yet.</p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  onClick={() => {
                    const blob = new Blob([JSON.stringify(settings, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `terminal-workspace-${new Date().toISOString().slice(0, 10)}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                    toast.success('Workspace exported');
                  }}
                >
                  Export JSON
                </Button>
                <label className="cursor-pointer">
                  <Button variant="outline" size="sm" className="text-xs" asChild>
                    <span>Import JSON</span>
                  </Button>
                  <input
                    type="file"
                    accept="application/json"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      const reader = new FileReader();
                      reader.onload = () => {
                        try {
                          const parsed = JSON.parse(reader.result);
                          updateSettings(parsed);
                          toast.success('Workspace imported');
                        } catch {
                          toast.error('Invalid JSON file');
                        }
                      };
                      reader.readAsText(file);
                      e.target.value = '';
                    }}
                  />
                </label>
              </div>
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="alerts"
                title="Price & signal alerts"
                hint="Toast notifications when price crosses a level or analyst signal matches."
                badge={(settings.alerts || []).length > 0 ? (
                  <Badge variant="secondary" className="shrink-0 text-xs">
                    {(settings.alerts || []).length}
                  </Badge>
                ) : null}
              >
              <div className="mt-2 flex flex-col gap-2 rounded-md border border-border/50 p-3">
                <div className="grid grid-cols-2 gap-2">
                  <div className="col-span-2 sm:col-span-1">
                    <Label className="text-xs text-muted-foreground">Symbol</Label>
                    <Input
                      className="mt-1 h-8 text-xs"
                      value={alertDraft.symbol}
                      onChange={(e) => setAlertDraft((d) => ({ ...d, symbol: e.target.value.toUpperCase() }))}
                      placeholder="BTCUSDT"
                    />
                  </div>
                  <div className="col-span-2 sm:col-span-1">
                    <Label className="text-xs text-muted-foreground">Type</Label>
                    <Select
                      value={alertDraft.type}
                      onValueChange={(type) => setAlertDraft((d) => ({ ...d, type }))}
                    >
                      <SelectTrigger className="mt-1 h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="price_above" className="text-xs">Price above</SelectItem>
                        <SelectItem value="price_below" className="text-xs">Price below</SelectItem>
                        <SelectItem value="signal_change" className="text-xs">Signal change</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {(alertDraft.type === 'price_above' || alertDraft.type === 'price_below') && (
                    <div className="col-span-2 sm:col-span-1">
                      <Label className="text-xs text-muted-foreground">Threshold</Label>
                      <Input
                        className="mt-1 h-8 text-xs num-mono"
                        type="number"
                        step="any"
                        value={alertDraft.threshold}
                        onChange={(e) => setAlertDraft((d) => ({ ...d, threshold: e.target.value }))}
                        placeholder="0.00"
                      />
                    </div>
                  )}
                  {alertDraft.type === 'signal_change' && (
                    <div className="col-span-2 sm:col-span-1">
                      <Label className="text-xs text-muted-foreground">Signal</Label>
                      <Select
                        value={alertDraft.signal}
                        onValueChange={(signal) => setAlertDraft((d) => ({ ...d, signal }))}
                      >
                        <SelectTrigger className="mt-1 h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="BUY" className="text-xs">BUY</SelectItem>
                          <SelectItem value="SELL" className="text-xs">SELL</SelectItem>
                          <SelectItem value="NONE" className="text-xs">NONE</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs"
                    onClick={() => {
                      const sym = (alertDraft.symbol || activeSymbol || '').trim().toUpperCase();
                      if (!sym) {
                        toast.error('Enter a symbol');
                        return;
                      }
                      const needsThreshold = alertDraft.type === 'price_above' || alertDraft.type === 'price_below';
                      const threshold = alertDraft.threshold === '' ? undefined : Number(alertDraft.threshold);
                      if (needsThreshold && (threshold == null || Number.isNaN(threshold))) {
                        toast.error('Enter a valid threshold');
                        return;
                      }
                      const rule = {
                        id: editingAlertId || `alert-${Date.now()}`,
                        symbol: sym,
                        type: alertDraft.type,
                        enabled: true,
                        ...(needsThreshold ? { threshold } : {}),
                        ...(alertDraft.type === 'signal_change' ? { signal: alertDraft.signal || 'BUY' } : {}),
                      };
                      const existing = settings.alerts || [];
                      if (editingAlertId) {
                        setAlerts(existing.map((a) => (a.id === editingAlertId ? rule : a)));
                        toast.success('Alert updated');
                      } else {
                        setAlerts([...existing, rule]);
                        toast.success(`Alert added for ${sym}`);
                      }
                      setEditingAlertId(null);
                      setAlertDraft({
                        symbol: activeSymbol,
                        type: 'price_above',
                        threshold: '',
                        signal: 'BUY',
                      });
                    }}
                  >
                    {editingAlertId ? 'Update alert' : 'Add alert'}
                  </Button>
                  {editingAlertId && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs"
                      onClick={() => {
                        setEditingAlertId(null);
                        setAlertDraft({
                          symbol: activeSymbol,
                          type: 'price_above',
                          threshold: '',
                          signal: 'BUY',
                        });
                      }}
                    >
                      Cancel edit
                    </Button>
                  )}
                </div>
              </div>
              {(settings.alerts || []).length > 0 && (
                <ul className="mt-2 flex flex-col gap-1">
                  {settings.alerts.map((a) => (
                    <li key={a.id} className="flex items-center justify-between rounded border border-border/50 px-2 py-1 text-xs">
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate text-left hover:text-foreground"
                        onClick={() => {
                          setEditingAlertId(a.id);
                          setAlertDraft({
                            symbol: a.symbol,
                            type: a.type,
                            threshold: a.threshold != null ? String(a.threshold) : '',
                            signal: a.signal || 'BUY',
                          });
                        }}
                      >
                        {a.symbol} · {a.type.replace('_', ' ')}
                        {a.threshold != null ? ` ${a.threshold}` : ''}
                        {a.signal ? ` → ${a.signal}` : ''}
                        {a.enabled === false ? ' (off)' : ''}
                      </button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-trading-down shrink-0"
                        onClick={() => setAlerts(settings.alerts.filter((x) => x.id !== a.id))}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              </SettingsAccordionSection>

              <SettingsAccordionSection value="display-density" title="Display density">
                <ToggleGroup
                  type="single"
                  value={settings.workspace?.density ?? 'compact'}
                  onValueChange={(v) => v && updateWorkspace({ density: v })}
                  className="w-full"
                >
                  <ToggleGroupItem value="compact" className="flex-1 text-xs">Compact</ToggleGroupItem>
                  <ToggleGroupItem value="comfortable" className="flex-1 text-xs">Comfortable</ToggleGroupItem>
                </ToggleGroup>
              </SettingsAccordionSection>

              <SettingsAccordionSection value="onboarding" title="Onboarding">
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs"
                  onClick={() => {
                    setOnboardingCompleted(false);
                    toast.message('Tour will show on next page load — refresh if needed');
                  }}
                >
                  Replay welcome tour
                </Button>
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="chart-linking"
                title="Multi-chart linking"
                hint="Assign link groups A, B, or C per chart pane. Watchlist updates panes sharing the focused pane's group."
              >
                <ToggleGroup
                  type="single"
                  value={settings.workspace?.chartLinkMode ?? 'all'}
                  onValueChange={(v) => v && updateWorkspace({ chartLinkMode: v })}
                  className="w-full"
                >
                  <ToggleGroupItem value="all" className="flex-1 text-xs">All in group A</ToggleGroupItem>
                  <ToggleGroupItem value="focused" className="flex-1 text-xs">Focused pane only</ToggleGroupItem>
                </ToggleGroup>
              </SettingsAccordionSection>

              <SettingsAccordionSection value="saved-layout" title="Saved layout">
                <dl className="settings-defaults-list num-mono text-xs">
                  <div><dt>Link mode</dt><dd>{settings.workspace?.chartLinkMode ?? 'all'}</dd></div>
                  <div><dt>Dock height</dt><dd>{settings.workspace?.dockHeight ?? '—'}px</dd></div>
                  <div><dt>Sidebar</dt><dd>{settings.workspace?.sidebarWidth ?? '—'}px</dd></div>
                  <div><dt>Timeframe</dt><dd>{settings.chartLayout.timeframe}</dd></div>
                  <div><dt>Chart type</dt><dd>{settings.chartLayout.chartType}</dd></div>
                  <div><dt>Multi layout</dt><dd>{settings.chartLayout.multiChartLayoutId}</dd></div>
                </dl>
              </SettingsAccordionSection>
            </Accordion>
          </TabsContent>

          <TabsContent value="system" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <Accordion type="multiple" defaultValue={['terminal-status', 'memory-observability', 'llm-narrator']} className="settings-accordion">
              <SettingsAccordionSection
                value="terminal-status"
                title="Terminal status"
                badge={(
                  <Badge
                    variant="outline"
                    className={cn(
                      'shrink-0 text-xs',
                      connected ? 'text-trading-up' : 'text-trading-down',
                    )}
                  >
                    {connected ? (isLive ? 'Live' : 'Simulated') : apiStatus}
                  </Badge>
                )}
              >
                <dl className="settings-defaults-list num-mono text-xs">
                  <div>
                    <dt className="flex items-center gap-1"><Wifi aria-hidden data-icon="inline-start" /> Feed</dt>
                    <dd className={cn(
                      connected ? 'text-trading-up' : apiStatus === 'ready' ? 'text-trading-accent' : 'text-trading-down',
                    )}>
                      {connected ? (isLive ? 'Live' : 'Simulated') : apiStatus}
                    </dd>
                  </div>
                  <div><dt>Mode</dt><dd>{terminalMode}</dd></div>
                  <div><dt>Distributed</dt><dd>{distributed ? 'Yes' : 'No'}</dd></div>
                  <div><dt>Bots</dt><dd>{isBotRunning ? 'Running' : 'Idle'}</dd></div>
                  {obsHealth?.metrics && (
                    <>
                      <div><dt>Open positions</dt><dd>{obsHealth.metrics.open_positions ?? '—'}</dd></div>
                      <div><dt>Pending orders</dt><dd>{obsHealth.metrics.pending_orders ?? '—'}</dd></div>
                      <div><dt>Ambiguous</dt><dd>{obsHealth.metrics.ambiguous_orders ?? '—'}</dd></div>
                    </>
                  )}
                  {obsHealth?.worker && (
                    <div>
                      <dt>Worker</dt>
                      <dd className={obsHealth.worker.alive ? 'text-trading-up' : 'text-trading-down'}>
                        {obsHealth.worker.alive ? 'Alive' : 'Down'}
                        {obsHealth.worker.heartbeat_age_sec != null && (
                          <> ({obsHealth.worker.heartbeat_age_sec}s)</>
                        )}
                      </dd>
                    </div>
                  )}
                  {obsHealth?.ib && (
                    <>
                      <div><dt>IB connected</dt><dd>{obsHealth.ib.connected ? 'yes' : 'no'}</dd></div>
                      <div><dt>IB streams</dt><dd>{obsHealth.ib.streams_active ?? '—'}</dd></div>
                      {obsHealth.ib.market_data_delayed && (
                        <div><dt>IB data</dt><dd className="text-trading-warn">Delayed quotes</dd></div>
                      )}
                    </>
                  )}
                  {obsHealth?.massive && (
                    <>
                      <div><dt>Massive WS</dt><dd>{obsHealth.massive.connected ? 'yes' : 'no'}</dd></div>
                      <div><dt>Stocks mode</dt><dd>{obsHealth.massive.stocks_mode ?? '—'}</dd></div>
                      <div><dt>Crypto mode</dt><dd>{obsHealth.massive.crypto_mode ?? '—'}</dd></div>
                      <div><dt>NBBO quotes</dt><dd>{obsHealth.massive.real_quote_symbols ?? 0} symbols</dd></div>
                      {obsHealth.massive.poll_fallback && (
                        <div><dt>Massive feed</dt><dd className="text-trading-warn">REST poll fallback</dd></div>
                      )}
                      {obsHealth.massive.last_error && (
                        <div><dt>Massive error</dt><dd className="text-trading-warn">{obsHealth.massive.last_error}</dd></div>
                      )}
                      {obsHealth.massive.seeded_symbols != null && (
                        <div>
                          <dt>Seeded</dt>
                          <dd>
                            {obsHealth.massive.seeded_symbols}/
                            {(obsHealth.massive.equity_symbols ?? 0) + (obsHealth.massive.crypto_symbols ?? 0)}
                          </dd>
                        </div>
                      )}
                      {obsHealth.massive.stocks_lag_sec != null && (
                        <div><dt>Stocks lag</dt><dd>{obsHealth.massive.stocks_lag_sec}s</dd></div>
                      )}
                      {obsHealth.massive.crypto_lag_sec != null && (
                        <div><dt>Crypto lag</dt><dd>{obsHealth.massive.crypto_lag_sec}s</dd></div>
                      )}
                    </>
                  )}
                  {obsHealth?.feed_lag_sec != null && (
                    <div><dt>Feed lag</dt><dd>{obsHealth.feed_lag_sec}s</dd></div>
                  )}
                  {obsHealth?.observability?.agent_analyze_p99_sec != null && (
                    <div><dt>Analyze p99</dt><dd>{obsHealth.observability.agent_analyze_p99_sec}s</dd></div>
                  )}
                  {obsHealth?.ws_clients != null && (
                    <div><dt>WS clients</dt><dd>{obsHealth.ws_clients}</dd></div>
                  )}
                </dl>
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="memory-observability"
                title="Memory & buffers"
                hint="Browser candle cache and backend feed health — useful on 16 GB machines."
                badge={<MemoryObservabilityBadge level={memoryObs.level} />}
              >
                <MemoryObservabilityBody client={memoryObs.client} health={memoryObs.health} />
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="llm-narrator"
                title="LLM narrator"
                hint="Rules decide BUY/SELL; the LLM adds narrative only. In sim mode, Ollama is preferred when running locally."
                badge={agentLlmAvailable ? (
                  <Badge variant="outline" className="shrink-0 text-xs text-trading-up">Ready</Badge>
                ) : (
                  <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">Off</Badge>
                )}
              >
                <LlmSettingsSection
                  agentLlmEnabled={agentLlmEnabled}
                  agentLlmAvailable={agentLlmAvailable}
                  agentLlmProvider={agentLlmProvider}
                  agentLlmModel={agentLlmModel}
                  agentLlmModels={agentLlmModels}
                  selectedLlmModel={selectedLlmModel}
                  setSelectedLlmModel={setSelectedLlmModel}
                />
              </SettingsAccordionSection>

              <SettingsAccordionSection
                value="operator-env"
                title="Operator / environment"
                hint="Server-controlled, read-only. Set via environment variables on the backend."
              >
                <dl className="settings-defaults-list num-mono text-xs">
                  <div><dt>Mode (broker)</dt><dd>{isLive ? `Live · ${brokerLabel(terminalMode)}` : 'Sim'}</dd></div>
                  {terminalMode === 'LIVE_MASSIVE' && (
                    <div><dt>Paper bots</dt><dd className={allowLiveBots ? 'text-trading-up' : 'text-muted-foreground'}>{allowLiveBots ? 'Sim OMS fills' : 'Disabled'}</dd></div>
                  )}
                  <div><dt>Role</dt><dd>{terminalRole ?? '—'}</dd></div>
                  <div>
                    <dt>Live bots</dt>
                    <dd className={allowLiveBots ? 'text-trading-up' : 'text-muted-foreground'}>
                      {allowLiveBots ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div>
                    <dt>Custom strategies</dt>
                    <dd className={allowCustomStrategies ? 'text-trading-up' : 'text-muted-foreground'}>
                      {allowCustomStrategies ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div><dt>Bot min candles</dt><dd>{botMinCandles ?? '—'}</dd></div>
                  <div>
                    <dt>Tick archive</dt>
                    <dd className={archiveTicksEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
                      {archiveTicksEnabled ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div>
                    <dt>Chart vision (LLM)</dt>
                    <dd className={agentVisionEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
                      {agentVisionEnabled ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div>
                    <dt>Chart agent</dt>
                    <dd className={agentEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
                      {agentEnabled ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div>
                    <dt>Market scanner</dt>
                    <dd className={scannerEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
                      {scannerEnabled ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div><dt>Distributed</dt><dd>{distributed ? 'Yes' : 'No'}</dd></div>
                  {distributed && (
                    <div>
                      <dt>Worker</dt>
                      <dd className={
                        (obsHealth?.worker?.alive ?? workerAlive)
                          ? 'text-trading-up'
                          : (obsHealth?.worker?.alive ?? workerAlive) === false
                            ? 'text-trading-down'
                            : 'text-muted-foreground'
                      }>
                        {(() => {
                          const alive = obsHealth?.worker?.alive ?? workerAlive;
                          const age = obsHealth?.worker?.heartbeat_age_sec ?? workerHeartbeatAge;
                          if (alive == null) return 'Unknown';
                          return `${alive ? 'Alive' : 'Down'}${age != null ? ` (${age}s)` : ''}`;
                        })()}
                      </dd>
                    </div>
                  )}
                  <div><dt>Archive backend</dt><dd>{archiveBackend ?? '—'}</dd></div>
                  <div>
                    <dt>Parquet export</dt>
                    <dd className={archiveParquetEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
                      {archiveParquetEnabled ? 'Enabled' : 'Disabled'}
                    </dd>
                  </div>
                  <div>
                    <dt>Operator build</dt>
                    <dd className={IS_OPERATOR ? 'text-trading-accent' : 'text-muted-foreground'}>
                      {IS_OPERATOR ? 'Yes' : 'No'}
                    </dd>
                  </div>
                </dl>
              </SettingsAccordionSection>

              {obsMetrics && (
                <SettingsAccordionSection value="metrics-snapshot" title="Metrics snapshot">
                  <dl className="settings-defaults-list num-mono text-xs">
                    <div><dt>Orders placed</dt><dd>{obsMetrics.orders_place_total ?? 0}</dd></div>
                    <div><dt>Preview allowed</dt><dd>{obsMetrics.orders_preview_allowed_total ?? 0}</dd></div>
                    <div><dt>Preview blocked</dt><dd>{obsMetrics.orders_preview_blocked_total ?? 0}</dd></div>
                    <div><dt>Analyze p99 (s)</dt><dd>{obsMetrics.agent_analyze_p99 ?? obsHealth?.observability?.agent_analyze_p99_sec ?? '—'}</dd></div>
                    <div><dt>Bot signals</dt><dd>{obsMetrics.bot_signals_total ?? obsHealth?.observability?.bot_signals_total ?? 0}</dd></div>
                    <div><dt>Orders blocked</dt><dd>{obsMetrics.bot_orders_blocked_total ?? obsHealth?.observability?.bot_orders_blocked_total ?? 0}</dd></div>
                    {(obsMetrics.massive_bars_received_total != null || obsHealth?.observability?.massive_bars_received_total != null) && (
                      <>
                        <div><dt>Massive bars</dt><dd>{obsMetrics.massive_bars_received_total ?? obsHealth?.observability?.massive_bars_received_total ?? 0}</dd></div>
                        <div><dt>Massive trades</dt><dd>{obsMetrics.massive_trades_received_total ?? obsHealth?.observability?.massive_trades_received_total ?? 0}</dd></div>
                        <div><dt>Massive quotes</dt><dd>{obsMetrics.massive_quotes_received_total ?? obsHealth?.observability?.massive_quotes_received_total ?? 0}</dd></div>
                        <div><dt>Massive poll updates</dt><dd>{obsMetrics.massive_poll_updates_total ?? obsHealth?.observability?.massive_poll_updates_total ?? 0}</dd></div>
                      </>
                    )}
                  </dl>
                  <p className="settings-section__hint">
                    Full Prometheus scrape at <code className="text-xs">/metrics</code>
                  </p>
                </SettingsAccordionSection>
              )}

              {IS_OPERATOR && (
                <SettingsAccordionSection
                  value="admin-simulation"
                  title="Admin & simulation"
                  hint="Market simulation, account seeding, diagnostics, and emergency controls."
                >
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 text-xs"
                    onClick={() => {
                      onOpenChange(false);
                      onOpenAdmin?.();
                    }}
                  >
                    <ShieldAlert className="text-trading-warn" aria-hidden data-icon="inline-start" />
                    Open System Control Panel
                  </Button>
                </SettingsAccordionSection>
              )}
            </Accordion>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
