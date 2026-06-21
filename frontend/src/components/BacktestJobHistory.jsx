/**
 * Recent backtest jobs — status, progress, resume polling, retry.
 */
import { useCallback, useEffect, useState } from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useStore } from '../store/useStore';
import { getStoreActions } from '../api/dispatch';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchBacktestJobs, fetchBacktestRun, watchBacktestJob } from '../api/endpoints';
import { withLlmModel } from '../api/endpoints';
import { toast } from 'sonner';

const STATUS_VARIANT = {
  pending: 'secondary',
  running: 'buy',
  completed: 'outline',
  failed: 'destructive',
  cancelled: 'secondary',
};

function fmtCreated(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export default function BacktestJobHistory() {
  const backtestRunning = useStore((s) => s.backtestRunning);
  const backtestJobId = useStore((s) => s.backtestJobId);
  const setBacktestLabOpen = useStore((s) => s.setBacktestLabOpen);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchBacktestJobs({ limit: 12 })
      .then(setJobs)
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, backtestRunning, backtestJobId]);

  const handleClick = async (job) => {
    const storeActions = getStoreActions();
    if (['pending', 'running'].includes(job.status)) {
      setBacktestLabOpen(true);
      watchBacktestJob(job.id, storeActions, { progress: job.progress });
      return;
    }
    if (job.status === 'completed' && job.run_id) {
      setBacktestLabOpen(true);
      try {
        await fetchBacktestRun(job.run_id, storeActions);
      } catch (_) {
        /* toast handled by caller if needed */
      }
      return;
    }
    if (job.status === 'completed' && job.results) {
      setBacktestLabOpen(true);
      storeActions.setBacktestResults({
        ...job.results,
        run_id: job.run_id ?? job.results.run_id,
      });
    }
  };

  const handleRetry = async (job, e) => {
    e.stopPropagation();
    if (backtestRunning) {
      toast.message('A backtest is already running');
      return;
    }
    const req = job.request || {};
    const action = req.sweep ? Action.RUN_BACKTEST_SWEEP : Action.RUN_BACKTEST;
    setBacktestLabOpen(true);
    const { ok, error } = await sendAction(action, withLlmModel(req));
    if (!ok && error) toast.error(error);
    else toast.message('Retrying backtest…');
  };

  return (
    <section className="algo-backtest-lab__section algo-backtest-lab__section--jobs mb-4">
      <p className="algo-backtest-table-scroll__caption mb-1.5">Recent jobs</p>
      {loading && jobs.length === 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          Loading jobs…
        </p>
      ) : jobs.length === 0 ? (
        <p className="text-xs text-muted-foreground">No backtest jobs yet.</p>
      ) : (
        <div className="algo-backtest-table-scroll algo-backtest-table-scroll--history">
          <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
            <thead>
              <tr>
                <th>When</th>
                <th>Symbol</th>
                <th>Status</th>
                <th>Progress</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const sym = job.request?.symbol ?? '—';
                const pct = job.progress?.pct;
                const isActive = job.id === backtestJobId;
                const canRetry = ['failed', 'cancelled'].includes(job.status);
                return (
                  <tr
                    key={job.id}
                    className={cn(
                      'cursor-pointer hover:bg-muted/40',
                      isActive && 'bg-primary/5',
                    )}
                    onClick={() => handleClick(job)}
                    title={
                      ['pending', 'running'].includes(job.status)
                        ? 'Resume watching job'
                        : job.run_id
                          ? 'Load run results'
                          : undefined
                    }
                  >
                    <td className="text-muted-foreground whitespace-nowrap">{fmtCreated(job.created_at)}</td>
                    <td className="whitespace-nowrap">{sym}</td>
                    <td>
                      <Badge variant={STATUS_VARIANT[job.status] ?? 'secondary'} className="text-[0.52rem]">
                        {job.status}
                      </Badge>
                    </td>
                    <td className="num-mono text-muted-foreground whitespace-nowrap">
                      {['pending', 'running'].includes(job.status)
                        ? (pct != null ? `${Math.round(pct)}%` : '…')
                        : job.error
                          ? 'failed'
                          : '—'}
                    </td>
                    <td className="text-right">
                      {canRetry && (
                        <Button
                          variant="ghost"
                          size="xs"
                          className="h-6 text-[0.58rem]"
                          onClick={(e) => handleRetry(job, e)}
                          title="Retry with same settings"
                        >
                          <RotateCcw className="size-3" data-icon="inline-start" />
                          Retry
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
