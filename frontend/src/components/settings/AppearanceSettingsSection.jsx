import React from 'react';
import { toast } from 'sonner';
import { useTheme } from 'next-themes';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion';
import { Badge } from '@/components/ui/badge';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Moon, Sun, Monitor, RotateCcw } from 'lucide-react';

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
          value={value?.startsWith('#') ? value : '#2563eb'}
          onChange={(e) => {
            onCustomize?.();
            onChange(e.target.value);
          }}
          className="settings-color-input"
          aria-label={`${label} color picker`}
        />
        <input
          type="text"
          value={value ?? ''}
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

export function SettingsAccordionSection({ value, title, hint, badge, children }) {
  return (
    <AccordionItem value={value} className="settings-accordion__item">
      <AccordionTrigger className="settings-accordion__trigger">
        <div className="flex min-w-0 flex-1 items-center justify-between gap-2 pr-1">
          <span className="settings-accordion__title">{title}</span>
          {badge}
        </div>
      </AccordionTrigger>
      <AccordionContent className="settings-accordion__content">
        <div className="settings-accordion__inner">
          {hint ? <p className="settings-section__hint m-0">{hint}</p> : null}
          {children}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

export default function AppearanceSettingsSection() {
  const { systemTheme: osTheme } = useTheme();
  const settings = useSettingsStore((s) => s.settings);
  const resolvedTheme = useSettingsStore((s) => s.resolvedTheme);
  const updateSettings = useSettingsStore((s) => s.updateSettings);
  const setThemeMode = useSettingsStore((s) => s.setThemeMode);
  const resetAppearance = useSettingsStore((s) => s.resetAppearance);

  const resolvedLabel = settings.theme === 'system'
    ? `System → ${resolvedTheme}`
    : settings.theme;

  return (
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
        <div className="settings-color-grid">
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
        </div>
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
  );
}

export { ColorField, PRESET_SWATCHES };
