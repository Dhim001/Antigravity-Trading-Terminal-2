import React, { useMemo } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Accordion } from '@/components/ui/accordion';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { SettingsAccordionSection, ColorField, PRESET_SWATCHES } from './AppearanceSettingsSection';
import { themeChartDefaults, getEffectiveSettings } from '../../settings/themePresets';
import { getIndicatorTheme, getIndicatorToolbarMeta } from '../../settings/indicatorThemes';
import { DEFAULT_TERMINAL_SETTINGS } from '../../settings/defaults';

const CHART_TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

export default function ChartLayoutSettingsSection() {
  const settings = useSettingsStore((s) => s.settings);
  const resolvedTheme = useSettingsStore((s) => s.resolvedTheme);
  const updateSettings = useSettingsStore((s) => s.updateSettings);
  const updateChartLayout = useSettingsStore((s) => s.updateChartLayout);

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

  const markChartCustom = () => {
    if (settings.syncChartToTheme !== false) {
      updateSettings({ syncChartToTheme: false });
    }
  };

  return (
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
        <div className="settings-color-grid">
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
        </div>
      </SettingsAccordionSection>

      <SettingsAccordionSection value="candle-colors" title="Candle colors">
        <div className="settings-color-grid">
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
        </div>
      </SettingsAccordionSection>

      <SettingsAccordionSection
        value="chart-overlays"
        title="Chart overlays"
        hint="Toggle trade markers, position lines, and analyst levels on the chart."
      >
        <div className="settings-check-grid">
          {[
            ['trades', 'Trade markers'],
            ['positions', 'Position SL/TP'],
            ['agentLevels', 'Analyst levels'],
            ['botMarkers', 'Bot markers'],
          ].map(([key, label]) => (
            <div key={key} className="settings-check-row">
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
        </div>
      </SettingsAccordionSection>
    </Accordion>
  );
}
