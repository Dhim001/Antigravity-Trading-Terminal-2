/**
 * Backtest Lab — resizable right sheet with Results | Optimizer | Jobs tabs.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Activity, GripVertical, Maximize2, Minimize2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStore } from '../store/useStore';
import { Button } from '@/components/ui/button';
import BacktestResultsPanel from './BacktestResultsPanel';
import BacktestProgressBar from './BacktestProgressBar';
import BacktestJobHistory from './BacktestJobHistory';
import BacktestSweepPanel from './BacktestSweepPanel';
import ErrorBoundary from './ErrorBoundary';

const LAB_WIDTH_KEY = 'terminal_backtest_lab_width';
const LAB_FULLSCREEN_KEY = 'terminal_backtest_lab_fullscreen';
const LAB_WIDTH_DEFAULT = 880;
const LAB_WIDTH_MIN = 520;
const LAB_WIDTH_MAX = 1280;

function readLabFullscreen() {
  try {
    const stored = localStorage.getItem(LAB_FULLSCREEN_KEY);
    if (stored === '0') return false;
    if (stored === '1') return true;
  } catch (_) {}
  return true;
}

const LAB_TABS = [
  { id: 'results', label: 'Results' },
  { id: 'optimizer', label: 'Optimizer' },
  { id: 'jobs', label: 'Jobs' },
];

function readLabWidth() {
  try {
    const n = parseInt(localStorage.getItem(LAB_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(LAB_WIDTH_MAX, Math.max(LAB_WIDTH_MIN, n));
  } catch (_) {}
  return LAB_WIDTH_DEFAULT;
}

export default function BacktestLabSheet() {
  const open = useStore((s) => s.backtestLabOpen);
  const setOpen = useStore((s) => s.setBacktestLabOpen);
  const labTab = useStore((s) => s.backtestLabTab);
  const setBacktestLabTab = useStore((s) => s.setBacktestLabTab);
  const backtestResults = useStore((s) => s.backtestResults);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const backtestRuns = useStore((s) => s.backtestRuns);
  const backtestRunning = useStore((s) => s.backtestRunning);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const backtestSnapshot = useStore((s) => s.backtestSnapshot);
  const backtestDays = useStore((s) => s.backtestDays);
  const backtestOos = useStore((s) => s.backtestOos);
  const selectedBotId = useStore((s) => s.selectedBotId);

  const [panelWidth, setPanelWidth] = useState(() => readLabWidth());
  const [fullscreen, setFullscreen] = useState(() => readLabFullscreen());
  const [resizing, setResizing] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const days = backtestResults?.meta?.days ?? backtestDays;
  const symbol = backtestResults?.meta?.symbol ?? activeSymbol;
  const strategy = backtestResults?.meta?.strategy ?? botStrategy;
  const timeframe = backtestResults?.meta?.timeframe ?? botTimeframe;
  const advisorBotId = selectedBotId ?? backtestResults?.meta?.bot_id ?? null;

  useEffect(() => {
    try { localStorage.setItem(LAB_WIDTH_KEY, String(panelWidth)); } catch (_) {}
  }, [panelWidth]);

  useEffect(() => {
    try { localStorage.setItem(LAB_FULLSCREEN_KEY, fullscreen ? '1' : '0'); } catch (_) {}
  }, [fullscreen]);

  const onResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    isDragging.current = true;
    setResizing(true);
    startX.current = e.clientX;
    startW.current = panelWidth;
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [panelWidth]);

  useEffect(() => {
    const onMove = (e) => {
      if (!isDragging.current) return;
      const delta = startX.current - e.clientX;
      const next = Math.min(LAB_WIDTH_MAX, Math.max(LAB_WIDTH_MIN, startW.current + delta));
      setPanelWidth(next);
    };
    const onUp = () => {
      if (!isDragging.current) return;
      isDragging.current = false;
      setResizing(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side="right"
        showCloseButton
        className={cn(
          'terminal-sheet backtest-lab w-full sm:max-w-none',
          resizing && 'backtest-lab--resizing',
          fullscreen && 'backtest-lab--fullscreen',
        )}
        style={fullscreen ? {
          width: '100vw',
          minWidth: '100vw',
          maxWidth: '100vw',
        } : {
          width: panelWidth,
          minWidth: LAB_WIDTH_MIN,
          maxWidth: 'min(96vw, 100%)',
        }}
      >
        {!fullscreen && (
          <div
            className={cn('backtest-lab__resize', resizing && 'dragging')}
            onMouseDown={onResizeMouseDown}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize backtest lab panel"
            title="Drag to resize"
          >
            <span className="backtest-lab__resize-grip" aria-hidden>
              <GripVertical />
            </span>
          </div>
        )}

        <div className="backtest-lab__header-tools">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="backtest-lab__expand-btn shrink-0"
            onClick={() => setFullscreen((f) => !f)}
            title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          >
            {fullscreen ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
          </Button>
        </div>

        <SheetHeader className="terminal-sheet__header backtest-lab__header">
          <SheetTitle className="backtest-lab__title">
            <Activity aria-hidden />
            Backtest Lab
          </SheetTitle>
          <SheetDescription className="backtest-lab__description">
            Strategy replay report — equity, trades, optimizer, and run history
          </SheetDescription>
        </SheetHeader>

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
          <div className="terminal-sheet__scroll backtest-lab__scroll">
            <BacktestProgressBar />

            {labTab === 'jobs' && <BacktestJobHistory />}

            {labTab === 'optimizer' && (
              <div className="backtest-lab__optimizer px-1 pt-2">
                <BacktestSweepPanel
                  symbol={symbol}
                  strategy={strategy}
                  days={days != null ? String(days) : backtestDays}
                  timeframe={timeframe}
                  oosPct={backtestOos ? 30 : backtestResults?.meta?.oos_pct}
                  results={backtestResults}
                />
              </div>
            )}

            {labTab === 'results' && (
              <>
                {backtestRunning && !backtestResults && (
                  <p className="backtest-lab__loading px-3 pt-2">
                    Running backtest…
                  </p>
                )}
                {backtestResults ? (
                  <ErrorBoundary name="Backtest report">
                    <BacktestResultsPanel
                      variant="full"
                      results={backtestResults}
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
                  </ErrorBoundary>
                ) : !backtestRunning && (
                  <div className="backtest-lab__empty">
                    <p>
                      No backtest loaded yet. Run one from the bottom dock, then return here for the full report.
                    </p>
                    <ol>
                      <li>Open the bottom dock → <strong>Algo</strong> tab</li>
                      <li>Configure symbol/strategy and click <strong>BACKTEST</strong></li>
                      <li>Click <strong>LAB</strong> in the footer, or use header <strong>Lab</strong></li>
                    </ol>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => {
                        window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
                      }}
                    >
                      Go to Algo tab
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
