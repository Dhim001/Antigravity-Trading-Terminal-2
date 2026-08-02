/**
 * Quick-action strip for Automation Studio / ML pipeline cockpit.
 */
import { FlaskConical, Layers, ListOrdered, Play, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export default function AutomationQuickActions({
  onFullPipeline,
  onRetrainStale,
  onOpenLab,
  onBatchTrain,
  onDeployQueue,
  pipelineActive = false,
  className,
}) {
  return (
    <div className={cn('automation-quick-actions', className)} role="toolbar" aria-label="Automation quick actions">
      <Button
        type="button"
        variant="secondary"
        size="xs"
        className="h-7 text-[0.65rem]"
        disabled={pipelineActive}
        title={pipelineActive ? 'Pipeline already running' : 'Train → Validate → Backtest → Gate'}
        onClick={onFullPipeline}
      >
        <Play size={12} aria-hidden />
        Full Pipeline
      </Button>
      <Button
        type="button"
        variant="outline"
        size="xs"
        className="h-7 text-[0.65rem]"
        title="Open batch train filtered to stale models"
        onClick={onRetrainStale}
      >
        <RefreshCw size={12} aria-hidden />
        Retrain Stale
      </Button>
      <Button
        type="button"
        variant="outline"
        size="xs"
        className="h-7 text-[0.65rem]"
        title="Open ML Lab"
        onClick={onOpenLab}
      >
        <FlaskConical size={12} aria-hidden />
        Lab
      </Button>
      <Button
        type="button"
        variant="outline"
        size="xs"
        className="h-7 text-[0.65rem]"
        title="Batch train multiple strategies"
        onClick={onBatchTrain}
      >
        <Layers size={12} aria-hidden />
        Batch Train
      </Button>
      {typeof onDeployQueue === 'function' && (
        <Button
          type="button"
          variant="outline"
          size="xs"
          className="h-7 text-[0.65rem]"
          title="Open deploy / approval queue"
          onClick={onDeployQueue}
        >
          <ListOrdered size={12} aria-hidden />
          Deploy Queue
        </Button>
      )}
    </div>
  );
}
