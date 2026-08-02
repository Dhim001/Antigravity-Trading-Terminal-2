import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { formatElapsed } from '@/components/ml-lab/MlLabConstants';
import { formatMlJobBudgetLabel } from '@/lib/mlJobTimeouts';

export function formatPollLogTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return '—';
  }
}

export function formatPollLogLine(entry) {
  const bits = [formatPollLogTime(entry.t)];
  if (entry.status) bits.push(`status=${entry.status}`);
  if (entry.pct != null) bits.push(`pct=${Math.round(entry.pct)}`);
  if (entry.phase) bits.push(`phase=${entry.phase}`);
  if (entry.detail) bits.push(`detail=${entry.detail}`);
  if (entry.note) bits.push(entry.note);
  return bits.join(' ');
}

export const POLL_LOG_PREF_KEY = 'ml-lab-show-poll-log';

export function JobPollLog({ entries, enabled, onEnabledChange, onClear }) {
  const logRef = useRef(null);
  const lines = Array.isArray(entries) ? entries : [];

  useEffect(() => {
    if (!enabled || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [enabled, lines.length, lines[lines.length - 1]?.t]);

  return (
    <div className="ml-training__poll-log">
      <div className="ml-training__poll-log-head">
        <label className="ml-training__poll-log-toggle">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onEnabledChange(Boolean(e.target.checked))}
          />
          <span>Show poll log</span>
        </label>
        {enabled && (
          <div className="ml-training__poll-log-actions">
            <span className="ml-training__header-meta num-mono">{lines.length} lines</span>
            {lines.length > 0 && typeof onClear === 'function' && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[0.6rem]"
                onClick={onClear}
              >
                Clear
              </Button>
            )}
          </div>
        )}
      </div>
      {enabled && (
        <pre
          ref={logRef}
          className="ml-training__poll-log-body num-mono"
          aria-label="Training job poll log"
        >
          {lines.length === 0
            ? '# Poll snapshots appear while Train / Validate runs…'
            : lines.map(formatPollLogLine).join('\n')}
        </pre>
      )}
    </div>
  );
}

export function JobProgressBar({ job, serverProgress, onCancel, cancelling }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!job?.active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [job?.active, job?.startedAt]);

  if (!job?.active) return null;

  const elapsed = Math.max(0, now - (job.startedAt || now));
  const timeoutMs = Math.max(job.timeoutMs || 60_000, 15_000);
  const hasServerPct = serverProgress?.pct != null && Number(serverProgress.pct) > 0;
  // Asymptotic estimate — never claims 100% until the request finishes.
  const ratio = Math.min(0.94, 1 - Math.exp(-elapsed / (timeoutMs * 0.45)));
  const estPct = Math.max(2, Math.round(ratio * 100));
  const pct = hasServerPct
    ? Math.max(1, Math.min(99, Math.round(Number(serverProgress.pct))))
    : estPct;
  const phases = job.phases || [];
  const phaseIdx = phases.findIndex((p) => pct < p.until);
  const phase = phases[phaseIdx >= 0 ? phaseIdx : Math.max(phases.length - 1, 0)];
  const phaseLabel = hasServerPct
    ? [serverProgress.phase, serverProgress.detail].filter(Boolean).join(' · ')
      || phase?.label
    : phase?.label;

  return (
    <div className="ml-training__progress" role="status" aria-live="polite">
      <div className="ml-training__progress-head">
        <span className="ml-training__progress-label">
          <Loader2 size={12} className="animate-spin" aria-hidden />
          {job.label}
        </span>
        <span className="ml-training__progress-meta num-mono">
          {pct}% · {formatElapsed(elapsed)}
          {timeoutMs >= 60_000 ? ` / ~${formatMlJobBudgetLabel(timeoutMs)}` : ''}
          {hasServerPct ? ' · live' : ''}
        </span>
      </div>
      <div
        className="ml-training__progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={job.label}
      >
        <div className="ml-training__progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="ml-training__progress-foot">
        {phaseLabel && (
          <p className="ml-training__progress-phase">{phaseLabel}</p>
        )}
        {typeof onCancel === 'function' && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 text-[0.65rem] shrink-0"
            disabled={cancelling}
            onClick={onCancel}
          >
            {cancelling ? <Loader2 size={12} className="animate-spin" /> : null}
            Cancel
          </Button>
        )}
      </div>
    </div>
  );
}
