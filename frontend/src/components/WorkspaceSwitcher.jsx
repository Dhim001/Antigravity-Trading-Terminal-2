/**
 * WorkspaceSwitcher — header preset popover (UX-6).
 */
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../store/useSettingsStore';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { LayoutTemplate, Check, Zap, TrendingUp, Brain, Landmark, Activity, Trash2, Edit2, Cloud, CloudOff } from 'lucide-react';
import { LAYOUT_MODE_CONFIG, PRESET_CATEGORIES } from '../settings/layoutModes';
import { BUILTIN_WORKSPACE_PRESETS } from '../settings/defaults';
import { cn } from '@/lib/utils';

const ICONS = {
  Zap, TrendingUp, Brain, Landmark, Activity
};

export default function WorkspaceSwitcher({ layoutMode, onLayoutModeChange }) {
  const [open, setOpen] = useState(false);
  const presets = useSettingsStore((s) => s.settings.workspacePresets);
  const loadWorkspacePreset = useSettingsStore((s) => s.loadWorkspacePreset);
  const saveWorkspacePreset = useSettingsStore((s) => s.saveWorkspacePreset);
  const deleteWorkspacePreset = useSettingsStore((s) => s.deleteWorkspacePreset);
  const cloudWorkspaces = useSettingsStore((s) => s.cloudWorkspaces);
  const cloudWorkspacesLoading = useSettingsStore((s) => s.cloudWorkspacesLoading);
  const fetchCloudWorkspaces = useSettingsStore((s) => s.fetchCloudWorkspaces);
  const saveCloudWorkspace = useSettingsStore((s) => s.saveCloudWorkspace);
  const deleteCloudWorkspace = useSettingsStore((s) => s.deleteCloudWorkspace);
  const loadCloudWorkspace = useSettingsStore((s) => s.loadCloudWorkspace);


  useEffect(() => {
    fetchCloudWorkspaces();
  }, [fetchCloudWorkspaces]);

  
  // Separate built-in and user presets
  const builtinPresets = presets.filter(p => p.id.startsWith('builtin-'));
  const userPresets = presets.filter(p => !p.id.startsWith('builtin-'));


  const handleApplyCloudPreset = (p) => {
    loadCloudWorkspace(p.id);
    if (p.state?.workspace?.layoutMode) {
      onLayoutModeChange(p.state.workspace.layoutMode);
    }
    setOpen(false);
  };
  const handleApplyPreset = (p) => {
    loadWorkspacePreset(p.id);
    if (p.workspace?.layoutMode) {
      onLayoutModeChange(p.workspace.layoutMode);
    }
    setOpen(false);
  };

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
      <PopoverContent align="center" className="workspace-switcher__menu w-64 p-2">
        <p className="px-2 py-1 text-[0.62rem] font-semibold uppercase text-muted-foreground">Professional Workflows</p>
        <div className="flex flex-col gap-0.5">
          {builtinPresets.map((p) => {
            const Icon = ICONS[p.icon] || LayoutTemplate;
            return (
              <button
                key={p.id}
                type="button"
                className="workspace-switcher__item flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/50"
                onClick={() => handleApplyPreset(p)}
              >
                <div className={cn("mt-0.5 shrink-0", PRESET_CATEGORIES[p.category]?.color)}>
                  <Icon size={14} />
                </div>
                <div className="flex flex-col">
                  <span className="text-xs font-medium text-foreground">{p.name}</span>
                  {p.description && (
                    <span className="text-[0.65rem] text-muted-foreground leading-tight mt-0.5">
                      {p.description}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
        
        <div className="my-2 h-px bg-border/50" />
        <p className="px-2 py-1 text-[0.62rem] font-semibold uppercase text-muted-foreground">My Workspaces</p>
        <div className="flex flex-col gap-0.5">
          {userPresets.slice(0, 8).map((p) => (
            <div key={p.id} className="group flex w-full items-center rounded-md hover:bg-muted/50">
              <button
                type="button"
                className="flex flex-1 items-center gap-2 px-2 py-1.5 text-left text-xs"
                onClick={() => handleApplyPreset(p)}
              >
                <LayoutTemplate size={12} className="text-muted-foreground" />
                <span className="truncate">{p.name}</span>
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                className="opacity-0 group-hover:opacity-100 h-6 w-6 shrink-0 mr-1 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  deleteWorkspacePreset(p.id);
                }}
                title="Delete preset"
              >
                <Trash2 size={12} />
              </Button>
            </div>
          ))}
          <button
            type="button"
            className="mt-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            onClick={() => {
              saveWorkspacePreset();
              setOpen(false);
            }}
          >
            <Edit2 size={12} />
            <span>Save current layout…</span>
          </button>
        </div>

        <div className="my-2 h-px bg-border/50" />
        <p className="px-2 py-1 text-[0.62rem] font-semibold uppercase text-muted-foreground flex items-center gap-1">
          <Cloud size={10} /> Cloud Workspaces
        </p>
        <div className="flex flex-col gap-0.5">
          {cloudWorkspacesLoading && <span className="text-xs text-muted-foreground px-2 py-1">Loading...</span>}
          {cloudWorkspaces.slice(0, 8).map((p) => (
            <div key={p.id} className="group flex w-full items-center rounded-md hover:bg-muted/50">
              <button
                type="button"
                className="flex flex-1 items-center gap-2 px-2 py-1.5 text-left text-xs"
                onClick={() => handleApplyCloudPreset(p)}
              >
                <Cloud size={12} className="text-blue-500" />
                <span className="truncate">{p.name}</span>
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                className="opacity-0 group-hover:opacity-100 h-6 w-6 shrink-0 mr-1 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  deleteCloudWorkspace(p.id);
                }}
                title="Delete cloud workspace"
              >
                <Trash2 size={12} />
              </Button>
            </div>
          ))}
          <button
            type="button"
            className="mt-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            onClick={() => {
              const name = prompt('Enter a name for this cloud workspace:');
              if (name) saveCloudWorkspace(name);
              setOpen(false);
            }}
          >
            <Cloud size={12} />
            <span>Save to Cloud…</span>
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
