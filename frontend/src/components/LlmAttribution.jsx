import { Cloud, Cpu, Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';

const PROVIDER_META = {
  ollama: { label: 'Ollama', Icon: Cpu, tone: 'ollama' },
  openrouter: { label: 'OpenRouter', Icon: Cloud, tone: 'cloud' },
};

function resolveProvider(provider) {
  if (!provider || provider === 'off') return null;
  const key = String(provider).toLowerCase();
  return PROVIDER_META[key] ?? { label: provider, Icon: Sparkles, tone: 'default' };
}

/**
 * Provider + model chip or subtle footer for LLM-generated text.
 * @param {'chip'|'subtle'} variant
 */
export default function LlmAttribution({
  provider,
  model,
  className,
  variant = 'chip',
  prefix,
  title,
}) {
  const meta = resolveProvider(provider);
  if (!meta && !model) return null;

  const Icon = meta?.Icon ?? Sparkles;
  const label = meta?.label ?? null;
  const tone = meta?.tone ?? 'default';

  if (variant === 'subtle') {
    const parts = [];
    if (label) parts.push(label);
    if (model) parts.push(model);
    return (
      <p
        className={cn('llm-attribution llm-attribution--subtle num-mono', className)}
        title={title ?? (parts.length ? parts.join(' · ') : undefined)}
      >
        {prefix ? `${prefix} ` : ''}
        {parts.join(' · ')}
      </p>
    );
  }

  return (
    <span
      className={cn(
        'llm-attribution llm-attribution--chip',
        tone !== 'default' && `llm-attribution--${tone}`,
        className,
      )}
      title={title ?? [label, model].filter(Boolean).join(' · ')}
    >
      <Icon className="llm-attribution__icon" aria-hidden />
      <span className="llm-attribution__body">
        {prefix && <span className="llm-attribution__prefix">{prefix}</span>}
        {label && <span className="llm-attribution__provider">{label}</span>}
        {label && model && <span className="llm-attribution__sep" aria-hidden>·</span>}
        {model && <span className="llm-attribution__model num-mono">{model}</span>}
      </span>
    </span>
  );
}
