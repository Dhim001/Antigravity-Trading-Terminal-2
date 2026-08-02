/**
 * AutomationStudio — full-height algo bot workspace + ML pipeline cockpit (UX-5).
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { toast } from 'sonner';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Cpu, GripVertical } from 'lucide-react';
import { useStore } from '../store/useStore';
import { useResearchStore } from '../store/useResearchStore';
import { openBacktestLabResults } from '../lib/backtestLab';
import { fetchBots } from '../api/endpoints';
import { getStoreActions } from '../api/dispatch';
import { AlgoTab } from './dock/AlgoPanel';
import ErrorBoundary from './ErrorBoundary';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import PipelineStatusBar from './PipelineStatusBar';
import PipelineAutoDeploySettings from './PipelineAutoDeploySettings';
import AutomationQuickActions from './AutomationQuickActions';
import {
  getMlPipeline,
  isPipelineActive,
  subscribeMlPipeline,
} from '@/lib/mlPipeline';
import { openAlgoDeployDialog, resolveDeployQueueAction } from '@/lib/pipelineNav';
import { openModelTrainingDock } from '@/lib/workspaceNav';
import { postMlLabRequest } from '@/lib/mlLabRequests';
import { isMlStrategy } from '@/config/strategies';

const STUDIO_WIDTH_KEY = 'terminal_automation_studio_width';
const STUDIO_WIDTH_DEFAULT = 960;
const STUDIO_WIDTH_MIN = 560;
const STUDIO_WIDTH_MAX = 1480;

function readStudioWidth() {
  try {
    const n = parseInt(localStorage.getItem(STUDIO_WIDTH_KEY), 10);
    if (!Number.isNaN(n)) return Math.min(STUDIO_WIDTH_MAX, Math.max(STUDIO_WIDTH_MIN, n));
  } catch (_) {}
  return STUDIO_WIDTH_DEFAULT;
}

export default function AutomationStudio({ open = false, onOpenChange }) {
  const [panelWidth, setPanelWidth] = useState(() => readStudioWidth());
  const [resizing, setResizing] = useState(false);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  const setBotDrawerOpen = useStore((s) => s.setBotDrawerOpen);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const botStrategy = useStore((s) => s.botStrategy);
  const botTimeframe = useStore((s) => s.botTimeframe);
  const backtestResults = useResearchStore((s) => s.backtestResults);

  const pipeline = useSyncExternalStore(
    subscribeMlPipeline,
    getMlPipeline,
    getMlPipeline,
  );
  const pipelineActive = isPipelineActive(pipeline);

  const tf = String(botTimeframe || '1m').toLowerCase() === 'tick'
    ? '1m'
    : String(botTimeframe || '1m').toLowerCase();

  useEffect(() => {
    try { localStorage.setItem(STUDIO_WIDTH_KEY, String(panelWidth)); } catch (_) {}
  }, [panelWidth]);

  useEffect(() => {
    const onOpen = () => {
      // Defer so the opening click doesn't hit the new overlay
      requestAnimationFrame(() => onOpenChange?.(true));
    };
    window.addEventListener('automation-studio-open', onOpen);
    return () => window.removeEventListener('automation-studio-open', onOpen);
  }, [onOpenChange]);

  useEffect(() => {
    if (open) setBotDrawerOpen(false);
  }, [open, setBotDrawerOpen]);

  useEffect(() => {
    if (!open) return;
    fetchBots(getStoreActions()).catch(() => {});
  }, [open]);

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
      const next = Math.min(STUDIO_WIDTH_MAX, Math.max(STUDIO_WIDTH_MIN, startW.current + delta));
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

  const handleFullPipeline = useCallback(() => {
    const strategy = isMlStrategy(botStrategy) ? botStrategy : 'ML_SIGNAL_BOOST';
    // Do not startPipeline here — ml-lab-run-pipeline handler owns the run
    // (avoids double-start that orphans the first pipelineId).
    openModelTrainingDock();
    postMlLabRequest('ml-lab-run-pipeline', {
      strategy, symbol: activeSymbol, timeframe: tf, mode: 'full',
    });
  }, [activeSymbol, botStrategy, tf]);

  const handleOpenLab = useCallback(() => {
    openModelTrainingDock();
  }, []);

  const handleBatchTrain = useCallback(() => {
    openModelTrainingDock();
    postMlLabRequest('ml-lab-open-batch', { scope: 'all', symbol: activeSymbol, timeframe: tf });
  }, [activeSymbol, tf]);

  const handleRetrainStale = useCallback(() => {
    openModelTrainingDock();
    postMlLabRequest('ml-lab-open-batch', { scope: 'stale', symbol: activeSymbol, timeframe: tf });
  }, [activeSymbol, tf]);

  const handleDeployQueue = useCallback(() => {
    const resolved = resolveDeployQueueAction(getMlPipeline());
    if (resolved.action === 'open_deploy') {
      openAlgoDeployDialog({ openStudio: false });
      if (resolved.pendingApproval) {
        toast.message('Pipeline awaiting deploy approval — confirm in the deploy dialog');
      } else if (resolved.stage === 'READY_TO_DEPLOY') {
        toast.message('Pipeline ready to deploy');
      } else if (resolved.stage === 'GATE_CHECK') {
        toast.message('Review gate status in the deploy dialog');
      }
      return;
    }
    const stageLabel = resolved.stage === 'IDLE' ? 'no active pipeline' : resolved.stage;
    const err = resolved.lastError ? ` — ${resolved.lastError}` : '';
    toast.message(`Deploy queue: ${stageLabel}${err}`, {
      action: {
        label: 'Open deploy',
        onClick: () => openAlgoDeployDialog({ openStudio: false }),
      },
    });
  }, []);

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          showCloseButton
          className={cn(
            'terminal-sheet automation-studio w-full sm:max-w-none',
            resizing && 'automation-studio--resizing',
          )}
          data-tour="automation-studio"
          style={{
            width: panelWidth,
            minWidth: STUDIO_WIDTH_MIN,
            maxWidth: 'min(96vw, 100%)',
          }}
        >
          <div
            className={cn('automation-studio__resize', resizing && 'dragging')}
            onMouseDown={onResizeMouseDown}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize automation studio panel"
            title="Drag to resize"
          >
            <span className="automation-studio__resize-grip" aria-hidden>
              <GripVertical />
            </span>
          </div>

          <SheetHeader className="terminal-sheet__header automation-studio__header">
            <div className="automation-studio__header-row">
              <SheetTitle className="automation-studio__title">
                <Cpu aria-hidden />
                Automation Studio
              </SheetTitle>
              <PipelineAutoDeploySettings compact className="automation-studio__deploy-mode" />
            </div>
            <SheetDescription className="automation-studio__description">
              Deploy bots, run backtests, and manage live execution
            </SheetDescription>
          </SheetHeader>

          <div className="terminal-sheet__body automation-studio__body flex min-h-0 flex-1 flex-col">
            <div className="automation-studio__pipeline-strip px-3 pt-2 space-y-2 shrink-0">
              <PipelineStatusBar />
              <AutomationQuickActions
                onFullPipeline={handleFullPipeline}
                onRetrainStale={handleRetrainStale}
                onOpenLab={handleOpenLab}
                onBatchTrain={handleBatchTrain}
                onDeployQueue={handleDeployQueue}
                pipelineActive={pipelineActive}
              />
            </div>

            <ErrorBoundary name="Automation studio algo">
              <AlgoTab hideToolbar />
            </ErrorBoundary>
            {backtestResults && (
              <div className="automation-studio__backtest-strip border-t px-3 py-2">
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  className="h-6 text-[0.62rem]"
                  onClick={() => openBacktestLabResults()}
                >
                  Open Backtest Lab (Results)
                </Button>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
