/**
 * Signal insight drawer (A2) — "why this signal?" from bot log metadata.
 */
import React, { useEffect, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useStore } from '../store/useStore';
import SubReportCards from './SubReportCards';
import { findInsightForLog } from '@/lib/botLogInsight';
import { formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { Lightbulb } from 'lucide-react';

export default function SignalInsightDrawer() {
  const [open, setOpen] = useState(false);
  const [logEntry, setLogEntry] = useState(null);
  const agentInsightHistory = useStore((s) => s.agentInsightHistory);

  useEffect(() => {
    const onOpen = (e) => {
      const entry = e.detail?.log;
      if (!entry) return;
      setLogEntry(entry);
      setOpen(true);
    };
    window.addEventListener('signal-insight-open', onOpen);
    return () => window.removeEventListener('signal-insight-open', onOpen);
  }, []);

  const meta = logEntry?.meta;
  const insight = logEntry ? findInsightForLog(logEntry, agentInsightHistory) : null;
  const symbol = meta?.symbol;
  const tf = meta?.timeframe;

  const focusChart = () => {
    if (meta?.bar_time != null) {
      window.dispatchEvent(new CustomEvent('backtest-focus-bar', {
        detail: { time: meta.bar_time, symbol },
      }));
    }
    if (symbol) {
      useStore.getState().setActiveSymbol(symbol);
    }
  };

  const openAnalyst = () => {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'analyst' }));
    window.dispatchEvent(new CustomEvent('insights-hub-open'));
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent side="right" className="terminal-sheet terminal-sheet--narrow w-full sm:max-w-md">
        <SheetHeader className="terminal-sheet__header">
          <SheetTitle className="flex items-center gap-2 text-sm">
            <Lightbulb className="size-4 text-primary" aria-hidden />
            Why this signal?
          </SheetTitle>
          <SheetDescription className="text-xs">
            {symbol && tf
              ? `${symbol} · ${formatBarTimeframeLabel(tf)}`
              : 'Signal context from bot log'}
          </SheetDescription>
        </SheetHeader>
        <div className="terminal-sheet__body terminal-sheet__scroll px-5 py-4 space-y-4">
          {logEntry?.message && (
            <p className="text-xs text-muted-foreground border-l-2 border-primary/40 pl-3">
              {logEntry.message}
            </p>
          )}
          {meta?.signal_id && (
            <p className="text-[0.62rem] text-muted-foreground font-mono truncate" title={meta.signal_id}>
              Signal {meta.signal_id}
            </p>
          )}
          {insight ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={insight.signal === 'BUY' ? 'buy' : insight.signal === 'SELL' ? 'sell' : 'secondary'}>
                  {insight.signal}
                </Badge>
                {insight.confidence != null && (
                  <span className="text-xs text-muted-foreground">
                    {Math.round(insight.confidence * 100)}% confidence
                  </span>
                )}
              </div>
              {insight.sub_reports ? (
                <SubReportCards subReports={insight.sub_reports} />
              ) : insight.reasons?.length > 0 ? (
                <ul className="bot-trade-explain__reasons text-xs">
                  {insight.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : null}
              {insight.narrative && (
                <p className="text-xs leading-relaxed">{insight.narrative}</p>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              No analyst insight cached for this signal bar.
              {meta?.bar_time != null ? ' Try opening the Analyst tab after the chart loads history.' : ''}
            </p>
          )}
          <div className="flex flex-wrap gap-2 pt-2">
            {meta?.bar_time != null && (
              <Button type="button" variant="outline" size="sm" className="h-7 text-xs" onClick={focusChart}>
                Focus chart bar
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" className="h-7 text-xs" onClick={openAnalyst}>
              Open Analyst
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
