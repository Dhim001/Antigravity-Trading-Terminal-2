import { useCallback, useEffect, useState } from 'react';
import { Check, Loader2, ShieldQuestion, X } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  approveAgentAction,
  fetchAgentActions,
  rejectAgentAction,
} from '../../api/endpoints';

const POLL_MS = 10_000;

/**
 * HITL desk queue — pending proposals from the silent autonomous actors
 * (Risk Sentinel, Regime Rotation, Alpha Decay, Scanner Deploy, Post-Trade).
 * Renders nothing when the queue is empty.
 */
export default function AgentActionsPanel() {
  const [actions, setActions] = useState([]);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    fetchAgentActions({ status: 'pending' })
      .then((rows) => setActions(Array.isArray(rows) ? rows : []))
      .catch(() => {
        // Advisory panel — keep the last good list on transient failures.
      });
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const decide = useCallback(
    async (action, approve) => {
      if (!action?.id || busyId) return;
      setBusyId(action.id);
      try {
        if (approve) {
          await approveAgentAction(action.id);
          toast.success(`Approved ${action.action_type} (#${action.id})`);
        } else {
          await rejectAgentAction(action.id);
          toast.message(`Rejected ${action.action_type} (#${action.id})`);
        }
        setActions((prev) => prev.filter((a) => a.id !== action.id));
      } catch (err) {
        toast.error(err?.message || 'Action failed');
      } finally {
        setBusyId(null);
      }
    },
    [busyId],
  );

  if (actions.length === 0) return null;

  return (
    <section className="algo-tab__panel" aria-label="Pending agent actions">
      <header className="algo-tab__panel-header">
        <span className="flex items-center gap-1.5">
          <ShieldQuestion className="size-3.5 text-amber-500" />
          Agent approvals
        </span>
        <Badge variant="secondary" className="num-mono h-5 px-1.5 text-[10px]">
          {actions.length}
        </Badge>
      </header>
      <div className="flex flex-col gap-2 p-2">
        {actions.map((a) => (
          <div
            key={a.id}
            className="rounded-md border border-border/60 bg-muted/30 px-2.5 py-2 text-xs"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-foreground">
                {a.actor} · <code className="text-[11px]">{a.action_type}</code>
              </span>
              <span className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="default"
                  className="h-6 gap-1 px-2 text-[11px]"
                  disabled={busyId === a.id}
                  onClick={() => decide(a, true)}
                >
                  {busyId === a.id ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Check className="size-3" />
                  )}
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 gap-1 px-2 text-[11px]"
                  disabled={busyId === a.id}
                  onClick={() => decide(a, false)}
                >
                  <X className="size-3" />
                  Reject
                </Button>
              </span>
            </div>
            {a.reason ? (
              <p className="mt-1 text-muted-foreground">{a.reason}</p>
            ) : null}
            {a.params && Object.keys(a.params).length > 0 ? (
              <p className="mt-0.5 truncate text-[10px] text-muted-foreground/70">
                {Object.entries(a.params)
                  .slice(0, 4)
                  .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                  .join(' · ')}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
