import { apiAction, apiRequest } from './client';
import { applyHttpEnvelope } from './dispatch';
import { Action, MessageType } from './protocol';
import { invokeHttpAction } from './transport';
import { useStore } from '../store/useStore';
import { normalizeAnalystTimeframe } from '../lib/agentInsights';
import { clearBacktestClientTimeout } from '../lib/backtestTimeouts';
import { trimBacktestPayload, buildBacktestOverlay } from '../lib/backtestSlim';
import { stopBacktestJobPolling, scheduleBacktestJobPoll } from '../lib/backtestPolling';
import { toast } from 'sonner';
import { normalizeOrderCapabilities } from '../lib/positionActions';

/** GET /api/v1/session — single-round-trip bootstrap snapshot. */
export async function fetchSession(storeActions) {
  try {
    const body = await apiRequest('/api/v1/session');
    if (body?.ok && body.session && storeActions) {
      applySessionToStore(body.session, storeActions);
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] Session snapshot unavailable:', e.message);
    return null;
  }
}

/** Hydrate store from GET /api/v1/session payload. */
export function applySessionToStore(session, storeActions) {
  if (!session || !storeActions) return;
  const t = session.terminal || {};
  const llm = session.llm || {};
  storeActions.setTerminalConfig({
    terminalMode: t.terminal_mode,
    terminalRole: t.terminal_role,
    executionMode: t.execution_mode,
    allowLiveBots: t.allow_live_bots,
    allowCustomStrategies: t.allow_custom_strategies,
    archiveParquetEnabled: t.archive_parquet_enabled,
    archiveBackend: t.archive_backend,
    archiveTicksEnabled: t.archive_ticks_enabled,
    botMinCandles: t.bot_min_candles,
    agentLlmEnabled: t.agent_llm_enabled,
    agentLlmAvailable: llm.available,
    agentLlmProvider: llm.provider,
    agentLlmModel: llm.model,
    agentLlmModels: llm.models || [],
    agentVisionEnabled: t.agent_vision_enabled,
    agentEnabled: t.agent_enabled,
    scannerEnabled: t.scanner_enabled,
    isOperator: t.operator_mode,
    orderCapabilities: normalizeOrderCapabilities(t.order_capabilities),
  });
  if (llm.preferred_model) {
    storeActions.setSelectedLlmModel(llm.preferred_model);
  } else if (llm.active_model && !useStore.getState().selectedLlmModel) {
    storeActions.setSelectedLlmModel(llm.active_model);
  }
  if (session.account) storeActions.updateAccount(session.account);
  if (session.history) storeActions.setTradeHistory(session.history);
  if (session.bots) storeActions.setBots(session.bots);
  if (session.strategies) storeActions.setStrategyCatalog(session.strategies);
  if (session.metrics) storeActions.setSystemStats(session.metrics);
  const job = session.active_backtest_job;
  if (job && ['pending', 'running'].includes(job.status)) {
    watchBacktestJob(job.id, storeActions, { progress: job.progress });
  }
}

/** GET /health — liveness + partial terminal metadata (not action-router envelope). */
export async function fetchHealth(storeActions) {
  const body = await apiRequest('/health');
  if (storeActions && (body.terminal_mode != null || body.terminal_role != null || body.llm != null)) {
    storeActions.setTerminalConfig({
      terminalMode: body.terminal_mode,
      terminalRole: body.terminal_role,
      executionMode: body.execution_mode,
      allowLiveBots: body.allow_live_bots,
      allowCustomStrategies: body.allow_custom_strategies,
      archiveParquetEnabled: body.archive_parquet_enabled,
      archiveBackend: body.archive_backend,
      agentLlmEnabled: body.agent_llm_enabled,
      agentLlmAvailable: body.llm?.available,
      agentLlmProvider: body.llm?.provider,
      agentLlmModel: body.llm?.model,
      agentLlmModels: body.llm?.models,
      agentVisionEnabled: body.agent_vision_enabled,
      agentEnabled: body.agent_enabled,
      scannerEnabled: body.scanner_enabled,
      botMinCandles: body.bot_min_candles,
      archiveTicksEnabled: body.archive_ticks_enabled,
      isOperator: body.operator_mode,
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

/** POST preview_order — unified server-side order validation. */
export async function previewOrder(payload) {
  const body = await invokeHttpAction(Action.PREVIEW_ORDER, payload);
  const msg = body.messages?.find((m) => m.type === MessageType.ORDER_PREVIEW);
  return msg?.data ?? body.data ?? null;
}

/** POST /api/v1/orders/preview — HITL insight draft validation. */
export async function previewInsightOrder(draft) {
  if (!draft?.symbol || !draft?.side) {
    throw new Error('Invalid order draft');
  }
  return previewOrder({
    symbol: draft.symbol,
    side: draft.side,
    type: draft.orderType || 'MARKET',
    quantity: draft.quantity,
    stop_loss_price: draft.stop_loss_price,
    take_profit_price: draft.take_profit_price,
  });
}

/** GET /api/v1/llm/models — Ollama + OpenRouter models on this system. */
export async function fetchLlmModels(storeActions) {
  try {
    const body = await apiRequest('/api/v1/llm/models');
    if (storeActions && body.ok) {
      const models = [...(body.ollama || []), ...(body.openrouter || [])];
      storeActions.setTerminalConfig({
        agentLlmModels: models,
        agentLlmModel: body.active_model,
      });
      if (body.preferred_model) {
        storeActions.setSelectedLlmModel(body.preferred_model);
      } else if (body.active_model && !useStore.getState().selectedLlmModel) {
        storeActions.setSelectedLlmModel(body.active_model);
      }
    }
    return body;
  } catch (e) {
    console.warn('[bootstrap] LLM models unavailable:', e.message);
    return null;
  }
}

/** POST /api/v1/llm/model — set preferred local/cloud model for narrators. */
export async function setPreferredLlmModel(model, storeActions) {
  const body = await apiRequest('/api/v1/llm/model', {
    method: 'POST',
    body: { model: model || '' },
  });
  if (storeActions && body.ok) {
    const models = [...(body.ollama || []), ...(body.openrouter || [])];
    storeActions.setTerminalConfig({
      agentLlmModels: models,
      agentLlmModel: body.active_model,
    });
    storeActions.setSelectedLlmModel(body.preferred_model || body.active_model || model || null);
  }
  return body;
}

/** GET /api/v1/llm/ops — Ollama HTTP + CLI health and tier install status. */
export async function fetchLlmOps() {
  return apiRequest('/api/v1/llm/ops');
}

/** POST /api/v1/llm/pull — operator: ollama pull (long-running). */
export async function pullLlmModel(model) {
  return apiRequest('/api/v1/llm/pull', {
    method: 'POST',
    body: { model },
    timeoutMs: 620_000,
  });
}

/** Payload helper — attach user-selected LLM model when set. */
export function withLlmModel(payload = {}) {
  const model = useStore.getState().selectedLlmModel;
  return model ? { ...payload, llm_model: model } : payload;
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

export async function fetchOptimizationRuns({ symbol, limit = 20 } = {}) {
  const qs = new URLSearchParams();
  if (symbol) qs.set('symbol', symbol);
  qs.set('limit', String(limit));
  const body = await apiRequest(`/api/v1/backtest/optimizations?${qs}`);
  return body?.runs ?? [];
}

export async function fetchOptimizationRun(runId) {
  const body = await apiRequest(`/api/v1/backtest/optimizations/${encodeURIComponent(runId)}`);
  if (!body?.ok || !body?.run) throw new Error(body?.error || 'Optimization run not found');
  return body.run;
}

export async function getOptimizationRun(runId) {
  const res = await fetch(`${API_BASE_URL}/api/v1/backtest/optimizations/${runId}`, { headers: getHeaders() });
  if (!res.ok) throw new Error('Failed to fetch optimization run');
  return res.json();
}

export async function fetchWorkspaces() {
  const res = await fetch(`${API_BASE_URL}/api/v1/workspaces`, { headers: getHeaders() });
  if (!res.ok) throw new Error('Failed to fetch workspaces');
  return res.json();
}

export async function saveWorkspace(id, name, state) {
  const res = await fetch(`${API_BASE_URL}/api/v1/workspaces`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ id, name, state }),
  });
  if (!res.ok) throw new Error('Failed to save workspace');
  return res.json();
}

export async function deleteWorkspace(id) {
  const res = await fetch(`${API_BASE_URL}/api/v1/workspaces/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!res.ok) throw new Error('Failed to delete workspace');
  return res.json();
}

/** GET /api/v1/bots/calibration — closed-trade win rates by setup bucket. */
export async function fetchBotCalibration({ botId, symbol, minSamples = 3, limit = 2000 } = {}) {
  const qs = new URLSearchParams();
  if (botId) qs.set('bot_id', botId);
  if (symbol) qs.set('symbol', symbol);
  qs.set('min_samples', String(minSamples));
  qs.set('limit', String(limit));
  const body = await apiRequest(`/api/v1/bots/calibration?${qs}`);
  if (!body?.ok) throw new Error(body?.error || 'Calibration unavailable');
  return body.calibration;
}

/** GET /api/v1/bots/filter-rejects — live + backtest CHART_AGENT filter reject aggregates. */
export async function fetchFilterRejects({ botId, symbol, strategy } = {}) {
  const qs = new URLSearchParams();
  if (botId) qs.set('bot_id', botId);
  if (symbol) qs.set('symbol', symbol);
  if (strategy) qs.set('strategy', strategy);
  const body = await apiRequest(`/api/v1/bots/filter-rejects?${qs}`);
  if (!body?.ok) throw new Error(body?.error || 'Filter rejects unavailable');
  return body.filter_rejects;
}

/** GET /api/v1/news/{symbol} — financial headlines (Finnhub, yfinance, Polygon). */
export async function fetchSymbolNews(symbol, {
  refresh = true,
  limit = 40,
  lookbackHours = 72,
  sources = null,
} = {}) {
  const qs = new URLSearchParams();
  qs.set('refresh', refresh ? 'true' : 'false');
  qs.set('limit', String(limit));
  qs.set('lookback_hours', String(lookbackHours));
  if (sources?.length) qs.set('sources', sources.join(','));
  const body = await apiRequest(`/api/v1/news/${encodeURIComponent(symbol)}?${qs}`, {
    timeoutMs: 30000,
  });
  if (!body?.ok) throw new Error(body?.error || 'News feed unavailable');
  return body;
}

function slimBacktestResultsForAdvisor(results) {
  if (!results || typeof results !== 'object') return null;
  return {
    run_id: results.run_id,
    meta: results.meta,
    summary: results.summary,
    total_pnl: results.total_pnl,
    win_rate: results.win_rate,
    trade_count: results.trade_count,
    max_drawdown: results.max_drawdown,
  };
}

/** POST /api/v1/bots/{botId}/strategy-suggest — LLM/heuristic param suggestions + shadow backtest. */
export async function fetchStrategySuggestion(botId, {
  days = 30,
  runBacktest = true,
  useLlm = true,
  recentResults = null,
} = {}) {
  const body = await apiRequest(`/api/v1/bots/${encodeURIComponent(botId)}/strategy-suggest`, {
    method: 'POST',
    timeoutMs: 180_000,
    body: {
      days,
      run_backtest: runBacktest,
      use_llm: useLlm,
      recent_results: slimBacktestResultsForAdvisor(recentResults),
    },
  });
  if (!body?.ok) throw new Error(body?.error || 'Strategy advisor unavailable');
  return body;
}

/** GET /api/v1/bots/{botId}/meta-label/status — GBM model status + dataset stats. */
export async function fetchMetaLabelStatus(botId) {
  const body = await apiRequest(`/api/v1/bots/${encodeURIComponent(botId)}/meta-label/status`);
  if (!body?.ok) throw new Error(body?.error || 'Meta-label status unavailable');
  return body;
}

/** POST /api/v1/bots/{botId}/meta-label/retrain — train GBM classifier from closed trades. */
export async function retrainMetaLabelModel(botId, { minSamples } = {}) {
  const body = await apiRequest(`/api/v1/bots/${encodeURIComponent(botId)}/meta-label/retrain`, {
    method: 'POST',
    timeoutMs: 120_000,
    body: minSamples != null ? { min_samples: minSamples } : {},
  });
  if (!body?.ok) throw new Error(body?.error || body?.result?.error || 'Meta-label retrain failed');
  return body;
}

/** POST /api/v1/backtest/meta-label-walk-forward — OOS GBM gate evaluation (no full backtest). */
export async function fetchMetaLabelWalkForward({
  botId,
  symbol,
  strategy = 'CHART_AGENT',
  config,
  days = 30,
  timeframe,
  rollingFolds = 2,
  trainPct = 70,
  minTrainSamples,
} = {}) {
  const body = await apiRequest('/api/v1/backtest/meta-label-walk-forward', {
    method: 'POST',
    timeoutMs: 300_000,
    body: {
      bot_id: botId || undefined,
      symbol: symbol || undefined,
      strategy,
      config: config || undefined,
      days,
      timeframe: timeframe || undefined,
      rolling_folds: rollingFolds,
      train_pct: trainPct,
      min_train_samples: minTrainSamples,
    },
  });
  if (!body?.ok && !body?.walk_forward) {
    throw new Error(body?.error || body?.walk_forward?.error || 'Walk-forward evaluation failed');
  }
  return body;
}

/** POST /api/v1/bots/{botId}/meta-label/operational — shadow / promote / rollback rollout. */
export async function applyMetaLabelOperational(botId, {
  stage,
  walkForward,
  requirePositiveOos = true,
  retrain,
} = {}) {
  const body = await apiRequest(`/api/v1/bots/${encodeURIComponent(botId)}/meta-label/operational`, {
    method: 'POST',
    timeoutMs: 180_000,
    body: {
      stage,
      walk_forward: walkForward || undefined,
      require_positive_oos: requirePositiveOos,
      retrain: retrain ?? stage === 'promote',
    },
  });
  if (!body?.ok) throw new Error(body?.error || 'Operational rollout failed');
  return body;
}

/** POST /api/v1/bots/calibration/apply — merge threshold suggestions into bot config. */
export async function applyCalibrationSuggestions({
  botId,
  symbol,
  kinds,
  applyAll = false,
  minSamples = 3,
} = {}) {
  const body = await apiRequest('/api/v1/bots/calibration/apply', {
    method: 'POST',
    body: {
      bot_id: botId,
      symbol: symbol || undefined,
      kinds: kinds || undefined,
      apply_all: applyAll,
      min_samples: minSamples,
    },
  });
  if (!body?.ok) throw new Error(body?.error || 'Failed to apply calibration suggestions');
  return body;
}

/** POST /api/v1/agent/pipeline/scan-deploy — rank scan results and deploy CHART_AGENT bots. */
export async function pipelineScanDeploy({
  symbols,
  maxDeploy = 3,
  minConfidence = 0.6,
  minScore = 2,
  allocation = 1000,
  timeframe = '1m',
  signalFilter = 'ACTIONABLE',
  dryRun = false,
  config,
} = {}) {
  const body = await apiRequest('/api/v1/agent/pipeline/scan-deploy', {
    method: 'POST',
    body: {
      symbols,
      max_deploy: maxDeploy,
      min_confidence: minConfidence,
      min_score: minScore,
      allocation,
      timeframe,
      signal_filter: signalFilter,
      dry_run: dryRun,
      config: config || undefined,
    },
  });
  if (!body?.ok) throw new Error(body?.error || 'Pipeline scan-deploy failed');
  return body.pipeline;
}

/** GET /api/v1/agent/pipeline/status — active pipeline-deployed bots. */
export async function fetchPipelineStatus({ strategy, timeframe } = {}) {
  const qs = new URLSearchParams();
  if (strategy) qs.set('strategy', strategy);
  if (timeframe) qs.set('timeframe', timeframe);
  const body = await apiRequest(`/api/v1/agent/pipeline/status?${qs}`);
  if (!body?.ok) throw new Error(body?.error || 'Pipeline status unavailable');
  return body;
}

export { stopBacktestJobPolling } from '../lib/backtestPolling';

export function startBacktestJobPolling(jobId, storeActions) {
  stopBacktestJobPolling();
  storeActions.setBacktestJobId(jobId);
  storeActions.setBacktestRunning(true);
  const pollStartedAt = Date.now();
  const pollMaxMs = 45 * 60 * 1000;
  const poll = () => {
    if (Date.now() - pollStartedAt > pollMaxMs) {
      stopBacktestJobPolling();
      clearBacktestClientTimeout();
      storeActions.setBacktestRunning(false);
      storeActions.setBacktestProgress(null);
      const msg = 'Background backtest stopped responding — check Jobs tab or retry';
      storeActions.setBacktestLastError?.(msg, null);
      toast.error(msg);
      return;
    }
    fetchBacktestJob(jobId)
      .then((fresh) => {
        if (!fresh) return;
        if (fresh.progress) storeActions.setBacktestProgress(fresh.progress);
        if (fresh.status === 'completed' && fresh.results) {
          stopBacktestJobPolling();
          clearBacktestClientTimeout();
          const wire = trimBacktestPayload({
            ...fresh.results,
            run_id: fresh.run_id ?? fresh.results.run_id,
          });
          storeActions.setBacktestResults(wire);
          storeActions.setBacktestRunning(false);
          storeActions.setBacktestProgress(null);
          storeActions.clearBacktestLastError?.();
          const overlay = buildBacktestOverlay(wire);
          if (overlay) storeActions.setBacktestOverlay(overlay);
          const pnl = wire?.total_pnl;
          const trades = wire?.trade_count ?? 0;
          toast.success(
            `Background backtest complete · ${pnl != null ? `$${Number(pnl).toFixed(2)}` : '—'} · ${trades} trades`,
            { action: { label: 'Open Lab', onClick: () => useStore.getState().openBacktestLab('results') } },
          );
          return;
        }
        if (fresh.status === 'failed' || fresh.status === 'cancelled') {
          stopBacktestJobPolling();
          clearBacktestClientTimeout();
          storeActions.setBacktestRunning(false);
          storeActions.setBacktestProgress(null);
          if (fresh.status === 'failed') {
            const msg = fresh.error || 'Background backtest failed';
            storeActions.setBacktestLastError?.(msg, fresh.request ?? null);
            toast.error(msg);
          } else {
            toast.info('Backtest cancelled');
          }
          return;
        }
        if (['pending', 'running'].includes(fresh.status)) {
          scheduleBacktestJobPoll(poll, 2000);
        }
      })
      .catch(() => {
        scheduleBacktestJobPoll(poll, 3000);
      });
  };
  scheduleBacktestJobPoll(poll, 1500);
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

export async function fetchCandles(symbol, storeActions, { limit = CHART_SNAPSHOT_BARS, interval, timeoutMs } = {}) {
  const encoded = encodeURIComponent(symbol);
  const params = new URLSearchParams();
  if (limit != null && limit > 0) params.set('limit', String(limit));
  if (interval && interval !== '1m') params.set('interval', interval);
  const qs = params.toString() ? `?${params.toString()}` : '';
  const body = await apiAction(`/api/v1/market/${encoded}/candles${qs}`, {
    timeoutMs: timeoutMs ?? (interval && interval !== '1m' ? 25000 : undefined),
  });
  applyHttpEnvelope(body, storeActions);
  return body;
}

export async function fetchAgentInsights(symbol, storeActions, limit = 30, timeframe = null) {
  try {
    const encoded = encodeURIComponent(symbol);
    const qs = new URLSearchParams({ limit: String(limit) });
    if (timeframe) {
      qs.set('timeframe', normalizeAnalystTimeframe(timeframe));
    }
    const body = await apiRequest(`/api/v1/agent/insights/${encoded}?${qs}`);
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
