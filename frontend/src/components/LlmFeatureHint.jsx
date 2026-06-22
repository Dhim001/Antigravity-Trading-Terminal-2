import { Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

/**
 * Inline hint when an LLM feature is disabled or unavailable (env / provider).
 */
export default function LlmFeatureHint({
  feature = 'LLM feature',
  enabled = true,
  available = true,
  envKeys = [],
  className,
  compact = false,
}) {
  if (enabled && available) return null;

  let message = '';
  if (!enabled) {
    message = `${feature} is disabled on the server. Set ${envKeys.join(' and ') || 'the required env vars'} in backend .env and restart.`;
  } else if (!available) {
    message = `${feature} requires a running LLM provider (Ollama or OpenRouter with API key).`;
  }

  if (!message) return null;

  return (
    <Alert
      className={cn(
        'border-border/60 bg-muted/30',
        compact ? 'py-1.5' : 'py-2',
        className,
      )}
      role="note"
    >
      <Info aria-hidden />
      <AlertDescription className={cn('text-xs', compact && 'leading-snug')}>
        {message}{' '}
        <Button
          type="button"
          variant="link"
          size="sm"
          className="inline h-auto p-0 text-xs"
          onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
        >
          Open Settings
        </Button>
      </AlertDescription>
    </Alert>
  );
}
