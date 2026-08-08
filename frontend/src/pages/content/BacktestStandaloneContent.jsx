import { Suspense, useEffect, useMemo, useState } from 'react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Activity, Loader2, PanelLeft } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStore } from '../../store/useStore';
import { useResearchStore } from '../../store/useResearchStore';
import BacktestProgressBar from '../../components/BacktestProgressBar';
import ErrorBoundary from '../../components/ErrorBoundary';
import { lazyImport } from '../../lib/lazyImport';
import { getStrategyCategory, isMlStrategy } from '../../config/strategies';
import { fetchBacktestRun } from '../../api/endpoints';
import { runBootstrap } from '../../api/bootstrap';
import { subscribeStandaloneEvents } from '../../lib/standalonePanels';
import {
  isHoldoutBacktestDays,
  resolveHoldoutDaysFromStatus,
  resolveMlBacktestDaysPayload,
} from '@/lib/mlBacktestRange';
import { getCachedModelStatus } from '@/lib/mlTrainingSession';
import { toast } from 'sonner';

const BacktestResultsPanel = lazyImport(() => import('../../components/BacktestResultsPanel'), 'backtest-results');
const BacktestSweepPanel = lazyImport(() => import('../../components/BacktestSweepPanel'), 'backtest-sweep');
const BacktestJobHistory = lazyImport(() => import('../../components/BacktestJobHistory'), 'backtest-jobs');

const LAB_TABS = [
  { id: 'results', label: 'Results' },
  { id: 'optimizer', label: 'Optimizer' },
  { id: 'jobs', label: 'Jobs' },
];

const LAB_DESCRIPTIONS = {
  normal: 'Strategy replay report — equity, trades, optimizer, and run history',
  ml: 'ML model backtest — predictions, feature importance, walk-forward validation',
  agent: 'Agent backtest — reasoning analysis, gate tuning, confidence calibration',
};

function LabPanelFallback() {
  return <p className="backtest-lab__loading px-3 pt-2">Loading panel…</p>;
}

function readDetachParams() {
  try {
    const q = new URLSearchParams(window.location.search);
    return {
      runId: q.get('runId') || null,
      labTab: q.get('labTab') || null,
    };
  } catch {
    return { runId: null, labTab: null };
  }
}

/**
 * Full-page Backtest Lab — same chrome/CSS as the Algo-panel sheet.
 * Hydrates from ?runId= when Detach / header opens this window.
 */
export default function BacktestStandaloneContent({ onReattach }) {
  const labTab = useResearchStore((s) => s.backtestLabTab);
  const setBacktestLabTab = useResearchStore((s) => s.setBacktestLabTab);
  const backtestResults = useResearchStore((s) => s.backtestResults);
  const setBacktestResults = useResearchStore((s) => s.setBacktestResults);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const backtestRuns = useResearchStore((s) => s.backtestRuns);
  const backtestRunning = useResearchStore((s) => s.backtestRunning);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const backtestSnapshot = useResearchStore((s) => s.backtestSnapshot);
  const backtestDays = useResearchStore((s) => s.backtestDays);
  const backtestOos = useResearchStore((s) => s.backtestOos);
  const selectedBotId = useStore((s) => s.selectedBotId);
  const [hydrating, setHydrating] = useState(false);
  const [bootReady, setBootReady] = useState(false);

  const days = backtestResults?.meta?.days ?? backtestDays;
  const symbol = backtestResults?.meta?.symbol ?? activeSymbol;
  const strategy = backtestResults?.meta?.strategy ?? botStrategy;
  const timeframe = backtestResults?.meta?.timeframe ?? botTimeframe;
  const advisorBotId = selectedBotId ?? backtestResults?.meta?.bot_id ?? null;
  const resultsOffloaded = Boolean(backtestResults?._offloaded);

  // Fingerprint / Select sentinel must stay as store selection (e.g. "holdout").
  const fingerprintDays = backtestDays;
  const optimizerDays = useMemo(() => {
    if (!isMlStrategy(strategy)) {
      const n = parseInt(String(days), 10);
      return Number.isFinite(n) && n > 0 ? String(n) : '7';
    }
    const status = getCachedModelStatus(symbol, strategy, timeframe || '1m');
    const holdoutDays = resolveHoldoutDaysFromStatus(status, {});
    const resolved = resolveMlBacktestDaysPayload(
      isHoldoutBacktestDays(backtestDays) ? backtestDays : String(days ?? backtestDays),
      holdoutDays,
      { isMl: true },
    );
    return String(resolved.days);
  }, [strategy, symbol, timeframe, days, backtestDays]);

  const strategyCategory = useMemo(() => getStrategyCategory(strategy), [strategy]);
  const labDescription = LAB_DESCRIPTIONS[strategyCategory] ?? LAB_DESCRIPTIONS.normal;

  const loadRun = async (runId, tab) => {
    if (!runId) return;
    const nextTab = tab && ['results', 'optimizer', 'jobs'].includes(tab) ? tab : 'results';
    setBacktestLabTab(nextTab);
    setHydrating(true);
    try {
      await fetchBacktestRun(runId, { setBacktestResults });
      setBacktestLabTab(nextTab);
    } catch (err) {
      toast.error(err?.message || 'Could not load detached backtest run');
      setBacktestLabTab('jobs');
    } finally {
      setHydrating(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { runId, labTab: tab } = readDetachParams();
      if (tab && ['results', 'optimizer', 'jobs'].includes(tab)) {
        setBacktestLabTab(tab);
      }
      try {
        await runBootstrap({ light: true, skipCandles: true });
      } catch {
        /* still try — api client may already work */
      }
      if (cancelled) return;
      setBootReady(true);

      if (runId) {
        const cur = useResearchStore.getState().backtestResults;
        if (!cur || cur.run_id !== runId || cur._offloaded) {
          await loadRun(runId, tab || 'results');
        }
      } else if (!useResearchStore.getState().backtestResults) {
        if (!tab || tab === 'results') setBacktestLabTab('jobs');
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- hydrate once from URL
  }, []);

  useEffect(() => {
    return subscribeStandaloneEvents('backtest-lab', (msg) => {
      if (msg?.type !== 'navigate') return;
      const tab = msg.labTab || msg.tab;
      const runId = msg.runId;
      if (tab && ['results', 'optimizer', 'jobs'].includes(String(tab))) {
        setBacktestLabTab(String(tab));
      }
      if (runId) void loadRun(String(runId), tab || 'results');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="backtest-lab backtest-lab--standalone backtest-lab--fullscreen">
      <header className="terminal-sheet__header backtest-lab__header">
        <div className="backtest-lab__header-main">
          <h1 className="backtest-lab__title">
            <Activity aria-hidden />
            Backtest Lab
          </h1>
          <p className="backtest-lab__description">{labDescription}</p>
        </div>
        <div className="backtest-lab__header-tools">
          {typeof onReattach === 'function' && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs gap-1 shrink-0"
              onClick={onReattach}
              title="Close this window and return to the trading layout"
            >
              <PanelLeft size={14} aria-hidden />
              Reattach
            </Button>
          )}
        </div>
      </header>

      <div className="backtest-lab__tabs">
        <Tabs value={labTab} onValueChange={setBacktestLabTab}>
          <TabsList className="w-full">
            {LAB_TABS.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="terminal-sheet__body backtest-lab__body">
        <div
          className={cn(
            'terminal-sheet__scroll backtest-lab__scroll',
            labTab === 'jobs' && 'backtest-lab__scroll--jobs',
          )}
        >
          {labTab !== 'optimizer' && (
            <div className="algo-backtest-progress-sticky sticky top-0 z-10 bg-background/95 py-1 backdrop-blur supports-[backdrop-filter]:bg-background/80">
              <BacktestProgressBar />
            </div>
          )}

          {labTab === 'jobs' && (
            <ErrorBoundary name="Backtest jobs">
              <Suspense fallback={<LabPanelFallback />}>
                <BacktestJobHistory />
              </Suspense>
            </ErrorBoundary>
          )}

          {labTab === 'optimizer' && (
            <div className="backtest-lab__optimizer">
              {!symbol || !strategy ? (
                <div className="backtest-lab__empty">
                  <p>
                    Select a completed run from <strong>Jobs</strong> (or Detach with an active
                    report) before opening the Optimizer.
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setBacktestLabTab('jobs')}
                  >
                    Open Jobs
                  </Button>
                </div>
              ) : (
                <ErrorBoundary name="Optimizer panel">
                  <Suspense fallback={<LabPanelFallback />}>
                    <BacktestSweepPanel
                      symbol={symbol}
                      strategy={strategy}
                      strategyCategory={strategyCategory}
                      days={optimizerDays}
                      timeframe={timeframe}
                      oosPct={backtestOos ? 30 : backtestResults?.meta?.oos_pct}
                      results={resultsOffloaded ? null : backtestResults}
                    />
                  </Suspense>
                </ErrorBoundary>
              )}
            </div>
          )}

          {labTab === 'results' && (
            <>
              {(!bootReady || hydrating || resultsOffloaded) && (
                <p className="backtest-lab__loading px-3 pt-2 flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin" aria-hidden />
                  {!bootReady
                    ? 'Connecting…'
                    : hydrating
                      ? 'Loading detached run…'
                      : 'Restoring full report…'}
                </p>
              )}
              {backtestRunning && !backtestResults && (
                <p className="backtest-lab__loading px-3 pt-2">Running backtest…</p>
              )}
              {backtestResults && !resultsOffloaded ? (
                <ErrorBoundary name="Backtest report">
                  <Suspense fallback={<LabPanelFallback />}>
                    <BacktestResultsPanel
                      variant="full"
                      results={backtestResults}
                      strategyCategory={strategyCategory}
                      backtestDays={fingerprintDays}
                      backtestTimeframe={timeframe}
                      symbol={symbol}
                      strategy={strategy}
                      recentRuns={backtestRuns}
                      snapshot={backtestSnapshot}
                      showReasoningSection={agentLlmAvailable}
                      oosPct={backtestOos ? 30 : backtestResults?.meta?.oos_pct}
                      advisorBotId={advisorBotId}
                      agentLlmAvailable={agentLlmAvailable}
                    />
                  </Suspense>
                </ErrorBoundary>
              ) : (
                bootReady && !backtestRunning && !hydrating && !resultsOffloaded && (
                  <div className="backtest-lab__empty">
                    <p>
                      No backtest loaded in this window. Open <strong>Jobs</strong> for saved runs,
                      or Detach from the main Lab after a completed run.
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => setBacktestLabTab('jobs')}
                    >
                      Open Jobs
                    </Button>
                  </div>
                )
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
