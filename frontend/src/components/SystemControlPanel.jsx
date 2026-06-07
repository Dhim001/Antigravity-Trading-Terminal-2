import React, { useState, useEffect } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { X, Play, ShieldAlert, Cpu, Database, TrendingUp, Sliders, DollarSign, RefreshCw } from 'lucide-react';

export default function SystemControlPanel({ isOpen, onClose }) {
  const { systemStats, activeSymbol } = useStore();
  const [activeTab, setActiveTab] = useState('simulation'); // 'simulation' | 'account' | 'diagnostics'
  
  // Simulation tab states
  const [volatility, setVolatility] = useState(systemStats.volatility_multiplier || 1.0);
  const [tickInterval, setTickInterval] = useState(systemStats.tick_interval || 0.25);
  const [bias, setBias] = useState('RANDOM');
  
  // Account seeding states
  const [seedAsset, setSeedAsset] = useState('USD');
  const [seedAmount, setSeedAmount] = useState('10000');
  const [isResetting, setIsResetting] = useState(false);

  // Sync component local state when store systemStats changes
  useEffect(() => {
    if (systemStats) {
      if (systemStats.volatility_multiplier !== undefined) setVolatility(systemStats.volatility_multiplier);
      if (systemStats.tick_interval !== undefined) setTickInterval(systemStats.tick_interval);
    }
  }, [systemStats]);

  // Request fresh server stats on tab change to diagnostics
  useEffect(() => {
    if (isOpen) {
      sendWebSocketAction('admin_get_stats');
    }
  }, [isOpen, activeTab]);

  if (!isOpen) return null;

  // Actions
  const handleUpdateSimulation = (updates = {}) => {
    const payload = {
      tick_interval: updates.tickInterval !== undefined ? updates.tickInterval : tickInterval,
      volatility_multiplier: updates.volatility !== undefined ? updates.volatility : volatility,
      symbol: activeSymbol,
      bias: updates.bias !== undefined ? updates.bias : bias,
    };
    sendWebSocketAction('admin_set_simulation', payload);
  };

  const handleSeedBalance = () => {
    const amount = parseFloat(seedAmount);
    if (isNaN(amount) || amount <= 0) {
      alert("Please enter a valid balance amount");
      return;
    }
    sendWebSocketAction('admin_seed_balance', {
      asset: seedAsset,
      amount: amount
    });
  };

  const handleNuclearReset = () => {
    if (window.confirm("🚨 WARNING: This will delete ALL database positions, orders, and trade histories. Default account balances will be re-seeded. Do you want to proceed?")) {
      setIsResetting(true);
      sendWebSocketAction('admin_reset_system');
      setTimeout(() => {
        setIsResetting(false);
        onClose();
      }, 1500);
    }
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
      background: 'rgba(5, 8, 15, 0.75)', backdropFilter: 'blur(8px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000, animation: 'fade-in 0.2s ease-out'
    }} onClick={onClose}>
      
      {/* Modal Card */}
      <div style={{
        background: 'rgba(10, 15, 26, 0.95)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderRadius: '12px',
        width: '540px',
        maxWidth: '90%',
        boxShadow: '0 20px 50px rgba(0,0,0,0.8), 0 0 30px rgba(37,99,235,0.15)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        animation: 'slide-up 0.25s cubic-bezier(0.16, 1, 0.3, 1)'
      }} onClick={e => e.stopPropagation()}>
        
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 20px', borderBottom: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(255,255,255,0.02)'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Cpu size={18} style={{ color: '#3b82f6' }} />
            <span style={{ fontSize: '0.95rem', fontWeight: '700', color: '#fff', letterSpacing: '0.5px' }}>
              SYSTEM ADMIN CONTROL PANEL
            </span>
          </div>
          <button onClick={onClose} style={{
            background: 'transparent', border: 'none', color: 'var(--text-muted)',
            cursor: 'pointer', transition: 'color 0.15s'
          }} onMouseEnter={e => e.currentTarget.style.color = '#fff'} onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}>
            <X size={18} />
          </button>
        </div>

        {/* Tab Headers */}
        <div style={{ display: 'flex', background: 'rgba(0,0,0,0.2)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          {[
            { id: 'simulation', label: 'Market Simulation', icon: <Sliders size={14} /> },
            { id: 'account', label: 'Account Admin', icon: <DollarSign size={14} /> },
            { id: 'diagnostics', label: 'Diagnostics', icon: <Database size={14} /> },
          ].map(t => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: '8px',
                padding: '12px 20px', background: 'transparent', border: 'none',
                color: activeTab === t.id ? '#60a5fa' : 'var(--text-muted)',
                fontSize: '0.78rem', fontWeight: '600', cursor: 'pointer',
                borderBottom: activeTab === t.id ? '2px solid #3b82f6' : '2px solid transparent',
                transition: 'all 0.15s'
              }}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div style={{ padding: '24px 20px', minHeight: '260px' }}>
          
          {/* Tab 1: Simulation */}
          {activeTab === 'simulation' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              
              {/* Drift/Bias override */}
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', fontWeight: '600' }}>
                  Drift Override for {activeSymbol}
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
                  {[
                    { id: 'UP', label: 'Bullish (Pump)', color: '#10b981', bg: 'rgba(16,185,129,0.1)' },
                    { id: 'DOWN', label: 'Bearish (Dump)', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
                    { id: 'RANDOM', label: 'Random Walk', color: '#94a3b8', bg: 'rgba(255,255,255,0.03)' },
                  ].map(b => (
                    <button
                      key={b.id}
                      onClick={() => { setBias(b.id); handleUpdateSimulation({ bias: b.id }); }}
                      style={{
                        padding: '10px', borderRadius: '6px', border: `1px solid ${bias === b.id ? b.color : 'rgba(255,255,255,0.08)'}`,
                        background: bias === b.id ? b.bg : 'rgba(255,255,255,0.01)',
                        color: bias === b.id ? b.color : 'var(--text-secondary)',
                        fontSize: '0.75rem', fontWeight: '700', cursor: 'pointer',
                        transition: 'all 0.15s'
                      }}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Volatility Multiplier */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: '600' }}>
                    Volatility Multiplier
                  </label>
                  <span className="num-mono" style={{ fontSize: '0.8rem', color: '#60a5fa', fontWeight: '700' }}>
                    {volatility.toFixed(1)}x
                  </span>
                </div>
                <input
                  type="range" min="0.2" max="5.0" step="0.2"
                  value={volatility}
                  onChange={e => {
                    const val = parseFloat(e.target.value);
                    setVolatility(val);
                    handleUpdateSimulation({ volatility: val });
                  }}
                  style={{ width: '100%', cursor: 'pointer' }}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                  <span>0.2x (Stable)</span>
                  <span>1.0x (Normal)</span>
                  <span>5.0x (Highly Volatile)</span>
                </div>
              </div>

              {/* Tick interval / Server speed */}
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', fontWeight: '600' }}>
                  Tick Broadcast Speed
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '6px' }}>
                  {[
                    { val: 1.0, label: '1s (Slow)' },
                    { val: 0.5, label: '500ms' },
                    { val: 0.25, label: '250ms (Normal)' },
                    { val: 0.1, label: '100ms (Fast)' },
                  ].map(speed => (
                    <button
                      key={speed.val}
                      onClick={() => { setTickInterval(speed.val); handleUpdateSimulation({ tickInterval: speed.val }); }}
                      style={{
                        padding: '8px 4px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.08)',
                        background: tickInterval === speed.val ? 'rgba(59,130,246,0.15)' : 'rgba(255,255,255,0.02)',
                        color: tickInterval === speed.val ? '#60a5fa' : 'var(--text-secondary)',
                        fontSize: '0.7rem', fontWeight: '600', cursor: 'pointer',
                        borderColor: tickInterval === speed.val ? '#3b82f6' : 'rgba(255,255,255,0.08)',
                        transition: 'all 0.15s'
                      }}
                    >
                      {speed.label}
                    </button>
                  ))}
                </div>
              </div>

            </div>
          )}

          {/* Tab 2: Account seeding */}
          {activeTab === 'account' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              
              {/* Seed balance */}
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', fontWeight: '600' }}>
                  Credit Account Balance
                </label>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <select
                    value={seedAsset}
                    onChange={e => setSeedAsset(e.target.value)}
                    style={{
                      background: '#111827', border: '1px solid rgba(255,255,255,0.12)',
                      color: '#fff', borderRadius: '6px', padding: '10px', fontSize: '0.8rem',
                      outline: 'none', cursor: 'pointer'
                    }}
                  >
                    {['USD', 'USDT', 'BTC', 'ETH', 'AAPL', 'TSLA', 'MSFT'].map(asset => (
                      <option key={asset} value={asset}>{asset}</option>
                    ))}
                  </select>
                  <input
                    type="number"
                    value={seedAmount}
                    onChange={e => setSeedAmount(e.target.value)}
                    style={{
                      flex: 1, background: '#111827', border: '1px solid rgba(255,255,255,0.12)',
                      color: '#fff', borderRadius: '6px', padding: '10px 14px', fontSize: '0.82rem',
                      outline: 'none', fontFamily: 'var(--font-mono)'
                    }}
                    placeholder="Amount to credit..."
                  />
                  <button
                    onClick={handleSeedBalance}
                    style={{
                      background: '#2563eb', color: '#fff', border: 'none', borderRadius: '6px',
                      padding: '0 20px', fontWeight: '600', fontSize: '0.78rem', cursor: 'pointer',
                      transition: 'background 0.15s'
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = '#1d4ed8'}
                    onMouseLeave={e => e.currentTarget.style.background = '#2563eb'}
                  >
                    Credit Balance
                  </button>
                </div>
              </div>

              <hr style={{ border: 'none', borderBottom: '1px solid rgba(255,255,255,0.06)' }} />

              {/* Reset simulator system */}
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '8px', textTransform: 'uppercase', fontWeight: '600' }}>
                  System Reset Actions
                </label>
                <button
                  onClick={handleNuclearReset}
                  disabled={isResetting}
                  style={{
                    width: '100%', padding: '12px', background: 'rgba(239, 68, 68, 0.1)',
                    border: '1px solid #ef4444', borderRadius: '6px', color: '#ef4444',
                    fontWeight: '700', fontSize: '0.78rem', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                    transition: 'all 0.15s',
                    boxShadow: '0 0 10px rgba(239,68,68,0.05)'
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)';
                    e.currentTarget.style.boxShadow = '0 0 15px rgba(239,68,68,0.15)';
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)';
                    e.currentTarget.style.boxShadow = '0 0 10px rgba(239,68,68,0.05)';
                  }}
                >
                  {isResetting ? <RefreshCw size={14} className="animate-spin" /> : <ShieldAlert size={14} />}
                  {isResetting ? "RESETTING SYSTEM CONFIGURATIONS..." : "NUCLEAR RESET: WIPE SYSTEM DATABASE"}
                </button>
                <span style={{ display: 'block', fontSize: '0.66rem', color: 'var(--text-muted)', marginTop: '6px', textAlign: 'center' }}>
                  Warning: Wipes active positions, cancels pending orders, and clears trade blotter logs.
                </span>
              </div>

            </div>
          )}

          {/* Tab 3: Diagnostics */}
          {activeTab === 'diagnostics' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)', padding: '14px', borderRadius: '8px' }}>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: '600', marginBottom: '4px' }}>
                    Active Client Sockets
                  </div>
                  <div className="num-mono" style={{ fontSize: '1.4rem', fontWeight: '700', color: '#60a5fa' }}>
                    {systemStats.clients || 1}
                  </div>
                </div>
                <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)', padding: '14px', borderRadius: '8px' }}>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: '600', marginBottom: '4px' }}>
                    Active Tick Rate
                  </div>
                  <div className="num-mono" style={{ fontSize: '1.4rem', fontWeight: '700', color: '#34d399' }}>
                    {(1.0 / (systemStats.tick_interval || 0.25)).toFixed(1)} <span style={{ fontSize: '0.8rem', fontWeight: '400' }}>ticks/sec</span>
                  </div>
                </div>
              </div>

              <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.04)', padding: '16px', borderRadius: '8px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div style={{ fontSize: '0.72rem', color: '#fff', fontWeight: '700', borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '6px', marginBottom: '2px' }}>
                  Database Row Counts
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Open Positions count:</span>
                  <span className="num-mono" style={{ fontWeight: '600', color: '#fff' }}>{systemStats.positions_count || 0}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Pending Orders count:</span>
                  <span className="num-mono" style={{ fontWeight: '600', color: '#fff' }}>{systemStats.pending_orders_count || 0}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Filled Trades count:</span>
                  <span className="num-mono" style={{ fontWeight: '600', color: '#fff' }}>{systemStats.filled_trades_count || 0}</span>
                </div>
              </div>

            </div>
          )}

        </div>

        {/* Footer */}
        <div style={{
          padding: '12px 20px', borderTop: '1px solid rgba(255,255,255,0.08)',
          background: 'rgba(0,0,0,0.15)', display: 'flex', justifyContent: 'flex-end'
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '6px 16px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.1)',
              background: 'rgba(255,255,255,0.03)', color: 'var(--text-secondary)',
              fontSize: '0.75rem', fontWeight: '600', cursor: 'pointer',
              fontFamily: 'var(--font-sans)', transition: 'all 0.15s'
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
            onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
          >
            Close Panel
          </button>
        </div>

      </div>
    </div>
  );
}
