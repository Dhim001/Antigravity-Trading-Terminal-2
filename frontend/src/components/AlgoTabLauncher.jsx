/**
 * AlgoTabLauncher — compact dock card or full studio embed (UX-5).
 * Renders Algo tab via dock event; full mode opens embedded panel.
 */
import { useStore } from '../store/useStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Cpu, ExternalLink, Play, Pause } from 'lucide-react';

export default function AlgoTabLauncher({ full = false }) {
  const activeBots = useStore((s) => s.activeBots);
  const isBotRunning = useStore((s) => s.isBotRunning);
  const activeSymbol = useStore((s) => s.activeSymbol);
  const runningCount = activeBots.filter((b) => b.status === 'RUNNING').length;

  const openStudio = () => {
    window.dispatchEvent(new CustomEvent('automation-studio-open'));
  };

  const openDockAlgo = () => {
    window.dispatchEvent(new CustomEvent('dock-tab', { detail: 'algo' }));
    window.dispatchEvent(new CustomEvent('dock-group', { detail: 'automation' }));
  };

  if (full) {
    return (
      <div className="algo-studio-embed h-full overflow-hidden" id="algo-studio-mount">
        <iframe
          title="Algo workspace"
          className="hidden"
          aria-hidden
        />
        <div className="dock-panel-tab h-full">
          <div className="dock-panel-tab__toolbar">
            <div className="dock-panel-tab__toolbar-lead">
              <div className="dock-panel-tab__toolbar-icon"><Cpu size={14} /></div>
              <div className="dock-panel-tab__toolbar-copy">
                <span className="dock-panel-tab__toolbar-title">Algo Trading</span>
                <span className="dock-panel-tab__toolbar-subtitle num-mono">
                  {runningCount} running · {activeSymbol}
                </span>
              </div>
            </div>
          </div>
          <p className="px-4 py-3 text-xs text-muted-foreground">
            Full algo controls are in the dock <strong>Automation → Algo Bot</strong> tab.
            Use the dock for deploy, backtest, and live logs — this studio keeps focus while configuring.
          </p>
          <div className="px-4 pb-4">
            <Button size="sm" className="text-xs" onClick={openDockAlgo}>
              <ExternalLink data-icon="inline-start" />
              Open Algo tab in dock
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="algo-tab-launcher flex flex-col items-center justify-center gap-3 p-6 text-center">
      <div className="rounded-full bg-primary/10 p-3 text-primary">
        <Cpu size={24} aria-hidden />
      </div>
      <div>
        <p className="text-sm font-semibold">Automation Studio</p>
        <p className="mt-1 text-xs text-muted-foreground max-w-[240px]">
          {runningCount} bot{runningCount === 1 ? '' : 's'} running on {activeSymbol}.
          Open the studio for deploy, backtest, and strategy templates.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {isBotRunning && (
          <Badge variant="live" className="text-[0.58rem]">LIVE</Badge>
        )}
        <Button size="sm" className="text-xs" onClick={openStudio}>
          <ExternalLink data-icon="inline-start" />
          Open Studio
        </Button>
        <Button variant="outline" size="sm" className="text-xs" onClick={openDockAlgo}>
          <Play data-icon="inline-start" />
          Dock tab
        </Button>
      </div>
    </div>
  );
}
