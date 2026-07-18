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
import { apiRequest, isAbortError } from '@/api/client';
import { cn } from '@/lib/utils';
import { isMlStrategy } from '@/config/strategies';
import {
  getCachedModelStatus,
  resolveModelStatusFetch,
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

export default function MlModelStatusBadge({
  strategy,
  symbol,
  modelVersion = '',
  compact = false,
}) {
  const [status, setStatus] = useState(() => getCachedModelStatus(symbol, strategy));
  const statusRef = useRef(status);
  statusRef.current = status;

  const fetchStatus = useCallback(async () => {
    if (!symbol || !strategy || !isMlStrategy(strategy)) return;
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}`
      );
      const next = resolveModelStatusFetch(symbol, strategy, {
        body,
        previous: statusRef.current,
      });
      setStatus(next);
    } catch (err) {
      if (isAbortError(err)) return;
      const next = resolveModelStatusFetch(symbol, strategy, {
        error: err,
        previous: statusRef.current,
      });
      setStatus(next);
    }
  }, [symbol, strategy]);

  useEffect(() => {
    const cached = getCachedModelStatus(symbol, strategy);
    if (cached) setStatus(cached);
    fetchStatus();
  }, [fetchStatus, symbol, strategy]);

  const openTraining = useCallback((e) => {
    e.stopPropagation();
    openModelTrainingDock();
  }, []);

  if (!isMlStrategy(strategy)) return null;
  if (status === null) return null;

  if (status?.trained) {
    const pin = String(modelVersion || '').trim();
    const short = formatPinnedVersionShort(pin, status.trained_at);
    const title = pin
      ? `Pinned model ${pin}`
      : `Using latest activated model${status.trained_at ? ` (${status.trained_at})` : ''}`;
    return (
      <span
        className={cn(
          'ml-model-badge ml-model-badge--trained',
          pin && 'ml-model-badge--pinned',
        )}
        title={title}
      >
        {pin ? <Pin size={10} /> : <CheckCircle2 size={10} />}
        {!compact && <span>{pin ? `v ${short}` : short}</span>}
        {compact && <span className="ml-model-badge__ver">{short}</span>}
      </span>
    );
  }

  if (status?.error) {
    return (
      <span className="ml-model-badge ml-model-badge--error" title={status.error}>
        <AlertCircle size={10} />
        {!compact && (
          <button
            className="ml-model-badge__train-btn"
            onClick={openTraining}
            type="button"
          >
            Open Training
          </button>
        )}
      </span>
    );
  }

  return (
    <span className="ml-model-badge ml-model-badge--untrained" title="No model trained for this symbol">
      <XCircle size={10} />
      <button
        className="ml-model-badge__train-btn"
        onClick={openTraining}
        type="button"
        aria-label="Open Model Training"
      >
        Train
      </button>
    </span>
  );
}
