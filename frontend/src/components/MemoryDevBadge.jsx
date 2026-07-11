import { useEffect, useState } from 'react';
import { fetchHealthLive, fetchMassiveFeedHealth } from '../api/endpoints';
import {
  collectClientMemoryStats,
  memoryPressureLevel,
} from '../services/memoryObservability';

const PRESSURE_CLASS = {
  ok: 'text-green-400',
  warn: 'text-yellow-400',
  critical: 'text-red-400',
};

/** Dev-only footer: JS heap + candle buffers + backend WS/lag snapshot. */
export default function MemoryDevBadge() {
  const [client, setClient] = useState(null);
  const [server, setServer] = useState(null);

  useEffect(() => {
    if (!import.meta.env.DEV) return undefined;

    const tickClient = () => setClient(collectClientMemoryStats());
    tickClient();
    const clientId = setInterval(tickClient, 2000);

    const tickServer = () => {
      // Prefer light probes — never hit full /health from the 10s badge poller.
      Promise.all([
        fetchHealthLive().catch(() => null),
        fetchMassiveFeedHealth().catch(() => null),
      ]).then(([live, massive]) => {
        const h = massive?.massive ? massive : live;
        setServer({
          wsClients: h?.ws_clients ?? live?.ws_clients ?? null,
          cryptoLag: h?.massive?.crypto_lag_sec ?? h?.feed_lag_sec ?? null,
          htCache: h?.massive?.ht_cache_entries ?? null,
        });
      });
    };
    tickServer();
    const serverId = setInterval(tickServer, 10000);

    return () => {
      clearInterval(clientId);
      clearInterval(serverId);
    };
  }, []);

  if (!import.meta.env.DEV || !client) return null;

  const level = memoryPressureLevel(client);
  const colorClass = PRESSURE_CLASS[level] ?? PRESSURE_CLASS.ok;

  const heapLine = client.heapMb != null
    ? client.heapLimitMb != null
      ? `${client.heapMb}/${client.heapLimitMb}MB (${client.heapPct ?? '—'}%)`
      : `${client.heapMb}MB`
    : 'heap n/a';

  const bufLine = `buf ${client.symbols1m}/${client.budgets.maxSymbols}sym · ${client.bars1m}b`
    + (client.htKeys > 0 ? ` · ht ${client.htKeys}/${client.htBars}b` : '');

  const serverBits = [];
  if (server?.wsClients != null) serverBits.push(`ws ${server.wsClients}`);
  if (server?.cryptoLag != null) serverBits.push(`lag ${server.cryptoLag}s`);
  if (server?.htCache != null) serverBits.push(`ht-cache ${server.htCache}`);

  return (
    <div
      className={`fixed right-1 z-[9999] pointer-events-none select-none rounded px-2 py-1 text-[10px] font-mono opacity-75 bg-black/80 ${colorClass}`}
      style={{ bottom: 'calc(var(--dock-h, 320px) + 0.35rem)' }}
      aria-hidden
      title={client.pinnedSymbol ? `Pinned: ${client.pinnedSymbol}` : undefined}
    >
      <div>{heapLine}{serverBits.length ? ` · ${serverBits.join(' · ')}` : ''}</div>
      <div className="opacity-90">{bufLine}{client.pinnedSymbol ? ` · pin ${client.pinnedSymbol}` : ''}</div>
    </div>
  );
}
