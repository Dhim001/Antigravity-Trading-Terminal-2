/**
 * OrdersPanel.jsx — Orders dock tab (extracted from ResizableDock).
 *
 * Displays pending orders with type, side, price, quantity, bot attribution,
 * and cancel controls. Includes toolbar stats and footer totals.
 */
import React, { useMemo } from 'react';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { priceDecimals, fmtP } from '../../lib/dockFormatters';
import { cn } from '@/lib/utils';
import { List, XSquare } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '../DataTableShell';
import StrategyBadge from '../StrategyBadge';
import { WidgetEmpty } from '../WidgetShell';
import { buildBotLookup } from '@/lib/botAttribution';

export default function OrdersTab() {
  const orders = useStore(state => state.orders);
  const activeBots = useStore(state => state.activeBots);
  const { byId } = buildBotLookup(activeBots);
  const active = orders.filter(o => o.status === 'PENDING');

  const stats = useMemo(() => {
    let buyCount = 0;
    let sellCount = 0;
    let totalValue = 0;
    for (const ord of active) {
      if (ord.side === 'BUY') buyCount += 1;
      else sellCount += 1;
      totalValue += (ord.price || 0) * ord.quantity;
    }
    return { buyCount, sellCount, totalValue };
  }, [active]);

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <List size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Pending Orders</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {active.length} order{active.length === 1 ? '' : 's'}
              {active.length > 0 && (
                <> · {stats.buyCount}B / {stats.sellCount}S</>
              )}
            </span>
          </div>
        </div>
        {active.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Notional</span>
            <span className="dock-panel-tab__meta-value num-mono">
              ${fmtP(stats.totalValue)}
            </span>
          </div>
        )}
      </header>

      {active.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={List} message="No pending orders" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[640px]">
              <DataTableHeader>
                <tr>
                  <DataTableHead>Symbol</DataTableHead>
                  <DataTableHead>Source</DataTableHead>
                  <DataTableHead>Type</DataTableHead>
                  <DataTableHead>Side</DataTableHead>
                  <DataTableHead align="right">Price</DataTableHead>
                  <DataTableHead align="right">Qty</DataTableHead>
                  <DataTableHead align="right">Value</DataTableHead>
                  <DataTableHead align="center">Cancel</DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                {active.map(ord => {
                  const dec = priceDecimals(ord.symbol, ord.price);
                  const isBuy = ord.side === 'BUY';
                  const value = (ord.price || 0) * ord.quantity;
                  const bot = ord.bot_id ? byId[ord.bot_id] : null;
                  return (
                    <DataTableRow key={ord.id} rowVariant="dock" deferred>
                      <DataTableCell className="font-bold">{ord.symbol}</DataTableCell>
                      <DataTableCell className="text-xs">
                        {bot ? (
                          <StrategyBadge strategy={bot.strategy} compact />
                        ) : (
                          <span className="text-muted-foreground">Manual</span>
                        )}
                      </DataTableCell>
                      <DataTableCell className="text-xs text-secondary-foreground">{ord.type}</DataTableCell>
                      <DataTableCell>
                        <Badge variant={isBuy ? 'buy' : 'sell'}>{ord.side}</Badge>
                      </DataTableCell>
                      <DataTableCell numeric align="right">
                        {ord.price ? ord.price.toFixed(dec) : 'MKT'}
                      </DataTableCell>
                      <DataTableCell numeric align="right">
                        {ord.quantity.toLocaleString(undefined, { minimumFractionDigits: 4 })}
                      </DataTableCell>
                      <DataTableCell numeric align="right" className="text-secondary-foreground">
                        ${fmtP(value)}
                      </DataTableCell>
                      <DataTableCell align="center">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => sendAction(Action.CANCEL_ORDER, { order_id: ord.id })}
                          title="Cancel order"
                          className="text-trading-down hover:text-trading-down"
                        >
                          <XSquare />
                        </Button>
                      </DataTableCell>
                    </DataTableRow>
                  );
                })}
              </DataTableBody>
            </DataTableRoot>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>
              {active.length} pending · {stats.buyCount} buy · {stats.sellCount} sell
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Total notional:{' '}
              <span className="num-mono font-bold">${fmtP(stats.totalValue)}</span>
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
