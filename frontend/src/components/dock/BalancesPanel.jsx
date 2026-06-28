/**
 * BalancesPanel.jsx — Balances dock tab (extracted from ResizableDock).
 *
 * Displays account balances with per-asset USD mark values, total equity,
 * cash available/locked breakdown, and holdings summary.
 */
import React, { useMemo } from 'react';
import { useStore } from '../../store/useStore';
import { fmtP, assetFromSymbol, buildBalanceView, QUOTE_ASSETS } from '../../lib/dockFormatters';
import { cn } from '@/lib/utils';
import { Landmark } from 'lucide-react';
import {
  DataTableRoot,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '../DataTableShell';
import { WidgetEmpty } from '../WidgetShell';
import { useShallow } from 'zustand/react/shallow';

export default function BalancesTab() {
  const balances = useStore((state) => state.balances);
  const assetMark = useStore(useShallow((state) => {
    const map = {};
    for (const sym of state.symbolsList || []) {
      const price = state.tickerData[sym]?.price;
      if (price == null) continue;
      const asset = assetFromSymbol(sym);
      map[asset] = Math.round(price * 100) / 100;
    }
    return map;
  }));

  const { rows, stats } = useMemo(
    () => buildBalanceView(balances, assetMark),
    [balances, assetMark],
  );

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <Landmark size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Account Balances</span>
            <span className="dock-panel-tab__toolbar-subtitle num-mono">
              {rows.length} asset{rows.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>
        {rows.length > 0 && (
          <div className="dock-panel-tab__toolbar-meta">
            <span className="dock-panel-tab__meta-label">Total equity</span>
            <span className="dock-panel-tab__meta-value num-mono">
              ${fmtP(stats.totalEquity)}
            </span>
          </div>
        )}
      </header>

      {rows.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty icon={Landmark} message="Loading balances…" />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <DataTableRoot variant="dock" className="dock-panel-tab__table min-w-[560px]">
              <DataTableHeader>
                <tr>
                  <DataTableHead>Asset</DataTableHead>
                  <DataTableHead align="right">Total Balance</DataTableHead>
                  <DataTableHead align="right">Locked</DataTableHead>
                  <DataTableHead align="right">Available</DataTableHead>
                  <DataTableHead align="right">USD Value</DataTableHead>
                </tr>
              </DataTableHeader>
              <DataTableBody>
                {rows.map(({ asset, bal, avail, usdValue, isQuote }) => {
                  const dec = isQuote ? 2 : 6;
                  return (
                    <DataTableRow key={asset} rowVariant="dock" deferred>
                      <DataTableCell className="font-bold">{asset}</DataTableCell>
                      <DataTableCell numeric align="right">
                        {bal.balance.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </DataTableCell>
                      <DataTableCell numeric align="right" className="text-muted-foreground">
                        {bal.locked.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </DataTableCell>
                      <DataTableCell
                        numeric
                        align="right"
                        className={cn('font-bold', avail > 0 ? 'text-foreground' : 'text-muted-foreground')}
                      >
                        {avail.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })}
                      </DataTableCell>
                      <DataTableCell numeric align="right" className="text-secondary-foreground">
                        {usdValue != null ? `$${fmtP(usdValue)}` : '—'}
                      </DataTableCell>
                    </DataTableRow>
                  );
                })}
              </DataTableBody>
            </DataTableRoot>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>{rows.length} assets · cash + holdings</span>
            <span className="dock-panel-tab__footer-highlight">
              Cash available:{' '}
              <span className="num-mono font-bold">${fmtP(stats.cashAvailable)}</span>
              {stats.cashLocked > 0 && (
                <span className="text-muted-foreground">
                  {' '}· locked ${fmtP(stats.cashLocked)}
                </span>
              )}
              {stats.holdingsUsd > 0 && (
                <span className="text-muted-foreground">
                  {' '}· holdings ${fmtP(stats.holdingsUsd)}
                </span>
              )}
              {' '}· total ${fmtP(stats.totalEquity)}
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
