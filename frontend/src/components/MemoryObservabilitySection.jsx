import { useEffect, useState } from 'react';
import { fetchHealthLive, fetchMassiveFeedHealth } from '../api/endpoints';
import {
  collectClientMemoryStats,
  memoryPressureLevel,
} from '../services/memoryObservability';
import { RefreshCw } from 'lucide-react';
import { cn } from '../lib/utils';
import { Button } from '@/components/ui/button';
import { refreshFrontend } from '../lib/refreshFrontend';

const LEVEL_LABEL = {
  ok: 'Normal',
  warn: 'Elevated',
  critical: 'High',
};

const LEVEL_CLASS = {
  ok: 'text-trading-up',
  warn: 'text-trading-warn',
  critical: 'text-trading-down',
};

/** Hook + badge label for Settings → System memory accordion. */
export function useMemoryObservability() {
  const [client, setClient] = useState(() => collectClientMemoryStats());
  const [health, setHealth] = useState(null);

  useEffect(() => {
    const id = setInterval(() => setClient(collectClientMemoryStats()), 3000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      // Light probes only — full /health is reserved for Settings diagnostics / bootstrap.
      Promise.all([
        fetchHealthLive().catch(() => null),
        fetchMassiveFeedHealth().catch(() => null),
      ]).then(([live, massive]) => {
        if (cancelled) return;
        setHealth({
          ...(live || {}),
          ...(massive?.massive ? { massive: massive.massive } : {}),
          ws_clients: live?.ws_clients ?? massive?.ws_clients,
        });
      });
    };
    load();
    const id = setInterval(load, 8000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const level = memoryPressureLevel(client);
  return { client, health, level };
}

export function MemoryObservabilityBadge({ level }) {
  return (
    <span className={cn('text-xs font-medium', LEVEL_CLASS[level])}>
      {LEVEL_LABEL[level]}
    </span>
  );
}

export function MemoryObservabilityBody({ client, health }) {
  const level = memoryPressureLevel(client);

  return (
    <>
      <dl className="settings-defaults-list num-mono text-xs">
        <div>
          <dt>JS heap</dt>
          <dd className={LEVEL_CLASS[level]}>
            {client.heapMb != null
              ? `${client.heapMb} MB${client.heapLimitMb != null ? ` / ${client.heapLimitMb} MB (${client.heapPct ?? '—'}%)` : ''}`
              : 'Unavailable (non-Chromium)'}
          </dd>
        </div>
        <div><dt>Buffered symbols</dt><dd>{client.symbols1m} / {client.budgets.maxSymbols} LRU</dd></div>
        <div><dt>1m bars in tab</dt><dd>{client.bars1m} (cap {client.budgets.maxBars1m}/symbol)</dd></div>
        <div><dt>HT buffers</dt><dd>{client.htKeys} keys · {client.htBars} bars</dd></div>
        <div><dt>Pinned symbol</dt><dd>{client.pinnedSymbol ?? '—'}</dd></div>
        <div><dt>Display bar budget</dt><dd>{client.budgets.defaultDisplay} default · {client.budgets.maxDisplay} max</dd></div>
        <div><dt>Archive cap (browser)</dt><dd>{client.budgets.maxArchive} bars</dd></div>
        {health?.ws_clients != null && (
          <div><dt>WS clients</dt><dd>{health.ws_clients}</dd></div>
        )}
        {health?.massive?.crypto_lag_sec != null && (
          <div><dt>Crypto lag</dt><dd>{health.massive.crypto_lag_sec}s</dd></div>
        )}
        {health?.massive?.ht_cache_entries != null && (
          <div><dt>Server HT cache</dt><dd>{health.massive.ht_cache_entries} entries</dd></div>
        )}
        {health?.massive_ht_limits && (
          <div>
            <dt>HT fetch limits</dt>
            <dd className="text-[10px] leading-relaxed">
              {Object.entries(health.massive_ht_limits)
                .map(([tf, lim]) => `${tf}:${lim.chart}/${lim.analysis}`)
                .join(' · ')}
            </dd>
          </div>
        )}
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1.5"
          onClick={() => refreshFrontend()}
        >
          <RefreshCw className="size-3.5" aria-hidden />
          Refresh UI
        </Button>
        <span className="text-[11px] text-muted-foreground">
          Reloads this window after frontend changes — same as the header ↻ button.
        </span>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground leading-snug">
        If heap stays above 70% or the tab crashes, run one profile only (Massive on :5175),
        use <strong className="font-medium text-foreground/80">Refresh UI</strong>, or
        {' '}<code className="text-xs">.\scripts\start-massive.ps1 -Restart</code>.
        See <code className="text-xs">docs/MEMORY_16GB.md</code>.
      </p>
    </>
  );
}
