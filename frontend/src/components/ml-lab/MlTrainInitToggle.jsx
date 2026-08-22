import { parseTrainInit, persistTrainInit } from '@/components/ml-lab/MlLabConstants';
import { cn } from '@/lib/utils';

/**
 * Lab control for Trigger retrain / Apply & Retrain / batch train:
 * fine-tune the live champion (and optional donor) vs a full from-scratch fit.
 */
export function MlTrainInitToggle({ value, onChange, disabled = false }) {
  const init = parseTrainInit(value);
  const select = (next) => {
    const resolved = persistTrainInit(next);
    onChange?.(resolved);
  };
  return (
    <div className="ml-training__init">
      <div className="ml-training__init-row">
        <span className="ml-training__init-label">Start from</span>
        <div
          className="ml-training__init-group"
          role="radiogroup"
          aria-label="Train initialization"
        >
          <button
            type="button"
            role="radio"
            aria-checked={init === 'warm'}
            disabled={disabled}
            className={cn(init === 'warm' && 'is-active')}
            onClick={() => select('warm')}
            title="Resume the live champion when one exists. LSTM uses a short low-LR pass; GBM adds extra trees."
          >
            Fine-tune existing
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={init === 'scratch'}
            disabled={disabled}
            className={cn(init === 'scratch' && 'is-active')}
            onClick={() => select('scratch')}
            title="Random weights, full Advanced budget. Ignores the live champion and any donor."
          >
            From scratch
          </button>
        </div>
      </div>
      <p className="ml-training__init-hint">
        {init === 'scratch'
          ? 'Random init and the full Advanced budget. Ignores the live champion and any donor.'
          : 'Resume the live champion when it exists (GBM extra trees, LSTM short epochs). No champion → still starts from scratch. Walk-forward folds always train from scratch.'}
      </p>
    </div>
  );
}
