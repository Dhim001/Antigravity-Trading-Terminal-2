import React, { useState, useCallback } from 'react';
import { useStore } from './store/useStore';
import { useWebSocket } from './hooks/useWebSocket';

// Core components
import WatchlistWidget       from './components/WatchlistWidget';
import ChartWidget           from './components/ChartWidget';
import MultiChartGrid        from './components/MultiChartGrid';
import OrderBookWidget       from './components/OrderBookWidget';
import OrderEntryWidget      from './components/OrderEntryWidget';
import AlgoTraderEngine      from './components/AlgoTraderEngine';
import SystemControlPanel    from './components/SystemControlPanel';
import MarketOverviewStrip   from './components/MarketOverviewStrip';
import ResizableDock         from './components/ResizableDock';

import {
  TrendingUp, LayoutGrid, BarChart2, Settings,
} from 'lucide-react';

const DOCK_DEFAULT = 320;

export default function App() {
  const { connectionStatus, viewMode, setViewMode, isLive, terminalMode } = useStore();
  useWebSocket('ws://127.0.0.1:8765');

  const [showAdmin, setShowAdmin]   = useState(false);
  const [dockHeight, setDockHeight] = useState(DOCK_DEFAULT);

  // Callback from ResizableDock so grid row can be kept in sync
  const handleDockHeightChange = useCallback(h => setDockHeight(h), []);

  return (
    <div
      className="dashboard-container"
      style={{
        '--dock-h': `${dockHeight}px`,
      }}
    >
      {/* Headless engines */}
      <AlgoTraderEngine />
      <SystemControlPanel isOpen={showAdmin} onClose={() => setShowAdmin(false)} />

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <header className="terminal-header">
        <div className="brand-section">
          <TrendingUp size={20} className="logo-icon" />
          <span className="brand-title">ANTIGRAVITY</span>

          {/* Live mode badge */}
          {isLive && (
            <span style={{
              background: 'rgba(239,68,68,0.12)', color: '#ef4444',
              border: '1px solid rgba(239,68,68,0.35)',
              fontSize: 'var(--fs-2xs)', fontWeight: 800, padding: '3px 10px',
              borderRadius: 'var(--r-sm)', letterSpacing: '0.8px',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              <span style={{ width: 5, height: 5, borderRadius: '50%', background: '#ef4444', animation: 'ping 1s cubic-bezier(0,0,0.2,1) infinite' }} />
              LIVE · {terminalMode}
            </span>
          )}

          {/* Simulated badge (shown when not live) */}
          {!isLive && (
            <span style={{
              background: 'rgba(148,163,184,0.08)', color: '#94a3b8',
              border: '1px solid rgba(148,163,184,0.15)',
              fontSize: 'var(--fs-2xs)', fontWeight: 700, padding: '3px 10px',
              borderRadius: 'var(--r-sm)', letterSpacing: '0.5px',
            }}>
              SIMULATED
            </span>
          )}

          {/* Admin / settings */}
          <button
            onClick={() => setShowAdmin(true)}
            title="System Control & Admin Panel"
            style={{
              background: 'transparent', border: 'none',
              color: 'var(--text-muted)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', marginLeft: 6,
              transition: 'color 0.15s', padding: 4, borderRadius: 'var(--r-sm)',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = '#3b82f6'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; }}
          >
            <Settings size={15} />
          </button>
        </div>

        {/* Center: View mode toggle */}
        <div className="view-toggle">
          <button
            className={`view-toggle-btn${viewMode === 'single' ? ' active' : ''}`}
            onClick={() => setViewMode('single')}
            title="Single chart with indicators"
          >
            <BarChart2 size={13} /> Chart
          </button>
          <button
            className={`view-toggle-btn${viewMode === 'multi' ? ' active' : ''}`}
            onClick={() => setViewMode('multi')}
            title="Multi-asset grid view"
          >
            <LayoutGrid size={13} /> Multi-Chart
          </button>
        </div>

        {/* Right: Connection badge */}
        <div className="connection-badge">
          <span className={`status-dot ${connectionStatus}`} style={{
            background: isLive && connectionStatus === 'connected' ? '#f59e0b' : undefined,
            boxShadow: isLive && connectionStatus === 'connected' ? '0 0 8px #f59e0b' : undefined,
          }} />
          <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>
            {connectionStatus === 'connected'
              ? (isLive ? 'Live Broker' : 'Simulated')
              : 'Disconnected'}
          </span>
        </div>
      </header>

      {/* ── Market Overview Strip ──────────────────────────────────────────── */}
      <MarketOverviewStrip />

      {/* ── Watchlist Sidebar (left column, spans rows 3+4) ───────────────── */}
      <aside className="watchlist-sidebar" style={{ gridRow: '3 / 5' }}>
        <WatchlistWidget />
      </aside>

      {/* ── Main Workspace (chart area, row 3) ────────────────────────────── */}
      <main className="workspace-main">
        {viewMode === 'single'
          ? <ChartWidget />
          : <MultiChartGrid onSwitchToSingle={() => setViewMode('single')} />
        }
      </main>

      {/* ── Right Trading Panel (order entry + order book, spans rows 3+4) ── */}
      <section className="trading-panel" style={{ gridRow: '3 / 5', display: 'grid', gridTemplateRows: '380px 1fr' }}>
        <OrderEntryWidget />
        <OrderBookWidget />
      </section>

      {/* ── Bottom Dock (row 4, center column) ───────────────────────────── */}
      <ResizableDock setDockHeight={handleDockHeightChange} />
    </div>
  );
}
