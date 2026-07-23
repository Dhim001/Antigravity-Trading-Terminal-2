import { AlgoTab } from '../../components/dock/AlgoPanel';
import { Button } from '@/components/ui/button';
import { useResearchStore } from '../../store/useResearchStore';
import { openStandaloneWindow } from '../../lib/standalonePanels';

export default function AutomationStandaloneContent() {
  const backtestResults = useResearchStore((s) => s.backtestResults);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-auto">
        <AlgoTab hideToolbar />
      </div>
      {backtestResults && (
        <div className="shrink-0 border-t border-border px-3 py-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => openStandaloneWindow('backtest-lab')}
          >
            Open Backtest Lab (standalone)
          </Button>
        </div>
      )}
    </div>
  );
}
