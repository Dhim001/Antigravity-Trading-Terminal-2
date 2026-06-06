import React, { useState } from 'react';
import { useStore } from '../store/useStore';
import { sendWebSocketAction } from '../services/websocket';
import { Briefcase, List, Landmark, XSquare } from 'lucide-react';

export default function PositionManagerWidget() {
  const { positions, orders, balances, tickerData } = useStore();
  const [activeTab, setActiveTab] = useState('positions'); // positions, orders, balances

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
                  
                  const priceDecimals = symbol.includes("BTC") || symbol.includes("ETH") ? 2 : 2;

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
                        {pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: priceDecimals })}
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
                  const priceDecimals = order.symbol.includes("BTC") || order.symbol.includes("ETH") ? 2 : 2;
                  const qtyDecimals = order.symbol.includes("BTC") ? 4 : (order.symbol.includes("ETH") ? 3 : 1);
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

      </div>
    </div>
  );
}
