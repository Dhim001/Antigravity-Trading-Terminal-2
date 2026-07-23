import { useCallback, useMemo } from 'react';
import { ExternalLink } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { useDetachedPanels } from '../../hooks/useDetachedPanels';
import {
  getStandalonePanelDef,
  openStandaloneWindow,
} from '../../lib/standalonePanels';
import DetachedDockLink from './DetachedDockLink';

/**
 * Dock/Flex tab that either shows content or a link to a standalone ?panel= window.
 */
export default function StandaloneDockPanel({
  panelId,
  dockTabId,
  children,
  showDetachBar = true,
}) {
  const { isDetached, detach, attach } = useDetachedPanels();
  const def = getStandalonePanelDef(panelId);
  const dockTabs = useMemo(
    () => (def?.dockTabs?.length ? def.dockTabs : [dockTabId]),
    [def, dockTabId],
  );
  const detached = dockTabs.some((t) => isDetached(t));

  const handleDetach = useCallback(() => {
    const win = openStandaloneWindow(panelId);
    if (!win) {
      toast.error('Popup blocked', {
        description: 'Allow popups for this site, then click Detach again.',
      });
      return;
    }
    for (const t of dockTabs) detach(t);
  }, [panelId, dockTabs, detach]);

  const handleAttach = useCallback(() => {
    for (const t of dockTabs) attach(t);
  }, [dockTabs, attach]);

  if (detached) {
    return <DetachedDockLink panelId={panelId} onAttach={handleAttach} />;
  }

  const body = typeof children === 'function' ? children({ onDetach: handleDetach }) : children;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {showDetachBar && (
        <div className="flex shrink-0 justify-end border-b border-border/60 px-2 py-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs gap-1"
            title="Open in a separate window"
            onClick={handleDetach}
          >
            <ExternalLink size={14} aria-hidden />
            Detach
          </Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-hidden">{body}</div>
    </div>
  );
}
