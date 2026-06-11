/**
 * MultiChartGrid.jsx
 * Multi-asset chart grid with layout presets and independent MiniChartWidget cells.
 */
import React, { useState, useEffect } from 'react';
import { useStore } from '../store/useStore';
import MiniChartWidget from './MiniChartWidget';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { LayoutGrid } from 'lucide-react';

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
  const layout = LAYOUTS.find(l => l.id === layoutId);

  useEffect(() => {
    if (activeSymbol && symbols[focusedIdx] !== activeSymbol) {
      setSymbols(prev => {
        const next = [...prev];
        if (focusedIdx < next.length) next[focusedIdx] = activeSymbol;
        return next;
      });
    }
  }, [activeSymbol, focusedIdx]);

  useEffect(() => {
    try { localStorage.setItem('terminal_multi_chart_layout_id', layoutId); } catch (_) {}
  }, [layoutId]);

  useEffect(() => {
    try { localStorage.setItem('terminal_multi_chart_symbols', JSON.stringify(symbols)); } catch (_) {}
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
    setFocusedIdx(0);
  };

  const handleFocus = (idx, sym) => {
    setFocusedIdx(idx);
    setActiveSymbol(sym);
  };

  const getCellClassName = (idx) => cn(
    maximizedIdx !== null && maximizedIdx !== idx && 'pointer-events-none opacity-15',
    'transition-opacity duration-200',
  );

  const renderCell = (i, extraClassName) => (
    <MiniChartWidget
      key={`cell-${i}-${symbols[i] || layout.defaults[i]}`}
      defaultSymbol={symbols[i] || layout.defaults[i]}
      isFocused={focusedIdx === i}
      onFocus={(sym) => {
        const next = [...symbols];
        next[i] = sym;
        setSymbols(next);
        handleFocus(i, sym);
      }}
      isMaximized={maximizedIdx === i}
      onToggleMaximize={() => setMaximizedIdx(maximizedIdx === i ? null : i)}
      className={cn(getCellClassName(i), extraClassName)}
    />
  );

  const renderCells = () => {
    const count = layout.defaults.length;

    if (layout.id === '1+2') {
      return (
        <div className="relative grid min-h-0 flex-1 grid-cols-[1.6fr_1fr] grid-rows-2 gap-1 p-1">
          {renderCell(0, 'row-span-2')}
          {renderCell(1)}
          {renderCell(2)}
        </div>
      );
    }

    return (
      <div className={cn(
        'relative grid min-h-0 flex-1 gap-1 overflow-hidden p-1',
        GRID_CLASS[layout.cols],
        layout.rows === 2 && 'grid-rows-2',
        layout.rows === 1 && 'grid-rows-1',
      )}>
        {Array.from({ length: count }, (_, i) => renderCell(i))}
      </div>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background">
      <div className="flex h-[42px] shrink-0 items-center justify-between gap-3 border-b border-border bg-muted/30 px-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <LayoutGrid size={14} className="shrink-0 text-primary" />
          <span className="text-xs font-bold uppercase tracking-wide text-secondary-foreground">
            Multi-Chart View
          </span>
          <span className="hidden truncate text-[0.72rem] text-muted-foreground sm:inline">
            {layout.description} — click any chart to focus
          </span>
        </div>

        <ToggleGroup
          type="single"
          size="sm"
          spacing={1}
          value={layoutId}
          onValueChange={handleLayoutChange}
          className="shrink-0 rounded-md bg-muted/40 p-0.5"
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

        <Button variant="outline" size="sm" className="shrink-0 text-xs" onClick={onSwitchToSingle}>
          ← Single Chart
        </Button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {renderCells()}
      </div>
    </div>
  );
}
