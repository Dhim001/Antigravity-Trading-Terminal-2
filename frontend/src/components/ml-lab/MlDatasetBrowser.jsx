import { Loader2, Trash2 } from 'lucide-react';
import FeatureImportanceChart from '@/components/FeatureImportanceChart';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { normalizeTopFeatures } from '@/components/ml-lab/MlLabConstants';

export function DatasetBrowser({
  dataset,
  versions,
  activatingVersionId,
  deletingVersionId,
  onActivateVersion,
  onDeleteVersion,
  onCopyPin,
}) {
  if (!dataset && !(versions && versions.length)) return null;
  const labels = dataset?.label_distribution;
  const features = Array.isArray(dataset?.feature_names) ? dataset.feature_names : [];
  const topFeatures = normalizeTopFeatures(dataset?.top_features).slice(0, 10);
  const versionBusy = Boolean(activatingVersionId || deletingVersionId);
  return (
    <section className="ml-training__dataset">
      <div className="ml-training__card-head">
        <h4 className="ml-training__section-title">Dataset & versions</h4>
        <span className="ml-training__header-meta">
          Activate sets the live root · Delete removes a non-active snapshot · pin via Model version pin
        </span>
      </div>
      <div className="ml-training__dataset-grid">
        <div className="ml-training__dataset-main">
          {dataset && (
            <div className="ml-training__dataset-stats">
              <div>
                <span className="text-muted-foreground">Seq. samples</span>
                <p className="num-mono font-medium">
                  {dataset.sample_count ?? dataset.train_samples ?? '—'}
                  {dataset.val_samples != null ? ` / val ${dataset.val_samples}` : ''}
                </p>
                {(dataset.candle_bars != null || dataset.bar_target != null) && (
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    {dataset.candle_bars != null ? `${dataset.candle_bars} bars` : null}
                    {dataset.bar_target != null ? ` · target ${dataset.bar_target}` : null}
                  </p>
                )}
              </div>
              <div>
                <span className="text-muted-foreground">Schema</span>
                <p className="num-mono font-medium">
                  {dataset.feature_schema_version != null
                    ? `v${dataset.feature_schema_version}`
                    : '—'}
                  {dataset.lookback != null ? ` · lb ${dataset.lookback}` : ''}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground">Type</span>
                <p className="num-mono font-medium">{dataset.model_type || '—'}</p>
              </div>
            </div>
          )}
          {labels && typeof labels === 'object' && (
            <div>
              <p className="ml-training__subsection-label">Label distribution</p>
              <div className="ml-training__label-dist">
                {Object.entries(labels).map(([k, v]) => (
                  <span key={k}>
                    <span className="ml-training__metric-key">{k}</span>
                    <strong className="num-mono">{v}</strong>
                  </span>
                ))}
              </div>
            </div>
          )}
          {features.length > 0 && (
            <p className="ml-training__feature-line">
              Features ({features.length}):{' '}
              <span className="num-mono">
                {features.slice(0, 12).join(', ')}
                {features.length > 12 ? ` +${features.length - 12}` : ''}
              </span>
            </p>
          )}
          {topFeatures.length > 0 && (
            <div className="ml-training__feature-importance">
              <p className="ml-training__subsection-label">Feature importance</p>
              <FeatureImportanceChart features={topFeatures} maxBars={10} compact />
            </div>
          )}
        </div>
        {Array.isArray(versions) && versions.length > 0 && (
          <div className="ml-training__dataset-versions">
            <p className="ml-training__subsection-label">Version history</p>
            <ul className="ml-training__version-list">
              {versions.slice(0, 12).map((v) => {
                const id = v.version_id || v.trained_at;
                const activating = activatingVersionId && (
                  activatingVersionId === v.version_id
                  || activatingVersionId === v.trained_at
                );
                const deleting = deletingVersionId && (
                  deletingVersionId === v.version_id
                  || deletingVersionId === v.trained_at
                );
                const pinValue = v.trained_at || v.version_id || '';
                return (
                  <li
                    key={id}
                    className={cn(
                      'ml-training__version-row',
                      v.is_current && 'ml-training__version-row--current',
                    )}
                  >
                    <div className="ml-training__version-meta num-mono">
                      <span className="ml-training__version-id">{v.version_id || '—'}</span>
                      <span className="text-muted-foreground">
                        {v.trained_at ? new Date(v.trained_at).toLocaleString() : '—'}
                        {v.is_current ? ' · current' : ''}
                        {v.sample_count != null ? ` · n=${v.sample_count}` : ''}
                      </span>
                    </div>
                    <div className="ml-training__version-actions">
                      {pinValue && onCopyPin && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem]"
                          title="Copy pin value for bot config model_version"
                          onClick={() => onCopyPin(pinValue)}
                        >
                          Copy pin
                        </Button>
                      )}
                      {v.is_current ? (
                        <span className="ml-training__version-badge">Active</span>
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem] gap-1"
                          disabled={versionBusy || !onActivateVersion}
                          onClick={() => onActivateVersion?.(v)}
                        >
                          {activating ? <Loader2 size={10} className="animate-spin" /> : null}
                          Use this
                        </Button>
                      )}
                      {!v.is_current && onDeleteVersion && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-1.5 text-[0.6rem] gap-1 text-destructive hover:text-destructive"
                          disabled={versionBusy}
                          title="Delete this snapshot from disk (cannot undo)"
                          onClick={() => onDeleteVersion(v)}
                        >
                          {deleting ? <Loader2 size={10} className="animate-spin" /> : <Trash2 size={10} />}
                          Delete
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
