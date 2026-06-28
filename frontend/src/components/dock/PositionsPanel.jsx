/**
 * PositionsPanel.jsx — Positions dock tab (extracted from ResizableDock).
 */
import React, { useMemo } from 'react';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { priceDecimals, fmtP } from '../../lib/dockFormatters';
import { cn } from '@/lib/utils';
import { Briefcase } from 'lucide-react';
import CollapsibleCard from './CollapsibleCard';
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
import { buildBotLookup, getPositionBots, shortBotId } from '@/lib/botAttribution';
import { selectPositionStats } from '../../store/selectors';
import { useShallow } from 'zustand/react/shallow';

// ── Position Row ──────────────────────────────────────────────────
const PositionRow = React.memo(function PositionRow({ sym, pos, ownerBots = [] }) {
  const mark = useStore(state => state.tickerData[sym]?.price ?? pos.avg_price);
  const activeSymbol = useStore(state => state.activeSymbol);

  const handleClose = () => {
    sendAction(Action.PLACE_ORDER, {
      symbol: sym,
      type: 'MARKET',
      side: pos.size > 0 ? 'SELL' : 'BUY',
      quantity: Math.abs(pos.size),
    });
  };

  const uPnl = pos.size * (mark - pos.avg_price);
  const pct  = pos.avg_price > 0 ? ((mark - pos.avg_price) / pos.avg_price) * 100 : 0;
  const isLong = pos.size >= 0;
  const dec = priceDecimals(sym, Math.max(mark, pos.avg_price));
  const isActive = sym === activeSymbol;

  return (
    <DataTableRow rowVariant="dock" deferred className={cn(isActive && 'row-active')}>
      <DataTableCell>
        <span className={cn('font-bold', isActive ? 'text-primary' : 'text-foreground')}>{sym}</span>
        {ownerBots.length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {ownerBots.map((bot) => (
              <span key={bot.id} className="inline-flex items-center">
                <StrategyBadge strategy={bot.strategy} compact />
                <span
                  className={cn(
                    'ml-1 text-xs num-mono',
                    bot._active === false ? 'text-muted-foreground/60' : 'text-muted-foreground',
                  )}
                  title={bot.id}
                >
                  {shortBotId(bot.id)}
                  {bot._active === false && (
                    <span className="ml-0.5 uppercase tracking-wide opacity-80">stopped</span>
                  )}
                  {bot._size != null && (
                    <span className="ml-0.5 opacity-70">({Math.abs(bot._size).toFixed(3)})</span>
                  )}
                </span>
              </span>
            ))}
          </div>
        )}
        {(pos.stop_loss_price || pos.take_profit_price) && (
          <div className="mt-0.5 icon-label-tight text-[0.62rem] text-muted-foreground">
            {pos.stop_loss_price && (
              <span className="text-trading-down">SL:{pos.stop_loss_price.toFixed(dec)}</span>
            )}
            {pos.take_profit_price && (
              <span className="text-trading-up">TP:{pos.take_profit_price.toFixed(dec)}</span>
            )}
          </div>
        )}
      </DataTableCell>
      <DataTableCell>
        <Badge variant={isLong ? 'buy' : 'sell'}>{isLong ? 'LONG' : 'SHORT'}</Badge>
      </DataTableCell>
      <DataTableCell numeric align="right">
        {Math.abs(pos.size).toLocaleString(undefined, { minimumFractionDigits: 4 })}
      </DataTableCell>
      <DataTableCell numeric align="right">
        {pos.avg_price.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </DataTableCell>
      <DataTableCell numeric align="right">
        {mark.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
      </DataTableCell>
      <DataTableCell numeric align="right" className={cn('font-bold', uPnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {uPnl >= 0 ? '+' : ''}{fmtP(uPnl)}
      </DataTableCell>
      <DataTableCell numeric align="right" className={cn('font-semibold', pct >= 0 ? 'text-trading-up' : 'text-trading-down')}>
        {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
      </DataTableCell>
      <DataTableCell align="center">
        <Button variant="destructive" size="xs" onClick={handleClose} title={`Close ${sym} position`}>
          CLOSE
        </Button>
      </DataTableCell>
    </DataTableRow>
  );
});

// ── Positions Tab ─────────────────────────────────────────────────
export default function PositionsTab() {
  const positions = useStore((state) => state.positions);
  const stats = useStore(useShallow(selectPositionStats));
  const activeBots = useStore((state) => state.activeBots);
  const tradeHistory = useStore((state) => state.tradeHistory);
  const entries = Object.entries(positions);

  const botCtx = { activeBots, tradeHistory };
  const pnlPositive = stats.totalPnl >= 0;

  return (
    <div className="dock-panel-tab dock-panel-tab--positions h-full flex flex-col p-2 space-y-2 overflow-y-auto">
      <CollapsibleCard title="Open Positions" icon={Briefcase} badge={entries.length} className="flex-shrink-0" contentClassName="max-h-[600px] overflow-y-auto">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Briefcase size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Open Positions</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {entries.length} position{entries.length === 1 ? '' : 's'}
              {entries.length > 0 && (
                <> · {stats.longCount}L / {stats.shortCount}S</>
              )}
            </span>
          </div>
        </div>
        {entries.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Unrealized</span>
            <span
              className={cn(
                'dock-panel-tab__meta-value num-mono',
                pnlPositive ? 'dock-panel-tab__meta-value--up' : 'dock-panel-tab__meta-value--down',
              )}
            >
              {pnlPositive ? '+' : ''}${fmtP(stats.totalPnl)}
            </span>
          </div>
        )}
      </header>

      {entries.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={Briefcase} message="No open positions" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[880px]">
              <DataTableHeader>
                <tr className="border-b border-border hover:bg-transparent">
                  <DataTableHead>Symbol</DataTableHead>
                  <DataTableHead>Side</DataTableHead>
                  <DataTableHead align="right">Size</DataTableHead>
                  <DataTableHead align="right">Avg Entry</DataTableHead>
                  <DataTableHead align="right">Mark Price</DataTableHead>
                  <DataTableHead align="right">Unrealized P&L</DataTableHead>
                  <DataTableHead align="right">% Return</DataTableHead>
                  <DataTableHead align="center">Close</DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                {entries.map(([sym, pos]) => (
                  <PositionRow
                    key={sym}
                    sym={sym}
                    pos={pos}
                    ownerBots={getPositionBots(sym, pos, botCtx)}
                  />
                ))}
              </DataTableBody>
            </DataTableRoot>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>
              {entries.length} open · {stats.longCount} long · {stats.shortCount} short
            </span>
            <span className="dock-panel-tab__footer-highlight">
              Total unrealized:{' '}
              <span
                className={cn(
                  'num-mono font-bold',
                  pnlPositive ? 'text-trading-up' : 'text-trading-down',
                )}
              >
                {pnlPositive ? '+' : ''}${fmtP(stats.totalPnl)}
              </span>
            </span>
          </footer>
        </>
      )}
      </CollapsibleCard>
    </div>
  );
}

