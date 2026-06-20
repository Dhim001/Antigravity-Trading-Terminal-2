import { Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';
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
  if (!narrative) return null;

  return (
    <div className={cn('llm-narrative', compact && 'llm-narrative--compact', className)}>
      {showIcon && (
        <Sparkles className="llm-narrative__icon" aria-hidden />
      )}
      <p className="llm-narrative__text">{narrative}</p>
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
