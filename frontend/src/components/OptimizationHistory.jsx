/**
 * Saved optimization sessions — list + load into sweep results view.
 */
import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useStore } from '../store/useStore';
import { fetchOptimizationRuns, fetchOptimizationRun } from '../api/endpoints';
import { toast } from 'sonner';

const OBJECTIVE_LABELS = {
  total_pnl: 'PnL',
  sharpe_ratio: 'Sharpe',
  profit_factor: 'PF',
};

function fmtCreated(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export default function OptimizationHistory() {
  const backtestResults = useStore((s) => s.backtestResults);
  const setBacktestResults = useStore((s) => s.setBacktestResults);
  const backtestRunning = useStore((s) => s.backtestRunning);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchOptimizationRuns({ limit: 15 })
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, backtestRunning, backtestResults?.sweep]);

  const loadRun = async (run) => {
    try {
      const full = await fetchOptimizationRun(run.id);
      if (!full) return;
      const req = full.request ?? {};
      const wf = full.walk_forward ?? null;
      const merged = {
        ...(backtestResults ?? {}),
        sweep: {
          configs_tested: full.results?.length ?? 0,
          best_config: full.best_config,
          objective: full.objective,
          results: full.results ?? [],
        },
        meta: {
          ...(backtestResults?.meta ?? {}),
          symbol: full.symbol,
          strategy: full.strategy,
          sweep_objective: full.objective,
          walk_forward: Boolean(req.walk_forward),
          rolling_folds: req.rolling_folds ?? wf?.rolling_folds ?? 1,
          train_pct: req.train_pct ?? wf?.train_pct ?? 70,
        },
      };
      if (wf) {
        merged.walk_forward = wf;
      } else if (req.walk_forward) {
        merged.walk_forward = {
          train_pct: req.train_pct ?? 70,
          rolling_folds: req.rolling_folds ?? 1,
          best_config: full.best_config,
          in_sample: {},
          out_of_sample: {},
        };
      }
      setBacktestResults(merged);
      toast.success('Loaded optimization session');
    } catch (_) {
      toast.error('Could not load optimization session');
    }
  };

  return (
    <section className="algo-backtest-lab__section algo-backtest-lab__section--opt-history mt-4">
      <p className="algo-backtest-table-scroll__caption mb-1.5">Saved optimizations</p>
      {loading && runs.length === 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          Loading…
        </p>
      ) : runs.length === 0 ? (
        <p className="text-xs text-muted-foreground">No saved optimizations yet</p>
      ) : (
        <div className="algo-backtest-table-scroll algo-backtest-table-scroll--history">
          <table className="terminal-table algo-backtest-table m-0 text-xs">
            <thead>
              <tr>
                <th>When</th>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Objective</th>
                <th className="text-right">Combos</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.id}
                  className="cursor-pointer hover:bg-muted/40"
                  onClick={() => loadRun(run)}
                  title="Load sweep results"
                >
                  <td className="text-muted-foreground whitespace-nowrap">{fmtCreated(run.created_at)}</td>
                  <td className="whitespace-nowrap">{run.symbol}</td>
                  <td className="whitespace-nowrap">{run.strategy}</td>
                  <td>
                    <Badge variant="outline" className="h-4 px-1 text-xs">
                      {OBJECTIVE_LABELS[run.objective] ?? run.objective}
                    </Badge>
                  </td>
                  <td className="num-mono text-right">{run.results?.length ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
