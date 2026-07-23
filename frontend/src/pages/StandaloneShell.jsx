import { useEffect } from 'react';
import { PanelLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useBootstrap } from '../hooks/useBootstrap';
import { useWebSocket } from '../hooks/useWebSocket';
import SettingsBootstrap from '../components/SettingsBootstrap';
import ErrorBoundary from '../components/ErrorBoundary';
import {
  broadcastStandaloneEvent,
  getStandalonePanelDef,
} from '../lib/standalonePanels';

/**
 * Shared chrome for detached ?panel=… windows.
 */
export default function StandaloneShell({ panelId, children }) {
  const def = getStandalonePanelDef(panelId);
  useBootstrap();
  useWebSocket();

  useEffect(() => {
    if (!def) return undefined;
    document.title = def.title;
    const onUnload = () => broadcastStandaloneEvent(def.id, 'closed');
    window.addEventListener('beforeunload', onUnload);
    broadcastStandaloneEvent(def.id, 'opened');
    return () => window.removeEventListener('beforeunload', onUnload);
  }, [def]);

  const handleReattach = () => {
    if (def) broadcastStandaloneEvent(def.id, 'reattach');
    window.close();
  };

  if (!def) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Unknown standalone panel.
      </div>
    );
  }

  return (
    <>
      <SettingsBootstrap />
      <div className="flex h-screen w-screen flex-col bg-background text-foreground">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2">
          <div className="text-sm font-medium">
            {def.title.replace(' · Antigravity', '')}
            <span className="ml-2 text-xs font-normal text-muted-foreground">standalone</span>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1"
            onClick={handleReattach}
            title="Close this window and return to the trading layout"
          >
            <PanelLeft size={14} aria-hidden />
            Reattach to terminal
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          <ErrorBoundary name={def.title}>
            {typeof children === 'function' ? children({ onReattach: handleReattach }) : children}
          </ErrorBoundary>
        </div>
      </div>
    </>
  );
}
