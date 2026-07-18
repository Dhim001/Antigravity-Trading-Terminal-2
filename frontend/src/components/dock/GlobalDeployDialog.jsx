/**
 * GlobalDeployDialog.jsx — Bot deploy confirmation dialog (extracted from ResizableDock).
 *
 * Shared deploy dialog triggered by the optimizer, cross-tab pendingDeploy flag,
 * or the Algo tab's deploy action. Shows strategy, symbol, allocation, SL/TP, and timeframe summary.
 */
import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import { useResearchStore } from '../../store/useResearchStore';
import { sendAction } from '../../api/transport';
import { Action } from '../../api/protocol';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import StrategyBadge from '../StrategyBadge';
import DeployGatePanel from '../DeployGatePanel';
import { deployTimeframeSummary } from '@/lib/barTimeframes';
import { buildDeployPayload } from '@/lib/deployGate';
import { useShallow } from 'zustand/react/shallow';

export default function GlobalDeployDialog({ switchToAlgoTab }) {
  const pendingDeploy = useResearchStore((s) => s.pendingDeploy);
  const setPendingDeploy = useResearchStore((s) => s.setPendingDeploy);
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
  const {
    backtestResults, backtestSnapshot, backtestDays,
  } = useResearchStore(useShallow((s) => ({
    backtestResults: s.backtestResults,
    backtestSnapshot: s.backtestSnapshot,
    backtestDays: s.backtestDays,
  })));
  const [deployOpen, setDeployOpen] = useState(false);
  const [forceDeploy, setForceDeploy] = useState(false);
  const [deployGate, setDeployGate] = useState(null);

  useEffect(() => {
    if (pendingDeploy) {
      switchToAlgoTab();
      setDeployOpen(true);
      setPendingDeploy(false);
    }
  }, [pendingDeploy, setPendingDeploy, switchToAlgoTab]);

  const liveBotsBlocked = isLive && !allowLiveBots;

  const confirmDeploy = () => {
    if (deployGate?.blocking && !forceDeploy) {
      toast.error(deployGate.block_reason || 'Deploy gate blocked');
      return;
    }
    setDeployOpen(false);
    if (liveBotsBlocked) {
      toast.error('Live bot trading is disabled. Set ALLOW_LIVE_BOTS=true on the server.');
      return;
    }
    if (!botConfig?.allocation || botConfig.allocation <= 0) {
      toast.error('Enter a valid max notional cap');
      return;
    }
    const days = parseInt(backtestDays, 10) || 7;
    const payload = buildDeployPayload({
      strategy: botStrategy,
      symbol: activeSymbol,
      timeframe: botExecutionMode === 'TICK' ? 'tick' : botTimeframe,
      allocation: botConfig.allocation,
      executionMode: botExecutionMode,
      config: botConfig,
      results: backtestResults,
      snapshot: backtestSnapshot,
      days,
      forceDeploy,
    });
    sendAction(Action.BOT_CREATE, payload);
  };

  return (
    <Dialog open={deployOpen} onOpenChange={(open) => {
      setDeployOpen(open);
      if (!open) setForceDeploy(false);
    }}>
      <DialogContent className="algo-dialog sm:max-w-md" overlayClassName="admin-panel-overlay">
        <DialogHeader>
          <DialogTitle>Deploy trading bot</DialogTitle>
          <DialogDescription className="text-xs leading-relaxed">
            Forward-test workflow: validate backtest OOS before allocating capital.
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
          {(botConfig?.model_artifact || botConfig?.model_version) && (
            <div>
              <span className="text-muted-foreground">ML model:</span>{' '}
              <strong className="num-mono text-xs">
                {botConfig.model_artifact || 'artifact'}
                {botConfig.model_version
                  ? ` · ${String(botConfig.model_version).slice(0, 19)}`
                  : ''}
              </strong>
            </div>
          )}
        </div>
        <DeployGatePanel
          results={backtestResults}
          symbol={activeSymbol}
          strategy={botStrategy}
          timeframe={botExecutionMode === 'TICK' ? 'tick' : botTimeframe}
          days={parseInt(backtestDays, 10) || 7}
          config={botConfig}
          snapshot={backtestSnapshot}
          onGateChange={setDeployGate}
          forceDeploy={forceDeploy}
          onForceDeployChange={setForceDeploy}
        />
        <DialogFooter showCloseButton={false}>
          <Button variant="outline" size="sm" onClick={() => setDeployOpen(false)}>Cancel</Button>
          <Button
            variant="buy"
            size="sm"
            onClick={confirmDeploy}
            disabled={liveBotsBlocked || (deployGate?.blocking && !forceDeploy)}
          >
            Confirm deploy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
