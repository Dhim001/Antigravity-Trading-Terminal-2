/**
 * WorkspaceSwitcher — header preset popover (UX-6).
 */
import { useState } from 'react';
import { useSettingsStore } from '../store/useSettingsStore';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { LayoutTemplate, Check } from 'lucide-react';
import { LAYOUT_MODE_CONFIG } from '../settings/layoutModes';
import { cn } from '@/lib/utils';

export default function WorkspaceSwitcher({ layoutMode, onLayoutModeChange }) {
  const [open, setOpen] = useState(false);
  const presets = useSettingsStore((s) => s.settings.workspacePresets);
  const loadWorkspacePreset = useSettingsStore((s) => s.loadWorkspacePreset);
  const saveWorkspacePreset = useSettingsStore((s) => s.saveWorkspacePreset);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="workspace-switcher"
          title="Workspace presets"
        >
          <LayoutTemplate data-icon="inline-start" />
          <span className="header-label">{LAYOUT_MODE_CONFIG[layoutMode]?.label || 'Trade'}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="center" className="workspace-switcher__menu w-56 p-2">
        <p className="px-2 py-1 text-[0.62rem] font-semibold uppercase text-muted-foreground">Layout mode</p>
        {Object.entries(LAYOUT_MODE_CONFIG).map(([id, cfg]) => (
          <button
            key={id}
            type="button"
            className="workspace-switcher__item flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/50"
            onClick={() => {
              onLayoutModeChange(id);
              setOpen(false);
            }}
          >
            <Check className={cn('size-3.5 shrink-0', layoutMode === id ? 'opacity-100' : 'opacity-0')} />
            <span className="font-medium">{cfg.label}</span>
          </button>
        ))}
        <div className="my-2 h-px bg-border/50" />
        <p className="px-2 py-1 text-[0.62rem] font-semibold uppercase text-muted-foreground">Saved</p>
        {presets.slice(0, 8).map((p) => (
          <button
            key={p.id}
            type="button"
            className="workspace-switcher__item flex w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/50"
            onClick={() => {
              loadWorkspacePreset(p.id);
              setOpen(false);
            }}
          >
            {p.name}
          </button>
        ))}
        <button
          type="button"
          className="mt-1 flex w-full rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/50"
          onClick={() => {
            saveWorkspacePreset();
            setOpen(false);
          }}
        >
          Save current layout…
        </button>
      </PopoverContent>
    </Popover>
  );
}
