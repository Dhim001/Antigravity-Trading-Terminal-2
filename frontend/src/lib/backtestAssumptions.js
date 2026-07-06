/** Build human-readable backtest assumption chips for the Lab report header. */

import {
  formatBacktestDaysChip,
  formatBacktestRangeLabel,
  resolveBacktestRange,
} from './backtestDisplay';
import { formatDirectionModeLabel, normalizeDirectionMode } from './botConfigDisplay';

export function buildBacktestAssumptionDetails(results) {
  if (!results) return { sections: [] };

  const summary = results.summary ?? {};
  const costs = results.costs ?? {};
  const meta = results.meta ?? {};
  const cfg = meta.config ?? {};
  const sections = [];

  const simMode = results.sim_mode ?? cfg.sim_mode ?? 'live_aligned';
  const liveParity = results.live_parity ?? meta.live_parity ?? cfg.live_parity;
  const directionMode = normalizeDirectionMode(cfg.direction_mode);
  sections.push({
    id: 'gates',
    title: 'Simulation & gates',
    rows: [
      { label: 'Sim mode', value: simMode, warn: simMode === 'research' },
      {
        label: 'Trade direction',
        value: formatDirectionModeLabel(directionMode),
      },
      {
        label: 'Live parity',
        value: liveParity === false ? 'OFF (filters/HTF skipped)' : 'ON',
        warn: liveParity === false,
      },
      {
        label: 'Parity gate blocks',
        value: String(summary.parity_gate_blocks ?? 0),
      },
      {
        label: 'Risk/size blocks',
        value: String(summary.blocked_entries ?? 0),
      },
      {
        label: 'Filter rejects',
        value: String(summary.filter_rejects_total ?? Object.values(summary.filter_rejects ?? {}).reduce((a, b) => a + b, 0)),
      },
    ],
  });

  sections.push({
    id: 'costs',
    title: 'Fill model & costs',
    rows: [
      {
        label: 'Fill model',
        value: costs.volume_participation ? 'Volume-scaled participation' : 'Bar close + fixed slippage',
      },
      {
        label: 'Slippage',
        value: `${summary.slippage_bps ?? costs.slippage_bps ?? cfg.slippage_bps ?? 0} bps`,
      },
      {
        label: 'Fees',
        value: `${summary.fee_bps ?? costs.fee_bps ?? cfg.fee_bps ?? 0} bps · $${Number(summary.total_fees ?? costs.total_fees ?? 0).toFixed(2)} total`,
      },
      {
        label: 'Starting equity',
        value: `$${Number(results.starting_equity ?? results.allocation ?? cfg.allocation ?? 0).toLocaleString()}`,
      },
    ],
  });

  const rangeInfo = resolveBacktestRange(meta);
  const dataRows = [
    {
      label: rangeInfo.hasMismatch ? 'Requested range' : 'Range',
      value: rangeInfo.hasMismatch && rangeInfo.requested != null
        ? `${rangeInfo.requested} days`
        : formatBacktestRangeLabel(meta),
    },
    { label: 'Timeframe', value: meta.timeframe ?? '1m' },
    { label: 'Bars replayed', value: meta.count != null ? Number(meta.count).toLocaleString() : '—' },
  ];
  if (rangeInfo.hasMismatch) {
    dataRows.push({
      label: 'Replayed span',
      value: formatBacktestRangeLabel(meta),
      warn: true,
    });
  }
  if (meta.oldest && meta.newest) {
    dataRows.push({
      label: 'Data slice',
      value: `${new Date(meta.oldest * 1000).toISOString().slice(0, 10)} → ${new Date(meta.newest * 1000).toISOString().slice(0, 10)}`,
    });
  }
  if (rangeInfo.rangeNote) {
    dataRows.push({ label: 'Range note', value: rangeInfo.rangeNote, warn: true });
  } else if (rangeInfo.timeframeNote && !rangeInfo.hasMismatch) {
    dataRows.push({ label: 'Range note', value: rangeInfo.timeframeNote, warn: true });
  }
  if (meta.oos_pct) {
    dataRows.push({ label: 'OOS window', value: `${meta.oos_pct}% hold-out` });
  }
  sections.push({ id: 'data', title: 'Data window', rows: dataRows });

  const auditRows = [
    { label: 'Run ID', value: results.run_id ?? '—', mono: true },
    { label: 'Strategy', value: meta.strategy ?? '—' },
    { label: 'Symbol', value: meta.symbol ?? '—' },
  ];
  if (meta.job_tier || meta.estimated_sec != null) {
    auditRows.push({
      label: 'Job tier',
      value: meta.job_tier
        ? `${meta.job_tier}${meta.estimated_sec != null ? ` · ~${meta.estimated_sec}s` : ''}`
        : (meta.estimated_sec != null ? `~${meta.estimated_sec}s` : '—'),
    });
  }
  if (meta.git_revision) {
    auditRows.push({ label: 'Git revision', value: meta.git_revision, mono: true });
  }
  if (results.execution_runtime) {
    auditRows.push({ label: 'Execution kernel', value: results.execution_runtime, mono: true });
  }
  if (cfg.calibration_gate_enabled) {
    auditRows.push({
      label: 'Meta-label gate',
      value: String(cfg.meta_label_model_mode ?? 'wilson'),
    });
  }
  sections.push({ id: 'audit', title: 'Reproducibility', rows: auditRows });

  return { sections };
}

export function buildBacktestAssumptions(results) {
  if (!results) return [];

  const summary = results.summary ?? {};
  const costs = results.costs ?? {};
  const meta = results.meta ?? {};
  const cfg = meta.config ?? {};
  const chips = [];

  const simMode = results.sim_mode ?? cfg.sim_mode;
  const directionMode = normalizeDirectionMode(cfg.direction_mode);
  if (simMode === 'research') {
    chips.push({ key: 'sim', label: 'Research mode', warn: true });
  } else {
    chips.push({ key: 'sim', label: 'Live-aligned gates' });
  }

  if (directionMode && directionMode !== 'LONG_ONLY') {
    chips.push({
      key: 'direction',
      label: formatDirectionModeLabel(directionMode),
      warn: simMode === 'research',
    });
  }

  const liveParity = results.live_parity ?? meta.live_parity ?? cfg.live_parity;
  if (liveParity === false) {
    chips.push({ key: 'parity', label: 'HTF/filter gates OFF', warn: true });
  } else if (cfg.confirm_timeframe || cfg.filter_strategy) {
    chips.push({ key: 'parity', label: 'Live parity (HTF + filters)' });
  }

  const slip = summary.slippage_bps ?? costs.slippage_bps ?? cfg.slippage_bps;
  const fee = summary.fee_bps ?? costs.fee_bps ?? cfg.fee_bps;
  if (slip != null || fee != null) {
    chips.push({
      key: 'costs',
      label: `Costs: ${slip ?? 0}bps slip · ${fee ?? 0}bps fee`,
    });
  }

  if (costs.volume_participation) {
    chips.push({ key: 'fill', label: 'Volume-scaled fills' });
  } else {
    chips.push({ key: 'fill', label: 'Bar close + fixed slip' });
  }

  const rangeInfo = resolveBacktestRange(meta);
  if (rangeInfo.requested != null || rangeInfo.replayedDays != null) {
    const daysChip = formatBacktestDaysChip(meta, null);
    chips.push({
      key: 'range',
      label: `${daysChip} · ${meta.timeframe ?? '1m'}`,
      warn: rangeInfo.hasMismatch,
    });
  }

  if (meta.count != null) {
    chips.push({ key: 'bars', label: `${Number(meta.count).toLocaleString()} bars` });
  }

  if (results.portfolio) {
    const n = results.symbols_tested ?? results.symbol_results?.length;
    if (n != null) chips.push({ key: 'portfolio', label: `Portfolio · ${n} symbols` });
  }

  return chips;
}
