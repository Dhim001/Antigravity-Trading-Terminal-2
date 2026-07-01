import { cn } from '@/lib/utils';

const DOMAIN_META = {
  trend: { label: 'Trend', color: 'text-primary' },
  indicator: { label: 'Indicator', color: 'text-trading-accent' },
  momentum: { label: 'Indicator', color: 'text-trading-accent' },
  risk: { label: 'Risk', color: 'text-trading-warn' },
  sentiment: { label: 'Sentiment', color: 'text-emerald-500' },
};

function resolveDomainData(subReports, domain) {
  if (domain === 'indicator') {
    return subReports.indicator ?? subReports.momentum;
  }
  return subReports[domain];
}

function DomainCard({ domain, data }) {
  if (!data) return null;
  const meta = DOMAIN_META[domain] || { label: domain, color: 'text-muted-foreground' };
  const score = data.score;
  const showScore = domain !== 'risk' && score != null;

  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-2 text-xs">
      <div className="mb-1 flex items-center justify-between">
        <span className={cn('font-semibold uppercase tracking-wide', meta.color)}>{meta.label}</span>
        {showScore && (
          <span className="num-mono text-[0.65rem] text-muted-foreground">
            {score > 0 ? '+' : ''}{score}
          </span>
        )}
        {domain === 'risk' && data.atr_regime && (
          <span className="text-[0.62rem] capitalize text-muted-foreground">{data.atr_regime}</span>
        )}
        {domain === 'sentiment' && data.aggregate_score != null && (
          <span className="text-[0.62rem] text-muted-foreground">
            {data.aggregate_score >= 0 ? '+' : ''}{Number(data.aggregate_score).toFixed(2)}
          </span>
        )}
      </div>
      {domain === 'risk' && data.suggested_size_factor != null && (
        <p className="mb-1 text-[0.62rem] text-muted-foreground">
          Size factor: {Math.round(data.suggested_size_factor * 100)}%
        </p>
      )}
      {data.reasons?.length > 0 ? (
        <ul className="space-y-0.5 text-[0.65rem] text-foreground/85">
          {data.reasons.map((r, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="opacity-40">•</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[0.62rem] text-muted-foreground">No detail</p>
      )}
    </div>
  );
}

/**
 * Stacked trend / indicator / risk cards for insight v2.
 */
export default function SubReportCards({ subReports, compact = false }) {
  if (!subReports) return null;
  const domains = ['trend', 'indicator', 'risk', 'sentiment'];
  const visible = domains.filter((d) => resolveDomainData(subReports, d));
  if (!visible.length) return null;
  return (
    <div className={cn('grid gap-2', compact ? 'grid-cols-1' : 'sm:grid-cols-2 lg:grid-cols-4')}>
      {visible.map((domain) => (
        <DomainCard key={domain} domain={domain} data={resolveDomainData(subReports, domain)} />
      ))}
    </div>
  );
}
