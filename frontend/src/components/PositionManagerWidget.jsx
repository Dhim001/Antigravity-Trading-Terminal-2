import React, { useState } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { Briefcase, List, Landmark, XSquare, Cpu, Play, Square, Trash2, Settings } from 'lucide-react';

export default function PositionManagerWidget() {
  const {
    positions, orders, balances, tickerData, activeSymbol,
    isBotRunning, botStrategy, botConfig, botLogs,
    startBot, stopBot, setBotStrategy, updateBotConfig, clearBotLogs
  } = useStore();

  const [activeTab, setActiveTab] = useState('positions'); // positions, orders, balances, algo

  const handleCancelOrder = (orderId) => {
    sendWebSocketAction("cancel_order", { order_id: orderId });
  };

  const getActiveOrders = () => {
    return orders.filter(o => o.status === 'PENDING');
  };

  return (
    <div className="widget-card" style={{ height: '100%' }}>
      {/* Tabs Header */}
      <div className="widget-header" style={{ borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', padding: '0 16px', height: '48px', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: '4px', height: '100%' }}>
          <button 
            onClick={() => setActiveTab('positions')}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '6px',
              padding: '0 16px', 
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'positions' ? '2px solid var(--color-accent)' : '2px solid transparent',
              color: activeTab === 'positions' ? '#fff' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer',
              height: '100%'
            }}
          >
            <Briefcase size={14} />
            Positions ({Object.keys(positions).length})
          </button>
          <button 
            onClick={() => setActiveTab('orders')}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '6px',
              padding: '0 16px', 
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'orders' ? '2px solid var(--color-accent)' : '2px solid transparent',
              color: activeTab === 'orders' ? '#fff' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer',
              height: '100%'
            }}
          >
            <List size={14} />
            Active Orders ({getActiveOrders().length})
          </button>
          <button 
            onClick={() => setActiveTab('balances')}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '6px',
              padding: '0 16px', 
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'balances' ? '2px solid var(--color-accent)' : '2px solid transparent',
              color: activeTab === 'balances' ? '#fff' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer',
              height: '100%'
            }}
          >
            <Landmark size={14} />
            Balances
          </button>
          <button 
            onClick={() => setActiveTab('algo')}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '6px',
              padding: '0 16px', 
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'algo' ? '2px solid var(--color-accent)' : '2px solid transparent',
              color: activeTab === 'algo' ? '#fff' : 'var(--text-muted)',
              fontSize: '0.8rem',
              fontWeight: '600',
              cursor: 'pointer',
              height: '100%'
            }}
          >
            <Cpu size={14} style={{ color: isBotRunning ? '#10b981' : 'inherit' }} />
            Algo Trading
            {isBotRunning && (
              <span style={{
                width: '6px', height: '6px', borderRadius: '50%', background: '#10b981',
                boxShadow: '0 0 6px #10b981', marginLeft: '2px', display: 'inline-block'
              }} />
            )}
          </button>
        </div>
      </div>

      {/* Tab Content */}
      <div className="widget-content" style={{ height: 'calc(100% - 48px)', overflowY: 'auto' }}>
        
        {/* Positions Tab */}
        {activeTab === 'positions' && (
          <table className="terminal-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th style={{ textAlign: 'right' }}>Avg Price</th>
                <th style={{ textAlign: 'right' }}>Mark Price</th>
                <th style={{ textAlign: 'right' }}>Unrealized PnL</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(positions).length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>
                    No active positions
                  </td>
                </tr>
              ) : (
                Object.entries(positions).map(([symbol, pos]) => {
                  const markPrice = tickerData[symbol]?.price || pos.avg_price;
                  const uPnl = pos.size * (markPrice - pos.avg_price);
                  const isLong = pos.size >= 0;
                  
                  const priceDecimals = (symbol.includes("XRP") || symbol.includes("ADA") || symbol.includes("DOGE") || markPrice < 2.0 || pos.avg_price < 2.0) ? 4 : 2;

                  return (
                    <tr key={symbol}>
                      <td style={{ fontWeight: '600' }}>{symbol}</td>
                      <td>
                        <span style={{ 
                          fontSize: '0.75rem', 
                          padding: '2px 6px', 
                          borderRadius: '4px',
                          fontWeight: '600',
                          backgroundColor: isLong ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                          color: isLong ? 'var(--color-up)' : 'var(--color-down)'
                        }}>
                          {isLong ? 'LONG' : 'SHORT'}
                        </span>
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        {Math.abs(pos.size).toLocaleString(undefined, { minimumFractionDigits: 4 })}
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        <div>{pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals })}</div>
                        {(pos.stop_loss_price || pos.take_profit_price) && (
                          <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '2px', display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                            {pos.stop_loss_price && <span style={{ color: 'var(--color-down)' }}>SL: {pos.stop_loss_price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals })}</span>}
                            {pos.take_profit_price && <span style={{ color: 'var(--color-up)' }}>TP: {pos.take_profit_price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals })}</span>}
                          </div>
                        )}
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        {markPrice.toLocaleString(undefined, { minimumFractionDigits: priceDecimals })}
                      </td>
                      <td className={`num-mono ${uPnl >= 0 ? 'text-up' : 'text-down'}`} style={{ textAlign: 'right', fontWeight: '600' }}>
                        {uPnl >= 0 ? '+' : ''}{uPnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}

        {/* Orders Tab */}
        {activeTab === 'orders' && (
          <table className="terminal-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Type</th>
                <th>Side</th>
                <th style={{ textAlign: 'right' }}>Price</th>
                <th style={{ textAlign: 'right' }}>Quantity</th>
                <th style={{ textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {getActiveOrders().length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>
                    No active pending orders
                  </td>
                </tr>
              ) : (
                getActiveOrders().map((order) => {
                  const priceDecimals = (order.symbol.includes("XRP") || order.symbol.includes("ADA") || order.symbol.includes("DOGE") || (order.price && order.price < 2.0)) ? 4 : 2;
                  const qtyDecimals = (order.symbol.includes("USDT") || order.symbol.includes("USD")) ? 4 : 2;
                  const isBuy = order.side === 'BUY';
                  
                  return (
                    <tr key={order.id}>
                      <td style={{ fontWeight: '600' }}>{order.symbol}</td>
                      <td style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{order.type}</td>
                      <td>
                        <span style={{ 
                          fontSize: '0.75rem', 
                          padding: '2px 6px', 
                          borderRadius: '4px',
                          fontWeight: '600',
                          backgroundColor: isBuy ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                          color: isBuy ? 'var(--color-up)' : 'var(--color-down)'
                        }}>
                          {order.side}
                        </span>
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        {order.price ? order.price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals }) : 'MKT'}
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        {order.quantity.toFixed(qtyDecimals)}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button 
                          onClick={() => handleCancelOrder(order.id)}
                          style={{ 
                            background: 'transparent', 
                            border: 'none', 
                            cursor: 'pointer',
                            color: 'var(--color-down)',
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '4px'
                          }}
                          title="Cancel Order"
                        >
                          <XSquare size={16} />
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}

        {/* Balances Tab */}
        {activeTab === 'balances' && (
          <table className="terminal-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th style={{ textAlign: 'right' }}>Total Balance</th>
                <th style={{ textAlign: 'right' }}>Locked in Orders</th>
                <th style={{ textAlign: 'right' }}>Available</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(balances).length === 0 ? (
                <tr>
                  <td colSpan={4} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>
                    Loading balances...
                  </td>
                </tr>
              ) : (
                Object.entries(balances).map(([asset, bal]) => {
                  const available = bal.balance - bal.locked;
                  const decimalPlaces = asset === 'USD' || asset === 'USDT' ? 2 : 4;
                  
                  return (
                    <tr key={asset}>
                      <td style={{ fontWeight: '600' }}>{asset}</td>
                      <td className="num-mono" style={{ textAlign: 'right' }}>
                        {bal.balance.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right', color: 'var(--text-muted)' }}>
                        {bal.locked.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                      <td className="num-mono" style={{ textAlign: 'right', fontWeight: '600', color: '#fff' }}>
                        {available.toLocaleString(undefined, { minimumFractionDigits: decimalPlaces, maximumFractionDigits: decimalPlaces })}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}

        {/* Algo Trading Tab */}
        {activeTab === 'algo' && (
          <div style={{
            display: 'grid',
            gridTemplateColumns: '320px 1fr',
            gap: '16px',
            padding: '16px',
            height: '100%',
            overflow: 'hidden',
            minHeight: 0,
          }}>
            {/* Left: Settings Panel */}
            <div style={{
              display: 'flex', flexDirection: 'column', gap: '12px',
              background: 'rgba(255,255,255,0.01)', border: '1px solid rgba(255,255,255,0.05)',
              borderRadius: '8px', padding: '14px', overflowY: 'auto'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fff', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '6px' }}>
                <Settings size={14} style={{ color: 'var(--color-accent)' }} />
                <span style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Bot Parameters</span>
              </div>

              {/* Strategy Select */}
              <div className="terminal-input-group" style={{ margin: 0 }}>
                <label className="terminal-label" style={{ fontSize: '0.7rem' }}>Select Strategy</label>
                <select
                  value={botStrategy}
                  onChange={e => setBotStrategy(e.target.value)}
                  disabled={isBotRunning}
                  style={{
                    width: '100%', background: '#0f172a', border: '1px solid rgba(255,255,255,0.1)',
                    color: '#fff', borderRadius: '5px', padding: '6px 10px',
                    fontSize: '0.75rem', cursor: isBotRunning ? 'not-allowed' : 'pointer', fontFamily: 'var(--font-sans)',
                    colorScheme: 'dark',
                  }}
                >
                  <option value="EMA_CROSS" style={{ background: '#0f172a', color: '#fff' }}>EMA Crossover (9/21)</option>
                  <option value="RSI_MEAN_REV" style={{ background: '#0f172a', color: '#fff' }}>RSI Mean Reversion (14)</option>
                  <option value="MACD_TREND" style={{ background: '#0f172a', color: '#fff' }}>MACD Trend Follower</option>
                </select>
              </div>

              {/* Quantity Input */}
              <div className="terminal-input-group" style={{ margin: 0 }}>
                <label className="terminal-label" style={{ fontSize: '0.7rem' }}>Order Size (Quantity)</label>
                <div className="terminal-input-wrapper">
                  <input
                    type="number"
                    step="any"
                    value={botConfig?.quantity || ''}
                    disabled={isBotRunning}
                    onChange={e => updateBotConfig({ quantity: parseFloat(e.target.value) || 0 })}
                    className="terminal-input"
                    style={{ padding: '6px 10px', fontSize: '0.75rem', height: 'auto' }}
                  />
                  <span className="terminal-input-suffix" style={{ fontSize: '0.68rem', padding: '0 8px' }}>Units</span>
                </div>
              </div>

              {/* Stop Loss Input */}
              <div className="terminal-input-group" style={{ margin: 0 }}>
                <label className="terminal-label" style={{ fontSize: '0.7rem' }}>Auto Stop Loss</label>
                <div className="terminal-input-wrapper">
                  <input
                    type="number"
                    step="0.1"
                    value={botConfig?.stopLossPercent || ''}
                    disabled={isBotRunning}
                    onChange={e => {
                      const val = parseFloat(e.target.value) || 0;
                      updateBotConfig({ stopLossPercent: val });
                      if (positions[activeSymbol] && positions[activeSymbol].size !== 0) {
                        sendWebSocketAction("update_position_sl_tp", {
                          symbol: activeSymbol,
                          stop_loss_percent: val,
                          take_profit_percent: botConfig.takeProfitPercent
                        });
                      }
                    }}
                    className="terminal-input"
                    style={{ padding: '6px 10px', fontSize: '0.75rem', height: 'auto' }}
                  />
                  <span className="terminal-input-suffix" style={{ fontSize: '0.68rem', padding: '0 8px' }}>%</span>
                </div>
              </div>

              {/* Take Profit Input */}
              <div className="terminal-input-group" style={{ margin: 0 }}>
                <label className="terminal-label" style={{ fontSize: '0.7rem' }}>Auto Take Profit</label>
                <div className="terminal-input-wrapper">
                  <input
                    type="number"
                    step="0.1"
                    value={botConfig?.takeProfitPercent || ''}
                    disabled={isBotRunning}
                    onChange={e => {
                      const val = parseFloat(e.target.value) || 0;
                      updateBotConfig({ takeProfitPercent: val });
                      if (positions[activeSymbol] && positions[activeSymbol].size !== 0) {
                        sendWebSocketAction("update_position_sl_tp", {
                          symbol: activeSymbol,
                          stop_loss_percent: botConfig.stopLossPercent,
                          take_profit_percent: val
                        });
                      }
                    }}
                    className="terminal-input"
                    style={{ padding: '6px 10px', fontSize: '0.75rem', height: 'auto' }}
                  />
                  <span className="terminal-input-suffix" style={{ fontSize: '0.68rem', padding: '0 8px' }}>%</span>
                </div>
              </div>

              {/* Start / Stop Toggle Button */}
              <button
                onClick={() => {
                  if (isBotRunning) {
                    stopBot();
                  } else {
                    startBot();
                  }
                }}
                className="terminal-btn"
                style={{
                  marginTop: 'auto',
                  background: isBotRunning ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)',
                  border: `1px solid ${isBotRunning ? 'var(--color-down)' : 'var(--color-up)'}`,
                  color: isBotRunning ? 'var(--color-down)' : 'var(--color-up)',
                  boxShadow: isBotRunning ? '0 0 10px rgba(239,68,68,0.2)' : '0 0 10px rgba(16,185,129,0.2)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                  fontWeight: '700', padding: '10px', fontSize: '0.82rem',
                }}
              >
                {isBotRunning ? (
                  <>
                    <Square size={14} fill="currentColor" />
                    STOP ALGO BOT
                  </>
                ) : (
                  <>
                    <Play size={14} fill="currentColor" />
                    START ALGO BOT
                  </>
                )}
              </button>
            </div>

            {/* Right: Console Log */}
            <div style={{
              display: 'flex', flexDirection: 'column', gap: '8px',
              background: '#04060a', border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: '8px', padding: '14px', overflow: 'hidden', minHeight: 0,
            }}>
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', justifyRules: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '6px', flexShrink: 0, justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
                  <Cpu size={14} style={{ color: isBotRunning ? '#10b981' : 'var(--text-muted)' }} />
                  <span style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Bot Operation Log</span>
                  <span style={{
                    fontSize: '0.65rem', padding: '2px 8px', borderRadius: '4px',
                    background: isBotRunning ? 'rgba(16,185,129,0.12)' : 'rgba(255,255,255,0.05)',
                    color: isBotRunning ? '#10b981' : 'var(--text-muted)', fontWeight: 600,
                  }}>
                    {isBotRunning ? `SCANNING ${activeSymbol}` : 'IDLE'}
                  </span>
                </div>
                <button
                  onClick={clearBotLogs}
                  title="Clear Console"
                  style={{
                    background: 'transparent', border: 'none', cursor: 'pointer',
                    color: 'var(--text-muted)', display: 'flex', alignItems: 'center', padding: '4px',
                    borderRadius: '4px', transition: 'color 0.2s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.color = '#fff'}
                  onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
                >
                  <Trash2 size={13} />
                </button>
              </div>

              {/* Monospace Output */}
              <div style={{
                flex: 1, overflowY: 'auto', fontFamily: 'var(--font-mono)',
                fontSize: '0.72rem', color: '#94a3b8', display: 'flex', flexDirection: 'column-reverse',
                gap: '4px', paddingRight: '4px', scrollBehavior: 'smooth'
              }}>
                {botLogs.length === 0 ? (
                  <div style={{ margin: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', opacity: 0.5 }}>
                    <Cpu size={24} />
                    <span>Bot console output is empty. Activate the bot to see logs.</span>
                  </div>
                ) : (
                  botLogs.map((log, idx) => {
                    let logColor = '#94a3b8'; // Neutral
                    if (log.includes('BUY') || log.includes('Profit') || log.includes('Success')) logColor = '#10b981'; // Green
                    if (log.includes('SELL') || log.includes('Loss') || log.includes('Stop Loss') || log.includes('Error')) logColor = '#ef4444'; // Red
                    if (log.includes('Running') || log.includes('Config')) logColor = '#60a5fa'; // Blue
                    return (
                      <div key={idx} style={{ color: logColor, whiteSpace: 'pre-wrap', lineHeight: 1.4 }}>
                        {log}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
