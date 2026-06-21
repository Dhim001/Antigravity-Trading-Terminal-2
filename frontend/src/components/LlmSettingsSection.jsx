import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Download, Loader2, RefreshCw } from 'lucide-react';
import { useStore } from '../store/useStore';
import {
  fetchLlmModels,
  fetchLlmOps,
  pullLlmModel,
  setPreferredLlmModel,
} from '../api/endpoints';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';

function modelLabel(meta, fallbackId) {
  if (!meta) return fallbackId;
  const parts = [meta.label || meta.id || fallbackId];
  if (meta.tier && meta.tier !== 'unknown') parts.push(meta.tier);
  if (meta.recommended) parts.push('recommended');
  return parts.join(' · ');
}

function buildMetaMap(modelsBody) {
  const map = new Map();
  for (const entry of modelsBody?.ollama_meta || []) {
    if (entry?.id) map.set(entry.id, entry);
  }
  for (const entry of modelsBody?.openrouter_meta || []) {
    if (entry?.id) map.set(entry.id, entry);
  }
  return map;
}

export default function LlmSettingsSection({
  agentLlmEnabled,
  agentLlmAvailable,
  agentLlmProvider,
  agentLlmModel,
  agentLlmModels,
  selectedLlmModel,
  setSelectedLlmModel,
}) {
  const [modelsBody, setModelsBody] = useState(null);
  const [ops, setOps] = useState(null);
  const [pullName, setPullName] = useState('');
  const [pullLoading, setPullLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const metaById = useMemo(() => buildMetaMap(modelsBody), [modelsBody]);

  const refreshLlm = useCallback(async () => {
    setRefreshing(true);
    try {
      const [models, opsStatus] = await Promise.all([
        fetchLlmModels(useStore.getState()),
        fetchLlmOps().catch(() => null),
      ]);
      if (models?.ok) setModelsBody(models);
      if (opsStatus?.ok !== false) setOps(opsStatus);
    } catch (e) {
      toast.error(e?.message || 'Failed to refresh LLM status');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refreshLlm();
  }, [refreshLlm]);

  useEffect(() => {
    const seed = selectedLlmModel || agentLlmModel || agentLlmModels?.[0];
    if (seed && !pullName) setPullName(seed);
  }, [selectedLlmModel, agentLlmModel, agentLlmModels, pullName]);

  const narratorModel = modelsBody?.narrator_model;
  const deepModel = modelsBody?.deep_model;
  const cliAvailable = ops?.cli_available;
  const recommendedMissing = useMemo(() => {
    const tiers = ops?.tier_models || {};
    return Object.entries(tiers)
      .filter(([, info]) => info?.configured && info?.installed === false)
      .map(([name, info]) => ({ name, model: info.configured }));
  }, [ops]);

  const handlePull = async (modelOverride) => {
    const name = (modelOverride || pullName).trim();
    if (!name) {
      toast.error('Enter a model name to pull');
      return;
    }
    setPullLoading(true);
    try {
      const result = await pullLlmModel(name);
      if (result?.ok) {
        toast.success(result.message || `Pulled ${name}`);
        await refreshLlm();
      } else {
        toast.error(result?.error || 'Pull failed');
      }
    } catch (e) {
      toast.error(e?.message || 'Pull failed');
    } finally {
      setPullLoading(false);
    }
  };

  const installedModels = useMemo(() => {
    if (modelsBody?.ok) {
      const fromApi = [...(modelsBody.ollama || []), ...(modelsBody.openrouter || [])];
      if (fromApi.length > 0) return fromApi;
    }
    return agentLlmModels || [];
  }, [modelsBody, agentLlmModels]);

  const sortedModels = useMemo(() => {
    const ids = [...installedModels];
    return ids.sort((a, b) => {
      const ma = metaById.get(a);
      const mb = metaById.get(b);
      if (ma?.recommended !== mb?.recommended) return ma?.recommended ? -1 : 1;
      if (ma?.tier === 'narrator' && mb?.tier !== 'narrator') return -1;
      if (mb?.tier === 'narrator' && ma?.tier !== 'narrator') return 1;
      return a.localeCompare(b);
    });
  }, [installedModels, metaById]);

  const selectedValue = useMemo(() => {
    const preferred = selectedLlmModel || modelsBody?.preferred_model;
    if (preferred && sortedModels.includes(preferred)) return preferred;
    if (agentLlmModel && sortedModels.includes(agentLlmModel)) return agentLlmModel;
    return sortedModels[0] || undefined;
  }, [selectedLlmModel, modelsBody?.preferred_model, agentLlmModel, sortedModels]);

  return (
    <>
      <div className="mb-2 flex items-center justify-end">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1 text-xs"
          disabled={refreshing}
          onClick={refreshLlm}
        >
          {refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Refresh
        </Button>
      </div>
      <dl className="settings-defaults-list num-mono text-[0.68rem]">
        <div>
          <dt>Server enabled</dt>
          <dd className={agentLlmEnabled ? 'text-trading-up' : 'text-muted-foreground'}>
            {agentLlmEnabled ? 'Yes' : 'No (AGENT_LLM_ENABLED)'}
          </dd>
        </div>
        <div>
          <dt>Provider</dt>
          <dd>{agentLlmProvider ?? '—'}</dd>
        </div>
        <div>
          <dt>Available</dt>
          <dd className={agentLlmAvailable ? 'text-trading-up' : 'text-trading-down'}>
            {agentLlmAvailable ? 'Yes' : 'No'}
          </dd>
        </div>
        {narratorModel && (
          <div>
            <dt>Narrator tier</dt>
            <dd>{narratorModel}</dd>
          </div>
        )}
        {deepModel && deepModel !== narratorModel && (
          <div>
            <dt>Deep tier</dt>
            <dd>{deepModel}</dd>
          </div>
        )}
      </dl>

      {sortedModels.length > 0 ? (
        <div className="flex flex-col gap-2">
          <Label className="text-xs text-muted-foreground">Preferred model</Label>
          <Select
            modal={false}
            value={selectedValue}
            onValueChange={async (v) => {
              const previous = selectedValue;
              setSelectedLlmModel(v);
              setPullName(v);
              try {
                await setPreferredLlmModel(v, useStore.getState());
                toast.success(`LLM model set to ${v}`);
                await refreshLlm();
              } catch (err) {
                setSelectedLlmModel(previous || null);
                toast.error(err?.message || 'Failed to set model');
              }
            }}
          >
            <SelectTrigger className="h-8 w-full text-xs">
              <SelectValue placeholder="Select model" />
            </SelectTrigger>
            <SelectContent position="popper" className="z-[100] max-h-72">
              <SelectGroup>
                <SelectLabel className="text-[0.65rem]">Installed models</SelectLabel>
                {sortedModels.map((m) => {
                  const meta = metaById.get(m);
                  const suffix = [
                    meta?.tier && meta.tier !== 'unknown' ? meta.tier : null,
                    meta?.recommended ? 'rec' : null,
                    meta?.reasoning_capable ? 'thinking' : null,
                  ].filter(Boolean).join(' · ');
                  return (
                    <SelectItem key={m} value={m} className="text-xs num-mono">
                      {suffix ? `${m} (${suffix})` : m}
                    </SelectItem>
                  );
                })}
              </SelectGroup>
            </SelectContent>
          </Select>
          {selectedValue && metaById.get(selectedValue)?.notes && (
            <p className="text-[0.62rem] text-muted-foreground">
              {modelLabel(metaById.get(selectedValue), selectedValue)} — {metaById.get(selectedValue).notes}
            </p>
          )}
        </div>
      ) : refreshing ? (
        <p className="text-[0.68rem] text-muted-foreground">Loading models…</p>
      ) : (
        <p className="text-[0.68rem] text-muted-foreground">
          No models detected — start Ollama (<code className="text-[0.62rem]">ollama serve</code>) or set OPENROUTER_API_KEY.
        </p>
      )}

      {cliAvailable && (
        <div className="mt-3 flex flex-col gap-2 rounded-md border border-border/60 bg-muted/20 p-2.5">
          <Label className="text-xs text-muted-foreground">Pull Ollama model</Label>
          <div className="flex gap-2">
            <Input
              className="h-8 text-xs num-mono"
              placeholder="gemma3:4b"
              value={pullName}
              onChange={(e) => setPullName(e.target.value)}
              disabled={pullLoading}
            />
            <Button
              type="button"
              size="sm"
              className="h-8 shrink-0 gap-1 text-xs"
              disabled={pullLoading || !pullName.trim()}
              onClick={handlePull}
            >
              {pullLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              Pull
            </Button>
          </div>
          {recommendedMissing.length > 0 && (
            <ul className="m-0 list-none space-y-1 p-0 text-[0.62rem] text-muted-foreground">
              {recommendedMissing.map(({ name, model }) => (
                <li key={name} className="flex flex-wrap items-center gap-1">
                  <span>Tier {name} not installed:</span>
                  <button
                    type="button"
                    className={cn(
                      'num-mono text-trading-accent underline-offset-2 hover:underline',
                      pullLoading && 'pointer-events-none opacity-50',
                    )}
                    onClick={() => {
                      setPullName(model);
                      handlePull(model);
                    }}
                  >
                    {model}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
