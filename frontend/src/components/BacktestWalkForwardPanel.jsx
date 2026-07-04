/**
 * BacktestWalkForwardPanel.jsx — Walk-forward in-sample vs out-of-sample summary.
 * Deploy uses the shared deploy gate (forward test before capital).
 */
import React, { useState, useCallback, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { toast } from 'sonner';
import BacktestOosStitchChart from './BacktestOosStitchChart';
import { evaluateDeployGate, buildDeployPayload } from '@/lib/deployGate';
import { backtestFingerprint } from '@/lib/backtestDisplay';

function Metric({ label, value, tone }) {
  return (
    <div className="algo-backtest-wf__metric">
      <span className="text-muted-foreground">{label}</span>
      <strong className={cn('num-mono', tone)}>{value}</strong>
    </div>
  );
}

function FoldSummary({ fold }) {
  const is = fold.in_sample ?? {};
  const oos = fold.out_of_sample ?? {};
  const isSummary = is.summary ?? {};
  const oosSummary = oos.summary ?? {};

  return (
    <tr>
      <td className="num-mono text-center">{fold.fold ?? '—'}</td>
      <td className={cn(
        'num-mono text-right whitespace-nowrap',
        (is.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {is.total_pnl != null ? `$${Number(is.total_pnl).toFixed(2)}` : '—'}
      </td>
      <td className={cn(
        'num-mono text-right whitespace-nowrap',
        (oos.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
      )}>
        {oos.total_pnl != null ? `$${Number(oos.total_pnl).toFixed(2)}` : '—'}
      </td>
      <td className="num-mono text-right">
        {oosSummary.sharpe_ratio != null ? Number(oosSummary.sharpe_ratio).toFixed(2) : '—'}
      </td>
      <td className="num-mono text-right">{oos.trade_count ?? oosSummary.total_trades ?? '—'}</td>
    </tr>
  );
}

function DeployButton({
  symbol,
  strategy,
  timeframe,
  allocation,
  bestConfig,
  walkForward,
  runId,
  results,
  days,
}) {
  const [deploying, setDeploying] = useState(false);
  const [deployed, setDeployed] = useState(null);
  const [forceDeploy, setForceDeploy] = useState(false);

  const gate = useMemo(
    () => evaluateDeployGate({
      results: { ...results, walk_forward: walkForward, run_id: runId },
      symbol,
      strategy,
      timeframe,
      days,
      config: bestConfig,
    }),
    [results, walkForward, runId, symbol, strategy, timeframe, days, bestConfig],
  );

  const canDeploy = gate.passed || forceDeploy;

  const handleDeploy = useCallback(async () => {
    if (deploying || deployed || !canDeploy) return;
    setDeploying(true);
    try {
      const snapshot = backtestFingerprint({
        symbol,
        strategy,
        days: String(days),
        timeframe,
        config: bestConfig,
      });
      const payload = buildDeployPayload({
        strategy: strategy || 'CHART_AGENT',
        symbol,
        timeframe: timeframe || '1m',
        allocation: allocation || 1000,
        executionMode: 'BAR_CLOSE',
        config: {
          ...(bestConfig || {}),
          walk_forward_deploy: true,
          pipeline_source: 'walk_forward_ui',
        },
        results: { run_id: runId },
        snapshot,
        days,
        forceDeploy,
      });
      const { ok, error } = await sendAction(Action.BOT_CREATE, payload);
      if (ok) {
        setDeployed({ ok: true });
        toast.success(`Bot deployed for ${symbol} using walk-forward best config`);
      } else {
        setDeployed({ ok: false, message: error });
        toast.error(`Deploy failed: ${error}`);
      }
    } catch (err) {
      setDeployed({ ok: false, message: err?.message });
      toast.error(`Deploy error: ${err?.message}`);
    } finally {
      setDeploying(false);
    }
  }, [
    deploying, deployed, canDeploy, symbol, strategy, timeframe, allocation,
    bestConfig, runId, days, forceDeploy,
  ]);

  if (deployed?.ok) {
    return (
      <div className="algo-backtest-wf__deploy-done">
        <span className="text-trading-up font-semibold text-xs">
          ✓ Bot deployed for {symbol}
        </span>
      </div>
    );
  }

  return (
    <div className="algo-backtest-wf__deploy-wrap">
      {gate.blocking && (
        <label className="deploy-gate__force mb-2">
          <input
            type="checkbox"
            checked={forceDeploy}
            onChange={(e) => setForceDeploy(e.target.checked)}
          />
          <span>Deploy anyway (bypass OOS gate)</span>
        </label>
      )}
      <button
        className={cn(
          'algo-backtest-wf__deploy-btn',
          !canDeploy && 'opacity-50 cursor-not-allowed',
        )}
        disabled={!canDeploy || deploying}
        onClick={handleDeploy}
        title={
          !canDeploy
            ? gate.block_reason || 'OOS validation must pass to deploy'
            : 'Deploy a bot using the walk-forward best config'
        }
      >
        {deploying ? (
          <span className="animate-pulse">Deploying…</span>
        ) : (
          <>🚀 Deploy from Walk-Forward</>
        )}
      </button>
    </div>
  );
}

export default function BacktestWalkForwardPanel({
  walkForward,
  symbol,
  strategy,
  timeframe,
  allocation,
  runId,
  results,
  days = 7,
}) {
  if (!walkForward) return null;

  const folds = walkForward.folds ?? [];
  const rolling = (walkForward.rolling_folds ?? 1) > 1;
  const aggregate = walkForward.aggregate ?? {};
  const is = walkForward.in_sample ?? {};
  const oos = walkForward.out_of_sample ?? {};
  const isSummary = is.summary ?? {};
  const oosSummary = oos.summary ?? {};
  const bestConfig = walkForward.best_config ?? {};

  return (
    <section className="algo-backtest-wf">
      <p className="algo-backtest-wf__title">
        {rolling
          ? `Rolling walk-forward (${walkForward.rolling_folds} folds, ${walkForward.train_pct ?? 70}% train per fold)`
          : `Walk-forward (${walkForward.train_pct ?? 70}% train → OOS test)`}
      </p>

      {rolling && folds.length > 0 && (
        <div className="algo-backtest-table-scroll overflow-x-auto mb-2">
          <table className="terminal-table algo-backtest-table m-0 text-xs">
            <thead>
              <tr>
                <th className="text-center">Fold</th>
                <th className="text-right">IS PnL</th>
                <th className="text-right">OOS PnL</th>
                <th className="text-right">OOS Sharpe</th>
                <th className="text-right">OOS Trades</th>
              </tr>
            </thead>
            <tbody>
              {folds.map((fold) => (
                <FoldSummary key={fold.fold} fold={fold} />
              ))}
            </tbody>
          </table>

          <div className="mt-2">
            <p className="text-[0.55rem] text-muted-foreground mb-1">Fold performance heatmap (OOS PnL)</p>
            <div className="flex gap-0.5 flex-wrap">
              {(() => {
                const maxAbs = Math.max(...folds.map((f) => Math.abs(f.out_of_sample?.total_pnl ?? 0)), 1);
                return folds.map((fold) => {
                  const oosPnl = fold.out_of_sample?.total_pnl ?? 0;
                  const intensity = Math.min(Math.abs(oosPnl) / maxAbs, 1);
                  const alpha = 0.15 + intensity * 0.75;
                  const hue = oosPnl >= 0 ? 145 : 0;
                  const bg = `hsla(${hue}, 70%, ${oosPnl >= 0 ? 35 : 40}%, ${alpha.toFixed(2)})`;
                  const oosSharpe = fold.out_of_sample?.summary?.sharpe_ratio;
                  const sign = oosPnl < 0 ? '-' : '';
                  const absVal = Math.abs(oosPnl);
                  const label = absVal >= 1000 ? `${sign}$${(absVal / 1000).toFixed(1)}k` : `${sign}$${absVal.toFixed(0)}`;
                  return (
                    <div
                      key={fold.fold}
                      className="rounded-sm text-center num-mono"
                      style={{
                        background: bg,
                        minWidth: '2.4rem',
                        padding: '0.25rem 0.35rem',
                        fontSize: '0.5rem',
                        lineHeight: 1.3,
                      }}
                      title={`Fold ${fold.fold}: OOS PnL $${Number(oosPnl).toFixed(2)}${oosSharpe != null ? `, Sharpe ${Number(oosSharpe).toFixed(2)}` : ''}`}
                    >
                      <div className="font-semibold">{fold.fold}</div>
                      <div className={oosPnl >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                        {label}
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </div>
      )}

      {aggregate.fold_count > 0 && (
        <div className="algo-backtest-wf__aggregate mb-2 rounded border border-border/50 p-2 text-xs">
          <p className="font-semibold mb-1">Aggregate OOS ({aggregate.fold_count} folds)</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 num-mono">
            <span>
              Mean PnL:{' '}
              <strong className={(aggregate.mean_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}>
                {aggregate.mean_pnl != null ? `$${Number(aggregate.mean_pnl).toFixed(2)}` : '—'}
              </strong>
            </span>
            <span>
              Mean Sharpe:{' '}
              <strong>{aggregate.mean_sharpe != null ? Number(aggregate.mean_sharpe).toFixed(2) : '—'}</strong>
            </span>
            <span>
              Stability:{' '}
              <strong>
                {aggregate.stability_score != null
                  ? `${Math.round(Number(aggregate.stability_score) * 100)}% positive folds`
                  : '—'}
              </strong>
            </span>
          </div>
          <BacktestOosStitchChart stitchCurve={walkForward.oos_equity_stitch} className="mt-2" />
        </div>
      )}

      {!rolling && (
        <div className="algo-backtest-wf__grid">
          <div className="algo-backtest-wf__col">
            <p className="algo-backtest-wf__col-title">In-sample (optimized)</p>
            <Metric
              label="PnL"
              value={is.total_pnl != null ? `$${Number(is.total_pnl).toFixed(2)}` : '—'}
              tone={(is.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}
            />
            <Metric
              label="Win rate"
              value={isSummary.win_rate != null ? `${Number(isSummary.win_rate).toFixed(1)}%` : '—'}
            />
            <Metric
              label="Trades"
              value={String(is.trade_count ?? isSummary.total_trades ?? '—')}
            />
          </div>
          <div className="algo-backtest-wf__col">
            <p className="algo-backtest-wf__col-title">Out-of-sample (validated)</p>
            <Metric
              label="PnL"
              value={oos.total_pnl != null ? `$${Number(oos.total_pnl).toFixed(2)}` : '—'}
              tone={(oos.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down'}
            />
            <Metric
              label="Win rate"
              value={oosSummary.win_rate != null ? `${Number(oosSummary.win_rate).toFixed(1)}%` : '—'}
            />
            <Metric
              label="Trades"
              value={String(oos.trade_count ?? oosSummary.total_trades ?? '—')}
            />
          </div>
        </div>
      )}

      <DeployButton
        symbol={symbol}
        strategy={strategy}
        timeframe={timeframe}
        allocation={allocation}
        bestConfig={bestConfig}
        walkForward={walkForward}
        runId={runId}
        results={results}
        days={days}
      />
    </section>
  );
}
