/**
 * BacktestWorkflowPresets — one-click research workflows.
 */
import React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { defaultPortfolioSymbols } from '@/lib/portfolioBacktest';

export const WORKFLOW_PRESETS = [
  {
    id: 'quick_baseline',
    label: '7d baseline',
    hint: 'Live parity · single symbol',
  },
  {
    id: 'oos_validate',
    label: 'OOS holdout',
    hint: '30% OOS window · deploy prep',
  },
  {
    id: 'portfolio_basket',
    label: 'Portfolio basket',
    hint: 'Multi-symbol · shared capital',
  },
  {
    id: 'wf_optimize',
    label: 'WF optimize',
    hint: 'Open Lab optimizer',
  },
  {
    id: 'meta_label_validate',
    label: 'Meta-label WF',
    hint: 'CHART_AGENT classifier validation',
  },
];

export function applyWorkflowPreset(
  presetId,
  {
    activeSymbol,
    symbolsList,
    botStrategy,
    setBacktestDays,
    setBacktestOos,
    setBacktestReasoning,
    setPortfolioBacktest,
    setPortfolioSymbols,
    setBacktestSimMode,
    setBacktestLiveParity,
    setMetaLabelWalkForward,
    openBacktestLab,
    setBacktestLabTab,
  },
) {
  switch (presetId) {
    case 'quick_baseline':
      setBacktestDays('7');
      setBacktestOos(false);
      setBacktestReasoning(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(false);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      break;
    case 'oos_validate':
      setBacktestDays('30');
      setBacktestOos(true);
      setBacktestReasoning(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(false);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      break;
    case 'portfolio_basket':
      setBacktestDays('7');
      setBacktestOos(false);
      setBacktestReasoning(false);
      setPortfolioBacktest(true);
      setPortfolioSymbols(defaultPortfolioSymbols(activeSymbol, symbolsList));
      setMetaLabelWalkForward(false);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      break;
    case 'wf_optimize':
      setBacktestDays('30');
      setBacktestOos(false);
      setBacktestReasoning(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(false);
      openBacktestLab('optimizer');
      break;
    case 'meta_label_validate':
      if (botStrategy !== 'CHART_AGENT') return false;
      setBacktestDays('30');
      setBacktestOos(false);
      setBacktestReasoning(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(true);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      openBacktestLab('optimizer');
      break;
    default:
      return false;
  }
  if (setBacktestLabTab) setBacktestLabTab('results');
  return true;
}

export default function BacktestWorkflowPresets({
  activePreset,
  onSelect,
  botStrategy,
  disabled,
  className,
}) {
  return (
    <div className={cn('bt-workflow-presets', className)}>
      <p className="bt-workflow-presets__label">Workflow presets</p>
      <div className="bt-workflow-presets__rail">
        {WORKFLOW_PRESETS.map((preset) => {
          const blocked = preset.id === 'meta_label_validate' && botStrategy !== 'CHART_AGENT';
          return (
            <Button
              key={preset.id}
              type="button"
              variant={activePreset === preset.id ? 'secondary' : 'outline'}
              size="xs"
              className="bt-workflow-presets__chip"
              disabled={disabled || blocked}
              title={blocked ? 'CHART_AGENT only' : preset.hint}
              onClick={() => onSelect(preset.id)}
            >
              {preset.label}
            </Button>
          );
        })}
      </div>
    </div>
  );
}
