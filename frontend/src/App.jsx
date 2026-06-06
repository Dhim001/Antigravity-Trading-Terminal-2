import React, { useState } from 'react';
import { useStore } from './store/useStore';
import { useWebSocket } from './hooks/useWebSocket';

// Components
import WatchlistWidget       from './components/WatchlistWidget';
import ChartWidget           from './components/ChartWidget';
import MultiChartGrid        from './components/MultiChartGrid';
import OrderBookWidget       from './components/OrderBookWidget';
import OrderEntryWidget      from './components/OrderEntryWidget';
import PositionManagerWidget from './components/PositionManagerWidget';
import TradeHistoryPanel     from './components/TradeHistoryPanel';

import { TrendingUp, LayoutGrid, BarChart2, Clock } from 'lucide-react';

export default function App() {
  const { connectionStatus } = useStore();
  useWebSocket('ws://127.0.0.1:8765');

  // 'single' = classic full-featured chart + indicators
  // 'multi'  = multi-asset grid view
  const [viewMode, setViewMode] = useState('single');
  const [showHistory, setShowHistory] = useState(false);

  return (
    <div className="dashboard-container">

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="terminal-header">
        <div className="brand-section">
          <TrendingUp size={22} className="logo-icon" />
          <span className="brand-title">ANTIGRAVITY LIVE TRADING TERMINAL</span>
        </div>

        {/* ── Controls Group ───────────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {/* View-mode toggle */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '2px',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '8px', padding: '3px',
          }}>
            <button
              onClick={() => setViewMode('single')}
              title="Single chart with indicators"
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '5px 12px', borderRadius: '5px', cursor: 'pointer',
                border: 'none', fontFamily: 'var(--font-sans)',
                background: viewMode === 'single' ? 'rgba(37,99,235,0.3)' : 'transparent',
                color: viewMode === 'single' ? '#60a5fa' : 'var(--text-muted)',
                fontSize: '0.78rem', fontWeight: '600',
                transition: 'all 0.15s',
              }}
            >
              <BarChart2 size={14} />
              Chart
            </button>
            <button
              onClick={() => setViewMode('multi')}
              title="Multi-asset grid view"
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '5px 12px', borderRadius: '5px', cursor: 'pointer',
                border: 'none', fontFamily: 'var(--font-sans)',
                background: viewMode === 'multi' ? 'rgba(37,99,235,0.3)' : 'transparent',
                color: viewMode === 'multi' ? '#60a5fa' : 'var(--text-muted)',
                fontSize: '0.78rem', fontWeight: '600',
                transition: 'all 0.15s',
              }}
            >
              <LayoutGrid size={14} />
              Multi-Chart
            </button>
          </div>

          {/* History Toggle Button */}
          <button
            onClick={() => setShowHistory(true)}
            title="Open Transaction & Trade History Blotter"
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '6px 12px', borderRadius: '8px', cursor: 'pointer',
              border: '1px solid rgba(255,255,255,0.1)',
              background: 'rgba(255,255,255,0.03)',
              color: 'var(--text-secondary)',
              fontSize: '0.78rem', fontWeight: '600',
              fontFamily: 'var(--font-sans)',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.08)';
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.2)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.03)';
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)';
            }}
          >
            <Clock size={14} />
            History
          </button>
        </div>

        {/* ── Connection badge ─────────────────────────────────────────── */}
        <div className="connection-badge">
          <span className={`status-dot ${connectionStatus}`} />
          <span style={{ textTransform: 'capitalize', color: 'var(--text-secondary)' }}>
            {connectionStatus === 'connected' ? 'Live' : 'Disconnected'}
          </span>
        </div>
      </header>

      {/* ── Watchlist sidebar (always visible) ─────────────────────────── */}
      <aside className="watchlist-sidebar">
        <WatchlistWidget />
      </aside>

      {/* ── Main workspace ─────────────────────────────────────────────── */}
      <main className="workspace-main">
        {viewMode === 'single' ? (
          // Classic single chart + position manager below
          <>
            <ChartWidget />
            <div style={{ background: 'var(--bg-secondary)', overflow: 'hidden' }}>
              <PositionManagerWidget />
            </div>
          </>
        ) : (
          // Multi-chart grid takes full height (no position manager below in grid mode)
          <MultiChartGrid onSwitchToSingle={() => setViewMode('single')} />
        )}
      </main>

      {/* ── Right execution panel (always visible) ─────────────────────── */}
      <section className="trading-panel" style={{ display: 'grid', gridTemplateRows: '340px 1fr' }}>
        <OrderEntryWidget />
        <OrderBookWidget />
      </section>

      {/* ── Trade History Slide-up Blotter ─────────────────────────────── */}
      {showHistory && (
        <TradeHistoryPanel onClose={() => setShowHistory(false)} />
      )}
    </div>
  );
}
