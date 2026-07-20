/**
 * ResizableWatchlistSidebar — legacy left rail (pre-FlexLayout).
 * Unmounted: WatchlistWidget lives in WorkspaceGrid. sidebar-toggle/expand
 * events are handled by WorkspaceGrid → focusFlexLayoutComponent('watchlist').
 */
import React, { useState, useRef, useEffect, useCallback } from 'react';
import { PanelLeftClose, PanelLeftOpen, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import WatchlistWidget from './WatchlistWidget';

const STORAGE_WIDTH = 'terminal_sidebar_width';
const STORAGE_COLLAPSED = 'terminal_sidebar_collapsed';

export const SIDEBAR_DEFAULT = 320;
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 420;
const SIDEBAR_COLLAPSED_W = 36;

function readWidth() {
  try {
    const n = parseInt(localStorage.getItem(STORAGE_WIDTH), 10);
    if (!Number.isNaN(n)) return Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, n));
  } catch (_) {}
  return SIDEBAR_DEFAULT;
}

function readCollapsed() {
  try {
    return localStorage.getItem(STORAGE_COLLAPSED) === '1';
  } catch (_) {
    return false;
  }
}

export default function ResizableWatchlistSidebar({ onLayoutChange }) {
  const activeSymbol = useStore(s => s.activeSymbol);
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

  // Apply workspace preset / settings changes without echoing layout callbacks back to the store.
  useEffect(() => {
    if (workspaceCollapsed === undefined || workspaceCollapsed === collapsed) return;
    setCollapsed(workspaceCollapsed);
  }, [workspaceCollapsed, collapsed]);

  useEffect(() => {
    if (workspaceWidth == null || workspaceWidth < SIDEBAR_MIN || workspaceWidth > SIDEBAR_MAX) return;
    setWidth((prev) => (prev === workspaceWidth ? prev : workspaceWidth));
  }, [workspaceWidth]);

  useEffect(() => {
    const onWorkspaceLoaded = (e) => {
      const ws = e.detail?.workspace;
      if (ws?.rightPanelCollapsed !== undefined) {
        setCollapsed(ws.rightPanelCollapsed);
      }
      if (ws?.sidebarWidth >= SIDEBAR_MIN && ws?.sidebarWidth <= SIDEBAR_MAX) {
        setWidth(ws.sidebarWidth);
      }
    };
    window.addEventListener('terminal:workspace-loaded', onWorkspaceLoaded);
    return () => window.removeEventListener('terminal:workspace-loaded', onWorkspaceLoaded);
  }, []);
  const [dragging, setDragging] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const effectiveWidth = collapsed ? SIDEBAR_COLLAPSED_W : width;

  useEffect(() => {
    onLayoutChange?.({ width: effectiveWidth, collapsed });
  }, [effectiveWidth, collapsed, onLayoutChange]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_WIDTH, String(width)); } catch (_) {}
  }, [width]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_COLLAPSED, collapsed ? '1' : '0'); } catch (_) {}
  }, [collapsed]);

  useEffect(() => {
    const onToggle = () => {
      setCollapsed(c => {
        const next = !c;
        updateWorkspace({ rightPanelCollapsed: next });
        return next;
      });
    };
    const onExpand = () => {
      setCollapsed(false);
      updateWorkspace({ rightPanelCollapsed: false });
    };
    window.addEventListener('sidebar-toggle', onToggle);
    window.addEventListener('sidebar-expand', onExpand);
    return () => {
      window.removeEventListener('sidebar-toggle', onToggle);
      window.removeEventListener('sidebar-expand', onExpand);
    };
  }, [updateWorkspace]);

  const onResizeMouseDown = useCallback((e) => {
    if (collapsed) return;
    e.preventDefault();
    isDragging.current = true;
    setDragging(true);
    startX.current = e.clientX;
    startW.current = width;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [collapsed, width]);

  useEffect(() => {
    const onMove = (e) => {
      if (!isDragging.current) return;
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startW.current + (e.clientX - startX.current)));
      setWidth(next);
    };
    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';

      setWidth((currentWidth) => {
        updateWorkspace({ sidebarWidth: currentWidth });
        return currentWidth;
      });
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [updateWorkspace]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed(c => { const next = !c; updateWorkspace({ rightPanelCollapsed: next }); return next; });
  }, [updateWorkspace]);

  const shortSym = activeSymbol?.replace('USDT', '') ?? '—';

  return (
    <aside
      className={cn('watchlist-sidebar', collapsed && 'watchlist-sidebar--collapsed')}
      data-collapsed={collapsed ? '' : undefined}
      data-tour="watchlist"
    >
      {!collapsed ? (
        <WatchlistWidget />
      ) : (
        <div
          className="watchlist-collapse-rail"
          onClick={toggleCollapsed}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              toggleCollapsed();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label="Expand watchlist"
        >
          <Button
            variant="ghost"
            size="icon-sm"
            className="watchlist-rail-btn"
            onClick={(e) => {
              e.stopPropagation();
              toggleCollapsed();
            }}
            aria-label="Expand watchlist"
            title="Expand watchlist"
          >
            <PanelLeftOpen aria-hidden />
          </Button>
          <div className="watchlist-rail-label" title={activeSymbol}>
            <Activity size={12} className="shrink-0 opacity-60" aria-hidden />
            <span className="watchlist-rail-symbol">{shortSym}</span>
          </div>
        </div>
      )}

      <div
        className={cn('sidebar-resize-handle', dragging && 'dragging', collapsed && 'sidebar-resize-handle--collapsed')}
        onMouseDown={onResizeMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-label={collapsed ? 'Expand watchlist' : 'Resize watchlist'}
      >
        <button
          type="button"
          className={cn('sidebar-edge-toggle', collapsed && 'sidebar-edge-toggle--visible')}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            toggleCollapsed();
          }}
          aria-label={collapsed ? 'Expand watchlist' : 'Collapse watchlist'}
          title={collapsed ? 'Expand watchlist' : 'Collapse watchlist'}
        >
          {collapsed ? <PanelLeftOpen size={12} aria-hidden /> : <PanelLeftClose size={12} aria-hidden />}
        </button>
      </div>
    </aside>
  );
}

export { SIDEBAR_COLLAPSED_W, SIDEBAR_MIN, SIDEBAR_MAX };
