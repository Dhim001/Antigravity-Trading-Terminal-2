import React, { useState } from 'react';
import { toast } from 'sonner';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Accordion } from '@/components/ui/accordion';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { RotateCcw } from 'lucide-react';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { SettingsAccordionSection } from './AppearanceSettingsSection';
import { normalizeWatchlistColumns } from '../../settings/watchlistColumns';
import {
  BUILTIN_WATCHLIST_COLUMN_PRESETS,
  buildCustomWatchlistPreset,
  resolveWatchlistColumnPresetId,
} from '../../settings/watchlistColumnPresets';

function WatchlistPresetSaveRow({ settings, updateSettings, updateWorkspace }) {
  const [name, setName] = useState('');

  const savePreset = () => {
    const preset = buildCustomWatchlistPreset(name, settings.workspace?.watchlistColumns);
    const next = [preset, ...(settings.watchlistColumnPresets ?? [])].slice(0, 8);
    updateSettings({ watchlistColumnPresets: next });
    updateWorkspace({ watchlistColumnPresetId: preset.id });
    setName('');
    toast.success(`Saved column preset “${preset.name}”`);
  };

  const deleteCustom = (id) => {
    updateSettings({
      watchlistColumnPresets: (settings.watchlistColumnPresets ?? []).filter((p) => p.id !== id),
    });
    if (settings.workspace?.watchlistColumnPresetId === id) {
      updateWorkspace({ watchlistColumnPresetId: 'custom' });
    }
  };

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <Label className="text-xs text-muted-foreground">Save current layout</Label>
      <div className="flex gap-2">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Preset name…"
          className="h-8 flex-1 text-xs"
        />
        <Button type="button" variant="outline" size="sm" className="text-xs" onClick={savePreset}>
          Save
        </Button>
      </div>
      {(settings.watchlistColumnPresets ?? []).length > 0 && (
        <div className="flex flex-col gap-1">
          {(settings.watchlistColumnPresets ?? []).map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">{p.name}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-[0.65rem] text-destructive"
                onClick={() => deleteCustom(p.id)}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function WorkspaceSettingsSection({ onOpenChange }) {
  const settings = useSettingsStore((s) => s.settings);
  const updateSettings = useSettingsStore((s) => s.updateSettings);
  const resetChartLayout = useSettingsStore((s) => s.resetChartLayout);
  const updateWorkspace = useSettingsStore((s) => s.updateWorkspace);
  const saveWorkspacePreset = useSettingsStore((s) => s.saveWorkspacePreset);
  const loadWorkspacePreset = useSettingsStore((s) => s.loadWorkspacePreset);
  const deleteWorkspacePreset = useSettingsStore((s) => s.deleteWorkspacePreset);
  const setOnboardingCompleted = useSettingsStore((s) => s.setOnboardingCompleted);

  const [presetName, setPresetName] = useState('');

  const handleResetLayout = () => {
    resetChartLayout();
    toast.success('Chart layout reset', {
      description: 'Indicators, timeframe, and multi-chart layout restored to defaults.',
    });
    onOpenChange?.(false);
  };

  return (
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
          <ul className="settings-list">
            {settings.workspacePresets.map((p) => (
              <li key={p.id} className="settings-list__item">
                <span className="settings-list__label truncate font-medium">{p.name}</span>
                <div className="flex shrink-0 gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      if (loadWorkspacePreset(p.id)) {
                        toast.success(`Loaded “${p.name}”`);
                        onOpenChange?.(false);
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

      <SettingsAccordionSection
        value="left-panel"
        title="Left panel"
        hint="Default left-rail tab: your watchlist or Alpaca gainers/losers/actives with leading headlines."
      >
        <ToggleGroup
          type="single"
          value={settings.workspace?.leftPanelTab === 'movers' ? 'movers' : 'watchlist'}
          onValueChange={(v) => {
            if (v !== 'watchlist' && v !== 'movers') return;
            updateWorkspace({ leftPanelTab: v });
            window.dispatchEvent(new CustomEvent('left-panel-tab', { detail: v }));
          }}
          className="w-full"
        >
          <ToggleGroupItem value="watchlist" className="flex-1 text-xs">Watchlist</ToggleGroupItem>
          <ToggleGroupItem value="movers" className="flex-1 text-xs">Movers</ToggleGroupItem>
        </ToggleGroup>
      </SettingsAccordionSection>

      <SettingsAccordionSection
        value="watchlist-columns"
        title="Watchlist columns"
        hint="Presets, optional columns, and asset-class sections on the All tab."
      >
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">Column presets</Label>
            <div className="flex flex-wrap gap-1">
              {[...BUILTIN_WATCHLIST_COLUMN_PRESETS, ...(settings.watchlistColumnPresets ?? [])].map((preset) => (
                <Button
                  key={preset.id}
                  type="button"
                  variant={
                    (settings.workspace?.watchlistColumnPresetId
                      ?? resolveWatchlistColumnPresetId(settings.workspace?.watchlistColumns, settings.watchlistColumnPresets))
                    === preset.id
                      ? 'secondary'
                      : 'outline'
                  }
                  size="sm"
                  className="h-7 text-xs"
                  title={preset.description}
                  onClick={() => updateWorkspace({
                    watchlistColumns: normalizeWatchlistColumns(preset.columns),
                    watchlistColumnPresetId: preset.id,
                  })}
                >
                  {preset.name}
                </Button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Checkbox
              id="settings-wl-sections"
              checked={settings.workspace?.watchlistSections !== false}
              onCheckedChange={(v) => updateWorkspace({ watchlistSections: v === true })}
            />
            <Label htmlFor="settings-wl-sections" className="text-xs font-normal">
              Group by asset class on All tab
            </Label>
          </div>

          <div className="flex flex-col gap-2">
            {[
              ['change_abs', 'Change ($)'],
              ['change_24h', 'Change (%)'],
              ['volume_24h', 'Volume (24h)'],
              ['avg_volume', 'Avg 1m volume'],
            ].map(([key, label]) => {
              const cols = settings.workspace?.watchlistColumns ?? {};
              const checked = cols[key] !== false;
              return (
                <div key={key} className="flex items-center gap-2">
                  <Checkbox
                    id={`settings-wl-${key}`}
                    checked={checked}
                    onCheckedChange={(v) => {
                      const next = normalizeWatchlistColumns({
                        ...(settings.workspace?.watchlistColumns || {}),
                        [key]: v === true,
                      });
                      updateWorkspace({
                        watchlistColumns: next,
                        watchlistColumnPresetId: resolveWatchlistColumnPresetId(
                          next,
                          settings.watchlistColumnPresets,
                        ),
                      });
                    }}
                  />
                  <Label htmlFor={`settings-wl-${key}`} className="text-xs font-normal">
                    {label}
                  </Label>
                </div>
              );
            })}
          </div>

          <WatchlistPresetSaveRow
            settings={settings}
            updateSettings={updateSettings}
            updateWorkspace={updateWorkspace}
          />
        </div>
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
    </Accordion>
  );
}
