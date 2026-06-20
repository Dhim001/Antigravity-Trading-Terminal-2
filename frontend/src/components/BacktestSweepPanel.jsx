/**
 * Parameter sweep + walk-forward controls with arbitrary param grid (P3/P4/P5).
 */
import React, { useMemo, useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { toast } from 'sonner';

export const SWEEP_PARAM_DEFS = [
  { key: 'trailing_stop_percent', label: 'Trailing SL %', placeholder: '1, 2, 3' },
  { key: 'take_profit_percent', label: 'Take profit %', placeholder: '2, 3, 5' },
  { key: 'stop_loss_percent', label: 'Stop loss % (fallback)', placeholder: '1, 2' },
  { key: 'min_confidence', label: 'Min confidence', placeholder: '0.5, 0.6, 0.7' },
  { key: 'allocation', label: 'Allocation $', placeholder: '5000, 10000' },
  { key: 'slippage_bps', label: 'Slippage bps', placeholder: '0, 5, 10' },
  { key: 'fee_bps', label: 'Fee bps', placeholder: '0, 5' },
];

function parseSweepValues(text) {
  if (!text || !String(text).trim()) return [];
  return String(text)
    .split(/[,;\s]+/)
    .map((v) => v.trim())
    .filter(Boolean)
    .map((v) => {
      const n = Number(v);
      return Number.isNaN(n) ? v : n;
    });
}

function buildSweepGrid(enabled, valuesByKey, maxCombos) {
  const sweep = { max_combos: maxCombos };
  for (const def of SWEEP_PARAM_DEFS) {
    if (!enabled[def.key]) continue;
    const vals = parseSweepValues(valuesByKey[def.key]);
    if (vals.length) sweep[def.key] = vals;
  }
  return Object.keys(sweep).length > 1 ? sweep : null;
}

// Bars/day per timeframe for a 24h market (crypto) vs a ~6.5h equity session.
// Used only to disable walk-forward when the estimate can't satisfy its
// 50-train + 50-test minimum.
const BARS_PER_DAY_24H = { '1m': 1440, '5m': 288, '15m': 96, '1h': 24, '4h': 6, '1d': 1 };
const BARS_PER_DAY_EQUITY = { '1m': 390, '5m': 78, '15m': 26, '1h': 7, '4h': 2, '1d': 1 };
const WALK_FORWARD_MIN_BARS = 100;

function isCryptoSymbol(symbol) {
  const s = String(symbol || '').toUpperCase();
  return s.includes('USDT') || s.endsWith('USD');
}

function estimateMaxBars(days, timeframe, symbol) {
  const d = parseInt(days, 10) || 0;
  const tf = String(timeframe || '1m').toLowerCase();
  if (isCryptoSymbol(symbol)) {
    return d * (BARS_PER_DAY_24H[tf] ?? 1440);
  }
  // Equities/ETFs: ~5 trading days per 7 calendar days, ~6.5h sessions.
  const tradingDays = d * (5 / 7);
  return tradingDays * (BARS_PER_DAY_EQUITY[tf] ?? 390);
}

function countCombos(sweep) {
  if (!sweep) return 0;
  let n = 1;
  for (const def of SWEEP_PARAM_DEFS) {
    const vals = sweep[def.key];
    if (Array.isArray(vals) && vals.length) n *= vals.length;
  }
  return Math.min(n, sweep.max_combos ?? 24);
}

export default function BacktestSweepPanel({
  symbol,
  strategy,
  days,
  timeframe,
  oosPct,
  results,
}) {
  const backtestRunning = useStore((s) => s.backtestRunning);
  const botConfig = useStore((s) => s.botConfig);
  const updateBotConfig = useStore((s) => s.updateBotConfig);
  const sweep = results?.sweep;

  const [enabled, setEnabled] = useState({
    trailing_stop_percent: true,
    take_profit_percent: true,
    stop_loss_percent: false,
    min_confidence: false,
    allocation: false,
    slippage_bps: false,
    fee_bps: false,
  });
  const [valuesByKey, setValuesByKey] = useState({
    trailing_stop_percent: '1, 2, 3',
    take_profit_percent: '2, 3, 5',
    stop_loss_percent: '1, 2',
    min_confidence: '0.55, 0.6, 0.65',
    allocation: String(botConfig.allocation ?? 10000),
    slippage_bps: '0, 5',
    fee_bps: '0, 5',
  });
  const [maxCombos, setMaxCombos] = useState(24);

  const sweepGrid = useMemo(
    () => buildSweepGrid(enabled, valuesByKey, maxCombos),
    [enabled, valuesByKey, maxCombos],
  );
  const comboCount = useMemo(() => countCombos(sweepGrid), [sweepGrid]);
  const walkForwardTooFewBars = useMemo(
    () => estimateMaxBars(days, timeframe, symbol) < WALK_FORWARD_MIN_BARS,
    [days, timeframe, symbol],
  );

  const runSweep = async (walkForward = false) => {
    if (!sweepGrid) {
      toast.error('Enable at least one parameter with values');
      return;
    }
    if (walkForward && walkForwardTooFewBars) {
      toast.error(`Walk-forward needs ~${WALK_FORWARD_MIN_BARS}+ bars — increase days or lower the timeframe`);
      return;
    }
    if (backtestRunning) return;
    useStore.getState().setBacktestRunning(true);
    useStore.getState().setBacktestProgress({
      pct: 0,
      phase: 'sweep',
      message: walkForward ? 'Starting walk-forward…' : 'Starting sweep…',
    });
    const { ok, error } = await sendAction(Action.RUN_BACKTEST_SWEEP, {
      symbol,
      strategy,
      config: botConfig,
      days: parseInt(days, 10) || 7,
      timeframe,
      oos_pct: oosPct || undefined,
      walk_forward: walkForward || undefined,
      train_pct: walkForward ? 70 : undefined,
      sweep: sweepGrid,
    });
    if (!ok && error) toast.error(error);
    if (!ok) {
      useStore.getState().setBacktestRunning(false);
      useStore.getState().setBacktestProgress(null);
    }
  };

  const applyConfig = (cfg) => {
    if (!cfg) return;
    updateBotConfig(cfg);
    toast.success('Applied sweep winner to deploy settings');
  };

  const bestConfig = results?.walk_forward?.best_config ?? results?.sweep?.best_config;

  return (
    <div className="algo-backtest-sweep">
      <div className="algo-backtest-sweep__header">
        <span className="algo-backtest-table-scroll__caption m-0">Parameter sweep</span>
        <div className="flex flex-wrap gap-1">
          <Button
            type="button"
            variant="outline"
            size="xs"
            className="h-6 text-[0.62rem]"
            disabled={backtestRunning || !sweepGrid}
            onClick={() => runSweep(false)}
          >
            Run sweep ({comboCount})
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            className="h-6 text-[0.62rem]"
            disabled={backtestRunning || !sweepGrid || walkForwardTooFewBars}
            onClick={() => runSweep(true)}
            title={
              walkForwardTooFewBars
                ? `Need ~${WALK_FORWARD_MIN_BARS}+ bars for a 70/30 split — increase days or use a lower timeframe`
                : 'Optimize on first 70% of bars, validate on last 30%'
            }
          >
            Walk-forward
          </Button>
        </div>
      </div>

      <div className="algo-backtest-sweep__grid space-y-2">
        {SWEEP_PARAM_DEFS.map((def) => (
          <div key={def.key} className="algo-backtest-sweep__row flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-[0.58rem] min-w-[7rem] cursor-pointer">
              <input
                type="checkbox"
                className="size-3 accent-primary"
                checked={Boolean(enabled[def.key])}
                onChange={(e) => setEnabled((prev) => ({ ...prev, [def.key]: e.target.checked }))}
              />
              {def.label}
            </label>
            <Input
              className="h-7 flex-1 min-w-[8rem] text-[0.62rem]"
              placeholder={def.placeholder}
              value={valuesByKey[def.key] ?? ''}
              disabled={!enabled[def.key]}
              onChange={(e) => setValuesByKey((prev) => ({ ...prev, [def.key]: e.target.value }))}
            />
          </div>
        ))}
        <div className="flex items-center gap-2 text-[0.58rem]">
          <Label className="text-muted-foreground shrink-0">Max combos</Label>
          <Input
            type="number"
            min={1}
            max={24}
            className="h-7 w-16 text-[0.62rem]"
            value={maxCombos}
            onChange={(e) => setMaxCombos(Math.min(24, Math.max(1, parseInt(e.target.value, 10) || 24)))}
          />
          <span className="text-muted-foreground">
            {comboCount} configuration{comboCount === 1 ? '' : 's'} (capped at 24)
          </span>
        </div>
        {enabled.trailing_stop_percent && enabled.stop_loss_percent && (
          <p className="text-[0.55rem] text-trading-warn">
            Stop loss is only used as a fallback when trailing stop is 0 — sweeping both may produce
            duplicate-behaving configs.
          </p>
        )}
      </div>

      {bestConfig && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="h-6 text-[0.62rem] self-start mt-2"
          onClick={() => applyConfig(bestConfig)}
        >
          Apply best config to deploy
        </Button>
      )}

      {sweep?.results?.length > 0 && (
        <table className="terminal-table algo-backtest-table m-0 mt-2 text-[0.58rem]">
          <thead>
            <tr>
              <th>Config</th>
              <th className="text-right">PnL</th>
              <th className="text-right">Trades</th>
              <th className="text-right">Win%</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sweep.results.map((row, i) => {
              const isBest = i === 0;
              const summary = row.summary ?? {};
              return (
                <tr key={row.label ?? i} className={cn(isBest && 'bg-primary/5')}>
                  <td className="max-w-[9rem] truncate" title={row.label}>{row.label}</td>
                  <td className={cn(
                    'num-mono text-right whitespace-nowrap',
                    (row.total_pnl ?? 0) >= 0 ? 'text-trading-up' : 'text-trading-down',
                  )}>
                    {row.error ? '—' : `$${Number(row.total_pnl ?? 0).toFixed(2)}`}
                  </td>
                  <td className="num-mono text-right">{row.trade_count ?? summary.total_trades ?? '—'}</td>
                  <td className="num-mono text-right">
                    {summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-right">
                    {isBest && row.config && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        className="h-5 text-[0.55rem] px-1"
                        onClick={() => applyConfig(row.config)}
                      >
                        Apply
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
