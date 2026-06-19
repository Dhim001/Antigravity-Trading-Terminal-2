/**
 * Backtest Lab — resizable right sheet (reliable layout, matches Automation Studio).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Activity, GripVertical } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useStore } from '../store/useStore';
import BacktestResultsPanel from './BacktestResultsPanel';
import BacktestProgressBar from './BacktestProgressBar';
import ErrorBoundary from './ErrorBoundary';

const LAB_WIDTH_KEY = 'terminal_backtest_lab_width';
const LAB_WIDTH_DEFAULT = 880;
const LAB_WIDTH_MIN = 520;
const LAB_WIDTH_MAX = 1280;

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
  const backtestResults = useStore((s) => s.backtestResults);
  const backtestRuns = useStore((s) => s.backtestRuns);
  const backtestRunning = useStore((s) => s.backtestRunning);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const backtestSnapshot = useStore((s) => s.backtestSnapshot);

  const [panelWidth, setPanelWidth] = useState(() => readLabWidth());
  const [resizing, setResizing] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const days = backtestResults?.meta?.days;

  useEffect(() => {
    try { localStorage.setItem(LAB_WIDTH_KEY, String(panelWidth)); } catch (_) {}
  }, [panelWidth]);

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
        )}
        style={{
          width: panelWidth,
          minWidth: LAB_WIDTH_MIN,
          maxWidth: 'min(96vw, 100%)',
        }}
      >
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

        <SheetHeader className="terminal-sheet__header backtest-lab__header">
          <SheetTitle className="backtest-lab__title">
            <Activity aria-hidden />
            Backtest Lab
          </SheetTitle>
          <SheetDescription className="backtest-lab__description">
            Strategy replay report — equity, trades, and run history
          </SheetDescription>
        </SheetHeader>

        <div className="terminal-sheet__body backtest-lab__body">
          <div className="terminal-sheet__scroll backtest-lab__scroll">
            <BacktestProgressBar />
            {backtestRunning && !backtestResults && (
              <p className="backtest-lab__loading text-sm text-muted-foreground">
                Running backtest…
              </p>
            )}
            {backtestResults ? (
              <ErrorBoundary name="Backtest report">
                <BacktestResultsPanel
                  variant="full"
                  results={backtestResults}
                  backtestDays={days != null ? String(days) : '7'}
                  backtestTimeframe={backtestResults?.meta?.timeframe ?? botTimeframe}
                  symbol={backtestResults?.meta?.symbol ?? activeSymbol}
                  strategy={backtestResults?.meta?.strategy ?? botStrategy}
                  recentRuns={backtestRuns}
                  snapshot={backtestSnapshot}
                />
              </ErrorBoundary>
            ) : !backtestRunning && (
              <div className="backtest-lab__empty">
                <p className="text-sm text-muted-foreground">
                  Run a backtest from the Algo Bot deploy panel to see results here.
                </p>
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
