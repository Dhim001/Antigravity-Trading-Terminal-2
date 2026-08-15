import React, { useSyncExternalStore } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DataTableRow, DataTableCell } from '../DataTableShell';
import StrategyBadge from '../StrategyBadge';
import MlModelStatusBadge, { isMlStrategy } from '../MlModelStatusBadge';
import { ModelHealthBadge } from '../ml-lab/ModelHealthBadge';
import { formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { useEffectiveRiskHold, botRuntimeActivityHint } from '@/lib/botRiskHold';
import { formatLastSignal } from '@/lib/formatTime';
import { botStatusLabel, normalizeBotStatus } from '@/lib/botAttribution';
import { selectAgentInsight } from '@/lib/agentInsights';
import {
  getCachedModelStatus,
  normalizeStatusTimeframe,
  subscribeModelStatusCache,
} from '@/lib/mlTrainingSession';
import { openModelTrainingDock } from '@/lib/workspaceNav';
import { postMlLabRequest } from '@/lib/mlLabRequests';
import { cn } from '@/lib/utils';
import { Pause, PlayCircle, RefreshCw } from 'lucide-react';

function statusBadgeVariant(status) {
  if (status === 'RUNNING') return 'buy';
  if (status === 'PAUSED') return 'outline';
  if (status === 'ERROR') return 'destructive';
  return 'sell';
}

function activityHintVariant(kind) {
  if (kind === 'cooling_off') return 'outline';
  if (kind === 'held') return 'secondary';
  return 'outline';
}

export default function ActiveBotRow({
  bot,
  ownedPos,
  selected,
  agentInsights,
  safeModeActive,
  onSelect,
  onPause,
  onResume,
  onStop,
  onSetStopLoss,
  onSetTakeProfit,
}) {
  const inPosition = ownedPos && Math.abs(ownedPos.size) > 0;
  const status = normalizeBotStatus(bot.status);
  const { hold: riskHold, remaining } = useEffectiveRiskHold(bot.risk_hold);
  const activity = botRuntimeActivityHint({ ...bot, status }, {
    hold: riskHold,
    remainingSec: remaining,
    safeModeActive,
  });
  const mlTimeframe = normalizeStatusTimeframe(
    bot.timeframe || bot.config?.timeframe || '1m',
  );
  // Subscribe so health badge updates when MlModelStatusBadge fills the cache.
  // A useMemo around getCachedModelStatus froze the initial null forever.
  const modelStatus = useSyncExternalStore(
    subscribeModelStatusCache,
    () => (
      isMlStrategy(bot.strategy)
        ? getCachedModelStatus(bot.symbol, bot.strategy, mlTimeframe)
        : null
    ),
    () => null,
  );

  const handleRetrain = (e) => {
    e?.stopPropagation?.();
    openModelTrainingDock();
    postMlLabRequest('ml-lab-retrain', {
      strategy: bot.strategy,
      symbol: bot.symbol,
      timeframe: mlTimeframe,
    });
  };

  return (
    <DataTableRow
      rowVariant="dock"
      deferred
      className={cn(
        'algo-bot-row cursor-pointer',
        selected && 'row-active',
        riskHold?.kind === 'cooloff' && 'algo-bot-row--cooloff',
        riskHold?.kind === 'streak_limit' && 'algo-bot-row--streak-hold',
        riskHold?.kind === 'drawdown' && 'algo-bot-row--drawdown-hold',
      )}
      onClick={() => onSelect(bot.id)}
    >
      <DataTableCell className="font-bold">{bot.symbol}</DataTableCell>
      <DataTableCell className="text-xs">
        <div className="flex flex-col items-start gap-0.5">
          <span className="inline-flex items-center gap-1 flex-wrap">
            <StrategyBadge strategy={bot.strategy} compact />
            {bot.execution_mode === 'TICK' && (
              <Badge variant="outline" className="h-4 px-1 text-[0.65rem]">TICK</Badge>
            )}
          </span>
          {isMlStrategy(bot.strategy) && (
            <>
              <MlModelStatusBadge
                strategy={bot.strategy}
                symbol={bot.symbol}
                timeframe={mlTimeframe}
                modelVersion={bot.config?.model_version}
                compact
              />
              <ModelHealthBadge
                bot={bot}
                status={modelStatus}
                compact
                onClick={handleRetrain}
              />
            </>
          )}
        </div>
      </DataTableCell>
      <DataTableCell align="center" className="text-xs num-mono text-muted-foreground">
        {bot.execution_mode === 'TICK' ? 'tick' : formatBarTimeframeLabel(bot.timeframe)}
      </DataTableCell>
      <DataTableCell align="center">
        {inPosition ? (
          <Badge
            variant={ownedPos.size > 0 ? 'buy' : 'sell'}
            title={`Bot size ${Math.abs(ownedPos.size).toFixed(4)}`}
          >
            {ownedPos.label}
          </Badge>
        ) : (
          <span className="text-secondary-foreground text-xs">FLAT</span>
        )}
      </DataTableCell>
      <DataTableCell numeric align="right">${bot.allocation.toLocaleString()}</DataTableCell>
      <DataTableCell
        numeric
        align="right"
        className={cn(
          'font-semibold',
          (bot.daily_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
        )}
      >
        {(bot.daily_pnl ?? 0) >= 0 ? '+' : ''}{(bot.daily_pnl ?? 0).toFixed(2)}
      </DataTableCell>
      <DataTableCell className="algo-last-signal">
        <span title={bot.last_signal_at || undefined}>{formatLastSignal(bot.last_signal_at)}</span>
        {bot.strategy === 'CHART_AGENT' && (() => {
          const insight = selectAgentInsight(
            agentInsights,
            bot.symbol,
            bot.execution_mode === 'TICK' ? '1m' : bot.timeframe,
          );
          return insight?.confidence != null ? (
            <span className="ml-1 text-xs text-muted-foreground">
              ({Math.round(insight.confidence * 100)}% conf)
            </span>
          ) : null;
        })()}
      </DataTableCell>
      <DataTableCell align="center">
        <div className="algo-bot-status-cell">
          <Badge variant={statusBadgeVariant(status)}>{botStatusLabel(status)}</Badge>
          {activity && (
            <Badge
              variant={activityHintVariant(activity.kind)}
              className={cn(
                'algo-bot-activity-hint',
                activity.kind === 'cooling_off' && 'algo-bot-activity-hint--cooloff',
                activity.kind === 'held' && 'algo-bot-activity-hint--held',
                activity.kind === 'no_signal' && 'algo-bot-activity-hint--no-signal',
              )}
              title={activity.title || activity.label}
            >
              {activity.label}
            </Badge>
          )}
        </div>
      </DataTableCell>
      <DataTableCell align="center" onClick={(e) => e.stopPropagation()}>
        <div className="algo-bot-actions">
          {isMlStrategy(bot.strategy) && (
            <Button
              variant="outline"
              size="xs"
              onClick={handleRetrain}
              title="Retrain model in ML Lab"
            >
              <RefreshCw />
            </Button>
          )}
          {status === 'RUNNING' && (
            <Button variant="outline" size="xs" onClick={() => onPause(bot.id)} title="Pause — stop evaluating new bars">
              <Pause />
              Pause
            </Button>
          )}
          {status === 'PAUSED' && (
            <Button variant="default" size="xs" onClick={() => onResume(bot.id)} title="Resume — start evaluating new bars">
              <PlayCircle />
              Resume
            </Button>
          )}
          {status !== 'STOPPED' && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => onSetStopLoss(bot)}
              title="Set stop loss on chart"
            >
              SL
            </Button>
          )}
          {status !== 'STOPPED' && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => onSetTakeProfit(bot)}
              title="Set take profit on chart"
            >
              TP
            </Button>
          )}
          {status !== 'STOPPED' && (
            <Button variant="destructive" size="xs" onClick={() => onStop(bot.id)} title="Stop bot">
              STOP
            </Button>
          )}
        </div>
      </DataTableCell>
    </DataTableRow>
  );
}
