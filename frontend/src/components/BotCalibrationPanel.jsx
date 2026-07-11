import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Play, Shield, ShieldCheck, ShieldOff, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useStore } from '../store/useStore';
import FilterRejectsDashboard from './FilterRejectsDashboard';
import BacktestMetaLabelWalkForwardPanel from './BacktestMetaLabelWalkForwardPanel';
import {
  applyCalibrationSuggestions,
  applyMetaLabelOperational,
  fetchBotCalibration,
  fetchFilterRejects,
  fetchMetaLabelStatus,
  fetchMetaLabelWalkForward,
  retrainMetaLabelModel,
} from '../api/endpoints';

function pct(value) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${(Number(value) * 100).toFixed(1)}%`;
}

/** Mirror backend suggestion_already_met — hide advisories the bot config already satisfies. */
function suggestionAlreadyMet(suggestion, config) {
  const cfg = config && typeof config === 'object' ? config : {};
  const kind = suggestion?.kind;
  if (kind === 'min_confidence') {
    const wanted = Number(suggestion?.suggested_min_confidence);
    const current = Number(cfg.min_confidence ?? 0);
    return Number.isFinite(wanted) && Number.isFinite(current) && current + 1e-9 >= wanted;
  }
  if (kind === 'min_score') {
    const wanted = Number(suggestion?.suggested_min_score);
    const current = Number(cfg.min_score ?? 0);
    return Number.isFinite(wanted) && Number.isFinite(current) && current >= wanted;
  }
  if (kind === 'block_elevated_vol') {
    return Boolean(cfg.block_elevated_vol);
  }
  return false;
}

function filterOpenSuggestions(suggestions, config) {
  if (!Array.isArray(suggestions)) return [];
  return suggestions.filter((s) => !suggestionAlreadyMet(s, config));
}

/** Apply API may return bot row or full get_bot_detail envelope. */
function unwrapBotPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (payload.config && (payload.id || payload.strategy)) return payload;
  if (payload.bot && typeof payload.bot === 'object') return payload.bot;
  return payload;
}

function CalibrationTable({ buckets, emptyLabel }) {
  if (!buckets?.length) {
    return <p className="text-xs text-muted-foreground m-0">{emptyLabel}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="terminal-table m-0 w-full text-xs">
        <thead>
          <tr>
            <th>Setup</th>
            <th className="text-right">N</th>
            <th className="text-right">Win%</th>
            <th className="text-right">Wilson↓</th>
            <th className="text-right">Exp</th>
            <th className="text-right">PnL</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((row) => {
            const label = [
              row.symbol,
              row.timeframe,
              row.atr_regime,
              `s${row.score_bucket}`,
              row.confidence_bucket,
            ].filter(Boolean).join(' · ');
            const expPos = Number(row.expectancy) >= 0;
            return (
              <tr key={`${label}-${row.sample_size}`}>
                <td className="max-w-[12rem] truncate" title={label}>{label}</td>
                <td className="text-right num-mono">{row.sample_size}</td>
                <td className="text-right num-mono">{pct(row.win_rate)}</td>
                <td className="text-right num-mono">{pct(row.wilson_lower)}</td>
                <td className={cn('text-right num-mono', expPos ? 'text-trading-up' : 'text-trading-down')}>
                  {Number(row.expectancy).toFixed(2)}
                </td>
                <td className={cn('text-right num-mono', row.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
                  ${Number(row.total_pnl).toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function BotCalibrationPanel({
  botId,
  symbol,
  strategy,
  className,
}) {
  const [loading, setLoading] = useState(true);
  const [calibration, setCalibration] = useState(null);
  const [filterData, setFilterData] = useState(null);
  const [metaLabel, setMetaLabel] = useState(null);
  const [metaLabelError, setMetaLabelError] = useState(null);
  const [error, setError] = useState(null);
  const [applying, setApplying] = useState(null);
  const [retraining, setRetraining] = useState(false);
  const [wfLoading, setWfLoading] = useState(false);
  const [wfResult, setWfResult] = useState(null);
  const [operationalBusy, setOperationalBusy] = useState(null);
  const botConfig = useStore((s) => (
    s.botDetail?.bot?.id === botId ? s.botDetail.bot.config : null
  ));

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cal, fr, ml] = await Promise.all([
        fetchBotCalibration({ botId, symbol, minSamples: 3 }),
        fetchFilterRejects({ botId, symbol, strategy }),
        fetchMetaLabelStatus(botId),
      ]);
      const storeCfg = useStore.getState().botDetail?.bot?.id === botId
        ? useStore.getState().botDetail?.bot?.config
        : null;
      const mergedSnap = {
        ...(cal?.config_snapshot || {}),
        ...(storeCfg && typeof storeCfg === 'object' ? storeCfg : {}),
      };
      setCalibration({
        ...cal,
        config_snapshot: mergedSnap,
        suggestions: filterOpenSuggestions(cal?.suggestions ?? [], mergedSnap),
      });
      setFilterData(fr);
      setMetaLabel(ml?.meta_label ?? null);
      setMetaLabelError(null);
    } catch (e) {
      const msg = e?.message || 'Failed to load calibration';
      setError(msg);
      if (/meta-label/i.test(msg)) {
        setMetaLabelError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [botId, symbol, strategy]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadData();
      if (cancelled) return;
    })();
    return () => { cancelled = true; };
  }, [loadData]);

  const handleApply = async ({ kinds, applyAll = false, label }) => {
    if (!botId || applying) return;
    setApplying(label || 'apply');
    try {
      const result = await applyCalibrationSuggestions({
        botId,
        symbol,
        kinds,
        applyAll,
      });
      const patch = result.patch || {};
      const patchKeys = Object.keys(patch);
      const botRow = unwrapBotPayload(result.bot) || unwrapBotPayload(result.detail);
      const conf = {
        ...(botRow?.config && typeof botRow.config === 'object' ? botRow.config : {}),
        ...(result.config_snapshot || {}),
        ...patch,
      };

      if (patchKeys.length === 0) {
        toast.message(result.message || 'No suggestions to apply');
      } else {
        const parts = [];
        if (patchKeys.includes('min_confidence')) {
          parts.push(`min_confidence=${conf.min_confidence ?? patch.min_confidence}`);
        }
        if (patchKeys.includes('min_score')) {
          parts.push(`min_score=${conf.min_score ?? patch.min_score}`);
        }
        if (patchKeys.includes('block_elevated_vol')) {
          parts.push('block_elevated_vol=on');
        }
        if (patchKeys.includes('calibration_gate_enabled')) {
          parts.push('calibration gate on');
        }
        toast.success(
          parts.length
            ? `Applied to live bot: ${parts.join(' · ')}`
            : `Applied: ${patchKeys.join(', ')}`,
        );

        // Refresh drawer config so BotConfigPanel shows the new thresholds.
        if (botRow) {
          const prev = useStore.getState().botDetail;
          if (prev?.bot?.id === botId) {
            useStore.getState().setBotDetail({
              ...prev,
              bot: {
                ...prev.bot,
                ...botRow,
                config: { ...(prev.bot?.config || {}), ...(botRow.config || {}), ...patch },
              },
            });
          }
        }

        // Clear satisfied suggestions immediately (don't wait on a stale backend reload).
        setCalibration((prev) => {
          if (!prev) return prev;
          const nextSnap = { ...(prev.config_snapshot || {}), ...conf };
          return {
            ...prev,
            config_snapshot: nextSnap,
            suggestions: filterOpenSuggestions(prev.suggestions, nextSnap),
          };
        });
      }

      await loadData();
    } catch (e) {
      toast.error(e?.message || 'Failed to apply suggestions');
    } finally {
      setApplying(null);
    }
  };

  const handleRetrainMetaLabel = async () => {
    if (!botId || retraining) return;
    setRetraining(true);
    try {
      const result = await retrainMetaLabelModel(botId);
      const metrics = result?.result?.metrics;
      toast.success(
        metrics?.val_auc != null
          ? `Meta-label model trained (val AUC ${(metrics.val_auc * 100).toFixed(1)}%)`
          : 'Meta-label model trained',
      );
      await loadData();
    } catch (e) {
      toast.error(e?.message || 'Meta-label retrain failed');
    } finally {
      setRetraining(false);
    }
  };

  const handleRunWalkForward = async () => {
    if (!botId || wfLoading || strategy !== 'CHART_AGENT') return;
    setWfLoading(true);
    setWfResult(null);
    try {
      const result = await fetchMetaLabelWalkForward({
        botId,
        symbol,
        strategy,
        days: 30,
      });
      const wf = result?.walk_forward ?? result;
      setWfResult(wf);
      if (wf?.ok) {
        toast.success('Walk-forward OOS evaluation complete');
      } else {
        toast.error(wf?.error || 'Walk-forward did not complete');
      }
    } catch (e) {
      toast.error(e?.message || 'Walk-forward failed');
    } finally {
      setWfLoading(false);
    }
  };

  const handleOperational = async (stage) => {
    if (!botId || operationalBusy) return;
    setOperationalBusy(stage);
    try {
      const result = await applyMetaLabelOperational(botId, {
        stage,
        walkForward: wfResult?.ok ? wfResult : undefined,
        requirePositiveOos: stage === 'promote',
        retrain: stage !== 'rollback',
      });
      const label = {
        shadow: 'Shadow gate enabled — blocks logged, not enforced',
        promote: 'Live GBM gate enabled',
        rollback: 'Meta-label gate rolled back to Wilson/off',
      }[stage] || stage;
      toast.success(label);
      if (result?.retrain && !result.retrain.ok && stage === 'shadow') {
        toast.message(result.retrain.error || 'Retrain skipped — accumulate more closed trades');
      }
      await loadData();
    } catch (e) {
      toast.error(e?.message || 'Operational update failed');
    } finally {
      setOperationalBusy(null);
    }
  };

  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 text-xs text-muted-foreground py-3', className)}>
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading calibration…
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive" className={className}>
        <AlertDescription className="text-xs">{error}</AlertDescription>
      </Alert>
    );
  }

  const overall = calibration?.overall;
  const effectiveConfig = {
    ...(calibration?.config_snapshot || {}),
    ...(botConfig && typeof botConfig === 'object' ? botConfig : {}),
  };
  const suggestions = filterOpenSuggestions(calibration?.suggestions ?? [], effectiveConfig);
  const symbolThresholds = calibration?.symbol_thresholds ?? {};
  const liveRejects = filterData?.live;
  const backtestRejects = filterData?.backtest;
  const operational = metaLabel?.operational;
  const opStage = operational?.stage ?? 'off';
  const wfImproved = wfResult?.ok && (() => {
    const d = wfResult?.aggregate?.gbm_vs_baseline_avg || {};
    const pnl = Number(d.total_pnl ?? 0);
    const exp = Number(d.expectancy ?? 0);
    const trades = Number(d.total_trades ?? 0);
    return pnl > 0 || (exp > 0 && trades <= 0);
  })();

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {overall && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Closed trades</span>
            <strong className="text-sm num-mono">{overall.closed_trades}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Win rate</span>
            <strong className="text-sm num-mono">{pct(overall.win_rate)}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Wilson lower</span>
            <strong className="text-sm num-mono">{pct(overall.wilson_lower)}</strong>
          </div>
          <div className="rounded-md border border-border/50 px-2 py-1.5">
            <span className="block text-xs text-muted-foreground">Total PnL</span>
            <strong className={cn('text-sm num-mono', overall.total_pnl >= 0 ? 'text-trading-up' : 'text-trading-down')}>
              ${Number(overall.total_pnl).toFixed(2)}
            </strong>
          </div>
        </div>
      )}

      {metaLabel && strategy === 'CHART_AGENT' && (
        <section className="rounded-md border border-primary/20 bg-primary/5 p-2.5 space-y-2">
          {metaLabel.load_error && (
            <p className="text-[0.65rem] text-amber-600 dark:text-amber-400 m-0">{metaLabel.load_error}</p>
          )}
          {metaLabelError && (
            <p className="text-[0.65rem] text-destructive m-0">{metaLabelError}</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium">Meta-label rollout</span>
                <Badge variant="outline" className="text-[0.62rem] capitalize">
                  {opStage}
                </Badge>
              </div>
              <p className="text-[0.65rem] text-muted-foreground m-0 mt-0.5">
                1) Walk-forward OOS → 2) Shadow gate → 3) Promote live after validation.
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1"
                disabled={wfLoading}
                onClick={handleRunWalkForward}
              >
                {wfLoading ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
                Walk-forward OOS
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1"
                disabled={retraining}
                onClick={handleRetrainMetaLabel}
              >
                {retraining ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}
                Retrain
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 text-xs">
            <div>
              <span className="text-muted-foreground">Model</span>
              <strong className="block num-mono">{metaLabel.model_loaded ? 'Loaded' : 'Not trained'}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">Closed trades</span>
              <strong className="block num-mono">{metaLabel.dataset?.sample_count ?? 0}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">Val AUC</span>
              <strong className="block num-mono">
                {metaLabel.metadata?.metrics?.val_auc != null
                  ? pct(metaLabel.metadata.metrics.val_auc)
                  : '—'}
              </strong>
            </div>
          </div>

          {metaLabel.metadata?.top_features?.length > 0 && (
            <p className="text-[0.62rem] text-muted-foreground m-0">
              Top features:{' '}
              {metaLabel.metadata.top_features.slice(0, 4).map((f) => f.name).join(', ')}
            </p>
          )}

          <div className="flex flex-wrap gap-1.5 pt-0.5">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={Boolean(operationalBusy) || opStage === 'shadow'}
              onClick={() => handleOperational('shadow')}
            >
              {operationalBusy === 'shadow' ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Shield className="size-3" />
              )}
              Enable shadow
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={Boolean(operationalBusy) || opStage === 'live' || !wfImproved}
              title={!wfImproved ? 'Run walk-forward OOS with positive GBM delta first' : undefined}
              onClick={() => handleOperational('promote')}
            >
              {operationalBusy === 'promote' ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ShieldCheck className="size-3" />
              )}
              Promote live
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={Boolean(operationalBusy) || opStage === 'off'}
              onClick={() => handleOperational('rollback')}
            >
              {operationalBusy === 'rollback' ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <ShieldOff className="size-3" />
              )}
              Rollback
            </Button>
          </div>

          {wfResult && (
            <BacktestMetaLabelWalkForwardPanel walkForward={wfResult} className="mt-1" />
          )}
        </section>
      )}

      {suggestions.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium">Threshold suggestions</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 text-xs gap-1"
              disabled={Boolean(applying)}
              onClick={() => handleApply({ applyAll: true, label: 'apply-all' })}
            >
              {applying === 'apply-all' ? (
                <Loader2 className="size-3 animate-spin" aria-hidden />
              ) : (
                <Sparkles className="size-3" aria-hidden />
              )}
              Apply all
            </Button>
          </div>
          {suggestions.map((s) => (
            <Alert key={`${s.symbol}-${s.kind}`} className="border-border/60 bg-muted/20 py-2">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <AlertDescription className="text-xs m-0 flex-1">{s.message}</AlertDescription>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 shrink-0 text-xs"
                  disabled={Boolean(applying)}
                  onClick={() => handleApply({
                    kinds: [s.kind],
                    label: `${s.symbol}-${s.kind}`,
                  })}
                >
                  {applying === `${s.symbol}-${s.kind}` ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                  ) : (
                    'Apply'
                  )}
                </Button>
              </div>
            </Alert>
          ))}
        </div>
      )}

      {Object.keys(symbolThresholds).length > 0 && (
        <section>
          <header className="mb-1.5 flex items-center gap-2">
            <span className="text-xs font-medium">Per-symbol thresholds</span>
            <Badge variant="secondary" className="text-xs">{Object.keys(symbolThresholds).length}</Badge>
          </header>
          <div className="flex flex-col gap-1">
            {Object.entries(symbolThresholds).map(([sym, row]) => (
              <div
                key={sym}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/50 px-2 py-1.5 text-xs"
              >
                <span className="font-medium">{sym}</span>
                <span className="num-mono text-muted-foreground">
                  n={row.sample_size} · win {pct(row.win_rate)} · Wilson {pct(row.wilson_lower)}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <header className="mb-1.5 text-xs font-medium">Setup buckets</header>
        <CalibrationTable
          buckets={calibration?.buckets}
          emptyLabel="Not enough closed trades with insight context yet."
        />
      </section>

      {liveRejects?.total > 0 && (
        <FilterRejectsDashboard
          rejects={liveRejects.by_bucket}
          total={liveRejects.total}
          title="Live filter rejects"
          hint="Signals blocked at runtime by CHART_AGENT filters and calibration gate (from bot logs)."
        />
      )}

      {backtestRejects?.total > 0 && (
        <FilterRejectsDashboard
          rejects={backtestRejects.by_bucket}
          total={backtestRejects.total}
          title="Backtest filter rejects"
          hint={`Aggregated from ${backtestRejects.runs_aggregated ?? 0} recent backtest/optimizer runs.`}
        />
      )}
    </div>
  );
}
