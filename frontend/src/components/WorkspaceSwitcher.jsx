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
      <PopoverContent align="center" className="workspace-switcher__menu w-72 p-0">
        <div className="workspace-switcher__header">
          <p className="workspace-switcher__title">Professional Workflows</p>
        </div>

        <div className="workspace-switcher__section">
          <div className="workspace-switcher__list">
            {builtinPresets.map((p) => {
              const Icon = ICONS[p.icon] || LayoutTemplate;
              return (
                <button
                  key={p.id}
                  type="button"
                  className="workspace-switcher__item"
                  onClick={() => handleApplyPreset(p)}
                >
                  <div className={cn('workspace-switcher__item-icon', PRESET_CATEGORIES[p.category]?.color)}>
                    <Icon size={14} aria-hidden />
                  </div>
                  <div className="workspace-switcher__item-body">
                    <span className="workspace-switcher__item-name">{p.name}</span>
                    {p.description && (
                      <span className="workspace-switcher__item-desc">{p.description}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="workspace-switcher__divider" />

        <div className="workspace-switcher__section">
          <p className="workspace-switcher__section-label">My Workspaces</p>
          <div className="workspace-switcher__list">
            {userPresets.slice(0, 8).map((p) => (
              <div key={p.id} className="workspace-switcher__row group">
                <button
                  type="button"
                  className="workspace-switcher__row-btn"
                  onClick={() => handleApplyPreset(p)}
                >
                  <LayoutTemplate size={12} className="workspace-switcher__row-icon" aria-hidden />
                  <span className="workspace-switcher__row-label">{p.name}</span>
                </button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="workspace-switcher__row-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteWorkspacePreset(p.id);
                  }}
                  title="Delete preset"
                >
                  <Trash2 size={12} aria-hidden />
                </Button>
              </div>
            ))}
            <button
              type="button"
              className="workspace-switcher__action"
              onClick={() => {
                saveWorkspacePreset();
                setOpen(false);
              }}
            >
              <Edit2 size={12} aria-hidden />
              <span>Save current layout…</span>
            </button>
          </div>
        </div>

        <div className="workspace-switcher__divider" />

        <div className="workspace-switcher__section workspace-switcher__section--cloud">
          <p className="workspace-switcher__section-label workspace-switcher__section-label--cloud">
            <Cloud size={10} aria-hidden />
            Cloud Workspaces
          </p>
          <div className="workspace-switcher__list">
            {cloudWorkspacesLoading && (
              <span className="workspace-switcher__loading">Loading…</span>
            )}
            {cloudWorkspaces.slice(0, 8).map((p) => (
              <div key={p.id} className="workspace-switcher__row group">
                <button
                  type="button"
                  className="workspace-switcher__row-btn"
                  onClick={() => handleApplyCloudPreset(p)}
                >
                  <Cloud size={12} className="workspace-switcher__row-icon workspace-switcher__row-icon--cloud" aria-hidden />
                  <span className="workspace-switcher__row-label">{p.name}</span>
                </button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="workspace-switcher__row-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteCloudWorkspace(p.id);
                  }}
                  title="Delete cloud workspace"
                >
                  <Trash2 size={12} aria-hidden />
                </Button>
              </div>
            ))}
            <button
              type="button"
              className="workspace-switcher__action"
              onClick={() => {
                const name = prompt('Enter a name for this cloud workspace:');
                if (name) saveCloudWorkspace(name);
                setOpen(false);
              }}
            >
              <Cloud size={12} aria-hidden />
              <span>Save to Cloud…</span>
            </button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
