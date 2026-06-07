/**
 * MultiChartGrid.jsx
 * Professional multi-asset chart grid inspired by Bloomberg Terminal, TradingView,
 * and ThinkOrSwim. Supports 1×1, 2×1, 2×2, 3×2, and 1+3 mosaic layouts.
 * Each cell is an independent MiniChartWidget with its own symbol selector.
 */
import React, { useState, useEffect } from 'react';
import { useStore } from '../store/useStore';
import MiniChartWidget from './MiniChartWidget';

// ── Layout Definitions ─────────────────────────────────────────────────────
const LAYOUTS = [
  {
    id: '1x1',
    label: '1×1',
    icon: (
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">
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
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">
        <rect x="1"  y="1" width="9" height="16" rx="1.5" />
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
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">
        <rect x="1"  y="1"  width="12" height="16" rx="1.5" />
        <rect x="15" y="1"  width="6"  height="7"  rx="1.5" />
        <rect x="15" y="10" width="6"  height="7"  rx="1.5" />
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
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">
        <rect x="1"  y="1"  width="9" height="7"  rx="1.5" />
        <rect x="12" y="1"  width="9" height="7"  rx="1.5" />
        <rect x="1"  y="10" width="9" height="7"  rx="1.5" />
        <rect x="12" y="10" width="9" height="7"  rx="1.5" />
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
      <svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">
        <rect x="1"  y="1"  width="5.5" height="7" rx="1" />
        <rect x="8.25" y="1" width="5.5" height="7" rx="1" />
        <rect x="15.5" y="1" width="5.5" height="7" rx="1" />
        <rect x="1"  y="10" width="5.5" height="7" rx="1" />
        <rect x="8.25" y="10" width="5.5" height="7" rx="1" />
        <rect x="15.5" y="10" width="5.5" height="7" rx="1" />
      </svg>
    ),
    cols: 3, rows: 2,
    defaults: ['BTCUSDT', 'ETHUSDT', 'AAPL', 'TSLA', 'MSFT', 'BTCUSDT'],
    description: '6 charts',
  },
];

// ── Component ──────────────────────────────────────────────────────────────
export default function MultiChartGrid({ onSwitchToSingle }) {
  const [layoutId, setLayoutId] = useState(() => {
    try {
      const saved = localStorage.getItem('terminal_multi_chart_layout_id');
      if (saved && LAYOUTS.some(l => l.id === saved)) {
        return saved;
      }
    } catch (_) {}
    return '2x2';
  });

  const [focusedIdx, setFocusedIdx]   = useState(0);
  const [maximizedIdx, setMaximizedIdx] = useState(null);

  const [symbols, setSymbols] = useState(() => {
    let savedLayoutId = '2x2';
    try {
      const savedL = localStorage.getItem('terminal_multi_chart_layout_id');
      if (savedL && LAYOUTS.some(l => l.id === savedL)) {
        savedLayoutId = savedL;
      }
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

  const { activeSymbol, setActiveSymbol } = useStore();

  const layout = LAYOUTS.find(l => l.id === layoutId);

  // Sync activeSymbol from store to the focused grid cell
  useEffect(() => {
    if (activeSymbol && symbols[focusedIdx] !== activeSymbol) {
      setSymbols(prev => {
        const next = [...prev];
        if (focusedIdx < next.length) {
          next[focusedIdx] = activeSymbol;
        }
        return next;
      });
    }
  }, [activeSymbol, focusedIdx]);

  // Sync state to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('terminal_multi_chart_layout_id', layoutId);
    } catch (_) {}
  }, [layoutId]);

  useEffect(() => {
    try {
      localStorage.setItem('terminal_multi_chart_symbols', JSON.stringify(symbols));
    } catch (_) {}
  }, [symbols]);

  // When layout changes, reset symbols to defaults and expand array if needed
  const handleLayoutChange = (newLayout) => {
    setLayoutId(newLayout.id);
    setMaximizedIdx(null);
    // Preserve existing symbols where possible, fill remainder with defaults
    setSymbols(prev => {
      const next = [...newLayout.defaults];
      for (let i = 0; i < Math.min(prev.length, next.length); i++) {
        next[i] = prev[i];
      }
      return next;
    });
    setFocusedIdx(0);
  };

  const handleFocus = (idx, sym) => {
    setFocusedIdx(idx);
    setActiveSymbol(sym);
  };

  // ── Render grid cells ──────────────────────────────────────────────────
  const renderCells = () => {
    const count = layout.defaults.length;

    // Helper to get cell styles (passed directly to MiniChartWidget)
    const getCellStyle = (idx, baseStyle = {}) => {
      const isMaximized = maximizedIdx === idx;
      const hasAnyMaximized = maximizedIdx !== null;
      return {
        ...baseStyle,
        opacity: hasAnyMaximized && !isMaximized ? 0.15 : 1,
        pointerEvents: hasAnyMaximized && !isMaximized ? 'none' : 'auto',
        transition: 'opacity 0.2s ease-in-out',
      };
    };

    // Special layout: 1 large left + 2 stacked right
    if (layout.id === '1+2') {
      return (
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1.6fr 1fr',
          gridTemplateRows: '1fr 1fr',
          gap: '4px',
          flex: 1,
          padding: '4px',
          minHeight: 0,
          position: 'relative',
        }}>
          <MiniChartWidget
            key={`cell-0-${symbols[0]}`}
            defaultSymbol={symbols[0]}
            isFocused={focusedIdx === 0}
            onFocus={(sym) => {
              const next = [...symbols];
              next[0] = sym;
              setSymbols(next);
              handleFocus(0, sym);
            }}
            isMaximized={maximizedIdx === 0}
            onToggleMaximize={() => setMaximizedIdx(maximizedIdx === 0 ? null : 0)}
            style={getCellStyle(0, { gridRow: '1 / 3' })}
          />
          <MiniChartWidget
            key={`cell-1-${symbols[1]}`}
            defaultSymbol={symbols[1]}
            isFocused={focusedIdx === 1}
            onFocus={(sym) => {
              const next = [...symbols];
              next[1] = sym;
              setSymbols(next);
              handleFocus(1, sym);
            }}
            isMaximized={maximizedIdx === 1}
            onToggleMaximize={() => setMaximizedIdx(maximizedIdx === 1 ? null : 1)}
            style={getCellStyle(1)}
          />
          <MiniChartWidget
            key={`cell-2-${symbols[2]}`}
            defaultSymbol={symbols[2]}
            isFocused={focusedIdx === 2}
            onFocus={(sym) => {
              const next = [...symbols];
              next[2] = sym;
              setSymbols(next);
              handleFocus(2, sym);
            }}
            isMaximized={maximizedIdx === 2}
            onToggleMaximize={() => setMaximizedIdx(maximizedIdx === 2 ? null : 2)}
            style={getCellStyle(2)}
          />
        </div>
      );
    }

    // Standard grid layouts
    return (
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${layout.cols}, 1fr)`,
        gridTemplateRows: `repeat(${layout.rows}, 1fr)`,
        gap: '4px',
        flex: 1,
        padding: '4px',
        minHeight: 0,
        overflow: 'hidden',
        position: 'relative',
      }}>
        {Array.from({ length: count }, (_, i) => (
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
            style={getCellStyle(i)}
          />
        ))}
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: '#070c13' }}>

      {/* ── Toolbar ─────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 14px', height: '42px', flexShrink: 0,
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        background: '#080d14',
      }}>
        {/* Left: Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: '700', color: 'var(--text-secondary)', letterSpacing: '0.5px', textTransform: 'uppercase' }}>
            Multi-Chart View
          </span>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            {layout.description} — click any chart to focus
          </span>
        </div>

        {/* Center: Layout Selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '2px', background: 'rgba(255,255,255,0.04)', borderRadius: '6px', padding: '3px' }}>
          {LAYOUTS.map(l => (
            <button
              key={l.id}
              title={l.description}
              onClick={() => handleLayoutChange(l)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                gap: '5px',
                padding: '4px 8px', borderRadius: '4px', cursor: 'pointer',
                border: 'none',
                background: layoutId === l.id ? 'rgba(37,99,235,0.25)' : 'transparent',
                color: layoutId === l.id ? '#60a5fa' : 'var(--text-muted)',
                transition: 'all 0.15s',
              }}
            >
              {l.icon}
              <span style={{ fontSize: '0.68rem', fontWeight: '600', fontFamily: 'var(--font-sans)' }}>{l.label}</span>
            </button>
          ))}
        </div>

        {/* Right: Back to single chart */}
        <button
          onClick={onSwitchToSingle}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '5px 12px', borderRadius: '6px', cursor: 'pointer',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.1)',
            color: 'var(--text-secondary)', fontSize: '0.75rem',
            fontWeight: '600', fontFamily: 'var(--font-sans)',
            transition: 'all 0.15s',
          }}
        >
          ← Single Chart
        </button>
      </div>

      {/* ── Grid ────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {renderCells()}
      </div>
    </div>
  );
}
