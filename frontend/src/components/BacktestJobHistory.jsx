/**
 * Backtest Library — searchable list of all past runs with metrics,
 * A/B compare selection, and one-click result loading.
 */
import { useCallback, useEffect, useState, useMemo } from 'react';
import { Loader2, RotateCcw, GitCompare, Pin, Search, ArrowUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { openBacktestLabJobs, openBacktestLabResults } from '../lib/backtestLab';
import { useResearchStore } from '../store/useResearchStore';
import { getStoreActions } from '../api/dispatch';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { fetchBacktestJobs, fetchBacktestRun, watchBacktestJob } from '../api/endpoints';
import { withLlmModel } from '../api/endpoints';
import { toast } from 'sonner';
import BacktestJobCompare from './BacktestJobCompare';

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

function extractMetrics(job) {
  const res = job.results || {};
  const summary = res.summary || {};
  return {
    pnl: res.total_pnl ?? summary.total_pnl ?? null,
    sharpe: summary.sharpe_ratio ?? null,
    winRate: summary.win_rate ?? null,
    trades: res.trade_count ?? summary.total_trades ?? null,
    maxDD: summary.max_drawdown ?? null,
    strategy: job.request?.strategy || '—',
  };
}

export default function BacktestJobHistory() {
  const backtestRunning = useResearchStore((s) => s.backtestRunning);
  const backtestJobId = useResearchStore((s) => s.backtestJobId);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState('created_at');
  const [sortDir, setSortDir] = useState('desc');
  const [compareIds, setCompareIds] = useState([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [pinned, setPinned] = useState(() => {
    try { return JSON.parse(localStorage.getItem('bt_pinned') || '[]'); } catch { return []; }
  });

  const refresh = useCallback(() => {
    setLoading(true);
    fetchBacktestJobs({ limit: 50 })
      .then(setJobs)
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh, backtestRunning, backtestJobId]);

  // Persist pins
  useEffect(() => {
    try { localStorage.setItem('bt_pinned', JSON.stringify(pinned)); } catch { /* noop */ }
  }, [pinned]);

  const togglePin = (id) => {
    setPinned((prev) => prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]);
  };

  const toggleCompare = (id) => {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((c) => c !== id);
      if (prev.length >= 2) { toast.message('Select up to 2 runs to compare'); return prev; }
      return [...prev, id];
    });
  };

  const filtered = useMemo(() => {
    let list = [...jobs];
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((j) => {
        const sym = (j.request?.symbol || '').toLowerCase();
        const strat = (j.request?.strategy || '').toLowerCase();
        return sym.includes(q) || strat.includes(q) || (j.id || '').includes(q);
      });
    }
    // Pre-compute metrics once per job (avoid O(N² log N) re-extraction in sort)
    const metricsMap = new Map();
    list.forEach((j) => { metricsMap.set(j.id, extractMetrics(j)); });

    // Pin to top, then sort by selected key
    list.sort((a, b) => {
      const ap = pinned.includes(a.id) ? 1 : 0;
      const bp = pinned.includes(b.id) ? 1 : 0;
      if (ap !== bp) return bp - ap;

      const am = metricsMap.get(a.id);
      const bm = metricsMap.get(b.id);
      let av, bv;
      if (sortKey === 'pnl') { av = am.pnl ?? -1e18; bv = bm.pnl ?? -1e18; }
      else if (sortKey === 'sharpe') { av = am.sharpe ?? -1e18; bv = bm.sharpe ?? -1e18; }
      else if (sortKey === 'trades') { av = am.trades ?? 0; bv = bm.trades ?? 0; }
      else { av = a.created_at || ''; bv = b.created_at || ''; }
      if (av < bv) return sortDir === 'asc' ? -1 : 1;
      if (av > bv) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });
    return list;
  }, [jobs, search, pinned, sortKey, sortDir]);

  const compareJobs = useMemo(
    () => compareIds.map((id) => jobs.find((j) => j.id === id)).filter(Boolean),
    [compareIds, jobs],
  );

  const handleSort = (key) => {
    if (sortKey === key) setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const handleClick = async (job) => {
    const storeActions = getStoreActions();
    if (['pending', 'running'].includes(job.status)) {
      openBacktestLabJobs();
      watchBacktestJob(job.id, storeActions, { progress: job.progress });
      return;
    }
    if (job.status === 'completed' && job.run_id) {
      openBacktestLabResults();
      try {
        await fetchBacktestRun(job.run_id, storeActions);
      } catch (_) { /* toast handled by caller if needed */ }
      return;
    }
    if (job.status === 'completed' && job.results) {
      openBacktestLabResults();
      storeActions.setBacktestResults({
        ...job.results,
        run_id: job.run_id ?? job.results.run_id,
      });
    }
  };

  const handleRetry = async (job, e) => {
    e.stopPropagation();
    if (backtestRunning) { toast.message('A backtest is already running'); return; }
    const req = job.request || {};
    const action = req.sweep ? Action.RUN_BACKTEST_SWEEP : Action.RUN_BACKTEST;
    openBacktestLabJobs();
    const { ok, error } = await sendAction(action, withLlmModel(req));
    if (!ok && error) toast.error(error);
    else toast.message('Retrying backtest…');
  };

  const SortBtn = ({ col, children }) => (
    <button
      className="inline-flex items-center gap-0.5 hover:text-foreground transition-colors"
      onClick={() => handleSort(col)}
    >
      {children}
      <ArrowUpDown className={cn('size-2.5', sortKey === col && 'text-primary')} />
    </button>
  );

  return (
    <section className="algo-backtest-lab__section algo-backtest-lab__section--jobs">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <p className="algo-backtest-table-scroll__caption">Backtest library</p>
        <div className="flex items-center gap-1.5">
          {compareIds.length === 2 && (
            <Button
              variant="outline"
              size="xs"
              className="h-6 text-[0.58rem]"
              onClick={() => setCompareOpen(true)}
            >
              <GitCompare className="size-3 mr-0.5" />
              Compare ({compareIds.length})
            </Button>
          )}
          <div className="relative">
            <Search className="size-3 absolute left-1.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
            <Input
              className="h-6 text-[0.58rem] pl-5 w-28"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </div>

      {loading && jobs.length === 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          Loading runs…
        </p>
      ) : filtered.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {search ? 'No matching runs.' : 'No backtest jobs yet.'}
        </p>
      ) : (
        <div className="algo-backtest-table-scroll algo-backtest-table-scroll--jobs-library">
          <table className="terminal-table algo-backtest-table m-0 text-[0.58rem]">
            <thead>
              <tr>
                <th className="w-4" />
                <th><SortBtn col="created_at">When</SortBtn></th>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Status</th>
                <th className="text-right"><SortBtn col="pnl">PnL</SortBtn></th>
                <th className="text-right"><SortBtn col="sharpe">Sharpe</SortBtn></th>
                <th className="text-right"><SortBtn col="trades">Trades</SortBtn></th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((job) => {
                const sym = job.request?.symbol ?? '—';
                const m = extractMetrics(job);
                const isActive = job.id === backtestJobId;
                const isPinned = pinned.includes(job.id);
                const isCompared = compareIds.includes(job.id);
                const canRetry = ['failed', 'cancelled'].includes(job.status);
                const isCompleted = job.status === 'completed';

                return (
                  <tr
                    key={job.id}
                    className={cn(
                      'cursor-pointer hover:bg-muted/40',
                      isActive && 'bg-primary/5',
                      isPinned && 'bg-amber-500/5',
                      isCompared && 'ring-1 ring-primary/30',
                    )}
                    onClick={() => handleClick(job)}
                  >
                    <td className="text-center">
                      <button
                        className={cn(
                          'p-0 bg-transparent border-0 cursor-pointer',
                          isPinned ? 'text-amber-400' : 'text-muted-foreground/40 hover:text-muted-foreground',
                        )}
                        onClick={(e) => { e.stopPropagation(); togglePin(job.id); }}
                        title={isPinned ? 'Unpin' : 'Pin as reference'}
                      >
                        <Pin className="size-3" />
                      </button>
                    </td>
                    <td className="text-muted-foreground whitespace-nowrap">{fmtCreated(job.created_at)}</td>
                    <td className="whitespace-nowrap font-medium">{sym}</td>
                    <td className="text-muted-foreground whitespace-nowrap">{m.strategy}</td>
                    <td>
                      <Badge variant={STATUS_VARIANT[job.status] ?? 'secondary'} className="text-[0.52rem]">
                        {job.status}
                      </Badge>
                    </td>
                    <td className={cn(
                      'text-right num-mono whitespace-nowrap',
                      m.pnl != null && (m.pnl >= 0 ? 'text-trading-up' : 'text-trading-down'),
                    )}>
                      {m.pnl != null ? `$${Number(m.pnl).toFixed(2)}` : '—'}
                    </td>
                    <td className="text-right num-mono whitespace-nowrap">
                      {m.sharpe != null ? Number(m.sharpe).toFixed(2) : '—'}
                    </td>
                    <td className="text-right num-mono">{m.trades ?? '—'}</td>
                    <td className="text-right">
                      <div className="inline-flex gap-0.5">
                        {isCompleted && (
                          <button
                            className={cn(
                              'p-0.5 bg-transparent border-0 cursor-pointer rounded',
                              isCompared ? 'text-primary' : 'text-muted-foreground/50 hover:text-muted-foreground',
                            )}
                            onClick={(e) => { e.stopPropagation(); toggleCompare(job.id); }}
                            title={isCompared ? 'Remove from compare' : 'Add to A/B compare'}
                          >
                            <GitCompare className="size-3" />
                          </button>
                        )}
                        {canRetry && (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="h-5 text-[0.52rem] px-1"
                            onClick={(e) => handleRetry(job, e)}
                            title="Retry with same settings"
                          >
                            <RotateCcw className="size-2.5" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <BacktestJobCompare
        jobs={compareJobs}
        open={compareOpen}
        onOpenChange={setCompareOpen}
      />
    </section>
  );
}
