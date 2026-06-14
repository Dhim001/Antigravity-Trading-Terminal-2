import React, { useCallback, useEffect } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from 'lucide-react';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';

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
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        Reconciliation applies to live broker modes only. Switch to a live feed to track ambiguous orders.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden p-3">
      <header className="flex shrink-0 items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <AlertTriangle size={16} className="text-trading-warn" aria-hidden />
          <h2 className="text-sm font-semibold">Reconciliation Center</h2>
          <Badge variant={ambiguousOrders.length ? 'destructive' : 'secondary'}>
            {ambiguousOrders.length} pending
          </Badge>
        </div>
        <div className="flex gap-1">
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

      <Alert className="shrink-0 border-trading-warn/30 bg-trading-warn/5">
        <AlertDescription className="text-xs leading-relaxed">
          Ambiguous orders had unknown broker outcomes (timeout, 5xx). Never resend automatically —
          reconcile against {terminalMode} positions, then mark resolved.
        </AlertDescription>
      </Alert>

      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border">
        {ambiguousOrders.length === 0 ? (
          <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
            <CheckCircle2 className="size-8 text-trading-up opacity-80" aria-hidden />
            No ambiguous orders pending review.
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-muted/80 backdrop-blur-sm">
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Symbol</th>
                <th className="px-3 py-2 font-medium">Side</th>
                <th className="px-3 py-2 font-medium">Qty</th>
                <th className="px-3 py-2 font-medium">Bot</th>
                <th className="px-3 py-2 font-medium">Message</th>
                <th className="px-3 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {ambiguousOrders.map(row => (
                <tr key={row.id} className="border-b border-border/60 hover:bg-muted/30">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-[0.68rem] text-muted-foreground">
                    {row.created_at?.slice(0, 19) || '—'}
                  </td>
                  <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                  <td className="px-3 py-2">
                    <Badge variant={row.side === 'BUY' ? 'buy' : 'sell'}>{row.side}</Badge>
                  </td>
                  <td className="px-3 py-2 font-mono">{row.quantity}</td>
                  <td className="max-w-[80px] truncate px-3 py-2 font-mono text-[0.65rem] text-muted-foreground">
                    {row.bot_id?.slice(0, 8) || '—'}
                  </td>
                  <td className="max-w-[200px] truncate px-3 py-2 text-muted-foreground" title={row.message}>
                    {row.message}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
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
        )}
      </div>
    </div>
  );
}
