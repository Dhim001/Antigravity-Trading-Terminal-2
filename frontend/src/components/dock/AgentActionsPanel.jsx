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
      const actionId = action?.id ?? action?.action_id;
      if (actionId == null || actionId === '' || busyId) return;
      setBusyId(actionId);
      try {
        const body = approve
          ? await approveAgentAction(actionId)
          : await rejectAgentAction(actionId);
        if (!body?.ok) {
          throw new Error(body?.error || (approve ? 'Approve failed' : 'Reject failed'));
        }
        if (approve) {
          toast.success(`Approved ${action.action_type} (#${actionId})`);
        } else {
          toast.message(`Rejected ${action.action_type} (#${actionId})`);
        }
        setActions((prev) => prev.filter((a) => (a.id ?? a.action_id) !== actionId));
      } catch (err) {
        toast.error(err?.message || 'Action failed');
      } finally {
        setBusyId(null);
        load();
      }
    },
    [busyId, load],
  );

  if (actions.length === 0) return null;

  return (
    <section className="algo-tab__panel algo-tab__panel--approvals" aria-label="Pending agent actions">
      <header className="algo-tab__panel-header">
        <div className="algo-tab__panel-heading">
          <div className="algo-tab__panel-title">
            <ShieldQuestion size={13} className="text-amber-500" aria-hidden />
            Agent approvals
          </div>
          <span className="algo-tab__panel-subtitle">HITL queue</span>
        </div>
        <Badge variant="secondary" className="num-mono h-5 shrink-0 px-1.5 text-[10px]">
          {actions.length}
        </Badge>
      </header>
      <div className="algo-approvals-list">
        {actions.map((a) => (
          <article key={a.id} className="algo-approval-card">
            <div className="algo-approval-card__head">
              <div className="algo-approval-card__who">
                <span className="algo-approval-card__actor">{a.actor}</span>
                <span className="algo-approval-card__type">{a.action_type}</span>
              </div>
              <div className="algo-approval-card__actions">
                <Button
                  type="button"
                  size="xs"
                  variant="default"
                  className="algo-approval-card__approve"
                  disabled={busyId != null}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    decide(a, true);
                  }}
                >
                  {busyId === (a.id ?? a.action_id) ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Check className="size-3" />
                  )}
                  Approve
                </Button>
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  className="algo-approval-card__reject"
                  disabled={busyId != null}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    decide(a, false);
                  }}
                >
                  <X className="size-3" />
                  Reject
                </Button>
              </div>
            </div>
            {a.reason ? (
              <p className="algo-approval-card__reason">{a.reason}</p>
            ) : null}
            {a.params && Object.keys(a.params).length > 0 ? (
              <p className="algo-approval-card__meta">
                {Object.entries(a.params)
                  .slice(0, 4)
                  .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                  .join(' · ')}
              </p>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
