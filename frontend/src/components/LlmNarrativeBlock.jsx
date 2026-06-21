import { Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';
import { stripLlmReasoning } from '@/lib/llmText';
import LlmAttribution from './LlmAttribution';

/** Styled narrative card with optional LLM attribution footer. */
export default function LlmNarrativeBlock({
  narrative,
  provider,
  model,
  className,
  compact = false,
  showIcon = true,
}) {
  const displayText = stripLlmReasoning(narrative);
  if (!displayText) return null;

  return (
    <div className={cn('llm-narrative', compact && 'llm-narrative--compact', className)}>
      {showIcon && (
        <Sparkles className="llm-narrative__icon" aria-hidden />
      )}
      <p className="llm-narrative__text">{displayText}</p>
      {(provider || model) && (
        <LlmAttribution
          provider={provider}
          model={model}
          variant="chip"
          className="llm-narrative__attribution"
        />
      )}
    </div>
  );
}
