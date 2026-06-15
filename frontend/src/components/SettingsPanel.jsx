import React, { useEffect, useMemo } from 'react';
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

  const { resolvedTheme: osTheme } = useTheme();

  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);
  const isLive = useStore((s) => s.isLive);
  const terminalMode = useStore((s) => s.terminalMode);
  const distributed = useStore((s) => s.distributed);
  const isBotRunning = useStore((s) => s.isBotRunning);

  const [activeTab, setActiveTab] = React.useState(panelTab);

  const effectiveChart = useMemo(
    () => getEffectiveSettings(settings, resolvedTheme).chart,
    [settings, resolvedTheme],
  );

  useEffect(() => {
    if (open) setActiveTab(panelTab);
  }, [open, panelTab]);

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
      <SheetContent side="right" className="settings-panel w-full sm:max-w-lg">
        <SheetHeader className="border-b border-border pb-4">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Palette size={16} className="text-primary" aria-hidden />
            Preferences
          </SheetTitle>
          <SheetDescription>
            Appearance, charts, layout, and system controls.
          </SheetDescription>
        </SheetHeader>

        <Tabs value={activeTab} onValueChange={handleTabChange} className="settings-panel__tabs">
          <TabsList variant="line" className="settings-panel__tablist w-full justify-start px-4">
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

          <TabsContent value="appearance" className="settings-panel__body">
            <section className="settings-section">
              <div className="flex items-center justify-between gap-2">
                <h3 className="settings-section__title">Color mode</h3>
                <Badge variant="outline" className="text-[0.62rem] capitalize">
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

          <TabsContent value="chart" className="settings-panel__body">
            <section className="settings-section">
              <div className="flex items-center justify-between gap-2">
                <h3 className="settings-section__title">Chart canvas</h3>
                <Button
                  variant={settings.syncChartToTheme !== false ? 'secondary' : 'outline'}
                  size="sm"
                  className="h-6 text-[0.62rem]"
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
          </TabsContent>

          <TabsContent value="layout" className="settings-panel__body">
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
              <h3 className="settings-section__title">Saved layout</h3>
              <dl className="settings-defaults-list num-mono text-[0.68rem]">
                <div><dt>Timeframe</dt><dd>{settings.chartLayout.timeframe}</dd></div>
                <div><dt>Chart type</dt><dd>{settings.chartLayout.chartType}</dd></div>
                <div><dt>Multi layout</dt><dd>{settings.chartLayout.multiChartLayoutId}</dd></div>
              </dl>
            </section>
          </TabsContent>

          <TabsContent value="system" className="settings-panel__body">
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
              </dl>
            </section>

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
