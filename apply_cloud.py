import sys

def modify():
    path = 'frontend/src/components/WorkspaceSwitcher.jsx'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    hooks_str = """
  const cloudWorkspaces = useSettingsStore((s) => s.cloudWorkspaces);
  const cloudWorkspacesLoading = useSettingsStore((s) => s.cloudWorkspacesLoading);
  const fetchCloudWorkspaces = useSettingsStore((s) => s.fetchCloudWorkspaces);
  const saveCloudWorkspace = useSettingsStore((s) => s.saveCloudWorkspace);
  const deleteCloudWorkspace = useSettingsStore((s) => s.deleteCloudWorkspace);
  const loadCloudWorkspace = useSettingsStore((s) => s.loadCloudWorkspace);

  import { useEffect } from 'react';
  useEffect(() => {
    fetchCloudWorkspaces();
  }, [fetchCloudWorkspaces]);
"""
    if 'cloudWorkspaces =' not in content:
        content = content.replace('  const deleteWorkspacePreset = useSettingsStore((s) => s.deleteWorkspacePreset);', '  const deleteWorkspacePreset = useSettingsStore((s) => s.deleteWorkspacePreset);' + hooks_str)
        content = content.replace("import { useState } from 'react';", "import { useState, useEffect } from 'react';")
        content = content.replace("  import { useEffect } from 'react';", "") 

    handle_cloud_str = """
  const handleApplyCloudPreset = (p) => {
    loadCloudWorkspace(p.id);
    if (p.state?.workspace?.layoutMode) {
      onLayoutModeChange(p.state.workspace.layoutMode);
    }
    setOpen(false);
  };
"""
    if 'handleApplyCloudPreset' not in content:
        content = content.replace('  const handleApplyPreset = (p) => {', handle_cloud_str + '  const handleApplyPreset = (p) => {')

    cloud_ui = """
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
"""
    if 'Cloud Workspaces' not in content:
        content = content.replace('      </PopoverContent>', cloud_ui + '      </PopoverContent>')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    modify()
