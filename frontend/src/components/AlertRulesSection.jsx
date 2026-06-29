import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { BellRing, Plus, Trash2, RefreshCw, History } from 'lucide-react';
import { Action } from '../api/protocol';
import { invokeHttpAction } from '../api/transport';
import { BAR_TIMEFRAMES, formatBarTimeframeLabel } from '@/lib/barTimeframes';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const CONDITIONS = [
  { id: 'price_above', label: 'Price above', needsThreshold: true },
  { id: 'price_below', label: 'Price below', needsThreshold: true },
  { id: 'rsi_above', label: 'RSI above', needsThreshold: true },
  { id: 'rsi_below', label: 'RSI below', needsThreshold: true },
  { id: 'macd_cross_bull', label: 'MACD bullish cross', needsThreshold: false },
  { id: 'macd_cross_bear', label: 'MACD bearish cross', needsThreshold: false },
  { id: 'signal_is', label: 'Analyst signal is', needsSignal: true },
  { id: 'pct_change_above', label: '% change above', needsThreshold: true },
  { id: 'pct_change_below', label: '% change below', needsThreshold: true },
];

const EMPTY_FORM = {
  id: null,
  name: '',
  symbol: '',
  timeframe: '1m',
  condition_type: 'rsi_above',
  threshold: '70',
  signal: 'BUY',
  cooldown_sec: '300',
  enabled: true,
};

function conditionLabel(type) {
  return CONDITIONS.find((c) => c.id === type)?.label || type;
}

export default function AlertRulesSection({ activeSymbol = '' }) {
  const [rules, setRules] = useState([]);
  const [channels, setChannels] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM, symbol: activeSymbol });
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const selectedCondition = CONDITIONS.find((c) => c.id === form.condition_type) || CONDITIONS[2];

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [rulesRes, channelsRes] = await Promise.all([
        invokeHttpAction(Action.ALERT_RULE_LIST, {}),
        invokeHttpAction(Action.NOTIFY_CHANNEL_LIST, {}),
      ]);
      setRules(rulesRes?.data?.alert_rules ?? []);
      setChannels(channelsRes?.data?.notification_channels ?? []);
    } catch (err) {
      toast.error(err?.message || 'Failed to load alert rules');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const res = await invokeHttpAction(Action.ALERT_RULE_HISTORY, { limit: 30 });
      setHistory(res?.data?.alert_rule_history ?? []);
    } catch (err) {
      toast.error(err?.message || 'Failed to load history');
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    if (showHistory) loadHistory();
  }, [showHistory, loadHistory]);

  const saveRule = async () => {
    if (!form.name.trim() || !form.symbol.trim()) {
      toast.error('Name and symbol are required');
      return;
    }
    if (selectedCondition.needsThreshold) {
      const t = Number(form.threshold);
      if (Number.isNaN(t)) {
        toast.error('Enter a valid threshold');
        return;
      }
    }
    try {
      const res = await invokeHttpAction(Action.ALERT_RULE_UPSERT, {
        id: form.id,
        name: form.name.trim(),
        symbol: form.symbol.trim().toUpperCase(),
        timeframe: form.timeframe,
        condition_type: form.condition_type,
        enabled: form.enabled,
        cooldown_sec: parseInt(form.cooldown_sec, 10) || 300,
        ...(selectedCondition.needsThreshold ? { threshold: Number(form.threshold) } : {}),
        ...(selectedCondition.needsSignal ? { signal: form.signal } : {}),
      });
      if (res?.data?.status === 'error') {
        toast.error(res.data.message || 'Save failed');
        return;
      }
      toast.success('Alert rule saved');
      setForm({ ...EMPTY_FORM, symbol: activeSymbol });
      setEditing(false);
      loadAll();
    } catch (err) {
      toast.error(err?.message || 'Save failed');
    }
  };

  const deleteRule = async (id) => {
    try {
      await invokeHttpAction(Action.ALERT_RULE_DELETE, { id });
      toast.success('Rule deleted');
      if (form.id === id) {
        setForm({ ...EMPTY_FORM, symbol: activeSymbol });
        setEditing(false);
      }
      loadAll();
    } catch (err) {
      toast.error(err?.message || 'Delete failed');
    }
  };

  const startEdit = (rule) => {
    setEditing(true);
    setForm({
      id: rule.id,
      name: rule.name,
      symbol: rule.symbol,
      timeframe: rule.timeframe || '1m',
      condition_type: rule.condition_type,
      threshold: rule.threshold != null ? String(rule.threshold) : '',
      signal: rule.signal || 'BUY',
      cooldown_sec: String(rule.cooldown_sec || 300),
      enabled: rule.enabled !== false,
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground max-w-xl">
          Server-side rules evaluated on bar close (RSI, price, MACD, analyst signal).
          Triggers webhook/Telegram/email channels subscribed to <strong>Alert rules</strong>.
        </p>
        <div className="flex gap-1">
          <Button variant="outline" size="xs" onClick={() => setShowHistory((v) => !v)}>
            <History data-icon="inline-start" aria-hidden />
            {showHistory ? 'Hide history' : 'History'}
          </Button>
          <Button variant="ghost" size="xs" onClick={loadAll} disabled={loading}>
            <RefreshCw data-icon="inline-start" className={loading ? 'animate-spin' : ''} aria-hidden />
            Refresh
          </Button>
        </div>
      </div>

      {channels.length === 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Add a notification channel below and enable the &quot;Alert rules&quot; event type.
        </p>
      )}

      {rules.length > 0 && (
        <ul className="space-y-2">
          {rules.map((rule) => (
            <li
              key={rule.id}
              className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs"
            >
              <BellRing size={14} className="shrink-0 text-muted-foreground" aria-hidden />
              <span className="font-medium">{rule.name}</span>
              <Badge variant="outline">{rule.symbol}</Badge>
              <Badge variant="secondary">{formatBarTimeframeLabel(rule.timeframe)}</Badge>
              {!rule.enabled && <Badge variant="secondary">Off</Badge>}
              <span className="text-muted-foreground truncate max-w-[200px]">
                {conditionLabel(rule.condition_type)}
                {rule.threshold != null ? ` ${rule.threshold}` : ''}
                {rule.signal ? ` → ${rule.signal}` : ''}
              </span>
              <span className="flex-1" />
              <Button variant="ghost" size="xs" onClick={() => startEdit(rule)}>Edit</Button>
              <Button variant="ghost" size="xs" onClick={() => deleteRule(rule.id)}>
                <Trash2 data-icon="inline-start" aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {showHistory && history.length > 0 && (
        <ul className="max-h-40 overflow-y-auto space-y-1 rounded border border-border/50 p-2 text-[11px] font-mono">
          {history.map((h) => (
            <li key={h.id} className="text-muted-foreground">
              {new Date((h.created_at || 0) * 1000).toLocaleString()} — {h.message}
            </li>
          ))}
        </ul>
      )}

      <div className="rounded-md border border-border/60 p-3 space-y-3">
        <div className="flex items-center gap-2">
          <Plus size={14} aria-hidden />
          <span className="text-sm font-medium">{editing ? 'Edit rule' : 'Add alert rule'}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label className="text-xs">Name</Label>
            <Input className="h-8 text-xs" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Symbol</Label>
            <Input className="h-8 text-xs font-mono uppercase" value={form.symbol} onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Timeframe</Label>
            <Select value={form.timeframe} onValueChange={(v) => setForm((f) => ({ ...f, timeframe: v }))}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {BAR_TIMEFRAMES.map((tf) => (
                  <SelectItem key={tf} value={tf}>{formatBarTimeframeLabel(tf)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Condition</Label>
            <Select value={form.condition_type} onValueChange={(v) => setForm((f) => ({ ...f, condition_type: v }))}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {CONDITIONS.map((c) => (
                  <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {selectedCondition.needsThreshold && (
          <div className="space-y-1.5">
            <Label className="text-xs">Threshold</Label>
            <Input className="h-8 text-xs font-mono" value={form.threshold} onChange={(e) => setForm((f) => ({ ...f, threshold: e.target.value }))} />
          </div>
        )}
        {selectedCondition.needsSignal && (
          <div className="space-y-1.5">
            <Label className="text-xs">Signal</Label>
            <Select value={form.signal} onValueChange={(v) => setForm((f) => ({ ...f, signal: v }))}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="BUY">BUY</SelectItem>
                <SelectItem value="SELL">SELL</SelectItem>
                <SelectItem value="NONE">NONE</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="space-y-1.5">
          <Label className="text-xs">Cooldown (seconds)</Label>
          <Input className="h-8 text-xs font-mono w-32" value={form.cooldown_sec} onChange={(e) => setForm((f) => ({ ...f, cooldown_sec: e.target.value }))} />
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <Checkbox checked={form.enabled} onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: Boolean(v) }))} />
          Enabled
        </label>
        <div className="flex gap-2">
          <Button size="sm" onClick={saveRule}>{editing ? 'Update' : 'Add'} rule</Button>
          {editing && (
            <Button size="sm" variant="outline" onClick={() => { setForm({ ...EMPTY_FORM, symbol: activeSymbol }); setEditing(false); }}>
              Cancel
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
