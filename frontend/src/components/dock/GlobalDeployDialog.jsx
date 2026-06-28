/**
 * GlobalDeployDialog.jsx — Bot deploy confirmation dialog (extracted from ResizableDock).
 *
 * Shared deploy dialog triggered by the optimizer, cross-tab pendingDeploy flag,
 * or the Algo tab's deploy action. Shows strategy, symbol, allocation, SL/TP, and timeframe summary.
 */
import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import StrategyBadge from '../StrategyBadge';
import { deployTimeframeSummary } from '@/lib/barTimeframes';
import { useShallow } from 'zustand/react/shallow';

export default function GlobalDeployDialog({ switchToAlgoTab }) {
  const pendingDeploy = useStore((s) => s.pendingDeploy);
  const setPendingDeploy = useStore((s) => s.setPendingDeploy);
  const {
    botStrategy, botConfig, activeSymbol, botExecutionMode, botTimeframe,
    isLive, allowLiveBots,
  } = useStore(useShallow((s) => ({
    botStrategy: s.botStrategy,
    botConfig: s.botConfig,
    activeSymbol: s.activeSymbol,
    botExecutionMode: s.botExecutionMode,
    botTimeframe: s.botTimeframe,
    isLive: s.isLive,
    allowLiveBots: s.allowLiveBots,
  })));
  const [deployOpen, setDeployOpen] = useState(false);

  useEffect(() => {
    if (pendingDeploy) {
      switchToAlgoTab();
      setDeployOpen(true);
      setPendingDeploy(false);
    }
  }, [pendingDeploy, setPendingDeploy, switchToAlgoTab]);

  const liveBotsBlocked = isLive && !allowLiveBots;

  const confirmDeploy = () => {
    setDeployOpen(false);
    if (liveBotsBlocked) {
      toast.error('Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true on the server.');
      return;
    }
    if (!botConfig?.allocation || botConfig.allocation <= 0) {
      toast.error('Enter a valid max notional cap');
      return;
    }
    sendAction(Action.BOT_CREATE, {
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: botExecutionMode === 'TICK' ? 'tick' : botTimeframe,
      allocation: botConfig.allocation,
      execution_mode: botExecutionMode,
      config: {
        ...botConfig,
        trailing_stop_percent: botConfig.trailing_stop_percent ?? 2,
        backtest_run_id: useStore.getState().backtestResults?.run_id ?? undefined,
      },
    });
  };

  return (
    <Dialog open={deployOpen} onOpenChange={setDeployOpen}>
      <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
        <DialogHeader>
          <DialogTitle>Deploy trading bot</DialogTitle>
          <DialogDescription className="text-xs leading-relaxed">
            This will start a live bot on the server using your current template and max notional cap.
          </DialogDescription>
        </DialogHeader>
        <div className="algo-dialog-summary">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground shrink-0">Strategy:</span>
            <StrategyBadge strategy={botStrategy} />
          </div>
          <div><span className="text-muted-foreground">Symbol:</span> <strong>{activeSymbol}</strong></div>
          <div><span className="text-muted-foreground">Max cap:</span> <strong>${botConfig?.allocation?.toLocaleString() ?? 0}</strong></div>
          <div>
            <span className="text-muted-foreground">Stop / TP:</span>{' '}
            <strong>
              SL {botConfig?.trailing_stop_percent ?? botConfig?.stop_loss_percent ?? '—'}%
              {' · '}
              {botConfig?.tp_mode === 'none'
                ? 'no TP'
                : botConfig?.tp_mode === 'strategy'
                  ? 'strategy target'
                  : `${botConfig?.take_profit_percent ?? '—'}% TP`}
            </strong>
          </div>
          <div><span className="text-muted-foreground">Timeframe:</span> <strong>{deployTimeframeSummary(botExecutionMode, botTimeframe)}</strong></div>
        </div>
        <DialogFooter showCloseButton={false}>
          <Button variant="outline" size="sm" onClick={() => setDeployOpen(false)}>Cancel</Button>
          <Button variant="buy" size="sm" onClick={confirmDeploy} disabled={liveBotsBlocked}>Confirm deploy</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
