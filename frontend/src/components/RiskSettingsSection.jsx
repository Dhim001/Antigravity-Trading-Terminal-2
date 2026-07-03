import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { RefreshCw, ShieldAlert, CheckCircle2, XCircle } from 'lucide-react';
import { Action } from '../api/protocol';
import { invokeHttpAction } from '../api/transport';
import { useStore } from '../store/useStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

function ConfigRow({ label, value, warn }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn('num-mono', warn && 'text-trading-warn')}>{value ?? '—'}</dd>
    </div>
  );
}

function CheckRow({ check }) {
  const ok = check?.allowed !== false;
  return (
    <li className={cn('flex items-start gap-2 text-xs', ok ? 'text-muted-foreground' : 'text-trading-down')}>
      {ok ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-trading-up" /> : (
        <XCircle size={14} className="mt-0.5 shrink-0" />
      )}
      <span>{check?.message || check?.id}</span>
    </li>
  );
}

export default function RiskSettingsSection() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(false);
  const [previewSide, setPreviewSide] = useState('BUY');
  const [previewNotional, setPreviewNotional] = useState('1000');
  const [previewSymbol, setPreviewSymbol] = useState(activeSymbol || 'AAPL');
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    if (activeSymbol) setPreviewSymbol(activeSymbol);
  }, [activeSymbol]);

  const loadConfig = useCallback(async () => {
    setLoading(true);
    try {
      const res = await invokeHttpAction(Action.RISK_GET_CONFIG, {});
      setConfig(res?.data?.risk_config ?? null);
    } catch (err) {
      toast.error(err?.message || 'Failed to load risk configuration');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const runPreview = useCallback(async () => {
    const notional = parseFloat(previewNotional);
    if (!previewSymbol?.trim()) {
      toast.error('Enter a symbol');
      return;
    }
    if (!Number.isFinite(notional) || notional <= 0) {
      toast.error('Enter a valid notional');
      return;
    }
    setPreviewLoading(true);
    try {
      const res = await invokeHttpAction(Action.RISK_PREVIEW_ENTRY, {
        symbol: previewSymbol.trim().toUpperCase(),
        side: previewSide,
        notional,
      });
      setPreview(res?.data?.risk_preview ?? null);
    } catch (err) {
      toast.error(err?.message || 'Preview failed');
      setPreview(null);
    } finally {
      setPreviewLoading(false);
    }
  }, [previewNotional, previewSide, previewSymbol]);

  useEffect(() => {
    const timer = setTimeout(runPreview, 400);
    return () => clearTimeout(timer);
  }, [runPreview]);

  const ks = config?.kill_switch ?? {};
  const tc = config?.time_controls ?? {};
  const pd = config?.position_duration ?? {};
  const dc = config?.dynamic_correlation ?? {};
  const pl = config?.portfolio_limits ?? {};
  const margin = config?.margin ?? {};

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground m-0">
          Server env configuration (read-only). Edit <code className="text-[0.65rem]">.env</code> and restart to change.
        </p>
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={loadConfig} disabled={loading}>
          <RefreshCw size={12} className={cn('mr-1', loading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <dl className="settings-defaults-list settings-defaults-list--grid num-mono text-xs">
        <ConfigRow
          label="Kill switch"
          value={ks.enabled ? `On · ${ks.max_drawdown_pct}% max DD` : 'Off'}
          warn={ks.tripped}
        />
        {ks.tripped && (
          <ConfigRow label="Kill switch status" value="TRIPPED" warn />
        )}
        <ConfigRow label="No-trade windows" value={tc.no_trade_windows || '—'} />
        <ConfigRow
          label="Equity no-trade now"
          value={tc.equity_no_trade_active ? tc.equity_no_trade_reason || 'Active' : 'Clear'}
          warn={tc.equity_no_trade_active}
        />
        <ConfigRow
          label="Weekend flatten"
          value={
            tc.weekend_flatten_enabled
              ? `Fri after ${tc.weekend_flatten_friday_after}${tc.weekend_flatten_active ? ' · ACTIVE' : ''}`
              : 'Off'
          }
          warn={tc.weekend_flatten_active}
        />
        <ConfigRow
          label="Max position hours"
          value={pd.enabled ? (pd.default_max_hours ?? 'Unlimited') : 'Off'}
        />
        <ConfigRow
          label="Dynamic correlation"
          value={
            dc.enabled
              ? `≥${dc.threshold} · ${dc.lookback_days}d lookback · ${dc.group_count ?? 0} groups`
              : 'Off'
          }
        />
        <ConfigRow label="Gross exposure cap" value={`${pl.max_gross_exposure_pct ?? '—'}% equity`} />
        <ConfigRow label="Group exposure cap" value={`${pl.max_group_exposure_pct ?? '—'}% equity`} />
        <ConfigRow
          label="Margin limits"
          value={
            margin.enabled
              ? `${margin.max_utilization_pct}% util · ${margin.max_leverage}x max lev`
              : 'Off'
          }
        />
      </dl>

      <div className="settings-form-card space-y-3">
        <div className="flex items-center gap-2">
          <ShieldAlert size={14} className="text-muted-foreground" />
          <p className="text-xs font-medium m-0">Live entry preview</p>
          {preview && (
            <Badge variant={preview.allowed ? 'outline' : 'destructive'} className="text-[0.6rem]">
              {preview.allowed ? 'Would allow' : 'Would block'}
            </Badge>
          )}
        </div>

        <div className="settings-form-grid">
          <div>
            <Label className="text-xs text-muted-foreground">Symbol</Label>
            <Input
              className="mt-1 h-8 text-xs num-mono"
              value={previewSymbol}
              onChange={(e) => setPreviewSymbol(e.target.value.toUpperCase())}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Side</Label>
            <Select value={previewSide} onValueChange={setPreviewSide}>
              <SelectTrigger className="mt-1 h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent position="popper">
                <SelectItem value="BUY" className="text-xs">BUY</SelectItem>
                <SelectItem value="SELL" className="text-xs">SELL</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Notional ($)</Label>
            <Input
              className="mt-1 h-8 text-xs num-mono"
              inputMode="decimal"
              value={previewNotional}
              onChange={(e) => setPreviewNotional(e.target.value)}
            />
          </div>
        </div>

        {previewLoading && (
          <p className="text-[0.62rem] text-muted-foreground m-0">Evaluating gates…</p>
        )}

        {preview && !previewLoading && (
          <div className="rounded-md border border-border/60 bg-muted/15 p-2">
            <p className="text-[0.62rem] text-muted-foreground m-0 mb-1.5">
              {preview.symbol} {preview.side} · ${Number(preview.notional).toFixed(2)} @ ${Number(preview.price).toFixed(2)}
            </p>
            <ul className="m-0 p-0 list-none space-y-1">
              {(preview.checks || []).map((c) => (
                <CheckRow key={c.id} check={c} />
              ))}
            </ul>
            {preview.block_reason && (
              <p className="text-xs text-trading-down mt-2 mb-0">{preview.block_reason}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
