import React, { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useTheme } from 'next-themes';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
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
import { Separator } from '@/components/ui/separator';
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
import { themeChartDefaults, getEffectiveSettings } from '../settings/themePresets';
import { fetchHealth, parseMetricsSummary } from '../api/endpoints';
import { HTTP_BASE_URL } from '../api/config';

const PRESET_SWATCHES = {
  bullish: ['#10b981', '#22c55e', '#00d4aa', '#4ade80'],
  bearish: ['#ef4444', '#f87171', '#ff4757', '#dc2626'],
  accent: ['#2563eb', '#3b82f6', '#6366f1', '#0ea5e9'],
};

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

export default function SettingsPanel({ open, onOpenChange, onOpenAdmin }) {
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
  const distributed = useStore((s) => s.distributed);
  const isBotRunning = useStore((s) => s.isBotRunning);

  const [activeTab, setActiveTab] = React.useState(panelTab);
  const [presetName, setPresetName] = React.useState('');
  const [obsHealth, setObsHealth] = useState(null);
  const [obsMetrics, setObsMetrics] = useState(null);

  const effectiveChart = useMemo(
    () => getEffectiveSettings(settings, resolvedTheme).chart,
    [settings, resolvedTheme],
  );

  useEffect(() => {
    if (open) setActiveTab(panelTab);
  }, [open, panelTab]);

  useEffect(() => {
    if (!open || activeTab !== 'system') return;
    let cancelled = false;
    (async () => {
      try {
        const health = await fetchHealth();
        if (!cancelled) setObsHealth(health);
      } catch {
        if (!cancelled) setObsHealth(null);
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
            <section className="settings-section">
              <div className="flex items-center justify-between gap-2">
                <h3 className="settings-section__title">Color mode</h3>
                <Badge variant="outline" className="text-xs capitalize">
                  {resolvedLabel}
                </Badge>
              </div>
              <ToggleGroup
                type="single"
                value={settings.theme}
                onValueChange={(v) => v && setThemeMode(v)}
                className="w-full"
              >
                <ToggleGroupItem value="dark" className="flex-1 gap-1.5 text-xs">
                  <Moon size={13} aria-hidden />
                  Dark
                </ToggleGroupItem>
                <ToggleGroupItem value="light" className="flex-1 gap-1.5 text-xs">
                  <Sun size={13} aria-hidden />
                  Light
                </ToggleGroupItem>
                <ToggleGroupItem value="system" className="flex-1 gap-1.5 text-xs">
                  <Monitor size={13} aria-hidden />
                  System
                </ToggleGroupItem>
              </ToggleGroup>
              {settings.theme === 'system' && (
                <p className="settings-section__hint">
                  Following OS preference ({osTheme || resolvedTheme}).
                </p>
              )}
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Trading colors</h3>
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
            </section>

            <Separator />

            <div className="flex justify-end">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => {
                  resetAppearance();
                  toast.success('Appearance reset for current theme');
                }}
              >
                <RotateCcw size={13} aria-hidden />
                Reset appearance
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="chart" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <section className="settings-section">
              <div className="flex items-center justify-between gap-2">
                <h3 className="settings-section__title">Chart canvas</h3>
                <Button
                  variant={settings.syncChartToTheme !== false ? 'secondary' : 'outline'}
                  size="sm"
                  className="h-7 text-xs"
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
              <p className="settings-section__hint">
                When synced, chart background and grid update with Dark / Light / System.
              </p>
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
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Candle colors</h3>
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
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Chart overlays</h3>
              <p className="settings-section__hint">Toggle trade markers, position lines, and analyst levels on the chart.</p>
              {[
                ['trades', 'Trade markers'],
                ['positions', 'Position SL/TP'],
                ['agentLevels', 'Analyst levels'],
                ['botMarkers', 'Bot markers'],
              ].map(([key, label]) => (
                <label key={key} className="mb-2 flex items-center justify-between text-xs">
                  <span>{label}</span>
                  <input
                    type="checkbox"
                    checked={settings.chartLayout?.overlays?.[key] !== false}
                    onChange={(e) => updateChartLayout({
                      overlays: { ...settings.chartLayout?.overlays, [key]: e.target.checked },
                    })}
                  />
                </label>
              ))}
            </section>
          </TabsContent>

          <TabsContent value="layout" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <section className="settings-section">
              <h3 className="settings-section__title">Chart layout</h3>
              <p className="settings-section__hint">
                Clears saved indicators, chart type, timeframe, and multi-chart grid.
                Symbol, dock size, and bot settings are preserved.
              </p>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="destructive" size="sm" className="gap-1.5 text-xs">
                    <RotateCcw size={13} aria-hidden />
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
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Workspace presets</h3>
              <p className="settings-section__hint">
                Save dock layout, sidebar width, view mode, and chart link mode.
              </p>
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
                          size="xs"
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
                          size="xs"
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
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Price & signal alerts</h3>
              <p className="settings-section__hint">
                Toast notifications when price crosses a level or analyst signal matches.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="text-xs"
                onClick={() => {
                  const id = `alert-${Date.now()}`;
                  setAlerts([
                    ...(settings.alerts || []),
                    {
                      id,
                      symbol: activeSymbol,
                      type: 'signal_change',
                      signal: 'BUY',
                      enabled: true,
                    },
                  ]);
                  toast.success(`Alert added for ${activeSymbol} BUY signal`);
                }}
              >
                Add BUY signal alert ({activeSymbol})
              </Button>
              {(settings.alerts || []).length > 0 && (
                <ul className="mt-2 flex flex-col gap-1">
                  {settings.alerts.map((a) => (
                    <li key={a.id} className="flex items-center justify-between rounded border border-border/50 px-2 py-1 text-xs">
                      <span>{a.symbol} · {a.type}{a.threshold != null ? ` ${a.threshold}` : ''}{a.signal ? ` → ${a.signal}` : ''}</span>
                      <Button
                        variant="ghost"
                        size="xs"
                        className="text-trading-down"
                        onClick={() => setAlerts(settings.alerts.filter((x) => x.id !== a.id))}
                      >
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Display density</h3>
              <ToggleGroup
                type="single"
                value={settings.workspace?.density ?? 'compact'}
                onValueChange={(v) => v && updateWorkspace({ density: v })}
                className="w-full"
              >
                <ToggleGroupItem value="compact" className="flex-1 text-xs">Compact</ToggleGroupItem>
                <ToggleGroupItem value="comfortable" className="flex-1 text-xs">Comfortable</ToggleGroupItem>
              </ToggleGroup>
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Onboarding</h3>
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
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Multi-chart linking</h3>
              <p className="settings-section__hint">
                Assign link groups A, B, or C per chart pane. Watchlist updates panes sharing the focused pane&apos;s group.
              </p>
              <ToggleGroup
                type="single"
                value={settings.workspace?.chartLinkMode ?? 'all'}
                onValueChange={(v) => v && updateWorkspace({ chartLinkMode: v })}
                className="w-full"
              >
                <ToggleGroupItem value="all" className="flex-1 text-xs">All in group A</ToggleGroupItem>
                <ToggleGroupItem value="focused" className="flex-1 text-xs">Focused pane only</ToggleGroupItem>
              </ToggleGroup>
            </section>

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Saved layout</h3>
              <dl className="settings-defaults-list num-mono text-[0.68rem]">
                <div><dt>Link mode</dt><dd>{settings.workspace?.chartLinkMode ?? 'all'}</dd></div>
                <div><dt>Dock height</dt><dd>{settings.workspace?.dockHeight ?? '—'}px</dd></div>
                <div><dt>Sidebar</dt><dd>{settings.workspace?.sidebarWidth ?? '—'}px</dd></div>
                <div><dt>Timeframe</dt><dd>{settings.chartLayout.timeframe}</dd></div>
                <div><dt>Chart type</dt><dd>{settings.chartLayout.chartType}</dd></div>
                <div><dt>Multi layout</dt><dd>{settings.chartLayout.multiChartLayoutId}</dd></div>
              </dl>
            </section>
          </TabsContent>

          <TabsContent value="system" className="terminal-tabs__body terminal-tabs__body--scroll settings-panel__body">
            <section className="settings-section">
              <h3 className="settings-section__title">Terminal status</h3>
              <dl className="settings-defaults-list num-mono text-[0.68rem]">
                <div>
                  <dt className="flex items-center gap-1"><Wifi size={11} aria-hidden /> Feed</dt>
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
                {obsHealth?.ws_clients != null && (
                  <div><dt>WS clients</dt><dd>{obsHealth.ws_clients}</dd></div>
                )}
              </dl>
            </section>

            {obsMetrics && (
              <>
                <Separator />
                <section className="settings-section">
                  <h3 className="settings-section__title">Metrics snapshot</h3>
                  <dl className="settings-defaults-list num-mono text-[0.68rem]">
                    <div><dt>Orders placed</dt><dd>{obsMetrics.orders_place_total ?? 0}</dd></div>
                    <div><dt>Preview allowed</dt><dd>{obsMetrics.orders_preview_allowed_total ?? 0}</dd></div>
                    <div><dt>Preview blocked</dt><dd>{obsMetrics.orders_preview_blocked_total ?? 0}</dd></div>
                    <div><dt>Analyze p99 (s)</dt><dd>{obsMetrics.agent_analyze_p99 ?? '—'}</dd></div>
                  </dl>
                  <p className="settings-section__hint mt-2">
                    Full Prometheus scrape at <code className="text-[0.62rem]">/metrics</code>
                  </p>
                </section>
              </>
            )}

            <Separator />

            <section className="settings-section">
              <h3 className="settings-section__title">Admin & simulation</h3>
              <p className="settings-section__hint">
                Market simulation, account seeding, diagnostics, and emergency controls.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => {
                  onOpenChange(false);
                  onOpenAdmin?.();
                }}
              >
                <ShieldAlert size={13} className="text-trading-warn" aria-hidden />
                Open System Control Panel
              </Button>
            </section>
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
