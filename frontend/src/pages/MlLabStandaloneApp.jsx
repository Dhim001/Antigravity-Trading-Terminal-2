import { useEffect } from 'react';
import { BrainCircuit, PanelLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBootstrap } from '../hooks/useBootstrap';
import { useWebSocket } from '../hooks/useWebSocket';
import SettingsBootstrap from '../components/SettingsBootstrap';
import ErrorBoundary from '../components/ErrorBoundary';
import ModelTrainingDashboard from '../components/dock/ModelTrainingDashboard';
import { broadcastMlLabEvent } from '../lib/mlLabWindow';

/**
 * Full-page ML Lab — loaded in its own browser/Electron window via ?panel=ml-lab.
 * Independent React tree from the trading terminal (no portal / shared layout).
 */
export default function MlLabStandaloneApp() {
  useBootstrap();
  useWebSocket();

  useEffect(() => {
    document.title = 'ML Lab · Antigravity';
    const onUnload = () => {
      broadcastMlLabEvent('closed');
    };
    window.addEventListener('beforeunload', onUnload);
    broadcastMlLabEvent('opened');
    return () => {
      window.removeEventListener('beforeunload', onUnload);
    };
  }, []);

  const handleReattach = () => {
    broadcastMlLabEvent('reattach');
    window.close();
  };

  return (
    <>
      <SettingsBootstrap />
      <div className="flex h-screen w-screen flex-col bg-background text-foreground">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BrainCircuit size={16} aria-hidden />
            ML Lab
            <span className="text-xs font-normal text-muted-foreground">standalone</span>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            onClick={handleReattach}
            title="Close this window and put ML Lab back in the trading dock"
          >
            <PanelLeft size={14} aria-hidden />
            Reattach to terminal
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          <ErrorBoundary name="ML Lab (standalone)">
            <ModelTrainingDashboard
              detached
              onAttach={handleReattach}
            />
          </ErrorBoundary>
        </div>
      </div>
    </>
  );
}
