/**
 * MlModelVersionSelect — pick which trained snapshot a bot should use.
 *
 * Empty value = follow the activated "current" model on disk.
 * Non-empty = pin bot.config.model_version (ISO trained_at or version_id).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiRequest, isAbortError } from '@/api/client';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { formatPinnedVersionShort, isMlStrategy } from '@/components/MlModelStatusBadge';
import { cn } from '@/lib/utils';
import {
  getCachedModelStatus,
  resolveModelStatusFetch,
} from '@/lib/mlTrainingSession';

const LATEST = '__latest__';

function formatVersionLabel(v) {
  if (!v) return '—';
  const when = v.trained_at
    ? new Date(v.trained_at).toLocaleString()
    : (v.version_id || 'unknown');
  const acc = v.metrics?.val_accuracy ?? v.metrics?.accuracy;
  const accBit = acc != null ? ` · acc ${(Number(acc) * 100).toFixed(0)}%` : '';
  const cur = v.is_current ? ' (current)' : '';
  return `${when}${accBit}${cur}`;
}

export default function MlModelVersionSelect({
  strategy,
  symbol,
  value,
  onChange,
  disabled = false,
  className,
  compact = false,
  showLabel = true,
}) {
  const [status, setStatus] = useState(() => getCachedModelStatus(symbol, strategy));
  const [loading, setLoading] = useState(false);
  const statusRef = useRef(status);
  statusRef.current = status;

  const fetchStatus = useCallback(async () => {
    if (!symbol || !strategy || !isMlStrategy(strategy)) {
      setStatus(null);
      return;
    }
    setLoading(true);
    try {
      const body = await apiRequest(
        `/api/v1/ml/model-status?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(strategy)}`,
      );
      setStatus(resolveModelStatusFetch(symbol, strategy, {
        body,
        previous: statusRef.current,
      }));
    } catch (err) {
      if (!isAbortError(err)) {
        setStatus(resolveModelStatusFetch(symbol, strategy, {
          error: err,
          previous: statusRef.current,
        }));
      }
    } finally {
      setLoading(false);
    }
  }, [symbol, strategy]);

  useEffect(() => {
    const cached = getCachedModelStatus(symbol, strategy);
    if (cached) setStatus(cached);
    fetchStatus();
  }, [fetchStatus, symbol, strategy]);

  if (!isMlStrategy(strategy)) return null;

  const versions = Array.isArray(status?.versions) ? status.versions : [];
  const trained = Boolean(status?.trained);
  const selectValue = trained ? (value ? String(value) : LATEST) : '__none__';
  const currentLabel = status?.trained_at
    ? `Latest · ${formatPinnedVersionShort(null, status.trained_at)}`
    : 'Latest (activated)';
  const triggerLabel = !trained
    ? (loading ? 'Checking…' : 'No model trained')
    : (value ? formatPinnedVersionShort(value, status?.trained_at) : currentLabel);

  return (
    <div className={cn('ml-version-select', compact && 'ml-version-select--compact', className)}>
      {showLabel && (
        <Label className="algo-field-label mb-1 block">
          Model version
          {loading && <Loader2 size={10} className="inline ml-1 animate-spin opacity-60" />}
        </Label>
      )}
      <Select
        value={selectValue}
        onValueChange={(v) => {
          if (v === '__none__' || v === LATEST) onChange('');
          else onChange(v);
        }}
        disabled={disabled || loading || !trained}
      >
        <SelectTrigger
          className={cn('h-8 w-full text-xs num-mono', compact && 'h-7')}
          aria-label="ML model version"
        >
          <SelectValue placeholder={triggerLabel}>{triggerLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent position="popper">
          {!trained ? (
            <SelectItem value="__none__" className="text-xs" disabled>
              No model trained
            </SelectItem>
          ) : (
            <>
              <SelectItem value={LATEST} className="text-xs">
                {currentLabel}
              </SelectItem>
              {versions.map((v) => {
                const pin = v.trained_at || v.version_id;
                if (!pin) return null;
                return (
                  <SelectItem key={pin} value={pin} className="text-xs num-mono">
                    {formatVersionLabel(v)}
                  </SelectItem>
                );
              })}
            </>
          )}
        </SelectContent>
      </Select>
      {!trained && !loading && (
        <p className="algo-field-hint text-amber-400/90">
          No trained model for {symbol || 'this symbol'} — train in Model Training first.
        </p>
      )}
      {trained && (
        <p className="algo-field-hint">
          {value
            ? `Pinned — bot loads this snapshot even if you activate a newer one.`
            : `Follows the activated model on disk (retrain/activate updates live).`}
        </p>
      )}
    </div>
  );
}
