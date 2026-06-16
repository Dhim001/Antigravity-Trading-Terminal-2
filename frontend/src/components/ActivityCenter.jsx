/**
 * ActivityCenter — unified alerts, bot events, stale warnings (UX-7).
 */
import { useMemo } from 'react';
import { useStore } from '../store/useStore';
import { useSettingsStore } from '../store/useSettingsStore';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Badge } from '@/components/ui/badge';
import { Bell, Bot, AlertTriangle, WifiOff } from 'lucide-react';
import { cn } from '@/lib/utils';

function timeLabel(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export default function ActivityCenter({ open, onOpenChange }) {
  const connectionStatus = useStore((s) => s.connectionStatus);
  const apiStatus = useStore((s) => s.apiStatus);
  const ambiguousOrders = useStore((s) => s.ambiguousOrders);
  const activeBots = useStore((s) => s.activeBots);
  const botLogs = useStore((s) => s.botLogs);
  const alerts = useSettingsStore((s) => s.settings.alerts || []);

  const items = useMemo(() => {
    const list = [];
    if (connectionStatus !== 'connected') {
      list.push({
        id: 'ws-stale',
        kind: 'warn',
        icon: WifiOff,
        title: apiStatus === 'ready' ? 'REST fallback active' : 'Backend unreachable',
        detail: 'Live prices may be stale until WebSocket reconnects.',
        ts: Date.now(),
      });
    }
    for (const o of ambiguousOrders.slice(0, 5)) {
      list.push({
        id: `ambig-${o.id}`,
        kind: 'warn',
        icon: AlertTriangle,
        title: `Ambiguous order ${o.symbol}`,
        detail: o.message || 'Needs reconciliation',
        ts: Date.now(),
      });
    }
    for (const b of activeBots.filter((x) => x.status === 'RUNNING').slice(0, 5)) {
      list.push({
        id: `bot-${b.id}`,
        kind: 'info',
        icon: Bot,
        title: `Bot running · ${b.symbol}`,
        detail: `${b.strategy || 'strategy'} · ${b.status}`,
        ts: Date.now(),
      });
    }
    for (const log of (botLogs || []).slice(0, 8)) {
      list.push({
        id: `log-${log.ts}-${log.message?.slice(0, 12)}`,
        kind: 'info',
        icon: Bot,
        title: log.message?.slice(0, 60) || 'Bot log',
        detail: log.level || 'info',
        ts: log.ts || Date.now(),
      });
    }
    for (const a of alerts.filter((x) => x.enabled !== false)) {
      list.push({
        id: `alert-${a.id}`,
        kind: 'alert',
        icon: Bell,
        title: `Alert · ${a.symbol}`,
        detail: `${a.type}${a.threshold != null ? ` ${a.threshold}` : ''}${a.signal ? ` → ${a.signal}` : ''}`,
        ts: Date.now(),
      });
    }
    return list.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  }, [connectionStatus, apiStatus, ambiguousOrders, activeBots, botLogs, alerts]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="activity-center w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="text-sm">Activity Center</SheetTitle>
          <SheetDescription className="text-xs">
            Alerts, bot events, and connection status
          </SheetDescription>
        </SheetHeader>
        <ul className="activity-center__list mt-4 space-y-2">
          {items.length === 0 ? (
            <li className="text-xs text-muted-foreground py-8 text-center">No recent activity</li>
          ) : items.map((item) => {
            const Icon = item.icon;
            return (
              <li
                key={item.id}
                className={cn(
                  'activity-center__item flex gap-2 rounded-lg border border-border/50 p-2.5 text-xs',
                  item.kind === 'warn' && 'border-trading-warn/30 bg-trading-warn/5',
                )}
              >
                <Icon size={14} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium truncate">{item.title}</span>
                    <span className="num-mono text-[0.58rem] text-muted-foreground shrink-0">
                      {timeLabel(item.ts)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-muted-foreground truncate">{item.detail}</p>
                </div>
              </li>
            );
          })}
        </ul>
        {alerts.length > 0 && (
          <p className="mt-4 text-[0.58rem] text-muted-foreground">
            {alerts.length} alert rule{alerts.length === 1 ? '' : 's'} configured in Settings → Layout
          </p>
        )}
      </SheetContent>
    </Sheet>
  );
}
