/**
 * ML / DL / RL hyperparameter optimizer for Backtest Lab.
 * Training controls live in Model Training dock tab.
 */
import { useCallback, useEffect, useState } from 'react';
import TaOptimizerPanel from './TaOptimizerPanel';
import MlModelStatusBadge from './MlModelStatusBadge';
import FeatureImportanceChart from './FeatureImportanceChart';
import ConfusionMatrixGrid from './ConfusionMatrixGrid';
import RlEpisodeReplay from './RlEpisodeReplay';
import AlphaDecayMonitor from './AlphaDecayMonitor';
import { getStrategyMeta, getMLSubtype } from '@/config/strategies';
import { getMlObjectiveOptions, getMlSubtypeSweepHint } from '@/lib/optimizerDefaults';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { BrainCircuit, ExternalLink } from 'lucide-react';
import { apiRequest } from '@/api/client';
import { openModelTrainingDock } from '../lib/workspaceNav';

const BASE_OBJECTIVES = [
  { value: 'robust_score', label: 'Robust score (Sharpe × √trades)' },
  { value: 'calmar_ratio', label: 'Calmar ratio' },
  { value: 'max_drawdown_penalty', label: 'PnL − DD penalty' },
  { value: 'sharpe_ratio', label: 'Sharpe ratio' },
  { value: 'total_pnl', label: 'Total PnL' },
  { value: 'win_rate', label: 'Win rate' },
];

const ML_OBJECTIVES = getMlObjectiveOptions(BASE_OBJECTIVES);

const ARTIFACT_BY_STRATEGY = {
  ML_SIGNAL_BOOST: 'model.joblib',
  LSTM_DIRECTION: 'lstm_direction.onnx',
  RL_PPO_AGENT: 'ppo_policy.onnx',
  TCN_MULTI_HORIZON: 'tcn_multi_horizon.onnx',
  VAE_REGIME_DETECTOR: 'vae_regime.onnx',
  TRANSFORMER_SIGNAL: 'transformer_signal.onnx',
  GNN_CROSS_ASSET: 'gnn_cross_asset.onnx',
};

function MlValidationFooter({ results, strategy }) {
  const ml = results?.ml_metrics;
  const rl = results?.rl_data;
  const subtype = getMLSubtype(strategy);
  const isVsOos = ml?.is_vs_oos;
  const gapWarn = isVsOos
    && Number(isVsOos.is_sharpe) > 0
    && Number(isVsOos.oos_sharpe) < Number(isVsOos.is_sharpe) * 0.5;

  const hasViz = Boolean(
    ml?.feature_importance?.length
    || ml?.confusion_matrix?.length
    || ml?.alpha_decay
    || rl?.action_distribution
    || rl?.episode_steps?.length
    || rl?.position_trajectory?.length
    || gapWarn,
  );

  if (!hasViz) {
    return (
      <section className="algo-backtest-sweep__card optimizer-panel__placeholder mt-3" aria-label="ML visualizations">
        <h5 className="algo-backtest-sweep__card-title">ML validation</h5>
        <p className="text-xs text-muted-foreground">
          Feature importance, confusion matrix, and IS/OOS gap warnings appear here when
          backtest results include <code className="mx-1">ml_metrics</code>.
          {subtype === 'rl' && (
            <> RL action distribution uses <code className="mx-1">rl_data</code>.</>
          )}
        </p>
      </section>
    );
  }

  return (
    <section className="algo-backtest-sweep__card mt-3 space-y-3" aria-label="ML visualizations">
      <h5 className="algo-backtest-sweep__card-title">ML validation</h5>
      {gapWarn && (
        <Alert variant="default" className="py-2 border-amber-500/40">
          <AlertDescription className="text-xs">
            IS Sharpe much higher than OOS — possible overfitting. Prefer robust_score or oos_is_ratio
            and walk-forward before deploy.
          </AlertDescription>
        </Alert>
      )}
      {ml?.alpha_decay && (
        <AlphaDecayMonitor alphaDecay={ml.alpha_decay} compact />
      )}
      {ml?.feature_importance?.length > 0 && (
        <div>
          <p className="text-[0.6rem] uppercase text-muted-foreground mb-1">Feature importance</p>
          <FeatureImportanceChart features={ml.feature_importance} maxBars={8} />
        </div>
      )}
      {ml?.confusion_matrix?.length > 0 && (
        <div>
          <p className="text-[0.6rem] uppercase text-muted-foreground mb-1">Confusion matrix</p>
          <ConfusionMatrixGrid matrix={ml.confusion_matrix} />
        </div>
      )}
      {subtype === 'rl' && rl?.action_distribution && (
        <p className="text-xs text-muted-foreground num-mono">
          Actions — long {rl.action_distribution.long ?? 0}
          {' · '}
          short {rl.action_distribution.short ?? 0}
          {' · '}
          flat {rl.action_distribution.flat ?? 0}
        </p>
      )}
      {subtype === 'rl' && (
        <RlEpisodeReplay rlData={rl} />
      )}
    </section>
  );
}

function ModelPinSlot({
  pinEnabled,
  setPinEnabled,
  modelStatus,
  artifactName,
  pinnedVersion,
  setPinnedVersion,
}) {
  const versions = Array.isArray(modelStatus?.versions) ? modelStatus.versions : [];
  const trainedAt = pinnedVersion || modelStatus?.trained_at;
  return (
    <div className="ml-optimizer__pin w-full mb-2 space-y-1.5">
      <label className="flex items-center gap-2 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={pinEnabled}
          onChange={(e) => setPinEnabled(e.target.checked)}
          disabled={!modelStatus?.trained}
        />
        Pin model artifact on deploy
      </label>
      {pinEnabled && modelStatus?.trained && (
        <div className="text-[0.65rem] text-muted-foreground space-y-1 pl-5">
          <p>
            <span className="uppercase tracking-wide">Artifact</span>{' '}
            <span className="num-mono text-foreground">{artifactName}</span>
          </p>
          {versions.length > 0 ? (
            <label className="flex flex-col gap-0.5">
              <span className="uppercase tracking-wide">Version</span>
              <select
                className="h-7 rounded border border-border/60 bg-background px-1.5 text-[0.65rem] num-mono text-foreground"
                value={pinnedVersion || modelStatus.trained_at || ''}
                onChange={(e) => setPinnedVersion(e.target.value)}
              >
                {versions.map((v) => (
                  <option
                    key={v.version_id || v.trained_at}
                    value={v.trained_at || v.version_id}
                  >
                    {(v.trained_at ? new Date(v.trained_at).toLocaleString() : v.version_id)
                      + (v.is_current ? ' (current)' : '')}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p>
              <span className="uppercase tracking-wide">Version</span>{' '}
              <span className="num-mono text-foreground">
                {trainedAt ? new Date(trainedAt).toLocaleString() : '—'}
              </span>
            </p>
          )}
        </div>
      )}
      {!modelStatus?.trained && (
        <p className="text-[0.65rem] text-amber-400/90 pl-5">
          No trained model for this symbol — train in Model Training first.
        </p>
      )}
    </div>
  );
}

export default function MlOptimizerPanel(props) {
  const { symbol, strategy, days, timeframe, results } = props;
  const meta = getStrategyMeta(strategy);
  const subtype = getMLSubtype(strategy);
  const subtypeLabel = subtype === 'rl' ? 'RL' : subtype === 'unsupervised' ? 'Unsupervised' : 'Supervised';
  const [modelStatus, setModelStatus] = useState(null);
  const [pinEnabled, setPinEnabled] = useState(true);
  const [pinnedVersion, setPinnedVersion] = useState('');

  const fetchStatus = useCallback(async () => {
    if (!symbol || !strategy) {
      setModelStatus(null);
      return;
    }
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}`,
      );
      setModelStatus(body);
      if (body?.trained) {
        setPinEnabled(true);
        setPinnedVersion(body.trained_at || body.model_version || '');
      }
    } catch {
      setModelStatus({ trained: false });
    }
  }, [symbol, strategy]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const artifactName = modelStatus?.artifact
    || ARTIFACT_BY_STRATEGY[String(strategy || '').toUpperCase()]
    || 'model';

  const getDeployExtras = useCallback(() => {
    if (!pinEnabled || !modelStatus?.trained) return null;
    return {
      model_symbol: String(symbol || '').toUpperCase(),
      model_version: pinnedVersion || modelStatus.trained_at || modelStatus.model_version || '',
      model_artifact: artifactName,
    };
  }, [pinEnabled, modelStatus, symbol, artifactName, pinnedVersion]);

  return (
    <div className="optimizer-panel optimizer-panel--ml">
      <section className="algo-backtest-sweep__card optimizer-panel__hero" aria-label="Model status">
        <div className="optimizer-panel__hero-row">
          <div className="optimizer-panel__hero-copy">
            <h5 className="algo-backtest-sweep__card-title flex items-center gap-2">
              <BrainCircuit size={14} aria-hidden />
              {meta.shortLabel} optimizer
              <span className="algo-template-btn__ml-pill">{subtypeLabel}</span>
            </h5>
            <p className="text-xs text-muted-foreground mt-1">
              {getMlSubtypeSweepHint(strategy)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              <span className="algo-backtest-sweep__chip">{symbol}</span>
              <span className="algo-backtest-sweep__chip">{strategy}</span>
              <span className="algo-backtest-sweep__chip num-mono">{days}d · {timeframe}</span>
            </p>
          </div>
          <div className="optimizer-panel__hero-actions flex flex-col items-end gap-2">
            <MlModelStatusBadge strategy={strategy} symbol={symbol} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1"
              onClick={openModelTrainingDock}
            >
              Model Training
              <ExternalLink size={12} aria-hidden />
            </Button>
          </div>
        </div>
      </section>

      <TaOptimizerPanel
        {...props}
        panelTitle="Hyperparameter sweep"
        objectiveOptions={ML_OBJECTIVES}
        footerSlot={<MlValidationFooter results={results} strategy={strategy} />}
        getDeployExtras={getDeployExtras}
        deploySlot={(
          <ModelPinSlot
            pinEnabled={pinEnabled}
            setPinEnabled={setPinEnabled}
            modelStatus={modelStatus}
            artifactName={artifactName}
            pinnedVersion={pinnedVersion}
            setPinnedVersion={setPinnedVersion}
          />
        )}
      />
    </div>
  );
}
