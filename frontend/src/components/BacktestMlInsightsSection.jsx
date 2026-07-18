/**
 * ML / RL insight blocks for Backtest Lab Results (Phase 2).
 */
import { Alert, AlertDescription } from '@/components/ui/alert';
import { StatCard } from '@/components/StatCard';
import { getMLSubtype } from '@/config/strategies';
import FeatureImportanceChart from './FeatureImportanceChart';
import ConfusionMatrixGrid from './ConfusionMatrixGrid';
import RlEpisodeReplay from './RlEpisodeReplay';
import AlphaDecayMonitor from './AlphaDecayMonitor';
import { cn } from '@/lib/utils';

function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const pct = n <= 1 && n >= 0 ? n * 100 : n;
  return `${pct.toFixed(digits)}%`;
}

function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function predictionMass(ml) {
  const counts = ml?.prediction_counts;
  if (counts && typeof counts === 'object') {
    return {
      buy: Number(counts.BUY || 0),
      sell: Number(counts.SELL || 0),
      none: Number(counts.NONE || 0),
    };
  }
  const matrix = ml?.confusion_matrix;
  if (!Array.isArray(matrix) || matrix.length < 3) {
    return { buy: 0, sell: 0, none: 0 };
  }
  const col = (c) => matrix.reduce((s, row) => s + Number(row?.[c] || 0), 0);
  return { buy: col(0), sell: col(1), none: col(2) };
}

/** Average P/R/F1 over BUY+SELL when any directional preds exist; else null. */
function avgDirectionalClassMetric(obj, mass) {
  if (obj == null) return null;
  if (typeof obj === 'number') return obj;
  if (typeof obj !== 'object') return null;
  if ((mass.buy + mass.sell) <= 0) return null;
  const keys = ['BUY', 'SELL'].filter((k) => obj[k] != null);
  if (!keys.length) return null;
  return keys.reduce((s, k) => s + Number(obj[k] || 0), 0) / keys.length;
}

/** True when ml_metrics carries classifier prediction output (not an RL stub). */
function hasClassifierPredictions(ml) {
  if (!ml || typeof ml !== 'object') return false;
  if (ml.prediction_counts && typeof ml.prediction_counts === 'object') return true;
  if (Array.isArray(ml.confusion_matrix) && ml.confusion_matrix.length >= 3) return true;
  if (ml.accuracy != null || ml.prediction_warning) return true;
  return false;
}

/** Show IS/OOS only when a real out-of-sample side exists (walk-forward). */
function hasRealOosSplit(isVsOos) {
  if (!isVsOos || typeof isVsOos !== 'object') return false;
  return isVsOos.oos_sharpe != null || isVsOos.oos_pnl != null;
}

function ConfidenceHistogram({ distribution }) {
  const rows = (distribution || []).filter((d) => d && d.bucket != null);
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Number(r.count) || 0), 1);
  return (
    <div className="ml-insights__hist" aria-label="Confidence distribution">
      {rows.map((r) => (
        <div key={r.bucket} className="ml-insights__hist-col" title={`${r.bucket}: ${r.count}`}>
          <div
            className="ml-insights__hist-bar"
            style={{ height: `${Math.max(4, (Number(r.count) / max) * 100)}%` }}
          />
          <span className="ml-insights__hist-label">{String(r.bucket).replace(/^0\./, '.')}</span>
        </div>
      ))}
    </div>
  );
}

function RlActionBars({ actionDistribution }) {
  if (!actionDistribution) return null;
  const entries = [
    { key: 'long', label: 'Long', tone: 'up' },
    { key: 'short', label: 'Short', tone: 'down' },
    { key: 'flat', label: 'Flat', tone: 'neutral' },
  ].map((e) => ({ ...e, value: Number(actionDistribution[e.key] ?? 0) }));
  const total = Math.max(entries.reduce((s, e) => s + e.value, 0), 1);

  return (
    <div className="ml-insights__actions" aria-label="RL action distribution">
      {entries.map((e) => (
        <div key={e.key} className="ml-insights__action-row">
          <span className="ml-insights__action-label">{e.label}</span>
          <div className="ml-insights__action-track">
            <div
              className={cn('ml-insights__action-fill', `ml-insights__action-fill--${e.tone}`)}
              style={{ width: `${(e.value / total) * 100}%` }}
            />
          </div>
          <span className="num-mono text-[0.6rem] text-muted-foreground">
            {e.value} ({((e.value / total) * 100).toFixed(0)}%)
          </span>
        </div>
      ))}
    </div>
  );
}

export default function BacktestMlInsightsSection({ results, strategy, compact = false }) {
  const ml = results?.ml_metrics;
  const rl = results?.rl_data;
  const tradeCount = Number(results?.trade_count ?? results?.summary?.total_trades ?? 0);
  const isRl = getMLSubtype(strategy ?? results?.meta?.strategy) === 'rl';
  const isVsOos = ml?.is_vs_oos;
  const showIsOos = hasRealOosSplit(isVsOos);
  const gapWarn = showIsOos
    && Number(isVsOos.is_sharpe) > 0
    && Number(isVsOos.oos_sharpe) < Number(isVsOos.is_sharpe) * 0.5;

  const hasAny = ml || rl || gapWarn || (isRl && (rl?.episode_steps?.length || rl?.position_trajectory?.length));
  if (!hasAny) {
    return (
      <section className="algo-backtest-lab__section ml-insights ml-insights--empty">
        <p className="algo-backtest-table-scroll__caption mb-1">{isRl ? 'RL insights' : 'ML insights'}</p>
        <p className="text-xs text-muted-foreground">
          {isRl
            ? <>Run an RL backtest to populate action distribution, confidence, and episode replay (<code className="mx-0.5">rl_data</code>).</>
            : <>Run an ML backtest to populate prediction quality, feature importance, and IS/OOS metrics (<code className="mx-0.5">ml_metrics</code>).</>}
        </p>
      </section>
    );
  }

  const showClassifier = !isRl && hasClassifierPredictions(ml);
  const mass = predictionMass(ml);
  const directional = Number(ml?.directional_predictions ?? (mass.buy + mass.sell));
  const allNone = showClassifier && directional === 0;
  const avgPrecision = avgDirectionalClassMetric(ml?.precision, mass);
  const avgRecall = avgDirectionalClassMetric(ml?.recall, mass);
  const avgF1 = avgDirectionalClassMetric(ml?.f1, mass);
  const meanRecall = ml?.mean_recall ?? ml?.auc_roc;
  const actions = rl?.action_distribution || {};
  const longN = Number(actions.long ?? 0);
  const shortN = Number(actions.short ?? 0);
  const flatN = Number(actions.flat ?? 0);

  return (
    <section className={cn('algo-backtest-lab__section ml-insights', compact && 'ml-insights--compact')}>
      <p className="algo-backtest-table-scroll__caption mb-1.5">
        {isRl ? 'RL policy insights' : 'Model prediction quality'}
      </p>
      <p className="text-[0.65rem] text-muted-foreground mb-2">
        {isRl
          ? 'Actions = gated policy outputs (Long / Short / Flat) after confidence thresholds. Trades come from position changes, not classifier BUY/SELL labels.'
          : 'Labels = next-bar direction (±5 bps), not triple-barrier training labels. Predictions = gated strategy signals (after confidence / model lookup).'}
      </p>

      {isRl && (
        <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
          <StatCard label="Trades" value={String(tradeCount)} tone={tradeCount > 0 ? 'up' : 'neutral'} />
          <StatCard label="Long actions" value={String(longN)} tone={longN > 0 ? 'up' : 'neutral'} />
          <StatCard label="Short actions" value={String(shortN)} tone={shortN > 0 ? 'down' : 'neutral'} />
          <StatCard label="Flat actions" value={String(flatN)} />
        </div>
      )}

      {showClassifier && (allNone || ml?.prediction_warning) && (
        <Alert variant="default" className="py-2 mb-2 border-amber-500/40">
          <AlertDescription className="text-xs">
            {ml?.prediction_warning
              || 'All gated predictions were NONE — headline accuracy is mostly the flat-bar baseline, not tradeable skill.'}
            {tradeCount === 0 ? ' No trades were executed.' : null}
            {' '}
            Predicted BUY {mass.buy} · SELL {mass.sell} · NONE {mass.none}
            {ml?.majority_class_baseline != null
              ? ` · majority baseline ${fmtPct(ml.majority_class_baseline)}`
              : null}
          </AlertDescription>
        </Alert>
      )}

      {showClassifier && !allNone && (
        <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
          <StatCard label="Accuracy" value={fmtPct(ml.accuracy)} tone="accent" />
          <StatCard label="Mean recall" value={fmtNum(meanRecall)} tone="accent" />
          <StatCard label="Precision" value={fmtPct(avgPrecision)} />
          <StatCard label="Recall" value={fmtPct(avgRecall)} />
          <StatCard label="F1" value={fmtPct(avgF1)} />
          <StatCard
            label="Dir. signals"
            value={`${directional}`}
            tone={directional > 0 ? 'up' : 'neutral'}
          />
        </div>
      )}

      {showClassifier && allNone && (
        <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
          <StatCard label="Dir. BUY/SELL" value="0" tone="down" />
          <StatCard label="Predicted NONE" value={String(mass.none)} />
          <StatCard label="Majority baseline" value={fmtPct(ml.majority_class_baseline ?? ml.accuracy)} />
          <StatCard label="Trades" value={String(tradeCount)} />
        </div>
      )}

      {showIsOos && (
        <>
          <p className="algo-backtest-table-scroll__caption mb-1.5">In-sample vs out-of-sample</p>
          {gapWarn && (
            <Alert variant="default" className="py-2 mb-2 border-amber-500/40">
              <AlertDescription className="text-xs">
                IS Sharpe ({fmtNum(isVsOos.is_sharpe)}) is much higher than OOS (
                {fmtNum(isVsOos.oos_sharpe)}) — possible overfitting.
              </AlertDescription>
            </Alert>
          )}
          <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact mb-2">
            <StatCard label="IS Sharpe" value={fmtNum(isVsOos.is_sharpe)} />
            <StatCard label="OOS Sharpe" value={fmtNum(isVsOos.oos_sharpe)} tone={gapWarn ? 'down' : 'neutral'} />
            <StatCard label="IS PnL" value={isVsOos.is_pnl != null ? `$${fmtNum(isVsOos.is_pnl)}` : '—'} />
            <StatCard label="OOS PnL" value={isVsOos.oos_pnl != null ? `$${fmtNum(isVsOos.oos_pnl)}` : '—'} />
          </div>
        </>
      )}

      {ml?.alpha_decay && (
        <AlphaDecayMonitor alphaDecay={ml.alpha_decay} compact={compact} className="mb-3" />
      )}

      <div className={cn('ml-insights__viz-grid', compact && 'ml-insights__viz-grid--compact')}>
        {!isRl && ml?.feature_importance?.length > 0 && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">
              Feature importance
              {allNone ? ' (from trained model on disk)' : ''}
            </p>
            <FeatureImportanceChart
              features={ml.feature_importance}
              maxBars={compact ? 5 : 10}
              compact={compact}
            />
          </div>
        )}
        {showClassifier && ml?.confusion_matrix?.length > 0 && !compact && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">Confusion matrix</p>
            <ConfusionMatrixGrid matrix={ml.confusion_matrix} />
          </div>
        )}
        {ml?.confidence_distribution?.length > 0 && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">Confidence distribution</p>
            <ConfidenceHistogram distribution={ml.confidence_distribution} />
          </div>
        )}
        {isRl && rl?.action_distribution && (
          <div>
            <p className="algo-backtest-table-scroll__caption mb-1">RL action distribution</p>
            <RlActionBars actionDistribution={rl.action_distribution} />
          </div>
        )}
      </div>

      {isRl && (
        <RlEpisodeReplay rlData={rl} compact={compact} className="mt-3" />
      )}
    </section>
  );
}
