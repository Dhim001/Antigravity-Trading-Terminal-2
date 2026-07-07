/**
 * Saved optimization sessions — list, load, compare (Tier 4).
 */
import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { useStore } from '../store/useStore';
import { fetchOptimizationRuns, fetchOptimizationRun } from '../api/endpoints';
import OptimizationRunCompare from './OptimizationRunCompare';
import { toast } from 'sonner';

const OBJECTIVE_LABELS = {
  total_pnl: 'PnL',
  sharpe_ratio: 'Sharpe',
  calmar_ratio: 'Calmar',
  profit_factor: 'PF',
  max_drawdown_penalty: 'PnL−DD',
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
  const [compareIds, setCompareIds] = useState([]);
  const [compareRuns, setCompareRuns] = useState([null, null]);
  const [compareLoading, setCompareLoading] = useState(false);

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

  const toggleCompare = async (run) => {
    const id = run.id;
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  };

  useEffect(() => {
    if (compareIds.length !== 2) {
      setCompareRuns([null, null]);
      return;
    }
    let cancelled = false;
    setCompareLoading(true);
    Promise.all(compareIds.map((id) => fetchOptimizationRun(id)))
      .then((pair) => {
        if (!cancelled) setCompareRuns(pair);
      })
      .catch(() => {
        if (!cancelled) toast.error('Could not load runs for compare');
      })
      .finally(() => {
        if (!cancelled) setCompareLoading(false);
      });
    return () => { cancelled = true; };
  }, [compareIds]);

  return (
    <section className="algo-backtest-lab__section algo-backtest-lab__section--opt-history mt-4">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <p className="algo-backtest-table-scroll__caption m-0">Saved optimizations</p>
        {compareIds.length > 0 && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            className="h-6 text-xs"
            onClick={() => setCompareIds([])}
          >
            Clear compare ({compareIds.length}/2)
          </Button>
        )}
      </div>
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
                <th className="w-8" />
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
                  className="hover:bg-muted/40"
                >
                  <td onClick={(e) => e.stopPropagation()}>
                    <Checkbox
                      checked={compareIds.includes(run.id)}
                      onCheckedChange={() => toggleCompare(run)}
                      aria-label={`Compare ${run.symbol}`}
                    />
                  </td>
                  <td
                    className="text-muted-foreground whitespace-nowrap cursor-pointer"
                    onClick={() => loadRun(run)}
                    title="Load sweep results"
                  >
                    {fmtCreated(run.created_at)}
                  </td>
                  <td className="whitespace-nowrap cursor-pointer" onClick={() => loadRun(run)}>{run.symbol}</td>
                  <td className="whitespace-nowrap cursor-pointer" onClick={() => loadRun(run)}>{run.strategy}</td>
                  <td className="cursor-pointer" onClick={() => loadRun(run)}>
                    <Badge variant="outline" className="h-4 px-1 text-xs">
                      {OBJECTIVE_LABELS[run.objective] ?? run.objective}
                    </Badge>
                  </td>
                  <td className="num-mono text-right cursor-pointer" onClick={() => loadRun(run)}>
                    {run.results?.length ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {compareLoading && (
        <p className="text-xs text-muted-foreground mt-2 flex items-center gap-1">
          <Loader2 size={12} className="animate-spin" /> Loading compare…
        </p>
      )}
      {compareRuns[0] && compareRuns[1] && !compareLoading && (
        <OptimizationRunCompare
          left={compareRuns[0]}
          right={compareRuns[1]}
          onClose={() => setCompareIds([])}
        />
      )}
    </section>
  );
}
