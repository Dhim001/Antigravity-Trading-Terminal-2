import { apiAction, apiRequest } from './client';
import { applyHttpEnvelope } from './dispatch';
import { useStore } from '../store/useStore';

/** GET /health — liveness + partial terminal metadata (not action-router envelope). */
export async function fetchHealth(storeActions) {
  const body = await apiRequest('/health');
  if (storeActions && (body.terminal_mode != null || body.terminal_role != null)) {
    storeActions.setTerminalConfig({
      terminalMode: body.terminal_mode,
      terminalRole: body.terminal_role,
      allowLiveBots: body.allow_live_bots,
      allowCustomStrategies: body.allow_custom_strategies,
      archiveParquetEnabled: body.archive_parquet_enabled,
      archiveBackend: body.archive_backend,
      ...(body.worker != null
        ? {
            distributed: true,
            workerAlive: body.worker.alive ?? null,
            workerHeartbeatAge: body.worker.heartbeat_age_sec ?? null,
          }
        : {}),
    });
  }
  return body;
}

/** Parse Prometheus text for a few terminal counters/histograms. */
export function parseMetricsSummary(text) {
  const lines = String(text || '').split('\n');
  const out = {};
  for (const line of lines) {
    if (line.startsWith('#') || !line.trim()) continue;
    const m = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([0-9.eE+-]+)/);
    if (!m) continue;
    const [, name, , val] = m;
    const num = parseFloat(val);
    if (name === 'orders_place_total' || name.endsWith('_total') && !name.includes('_count')) {
      out[name] = (out[name] || 0) + num;
    }
    if (name === 'agent_analyze_duration_seconds' && line.includes('quantile="0.99"')) {
      out.agent_analyze_p99 = num;
    }
  }
  return out;
}

export async function fetchStrategies(storeActions) {
  try {
    const body = await apiRequest('/api/v1/strategies');
    if (body.strategies) {
      storeActions.setStrategyCatalog(body.strategies);
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] Strategy catalog unavailable:', e.message);
    return null;
  }
}

export async function fetchBacktestRuns(storeActions, symbol) {
  try {
    const qs = symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=20` : '?limit=20';
    const body = await apiRequest(`/api/v1/backtest/runs${qs}`);
    if (body.runs) {
      storeActions.setBacktestRuns(body.runs);
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] Backtest runs unavailable:', e.message);
    return null;
  }
}

export async function fetchBacktestTrades(runId) {
  const body = await apiRequest(`/api/v1/backtest/runs/${encodeURIComponent(runId)}/trades`);
  return body?.trades ?? [];
}

export async function fetchBacktestRun(runId, storeActions) {
  const body = await apiRequest(`/api/v1/backtest/runs/${encodeURIComponent(runId)}`);
  if (!body?.ok || !body?.run) {
    throw new Error(body?.error || 'Backtest run not found');
  }
  const run = body.run;
  const results = {
    ...(run.results || {}),
    run_id: run.id,
    meta: {
      ...(run.results?.meta || {}),
      symbol: run.symbol,
      strategy: run.strategy,
      days: run.days,
    },
  };
  if (storeActions?.setBacktestResults) {
    storeActions.setBacktestResults(results);
  }
  return { run, results };
}

export async function fetchActiveBacktestJob() {
  const body = await apiRequest('/api/v1/backtest/jobs/active');
  return body?.job ?? null;
}

export async function fetchBacktestJob(jobId) {
  const body = await apiRequest(`/api/v1/backtest/jobs/${encodeURIComponent(jobId)}`);
  if (!body?.ok) throw new Error(body?.error || 'Job not found');
  return body.job;
}

export async function fetchBacktestJobs({ status, limit = 20 } = {}) {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  qs.set('limit', String(limit));
  const body = await apiRequest(`/api/v1/backtest/jobs?${qs}`);
  return body?.jobs ?? [];
}

let _backtestPollTimer = null;

function startBacktestJobPolling(jobId, storeActions) {
  if (_backtestPollTimer) {
    clearTimeout(_backtestPollTimer);
    _backtestPollTimer = null;
  }
  storeActions.setBacktestJobId(jobId);
  storeActions.setBacktestRunning(true);
  const poll = () => {
    fetchBacktestJob(jobId)
      .then((fresh) => {
        if (!fresh) return;
        if (fresh.progress) storeActions.setBacktestProgress(fresh.progress);
        if (fresh.status === 'completed' && fresh.results) {
          const wire = {
            ...fresh.results,
            run_id: fresh.run_id ?? fresh.results.run_id,
          };
          storeActions.setBacktestResults(wire);
          storeActions.setBacktestRunning(false);
          storeActions.setBacktestProgress(null);
          return;
        }
        if (fresh.status === 'failed' || fresh.status === 'cancelled') {
          storeActions.setBacktestRunning(false);
          storeActions.setBacktestProgress(null);
          return;
        }
        if (['pending', 'running'].includes(fresh.status)) {
          _backtestPollTimer = setTimeout(poll, 2000);
        }
      })
      .catch(() => {
        _backtestPollTimer = setTimeout(poll, 3000);
      });
  };
  _backtestPollTimer = setTimeout(poll, 1500);
}

export function watchBacktestJob(jobId, storeActions, { progress } = {}) {
  storeActions.setBacktestProgress(progress ?? { pct: 0, message: 'Resuming…' });
  startBacktestJobPolling(jobId, storeActions);
}

export function resumeActiveBacktestJob(storeActions) {
  return fetchActiveBacktestJob().then((job) => {
    if (!job || !['pending', 'running'].includes(job.status)) return null;
    watchBacktestJob(job.id, storeActions, { progress: job.progress });
    return job;
  }).catch(() => null);
}

export async function fetchAccount(storeActions) {
  const body = await apiAction('/api/v1/account');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchHistory(storeActions) {
  const body = await apiAction('/api/v1/history');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchBots(storeActions) {
  const body = await apiAction('/api/v1/bots');
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchCandles(symbol, storeActions) {
  const encoded = encodeURIComponent(symbol);
  const body = await apiAction(`/api/v1/market/${encoded}/candles`);
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchAgentInsights(symbol, storeActions, limit = 30) {
  try {
    const encoded = encodeURIComponent(symbol);
    const body = await apiRequest(`/api/v1/agent/insights/${encoded}?limit=${limit}`);
    if (body.insights && storeActions?.setAgentInsightHistory) {
      storeActions.setAgentInsightHistory(symbol, body.insights);
    }
    return body;
  } catch (e) {
    console.warn('[analyst] Insight history unavailable:', e.message);
    throw e;
  }
}

/** Fetch archived OHLCV range and prepend to chart buffer (scroll-left load). */
export async function fetchOlderCandles(symbol, from, to, interval = 'auto') {
  const encoded = encodeURIComponent(symbol);
  const qs = new URLSearchParams({
    from: String(from),
    to: String(to),
    interval,
  });
  const body = await apiAction(
    `/api/v1/market/${encoded}/history?${qs}`,
    { timeoutMs: 20000 },
  );
  const bars = body.data?.[symbol];
  if (!Array.isArray(bars) || bars.length === 0) return 0;

  const { prependHistory } = useStore.getState();
  prependHistory({ [symbol]: bars });
  return bars.length;
}
