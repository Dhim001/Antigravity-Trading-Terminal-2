import { useEffect, useState } from 'react';
import { BrainCircuit, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { fetchBotReasoning } from '../../api/endpoints';
import {
  formatReasoningAgent,
  formatReasoningTime,
  normalizeReasoningChains,
  reasoningVerdictVariant,
} from '@/lib/agentReasoning';
import { cn } from '@/lib/utils';

const POLL_MS = 15_000;
const FETCH_LIMIT = 5;

/**
 * Subtle collapsible "Last decision" strip — shows the newest persisted agent
 * reasoning chain for the selected bot (agent, verdict, notes, observations).
 */
export default function BotReasoningPanel({ botId }) {
  const [state, setState] = useState({ botId: null, chains: [] });
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!botId) return undefined;
    let cancelled = false;
    const load = () => {
      fetchBotReasoning(botId, { limit: FETCH_LIMIT })
        .then((rows) => {
          if (!cancelled) setState({ botId, chains: normalizeReasoningChains(rows) });
        })
        .catch(() => {
          // Keep the last good chains; reasoning is advisory only.
        });
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [botId]);

  // Key fetched chains to the bot they belong to so switching bots never
  // flashes the previous bot's decision.
  const chains = state.botId === botId ? state.chains : [];

  if (!botId || chains.length === 0) return null;

  const latest = chains[0];
  const when = formatReasoningTime(latest.ts) || formatReasoningTime(latest.created_at);

  return (
    <section className="algo-tab__panel algo-tab__panel--reasoning">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <header
            className="algo-tab__panel-header cursor-pointer select-none"
            aria-label="Toggle last agent decision"
          >
            <div className="algo-tab__panel-heading">
              <div className="algo-tab__panel-title">
                <BrainCircuit size={13} className="text-muted-foreground" aria-hidden />
                Last decision
                {latest.verdict && (
                  <Badge variant={reasoningVerdictVariant(latest.verdict)}>{latest.verdict}</Badge>
                )}
              </div>
              <span className="algo-tab__panel-subtitle">
                {formatReasoningAgent(latest.agent)}{when ? ` · ${when}` : ''}
              </span>
            </div>
            <ChevronRight
              className={cn('h-3.5 w-3.5 text-muted-foreground transition-transform', open && 'rotate-90')}
              aria-hidden
            />
          </header>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 pb-2 pt-1 text-xs leading-relaxed">
            {latest.notes && (
              <p className="mb-1 text-foreground/90">{latest.notes}</p>
            )}
            {latest.vetoes.length > 0 && (
              <div className="mb-1 flex flex-wrap gap-1">
                {latest.vetoes.map((veto, i) => (
                  <Badge key={`veto-${i}`} variant="outline" className="text-[10px]">
                    {veto}
                  </Badge>
                ))}
              </div>
            )}
            {latest.observations.length > 0 && (
              <ul className="space-y-0.5 text-muted-foreground">
                {latest.observations.map((obs, i) => (
                  <li key={`obs-${i}`} className="truncate" title={obs.detail}>
                    <span className="text-foreground/70">{obs.source}</span>
                    {obs.detail ? ` — ${obs.detail}` : ''}
                  </li>
                ))}
              </ul>
            )}
            {latest.size_multiplier != null && latest.size_multiplier !== 1 && (
              <div className="mt-1 text-muted-foreground">
                Size multiplier: {latest.size_multiplier}
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
