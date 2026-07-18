import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, Loader2, RotateCcw, Save, Settings2 } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import MlModelVersionSelect from '@/components/MlModelVersionSelect';
import { sendAction } from '../api/transport';
import { Action } from '../api/protocol';
import { cn } from '@/lib/utils';
import {
  DIRECTION_MODE_OPTIONS,
  TP_MODE_OPTIONS,
  buildConfigDraft,
  buildConfigFieldGroups,
  buildConfigPatch,
  getEditableConfigFields,
  confidenceRangeForStrategy,
} from '@/lib/botConfigDisplay';
import { BAR_TIMEFRAMES, formatBarTimeframeLabel } from '@/lib/barTimeframes';

const META_LABEL_MODE_OPTIONS = [
  { value: 'wilson', label: 'Wilson buckets (default)' },
  { value: 'gbm', label: 'GBM P(win) classifier' },
  { value: 'hybrid', label: 'Hybrid — GBM when trained, else Wilson' },
];

function ConfigField({ field, value, strategy, symbol, botTimeframe, disabled, onChange }) {
  const id = `bot-config-${field.key}`;

  if (field.input === 'model_version') {
    return (
      <div className="bot-config-field bot-config-field--full">
        <MlModelVersionSelect
          strategy={strategy}
          symbol={symbol}
          value={value || ''}
          onChange={(v) => onChange(field.key, v)}
          disabled={disabled}
          showLabel
        />
      </div>
    );
  }

  if (field.input === 'confirm_timeframe') {
    const selectValue = value ? String(value) : '__none__';
    const options = BAR_TIMEFRAMES.filter((tf) => {
      const key = tf.toLowerCase();
      return key !== String(botTimeframe || '').toLowerCase();
    });
    return (
      <div className="bot-config-field">
        <Label htmlFor={id} className="bot-config-field__label">{field.label}</Label>
        <Select
          value={selectValue}
          onValueChange={(v) => onChange(field.key, v === '__none__' ? '' : v.toLowerCase())}
          disabled={disabled}
        >
          <SelectTrigger id={id} className="bot-config-field__input h-8 w-full text-xs">
            <SelectValue placeholder="Disabled" />
          </SelectTrigger>
          <SelectContent position="popper">
            <SelectItem value="__none__" className="text-xs">Disabled</SelectItem>
            {options.map((tf) => (
              <SelectItem key={tf} value={tf.toLowerCase()} className="text-xs">
                {formatBarTimeframeLabel(tf)} trend confirm
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
      </div>
    );
  }

  if (field.input === 'select') {
    const strat = (strategy || '').toUpperCase();
    let options;
    let defaultValue;
    if (field.key === 'direction_mode') {
      options = DIRECTION_MODE_OPTIONS;
      defaultValue = 'LONG_ONLY';
    } else if (field.key === 'meta_label_model_mode') {
      options = META_LABEL_MODE_OPTIONS;
      defaultValue = 'wilson';
    } else {
      options = TP_MODE_OPTIONS.filter(
        (opt) => !opt.strategies || opt.strategies.includes(strat),
      );
      defaultValue = 'percent';
    }
    return (
      <div className="bot-config-field">
        <Label htmlFor={id} className="bot-config-field__label">{field.label}</Label>
        <Select value={value || defaultValue} onValueChange={(v) => onChange(field.key, v)} disabled={disabled}>
          <SelectTrigger id={id} className="bot-config-field__input h-8 w-full text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent position="popper">
            {options.map((opt) => (
              <SelectItem key={opt.value} value={opt.value} className="text-xs">
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
      </div>
    );
  }

  if (field.input === 'checkbox') {
    return (
      <div className="bot-config-field bot-config-field--checkbox">
        <label htmlFor={id} className="bot-config-field__checkbox-row">
          <input
            id={id}
            type="checkbox"
            className="accent-primary"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange(field.key, e.target.checked)}
          />
          <span className="bot-config-field__label">{field.label}</span>
        </label>
        {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
      </div>
    );
  }

  if (field.input === 'range') {
    const bounds = confidenceRangeForStrategy(strategy);
    const raw = parseFloat(value);
    const current = Number.isFinite(raw) ? raw : bounds.defaultValue;
    const pctLabel = bounds.max <= 0.1
      ? current.toFixed(4)
      : `${Math.round(current * 100)}%`;
    return (
      <div className="bot-config-field">
        <div className="bot-config-field__range-head">
          <Label htmlFor={id} className="bot-config-field__label">{field.label}</Label>
          <span className="bot-config-field__range-value">{pctLabel}</span>
        </div>
        <input
          id={id}
          type="range"
          min={bounds.min}
          max={bounds.max}
          step={bounds.step}
          value={current}
          disabled={disabled}
          className="bot-config-field__range w-full accent-primary"
          onChange={(e) => onChange(field.key, e.target.value)}
        />
        {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
      </div>
    );
  }

  if (field.input === 'text' || field.input === 'readonly') {
    return (
      <div className="bot-config-field">
        <Label htmlFor={id} className="bot-config-field__label">{field.label}</Label>
        <Input
          id={id}
          type="text"
          value={value ?? ''}
          disabled={disabled || field.input === 'readonly'}
          readOnly={field.input === 'readonly'}
          onChange={(e) => onChange(field.key, e.target.value)}
          className="bot-config-field__input h-8 text-xs num-mono"
        />
        {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
      </div>
    );
  }

  const suffix = field.kind === 'percent' ? '%' : field.kind === 'seconds' ? 's' : null;

  return (
    <div className="bot-config-field">
      <Label htmlFor={id} className="bot-config-field__label">{field.label}</Label>
      <div className="bot-config-field__input-wrap">
        <Input
          id={id}
          type="number"
          min="0"
          step={field.kind === 'integer' ? '1' : 'any'}
          placeholder={field.kind === 'percent' ? 'e.g. 2' : undefined}
          value={value ?? ''}
          disabled={disabled}
          onChange={(e) => onChange(field.key, e.target.value)}
          className={cn('bot-config-field__input h-8 text-xs', suffix && 'pr-8')}
        />
        {suffix && <span className="bot-config-field__suffix">{suffix}</span>}
      </div>
      {field.hint && <p className="bot-config-field__hint">{field.hint}</p>}
    </div>
  );
}

export default function BotConfigPanel({
  botId,
  strategy,
  symbol,
  config,
  botStatus,
  botTimeframe,
  position,
}) {
  const fields = useMemo(() => getEditableConfigFields(strategy, config), [strategy, config]);
  const baseDraft = useMemo(() => buildConfigDraft(config, fields), [config, fields]);
  const [overrides, setOverrides] = useState({});
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const draft = useMemo(() => ({ ...baseDraft, ...overrides }), [baseDraft, overrides]);
  const disabled = botStatus === 'STOPPED';
  const groups = useMemo(() => buildConfigFieldGroups(fields, draft), [fields, draft]);
  const fieldCount = groups.reduce((n, g) => n + g.fields.length, 0);

  useEffect(() => {
    setOverrides({});
    setDirty(false);
  }, [botId, baseDraft]);

  if (fieldCount === 0) return null;

  const updateField = (key, value) => {
    setOverrides((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const resetDraft = () => {
    setOverrides({});
    setDirty(false);
  };

  const saveConfig = async () => {
    if (!botId || saving) return;
    const patch = buildConfigPatch(draft, config, fields, { botTimeframe });
    if (Object.keys(patch).length === 0) {
      toast.message('No changes to save');
      return;
    }
    setSaving(true);
    try {
      await sendAction(Action.BOT_UPDATE_CONFIG, { bot_id: botId, config: patch });
      toast.success('Bot config updated');
      setDirty(false);
    } catch (err) {
      toast.error(err?.message || 'Failed to update bot config');
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="bot-detail-drawer__config">
      <summary className="bot-detail-drawer__config-summary">
        <Settings2 size={13} className="bot-detail-drawer__config-icon" aria-hidden />
        <span>Strategy config</span>
        <span className="bot-detail-drawer__config-summary-spacer" aria-hidden />
        {dirty && <Badge variant="outline" className="bot-detail-drawer__config-dirty">Unsaved</Badge>}
        <Badge variant="secondary" className="bot-detail-drawer__config-count">
          {fieldCount}
        </Badge>
        <ChevronDown size={14} className="bot-detail-drawer__config-chevron" aria-hidden />
      </summary>

      <div className="bot-detail-drawer__config-body">
        {disabled && (
          <p className="bot-config-panel__notice">
            Bot is stopped — config is read-only. Resume or redeploy to change parameters.
          </p>
        )}

        {position?.take_profit_price != null && (
          <p className="bot-config-panel__active-tp">
            Active TP on open position:{' '}
            <span className="num-mono">{Number(position.take_profit_price).toFixed(4)}</span>
            {position.take_profit_percent != null && (
              <> ({Number(position.take_profit_percent).toFixed(2)}%)</>
            )}
          </p>
        )}

        {groups.map((group) => (
          <section key={group.id} className="bot-config-group" aria-label={group.label}>
            <h4 className="bot-config-group__title">{group.label}</h4>
            <div className="bot-config-form">
              {group.fields.map((field) => (
                <ConfigField
                  key={field.key}
                  field={field}
                  value={draft[field.key]}
                  strategy={strategy}
                  symbol={symbol || config?.model_symbol}
                  botTimeframe={botTimeframe}
                  disabled={disabled || saving}
                  onChange={updateField}
                />
              ))}
            </div>
          </section>
        ))}

        <div className="bot-config-actions">
          <Button
            variant="outline"
            size="xs"
            disabled={disabled || saving || !dirty}
            onClick={resetDraft}
          >
            <RotateCcw />
            Reset
          </Button>
          <Button
            variant="default"
            size="xs"
            disabled={disabled || saving || !dirty}
            onClick={saveConfig}
          >
            {saving ? <Loader2 className="size-3 animate-spin" /> : <Save />}
            Save config
          </Button>
        </div>
      </div>
    </details>
  );
}
