/**
 * MlModelStatusBadge — inline badge showing ML model status per symbol.
 *
 * States:
 *   🟢 Trained   — model exists, shows training date / pinned version
 *   🔴 Untrained — no model for this symbol
 *   ⚠  Error     — last training attempt failed (CTA opens Model Training)
 */
import React, { useEffect, useState, useCallback, useRef } from 'react';
import { CheckCircle2, XCircle, AlertCircle, Pin } from 'lucide-react';
import { apiRequest } from '@/api/client';
import { cn } from '@/lib/utils';
import { isMlStrategy } from '@/config/strategies';
import {
  getCachedModelStatus,
  normalizeStatusTimeframe,
  resolveModelStatusFetch,
  subscribeModelStatusCache,
} from '@/lib/mlTrainingSession';
import { openModelTrainingDock } from '@/lib/workspaceNav';

export { isMlStrategy } from '@/config/strategies';

/** Short label for pinned / latest model timestamps in Algo UI. */
export function formatPinnedVersionShort(pin, fallbackTrainedAt) {
  const raw = pin || fallbackTrainedAt;
  if (!raw) return 'latest';
  try {
    const d = new Date(raw);
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }
  } catch {
    /* fall through */
  }
  return String(raw).slice(0, 16);
}

/** CTA as span — never nest <button> inside StrategyTemplateCard / other buttons. */
function TrainCta({ label, onActivate, ariaLabel }) {
  const onKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onActivate(e);
    }
  };
  return (
    <span
      className="ml-model-badge__train-btn"
      role="button"
      tabIndex={0}
      onClick={onActivate}
      onKeyDown={onKeyDown}
      aria-label={ariaLabel || label}
    >
      {label}
    </span>
  );
}

export default function MlModelStatusBadge({
  strategy,
  symbol,
  timeframe = '1m',
  modelVersion = '',
  compact = false,
  /** When false, status-only (safe inside parent <button> cards). */
  showCta = true,
}) {
  const tf = normalizeStatusTimeframe(timeframe);
  const [status, setStatus] = useState(() => getCachedModelStatus(symbol, strategy, tf));
  const statusRef = useRef(status);
  statusRef.current = status;

  const fetchStatus = useCallback(async () => {
    if (!symbol || !strategy || !isMlStrategy(strategy)) return;
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}&timeframe=${encodeURIComponent(tf)}`,
      );
      const next = resolveModelStatusFetch(symbol, strategy, {
        body,
        previous: statusRef.current,
        timeframe: tf,
      });
      setStatus(next);
    } catch (err) {
      // Aborts used to leave status=null → badge returned null ("vanished").
      const next = resolveModelStatusFetch(symbol, strategy, {
        error: err,
        previous: statusRef.current,
        timeframe: tf,
      });
      if (next != null) setStatus(next);
    }
  }, [symbol, strategy, tf]);

  useEffect(() => {
    const cached = getCachedModelStatus(symbol, strategy, tf);
    setStatus(cached);
    fetchStatus();
  }, [fetchStatus, symbol, strategy, tf]);

  // Lab / ActiveBotRow share the status cache — stay in sync after train
  // completes (invalidate + refetch) instead of freezing a pre-train snapshot.
  useEffect(() => {
    return subscribeModelStatusCache(() => {
      const cached = getCachedModelStatus(symbol, strategy, tf);
      if (cached) {
        setStatus(cached);
      } else if (symbol && strategy && isMlStrategy(strategy)) {
        fetchStatus();
      }
    });
  }, [symbol, strategy, tf, fetchStatus]);

  const openTraining = useCallback((e) => {
    e.stopPropagation();
    e.preventDefault?.();
    openModelTrainingDock();
  }, []);

  if (!isMlStrategy(strategy)) return null;
  if (status === null) {
    return (
      <span
        className="ml-model-badge ml-model-badge--loading"
        title={`Checking ${tf} model status…`}
      >
        <AlertCircle size={10} />
        {!compact && <span>…</span>}
      </span>
    );
  }

  if (status?.trained) {
    const pin = String(modelVersion || '').trim();
    const short = formatPinnedVersionShort(pin, status.trained_at);
    const desync = status.champion_desynced
      ? ` · newest ≠ live (${status.newest_version_id || '…'})`
      : '';
    const title = pin
      ? `Pinned ${tf} model ${pin}${desync}`
      : `Using latest ${tf} model${status.trained_at ? ` (${status.trained_at})` : ''}${desync}`;
    return (
      <span
        className={cn(
          'ml-model-badge ml-model-badge--trained',
          pin && 'ml-model-badge--pinned',
          status.champion_desynced && 'ml-model-badge--desynced',
        )}
        title={title}
      >
        {pin ? <Pin size={10} /> : <CheckCircle2 size={10} />}
        {!compact && <span>{pin ? `v ${short}` : short}{status.champion_desynced ? '≠' : ''}</span>}
        {compact && <span className="ml-model-badge__ver">{short}</span>}
      </span>
    );
  }

  if (status?.error) {
    return (
      <span className="ml-model-badge ml-model-badge--error" title={status.error}>
        <AlertCircle size={10} />
        {showCta && !compact && (
          <TrainCta label="Open Training" onActivate={openTraining} />
        )}
      </span>
    );
  }

  return (
    <span
      className="ml-model-badge ml-model-badge--untrained"
      title={`No ${tf} model trained for this symbol`}
    >
      <XCircle size={10} />
      {showCta ? (
        <TrainCta
          label="Train"
          onActivate={openTraining}
          ariaLabel="Open Model Training"
        />
      ) : (
        <span className="ml-model-badge__ver">no {tf}</span>
      )}
    </span>
  );
}
