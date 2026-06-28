import sys

path = 'frontend/src/components/ResizableWatchlistSidebar.jsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

replacement = """
  const workspaceWidth = useSettingsStore(s => s.settings.workspace?.sidebarWidth);
  const workspaceCollapsed = useSettingsStore(s => s.settings.workspace?.rightPanelCollapsed);
  const updateWorkspace = useSettingsStore(s => s.updateWorkspace);

  const [width, setWidth] = useState(() => {
    if (workspaceWidth >= SIDEBAR_MIN && workspaceWidth <= SIDEBAR_MAX) return workspaceWidth;
    return readWidth();
  });
  const [collapsed, setCollapsed] = useState(() => {
    if (workspaceCollapsed !== undefined) return workspaceCollapsed;
    return readCollapsed();
  });

  useEffect(() => {
    if (workspaceCollapsed !== undefined && workspaceCollapsed !== collapsed) {
      setCollapsed(workspaceCollapsed);
    }
  }, [workspaceCollapsed]);

  useEffect(() => {
    if (workspaceWidth !== undefined && workspaceWidth !== width) {
      setWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, workspaceWidth)));
    }
  }, [workspaceWidth]);
"""

content = content.replace(
    "  const workspaceWidth = useSettingsStore(s => s.settings.workspace?.sidebarWidth);\n  const [width, setWidth] = useState(() => {\n    if (workspaceWidth >= SIDEBAR_MIN && workspaceWidth <= SIDEBAR_MAX) return workspaceWidth;\n    return readWidth();\n  });\n  const [collapsed, setCollapsed] = useState(readCollapsed);",
    replacement.strip()
)

content = content.replace(
    "    const onToggle = () => setCollapsed(c => !c);\n    const onExpand = () => setCollapsed(false);",
    "    const onToggle = () => { setCollapsed(c => { updateWorkspace({ rightPanelCollapsed: !c }); return !c; }); };\n    const onExpand = () => { setCollapsed(false); updateWorkspace({ rightPanelCollapsed: false }); };"
)

content = content.replace(
    "const toggleCollapsed = useCallback(() => {\n    setCollapsed(c => !c);\n  }, []);",
    "const toggleCollapsed = useCallback(() => {\n    setCollapsed(c => { const next = !c; updateWorkspace({ rightPanelCollapsed: next }); return next; });\n  }, [updateWorkspace]);"
)

onup_replacement = """
    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      
      // Save new width to workspace settings
      setWidth(currentWidth => {
        updateWorkspace({ sidebarWidth: currentWidth });
        return currentWidth;
      });
    };
"""
content = content.replace(
    """    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };""",
    onup_replacement.strip()
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
