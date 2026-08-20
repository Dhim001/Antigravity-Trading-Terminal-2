import { useEffect, useMemo, useState } from 'react';
import { GitBranch, Loader2 } from 'lucide-react';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { fetchTransferDonors } from '@/api/endpoints';
import { isAbortError } from '@/api/client';
import {
  donorDisabledReason,
  freezeTrunkSupported,
  normalizeDonorList,
  scalerStrategySupported,
  transferSupported,
} from '@/lib/modelTransfer';
import { cn } from '@/lib/utils';

/**
 * "Transfer from donor" picker for the ML Lab train panel. Collapsible row
 * above Trigger retrain: pick a trained model from another asset to
 * warm-start this train instead of starting from scratch.
 *
 * Value shape: { enabled, symbol, versionId, scalerStrategy, freezeTrunk }.
 */
export function MlTransferDonorPicker({
  strategy,
  symbol,
  timeframe,
  disabled = false,
  value,
  onChange,
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [enabledBackend, setEnabledBackend] = useState(true);
  const [donors, setDonors] = useState(null);

  const supported = transferSupported(strategy);
  const showFreeze = freezeTrunkSupported(strategy);
  const showScaler = scalerStrategySupported(strategy);

  useEffect(() => {
    if (!open || !supported || !symbol || !strategy) return undefined;
    let cancelled = false;
    setLoading(true);
    fetchTransferDonors(strategy, symbol, timeframe)
      .then((body) => {
        if (cancelled) return;
        setEnabledBackend(body?.enabled !== false);
        setDonors(normalizeDonorList(body, symbol));
      })
      .catch((err) => {
        if (cancelled || isAbortError(err)) return;
        setDonors([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, supported, strategy, symbol, timeframe]);

  const reason = donorDisabledReason({
    enabled: enabledBackend,
    supported,
    donors: donors || [],
  });
  const selected = useMemo(
    () => (donors || []).find((d) => d.symbol === value?.symbol) || null,
    [donors, value?.symbol],
  );

  if (!supported) return null;

  const set = (patch) => onChange?.({ ...(value || {}), ...patch });
  const pickerDisabled = disabled || loading || Boolean(reason);

  return (
    <div className="ml-transfer" data-testid="ml-transfer-donor-picker">
      <button
        type="button"
        className="ml-transfer__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <GitBranch size={12} />
        <span>Transfer from donor</span>
        <span className="ml-transfer__toggle-hint">
          {value?.enabled && value?.symbol
            ? `warm-start from ${value.symbol}`
            : 'train from scratch'}
        </span>
        <span className={cn('ml-transfer__chevron', open && 'ml-transfer__chevron--open')}>▸</span>
      </button>
      {open && (
        <div className="ml-transfer__body">
          {loading && (
            <p className="text-[10px] text-muted-foreground flex items-center gap-1">
              <Loader2 size={10} className="animate-spin" /> Scanning other assets for donors…
            </p>
          )}
          {!loading && reason && (
            <p className="text-[10px] text-muted-foreground">{reason}</p>
          )}
          {!loading && !reason && (
            <>
              <label className="ml-transfer__row">
                <input
                  type="checkbox"
                  checked={Boolean(value?.enabled)}
                  disabled={pickerDisabled}
                  onChange={(e) => {
                    const enabled = e.target.checked;
                    set({
                      enabled,
                      symbol: enabled ? (value?.symbol || donors[0]?.symbol || '') : value?.symbol,
                    });
                  }}
                />
                <span className="text-xs">Warm-start this train from a donor model</span>
              </label>
              {value?.enabled && (
                <>
                  <div className="ml-transfer__row">
                    <Label className="ml-transfer__label">Donor asset</Label>
                    <Select
                      value={value?.symbol || ''}
                      onValueChange={(v) => set({ symbol: v, versionId: '' })}
                      disabled={pickerDisabled}
                    >
                      <SelectTrigger className="h-7 text-xs">
                        <SelectValue placeholder="Pick donor symbol" />
                      </SelectTrigger>
                      <SelectContent>
                        {(donors || []).map((d) => (
                          <SelectItem key={d.symbol} value={d.symbol}>
                            {d.symbol}
                            {d.meanReturnPct != null
                              ? ` · ${Number(d.meanReturnPct).toFixed(2)}%`
                              : d.accuracy != null
                                ? ` · acc ${(Number(d.accuracy) * 100).toFixed(1)}%`
                                : ''}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {selected && (
                    <p className="text-[10px] text-muted-foreground">
                      Donor trained {selected.trainedAt ? new Date(selected.trainedAt).toLocaleDateString() : '—'}
                      {' · '}registers as challenger, still gated by walk-forward.
                    </p>
                  )}
                  {showScaler && (
                    <div className="ml-transfer__row">
                      <Label className="ml-transfer__label">Feature scaler</Label>
                      <Select
                        value={value?.scalerStrategy || 'recompute'}
                        onValueChange={(v) => set({ scalerStrategy: v })}
                        disabled={pickerDisabled}
                      >
                        <SelectTrigger className="h-7 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="recompute">Recompute on target (recommended)</SelectItem>
                          <SelectItem value="carry">Carry donor scaler</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                  {showFreeze && (
                    <label className="ml-transfer__row">
                      <input
                        type="checkbox"
                        checked={Boolean(value?.freezeTrunk)}
                        disabled={pickerDisabled}
                        onChange={(e) => set({ freezeTrunk: e.target.checked })}
                      />
                      <span className="text-xs">Freeze trunk — adapt only the head</span>
                    </label>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
