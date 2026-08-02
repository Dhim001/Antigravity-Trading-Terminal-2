/**
 * Auto-deploy mode selector for the ML pipeline.
 */
import { useSyncExternalStore } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  getMlPipeline,
  setAutoDeployMode,
  subscribeMlPipeline,
} from '@/lib/mlPipeline';

const MODES = [
  { value: 'paper', label: 'Paper only', hint: 'Auto-deploy only in paper mode (default)' },
  { value: 'approval', label: 'Approval required', hint: 'Pause for confirm before deploy' },
  { value: 'full_auto', label: 'Full auto ⚠️', hint: 'Auto-deploy paper or live when gates pass' },
];

export default function PipelineAutoDeploySettings({ className, compact = false }) {
  const pipeline = useSyncExternalStore(
    subscribeMlPipeline,
    getMlPipeline,
    getMlPipeline,
  );
  const mode = pipeline.autoDeployMode || 'paper';

  const onChange = (value) => {
    if (value === 'full_auto') {
      const ok = window.confirm(
        'Full auto will automatically deploy bots with real capital when gates pass. Are you sure?',
      );
      if (!ok) return;
    }
    setAutoDeployMode(value);
  };

  return (
    <div className={cn('pipeline-auto-deploy', className)}>
      {!compact && (
        <Label className="text-[0.65rem] text-muted-foreground">Auto-Deploy Mode</Label>
      )}
      <Select value={mode} onValueChange={onChange}>
        <SelectTrigger
          size="sm"
          className={cn('h-7 text-[0.65rem]', compact && 'w-[8.5rem]')}
          title={MODES.find((m) => m.value === mode)?.hint}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {MODES.map((m) => (
            <SelectItem key={m.value} value={m.value} className="text-xs">
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {mode === 'full_auto' && (
        <p className="text-[0.6rem] text-amber-600 dark:text-amber-400 flex items-center gap-1 mt-0.5">
          <AlertTriangle size={10} aria-hidden />
          Bot will deploy to live trading automatically when gates pass
        </p>
      )}
    </div>
  );
}
