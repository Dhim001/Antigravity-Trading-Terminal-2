/**
 * BacktestWorkflowPresets — one-click research workflows.
 */
import React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { defaultPortfolioSymbols } from '@/lib/portfolioBacktest';
import { getAutoDeployMode, startPipeline } from '@/lib/mlPipeline';
import { postMlLabRequest } from '@/lib/mlLabRequests';

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
    id: 'wf_rigorous',
    label: 'WF rigorous',
    hint: '30d · Calmar · multi-fold WF · purged',
  },
  {
    id: 'meta_label_sweep',
    label: 'Meta-label sweep',
    hint: 'CHART_AGENT gate on/off comparison',
  },
  {
    id: 'portfolio_optimize',
    label: 'Portfolio optimize',
    hint: 'Basket symbols · shared risk params',
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
  {
    id: 'ml_full_pipeline',
    label: 'ML Pipeline',
    hint: 'Search → Train → Validate → Backtest → Gate',
  },
  {
    id: 'ml_retrain_validate',
    label: 'Retrain + Validate',
    hint: 'Train then walk-forward (no backtest)',
  },
  {
    id: 'ml_batch_train',
    label: 'Batch Train All',
    hint: 'Train multiple strategies for current symbol',
  },
];

export function applyWorkflowPreset(
  presetId,
  {
    activeSymbol,
    symbolsList,
    botStrategy,
    botTimeframe,
    trainingWindow,
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
    setOptimizerPreset,
    startPipeline: startPipelineCb,
    getAutoDeployMode: getAutoDeployModeCb,
    openBatchTrainDialog,
    onMlPipelineTrain,
  } = {},
) {
  const start = startPipelineCb || startPipeline;
  const deployMode = getAutoDeployModeCb || getAutoDeployMode;

  switch (presetId) {
    case 'ml_full_pipeline': {
      const pipelineId = start({
        strategy: botStrategy,
        symbol: activeSymbol,
        timeframe: botTimeframe,
        trainingWindow,
        autoAdvance: true,
        autoDeployMode: deployMode(),
        presetId: 'ml_full_pipeline',
      });
      onMlPipelineTrain?.({
        pipelineId,
        strategy: botStrategy,
        symbol: activeSymbol,
        timeframe: botTimeframe,
        trainingWindow,
        mode: 'full',
      });
      return true;
    }
    case 'ml_retrain_validate': {
      const pipelineId = start({
        strategy: botStrategy,
        symbol: activeSymbol,
        timeframe: botTimeframe,
        trainingWindow,
        autoAdvance: true,
        autoDeployMode: 'approval',
        presetId: 'ml_retrain_validate',
        stopAfterValidate: true,
      });
      onMlPipelineTrain?.({
        pipelineId,
        strategy: botStrategy,
        symbol: activeSymbol,
        timeframe: botTimeframe,
        trainingWindow,
        mode: 'retrain_validate',
      });
      return true;
    }
    case 'ml_batch_train':
      // Callback opens the Lab dock; the mailbox request survives the remount.
      openBatchTrainDialog?.();
      postMlLabRequest('ml-lab-open-batch', {
        scope: 'all', symbol: activeSymbol, timeframe: botTimeframe,
      });
      return true;
    case 'wf_rigorous':
      setBacktestDays('30');
      setBacktestOos(false);
      setBacktestReasoning(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(false);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      if (setOptimizerPreset) {
        setOptimizerPreset({
          objective: 'calmar_ratio',
          rollingWf: true,
          rollingFolds: 3,
          purgedSplits: true,
          wfMode: 'rolling',
        });
      }
      openBacktestLab('optimizer');
      break;
    case 'meta_label_sweep':
      if (botStrategy !== 'CHART_AGENT') return false;
      setBacktestDays('30');
      setBacktestOos(false);
      setPortfolioBacktest(false);
      setMetaLabelWalkForward(false);
      setBacktestSimMode('live_aligned');
      setBacktestLiveParity(true);
      if (setOptimizerPreset) {
        setOptimizerPreset({
          objective: 'calmar_ratio',
          enabled: {
            calibration_gate_enabled: true,
            meta_label_model_mode: true,
            trailing_stop_percent: true,
          },
          values: {
            calibration_gate_enabled: 'true, false',
            meta_label_model_mode: 'wilson, hybrid',
          },
        });
      }
      openBacktestLab('optimizer');
      break;
    case 'portfolio_optimize':
      setBacktestDays('14');
      setBacktestOos(false);
      setPortfolioBacktest(true);
      setPortfolioSymbols(defaultPortfolioSymbols(activeSymbol, symbolsList));
      setMetaLabelWalkForward(false);
      if (setOptimizerPreset) {
        setOptimizerPreset({ objective: 'calmar_ratio', portfolioSweep: true });
      }
      openBacktestLab('optimizer');
      break;
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
      // Meta-label WF lives on Algo BACKTEST (not Lab Optimizer).
      break;
    default:
      return false;
  }
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
          const blocked = (
            (preset.id === 'meta_label_validate' || preset.id === 'meta_label_sweep')
            && botStrategy !== 'CHART_AGENT'
          );
          const isActive = activePreset === preset.id;
          return (
            <Button
              key={preset.id}
              type="button"
              variant={isActive ? 'secondary' : 'outline'}
              size="xs"
              className={cn('bt-workflow-presets__chip', isActive && 'bt-workflow-presets__chip--active')}
              disabled={disabled || blocked}
              title={blocked ? 'CHART_AGENT only' : preset.hint}
              onClick={() => onSelect(preset.id)}
            >
              <span className="bt-workflow-presets__chip-label">{preset.label}</span>
            </Button>
          );
        })}
      </div>
    </div>
  );
}
