/**
 * MultiChartGrid.jsx
 * Multi-asset chart grid with layout presets and independent MiniChartWidget cells.
 */
import React, { useState, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import MiniChartWidget from './MiniChartWidget';
import { subscribeChartSymbols } from '../api/bootstrap';
import { getStoreActions } from '../api/dispatch';
import { CHART_LAYOUT_RESET_EVENT, DEFAULT_TERMINAL_SETTINGS } from '../settings/defaults';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { LayoutGrid, Link2 } from 'lucide-react';
import {
  cycleLinkGroup,
  defaultLinkGroups,
  LINK_GROUP_COLORS,
  LINK_GROUPS,
  resizeLinkGroups,
} from '../lib/chartLinkGroups';

const LAYOUTS = [
  {
    id: '1x1',
    label: '1×1',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor" className="shrink-0">
        <rect x="1" y="1" width="20" height="16" rx="1.5" opacity="0.9" />
      </svg>
    ),
    cols: 1, rows: 1,
    defaults: ['BTCUSDT'],
    description: 'Single chart',
  },
  {
    id: '2x1',
    label: '2×1',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor" className="shrink-0">
        <rect x="1" y="1" width="9" height="16" rx="1.5" />
        <rect x="12" y="1" width="9" height="16" rx="1.5" />
      </svg>
    ),
    cols: 2, rows: 1,
    defaults: ['BTCUSDT', 'ETHUSDT'],
    description: 'Side by side',
  },
  {
    id: '1+2',
    label: '1+2',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor" className="shrink-0">
        <rect x="1" y="1" width="12" height="16" rx="1.5" />
        <rect x="15" y="1" width="6" height="7" rx="1.5" />
        <rect x="15" y="10" width="6" height="7" rx="1.5" />
      </svg>
    ),
    cols: null, rows: null,
    defaults: ['BTCUSDT', 'ETHUSDT', 'AAPL'],
    description: 'Main + 2 right',
    custom: true,
  },
  {
    id: '2x2',
    label: '2×2',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor" className="shrink-0">
        <rect x="1" y="1" width="9" height="7" rx="1.5" />
        <rect x="12" y="1" width="9" height="7" rx="1.5" />
        <rect x="1" y="10" width="9" height="7" rx="1.5" />
        <rect x="12" y="10" width="9" height="7" rx="1.5" />
      </svg>
    ),
    cols: 2, rows: 2,
    defaults: ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA'],
    description: '4 charts',
  },
  {
    id: '3x2',
    label: '3×2',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor" className="shrink-0">
        <rect x="1" y="1" width="5.5" height="7" rx="1" />
        <rect x="8.25" y="1" width="5.5" height="7" rx="1" />
        <rect x="15.5" y="1" width="5.5" height="7" rx="1" />
        <rect x="1" y="10" width="5.5" height="7" rx="1" />
        <rect x="8.25" y="10" width="5.5" height="7" rx="1" />
        <rect x="15.5" y="10" width="5.5" height="7" rx="1" />
      </svg>
    ),
    cols: 3, rows: 2,
    defaults: ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA', 'MSFT', 'BTCUSDT'],
    description: '6 charts',
  },
];

const GRID_CLASS = {
  1: 'grid-cols-1',
  2: 'grid-cols-2',
  3: 'grid-cols-3',
};

export default function MultiChartGrid({ onSwitchToSingle }) {
  const [layoutId, setLayoutId] = useState(() => {
    try {
      const saved = localStorage.getItem('terminal_multi_chart_layout_id');
      if (saved && LAYOUTS.some(l => l.id === saved)) return saved;
    } catch (_) {}
    return '2x2';
  });

  const [linkGroups, setLinkGroups] = useState(() => {
    let savedLayoutId = '2x2';
    let chartLinkMode = 'all';
    try {
      const savedL = localStorage.getItem('terminal_multi_chart_layout_id');
      if (savedL && LAYOUTS.some(l => l.id === savedL)) savedLayoutId = savedL;
      const fromSettings = useSettingsStore.getState().settings?.workspace?.chartLinkMode;
      if (fromSettings === 'focused' || fromSettings === 'all') chartLinkMode = fromSettings;
    } catch (_) {}
    const layout = LAYOUTS.find(l => l.id === savedLayoutId) || LAYOUTS.find(l => l.id === '2x2');
    const count = layout?.defaults?.length ?? 4;
    try {
      const saved = localStorage.getItem('terminal_multi_chart_link_groups');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          return resizeLinkGroups(parsed, count, chartLinkMode);
        }
      }
    } catch (_) {}
    return defaultLinkGroups(count, chartLinkMode);
  });

  const [focusedIdx, setFocusedIdx] = useState(0);
  const [maximizedIdx, setMaximizedIdx] = useState(null);

  const [symbols, setSymbols] = useState(() => {
    let savedLayoutId = '2x2';
    try {
      const savedL = localStorage.getItem('terminal_multi_chart_layout_id');
      if (savedL && LAYOUTS.some(l => l.id === savedL)) savedLayoutId = savedL;
    } catch (_) {}

    const layout = LAYOUTS.find(l => l.id === savedLayoutId);
    const defaults = layout ? layout.defaults : ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA'];

    try {
      const saved = localStorage.getItem('terminal_multi_chart_symbols');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          const adjusted = [...defaults];
          for (let i = 0; i < Math.min(parsed.length, adjusted.length); i++) {
            if (parsed[i]) adjusted[i] = parsed[i];
          }
          return adjusted;
        }
      }
    } catch (_) {}
    return [...defaults];
  });

  const activeSymbol = useStore(state => state.activeSymbol);
  const setActiveSymbol = useStore(state => state.setActiveSymbol);
  const chartLinkMode = useSettingsStore(state => state.settings.workspace?.chartLinkMode ?? 'all');
  const updateWorkspace = useSettingsStore(state => state.updateWorkspace);
  const layout = LAYOUTS.find(l => l.id === layoutId);
  const paneCount = layout?.defaults?.length ?? 4;

  useEffect(() => {
    setLinkGroups((prev) => resizeLinkGroups(prev, paneCount, chartLinkMode));
  }, [paneCount, chartLinkMode]);

  useEffect(() => {
    if (!activeSymbol) return;
    const group = linkGroups[focusedIdx];
    if (!group) return;
    setSymbols(prev => {
      const next = [...prev];
      let changed = false;
      linkGroups.forEach((g, i) => {
        if (g === group && i < next.length && next[i] !== activeSymbol) {
          next[i] = activeSymbol;
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [activeSymbol, focusedIdx, linkGroups]);

  useEffect(() => {
    try { localStorage.setItem('terminal_multi_chart_layout_id', layoutId); } catch (_) {}
  }, [layoutId]);

  useEffect(() => {
    try { localStorage.setItem('terminal_multi_chart_symbols', JSON.stringify(symbols)); } catch (_) {}
  }, [symbols]);

  useEffect(() => {
    try { localStorage.setItem('terminal_multi_chart_link_groups', JSON.stringify(linkGroups)); } catch (_) {}
  }, [linkGroups]);

  useEffect(() => {
    const onReset = (e) => {
      const cl = e.detail?.chartLayout ?? DEFAULT_TERMINAL_SETTINGS.chartLayout;
      const newLayout = LAYOUTS.find((l) => l.id === cl.multiChartLayoutId) || LAYOUTS.find((l) => l.id === '2x2');
      if (!newLayout) return;
      setLayoutId(newLayout.id);
      setMaximizedIdx(null);
      setFocusedIdx(0);
      const next = [...newLayout.defaults];
      for (let i = 0; i < Math.min(cl.multiChartSymbols.length, next.length); i++) {
        if (cl.multiChartSymbols[i]) next[i] = cl.multiChartSymbols[i];
      }
      setSymbols(next);
      setLinkGroups(resizeLinkGroups(linkGroups, next.length, chartLinkMode));
    };
    window.addEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
    return () => window.removeEventListener(CHART_LAYOUT_RESET_EVENT, onReset);
  }, []);

  useEffect(() => {
    const unique = [...new Set(symbols.filter(Boolean))];
    if (unique.length === 0) return;
    subscribeChartSymbols(unique, getStoreActions());
  }, [symbols]);

  const handleLayoutChange = (newLayoutId) => {
    const newLayout = LAYOUTS.find(l => l.id === newLayoutId);
    if (!newLayout) return;
    setLayoutId(newLayout.id);
    setMaximizedIdx(null);
    setSymbols(prev => {
      const next = [...newLayout.defaults];
      for (let i = 0; i < Math.min(prev.length, next.length); i++) next[i] = prev[i];
      return next;
    });
    setLinkGroups(resizeLinkGroups(linkGroups, newLayout.defaults.length, chartLinkMode));
    setFocusedIdx(0);
  };

  // MEMORY #26 — under heap warn/critical, drop to ≤2 panes.
  useEffect(() => {
    const onEvt = (e) => {
      const maxPanes = e.detail?.multiChartMaxPanes;
      if (maxPanes == null) return;
      const current = LAYOUTS.find((l) => l.id === layoutId);
      if (!current || (current.defaults?.length ?? 0) <= maxPanes) return;
      const fallback = LAYOUTS.find((l) => l.id === '2x1') || LAYOUTS.find((l) => l.id === '1x1');
      if (!fallback || fallback.id === layoutId) return;
      handleLayoutChange(fallback.id);
    };
    window.addEventListener('memory-pressure', onEvt);
    return () => window.removeEventListener('memory-pressure', onEvt);
  }, [layoutId]);

  const handlePaneFocus = (idx, sym) => {
    setFocusedIdx(idx);
    const group = linkGroups[idx];
    setSymbols(prev => {
      const next = [...prev];
      if (group) {
        linkGroups.forEach((g, i) => {
          if (g === group && i < next.length) next[i] = sym;
        });
      } else if (idx < next.length) {
        next[idx] = sym;
      }
      return next;
    });
    setActiveSymbol(sym);
  };

  const handleLinkGroupChange = (idx, nextGroup) => {
    setLinkGroups(prev => {
      const next = [...prev];
      next[idx] = nextGroup;
      return next;
    });
  };

  const applyLinkPreset = (mode) => {
    updateWorkspace({ chartLinkMode: mode });
    setLinkGroups(defaultLinkGroups(paneCount, mode));
  };

  const getCellClassName = (idx) => cn(
    maximizedIdx === idx && 'multi-chart-cell--maximized',
    maximizedIdx !== null && maximizedIdx !== idx && 'multi-chart-cell--hidden',
    'transition-opacity duration-200',
  );

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && maximizedIdx !== null) {
        e.preventDefault();
        setMaximizedIdx(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [maximizedIdx]);

  const renderCell = (i, extraClassName) => {
    // When one pane is maximized, unmount the others so their ECharts + live
    // subscriptions are released. Restoring the grid remounts from shared candle buffers.
    const mounted = maximizedIdx === null || maximizedIdx === i;
    return (
      <div key={`wrap-${i}`} className={cn('multi-chart-cell', getCellClassName(i), extraClassName)}>
        {mounted ? (
          <MiniChartWidget
            key={`cell-${i}-${symbols[i] || layout.defaults[i]}`}
            defaultSymbol={symbols[i] || layout.defaults[i]}
            isFocused={focusedIdx === i}
            linkGroup={linkGroups[i] ?? null}
            onLinkGroupChange={(g) => handleLinkGroupChange(i, g)}
            onFocus={(sym) => handlePaneFocus(i, sym)}
            isMaximized={maximizedIdx === i}
            onToggleMaximize={() => setMaximizedIdx((prev) => (prev === i ? null : i))}
          />
        ) : null}
      </div>
    );
  };

  const renderCells = () => {
    const count = layout.defaults.length;

    if (layout.id === '1+2') {
      return (
        <div className="multi-chart-grid multi-chart-grid--1plus2">
          {renderCell(0, 'row-span-2')}
          {renderCell(1)}
          {renderCell(2)}
        </div>
      );
    }

    return (
      <div className={cn(
        'multi-chart-grid',
        GRID_CLASS[layout.cols],
        layout.rows === 2 && 'grid-rows-2',
        layout.rows === 1 && 'grid-rows-1',
      )}>
        {Array.from({ length: count }, (_, i) => renderCell(i))}
      </div>
    );
  };

  return (
    <div className="multi-chart-root">
      <div className="multi-chart-toolbar">
        <div className="multi-chart-toolbar__title">
          <LayoutGrid size={14} className="shrink-0 text-primary" aria-hidden />
          <span className="text-xs font-bold uppercase tracking-wide text-secondary-foreground">
            Multi-Chart View
          </span>
          <span className="hidden truncate text-[0.72rem] text-muted-foreground sm:inline">
            {layout.description} — link groups {LINK_GROUPS.join('/')}{linkGroups[focusedIdx] ? ` · pane ${focusedIdx + 1} in ${linkGroups[focusedIdx]}` : ' · focused pane unlinked'}
          </span>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {LINK_GROUPS.map((g) => (
            <span
              key={g}
              className="multi-chart-link-legend__dot"
              style={{ background: LINK_GROUP_COLORS[g] }}
              title={`Group ${g}`}
            />
          ))}
          <Button
            variant="outline"
            size="sm"
            className="h-7 shrink-0 gap-1 text-xs"
            title="All panes in group A"
            onClick={() => applyLinkPreset('all')}
          >
            <Link2 size={12} />
            All A
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 shrink-0 text-xs"
            title="Only focused pane linked (group A)"
            onClick={() => applyLinkPreset('focused')}
          >
            Focus
          </Button>
        </div>

        <div className="scroll-fade-x shrink-0">
          <ToggleGroup
            type="single"
            size="sm"
            spacing={1}
            value={layoutId}
            onValueChange={handleLayoutChange}
            className="scroll-panel-x no-scrollbar shrink-0 rounded-md bg-muted/40 p-0.5"
          >
            {LAYOUTS.map(l => (
              <ToggleGroupItem
                key={l.id}
                value={l.id}
                title={l.description}
                className="gap-1 px-2 data-[state=on]:bg-primary/20 data-[state=on]:text-primary"
              >
                {l.icon}
                <span className="text-[0.68rem] font-semibold">{l.label}</span>
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>

        <Button variant="outline" size="sm" className="shrink-0 text-xs" onClick={onSwitchToSingle}>
          ← Single Chart
        </Button>
      </div>

      {renderCells()}
    </div>
  );
}
