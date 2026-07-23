import { ExternalLink, PanelLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  closeStandaloneWindow,
  focusStandaloneWindow,
  getStandalonePanelDef,
  openStandaloneWindow,
} from '@/lib/standalonePanels';

/**
 * Dock-tab placeholder while a panel runs in a standalone window.
 */
export default function DetachedDockLink({
  panelId,
  onAttach,
  title,
  description,
}) {
  const def = getStandalonePanelDef(panelId);
  const label = title || def?.title?.replace(' · Antigravity', '') || 'Panel';

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-muted-foreground">
      <ExternalLink className="opacity-50" size={28} aria-hidden />
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">{label} is open in a separate window</p>
        <p className="text-xs max-w-[22rem] leading-snug">
          {description
            || 'It runs as its own page (independent of this layout). Focus brings it forward; Reattach docks it here again.'}
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 text-xs gap-1"
          onClick={() => {
            if (!focusStandaloneWindow(panelId)) {
              openStandaloneWindow(panelId);
            }
          }}
        >
          <ExternalLink size={14} aria-hidden />
          Focus window
        </Button>
        <Button
          type="button"
          size="sm"
          className="h-8 text-xs gap-1"
          onClick={() => {
            closeStandaloneWindow(panelId);
            onAttach?.();
          }}
        >
          <PanelLeft size={14} aria-hidden />
          Reattach to dock
        </Button>
      </div>
    </div>
  );
}
