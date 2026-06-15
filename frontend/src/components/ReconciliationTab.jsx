import React, { useCallback, useEffect } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from 'lucide-react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { WidgetEmpty } from './WidgetShell';

export default function ReconciliationTab() {
  const isLive = useStore(state => state.isLive);
  const ambiguousOrders = useStore(state => state.ambiguousOrders);
  const setAmbiguousOrders = useStore(state => state.setAmbiguousOrders);
  const terminalMode = useStore(state => state.terminalMode);

  const refresh = useCallback(() => {
    sendAction(Action.ADMIN_GET_RECONCILIATION, {});
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleAutoReconcile = () => {
    sendAction(Action.ADMIN_RECONCILE, {});
    setTimeout(refresh, 600);
  };

  const handleDismiss = (orderId) => {
    sendAction(Action.ADMIN_RESOLVE_AMBIGUOUS, {
      order_id: orderId,
      resolution: 'dismissed',
    });
    setAmbiguousOrders(ambiguousOrders.filter(o => o.id !== orderId));
  };

  const handleConfirmFilled = (orderId) => {
    sendAction(Action.ADMIN_RESOLVE_AMBIGUOUS, {
      order_id: orderId,
      resolution: 'confirmed_filled',
    });
    setAmbiguousOrders(ambiguousOrders.filter(o => o.id !== orderId));
  };

  if (!isLive) {
    return (
      <div className="dock-panel-tab">
        <header className="dock-panel-tab__toolbar">
          <div className="dock-panel-tab__toolbar-lead">
            <div className="dock-panel-tab__toolbar-icon" aria-hidden>
              <AlertTriangle size={14} />
            </div>
            <div className="dock-panel-tab__toolbar-copy">
              <span className="dock-panel-tab__toolbar-title">Reconciliation</span>
              <span className="dock-panel-tab__toolbar-subtitle">Live broker modes only</span>
            </div>
          </div>
        </header>
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={AlertTriangle}
            message="Reconciliation applies to live broker modes only. Switch to a live feed to track ambiguous orders."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="dock-panel-tab">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <AlertTriangle size={14} />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Reconciliation Center</span>
            <span className="dock-panel-tab__toolbar-subtitle">
              Ambiguous broker outcomes · {terminalMode}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions">
          <Badge variant={ambiguousOrders.length ? 'destructive' : 'secondary'}>
            {ambiguousOrders.length} pending
          </Badge>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={refresh}>
            <RefreshCw data-icon="inline-start" aria-hidden />
            Refresh
          </Button>
          <Button variant="default" size="sm" className="h-7 text-xs" onClick={handleAutoReconcile}>
            <CheckCircle2 data-icon="inline-start" aria-hidden />
            Auto-match
          </Button>
        </div>
      </header>

      <Alert className="dock-panel-tab__alert">
        <AlertDescription className="text-xs leading-relaxed">
          Ambiguous orders had unknown broker outcomes (timeout, 5xx). Never resend automatically —
          reconcile against {terminalMode} positions, then mark resolved.
        </AlertDescription>
      </Alert>

      {ambiguousOrders.length === 0 ? (
        <div className="dock-panel-tab__empty">
          <WidgetEmpty
            icon={CheckCircle2}
            message="No ambiguous orders pending review."
          />
        </div>
      ) : (
        <>
          <div className="dock-panel-tab__table-wrap scroll-panel-y scroll-panel-y-0">
            <table className="terminal-table dock-panel-tab__table min-w-[720px]">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="text-right">Qty</th>
                  <th>Bot</th>
                  <th>Message</th>
                  <th className="text-center">Actions</th>
                </tr>
              </thead>
              <tbody>
                {ambiguousOrders.map(row => (
                  <tr key={row.id}>
                    <td className="whitespace-nowrap font-mono text-[0.68rem] text-muted-foreground">
                      {row.created_at?.slice(0, 19) || '—'}
                    </td>
                    <td className="font-bold">{row.symbol}</td>
                    <td>
                      <Badge variant={row.side === 'BUY' ? 'buy' : 'sell'}>{row.side}</Badge>
                    </td>
                    <td className="num-mono text-right">{row.quantity}</td>
                    <td className="max-w-[80px] truncate font-mono text-[0.65rem] text-muted-foreground">
                      {row.bot_id?.slice(0, 8) || '—'}
                    </td>
                    <td className="max-w-[200px] truncate text-muted-foreground" title={row.message}>
                      {row.message}
                    </td>
                    <td className="text-center">
                      <div className="flex justify-center gap-1">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-6 px-2 text-[0.65rem]"
                          onClick={() => handleConfirmFilled(row.id)}
                        >
                          Filled
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-[0.65rem] text-muted-foreground"
                          onClick={() => handleDismiss(row.id)}
                        >
                          <XCircle data-icon="inline-start" aria-hidden />
                          Dismiss
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <footer className="dock-panel-tab__footer">
            <span>{ambiguousOrders.length} order{ambiguousOrders.length === 1 ? '' : 's'} need review</span>
            <span className="dock-panel-tab__footer-highlight">
              Use Auto-match to reconcile against live positions
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
