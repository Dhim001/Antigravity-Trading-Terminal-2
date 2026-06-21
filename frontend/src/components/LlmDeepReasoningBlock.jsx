import { AlertTriangle, Layers } from 'lucide-react';
import { cn } from '@/lib/utils';
import { stripLlmReasoning } from '@/lib/llmText';
import LlmAttribution from './LlmAttribution';

/** Deep reasoning enrichment panel (metadata only — does not change signals). */
export default function LlmDeepReasoningBlock({
  summary,
  riskNotes,
  provider,
  model,
  className,
}) {
  const displaySummary = stripLlmReasoning(summary);
  const displayRisk = stripLlmReasoning(riskNotes);
  if (!displaySummary && !displayRisk) return null;

  return (
    <div className={cn('llm-deep-reasoning', className)}>
      <div className="llm-deep-reasoning__header">
        <Layers className="llm-deep-reasoning__header-icon" aria-hidden />
        <div>
          <p className="llm-deep-reasoning__title">Deep reasoning</p>
          <p className="llm-deep-reasoning__subtitle">Metadata only · signal unchanged</p>
        </div>
      </div>

      {displaySummary && (
        <p className="llm-deep-reasoning__summary">{displaySummary}</p>
      )}

      {displayRisk && (
        <div className="llm-deep-reasoning__risk">
          <AlertTriangle className="llm-deep-reasoning__risk-icon" aria-hidden />
          <p className="llm-deep-reasoning__risk-text">{displayRisk}</p>
        </div>
      )}

      <LlmAttribution
        provider={provider}
        model={model}
        variant="chip"
        className="llm-deep-reasoning__attribution"
      />
    </div>
  );
}
