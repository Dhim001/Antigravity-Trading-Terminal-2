import { Suspense, useMemo } from 'react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { useStore } from '../../store/useStore';
import { useResearchStore } from '../../store/useResearchStore';
import BacktestProgressBar from '../../components/BacktestProgressBar';
import ErrorBoundary from '../../components/ErrorBoundary';
import { lazyImport } from '../../lib/lazyImport';
import { getStrategyCategory } from '../../config/strategies';

const BacktestResultsPanel = lazyImport(() => import('../../components/BacktestResultsPanel'), 'backtest-results');
const BacktestSweepPanel = lazyImport(() => import('../../components/BacktestSweepPanel'), 'backtest-sweep');
const BacktestJobHistory = lazyImport(() => import('../../components/BacktestJobHistory'), 'backtest-jobs');

const LAB_TABS = [
  { id: 'results', label: 'Results' },
  { id: 'optimizer', label: 'Optimizer' },
  { id: 'jobs', label: 'Jobs' },
];

function LabFallback() {
  return <p className="px-3 pt-2 text-sm text-muted-foreground">Loading panel…</p>;
}

/**
 * Full-page Backtest Lab (no sheet chrome).
 * Job history / API-backed runs work across windows; in-memory results
 * come from this window's research store (run Algo here or open Jobs).
 */
export default function BacktestStandaloneContent() {
  const labTab = useResearchStore((s) => s.backtestLabTab);
  const setBacktestLabTab = useResearchStore((s) => s.setBacktestLabTab);
  const backtestResults = useResearchStore((s) => s.backtestResults);
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

  const days = backtestResults?.meta?.days ?? backtestDays;
  const symbol = backtestResults?.meta?.symbol ?? activeSymbol;
  const strategy = backtestResults?.meta?.strategy ?? botStrategy;
  const timeframe = backtestResults?.meta?.timeframe ?? botTimeframe;
  const advisorBotId = selectedBotId ?? backtestResults?.meta?.bot_id ?? null;
  const strategyCategory = useMemo(() => getStrategyCategory(strategy), [strategy]);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="shrink-0 border-b border-border px-3 py-2">
        <Tabs value={labTab} onValueChange={setBacktestLabTab}>
          <TabsList>
            {LAB_TABS.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <BacktestProgressBar />

        {labTab === 'jobs' && (
          <Suspense fallback={<LabFallback />}>
            <BacktestJobHistory />
          </Suspense>
        )}

        {labTab === 'optimizer' && (
          <div className="p-2">
            <ErrorBoundary name="Optimizer panel">
              <Suspense fallback={<LabFallback />}>
                <BacktestSweepPanel
                  symbol={symbol}
                  strategy={strategy}
                  strategyCategory={strategyCategory}
                  days={days != null ? String(days) : backtestDays}
                  timeframe={timeframe}
                  oosPct={backtestOos ? 30 : backtestResults?.meta?.oos_pct}
                  results={backtestResults}
                />
              </Suspense>
            </ErrorBoundary>
          </div>
        )}

        {labTab === 'results' && (
          <>
            {backtestRunning && !backtestResults && (
              <p className="px-3 pt-2 text-sm text-muted-foreground">Running backtest…</p>
            )}
            {backtestResults ? (
              <ErrorBoundary name="Backtest report">
                <Suspense fallback={<LabFallback />}>
                  <BacktestResultsPanel
                    variant="full"
                    results={backtestResults}
                    strategyCategory={strategyCategory}
                    backtestDays={days != null ? String(days) : '7'}
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
              !backtestRunning && (
                <div className="space-y-3 p-6 text-sm text-muted-foreground">
                  <p>
                    No backtest loaded in this window. Open <strong>Jobs</strong> for saved runs,
                    or run a backtest from the Algo Bot standalone window.
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
  );
}
