import { cn } from '@/lib/utils';
import { Card, CardContent } from '@/components/ui/card';

const TONE_CLASS = {
  up: 'text-trading-up',
  down: 'text-trading-down',
  accent: 'text-primary',
  neutral: 'text-foreground',
};

/** Compact stat tile used in history, equity curve, and dock panels */
export function StatCard({ label, value, sub, icon: Icon, tone = 'neutral' }) {
  const toneClass = TONE_CLASS[tone] ?? TONE_CLASS.neutral;

  return (
    <Card size="sm" className="min-w-[100px] flex-1 rounded-md py-2 shadow-none">
      <CardContent className="flex flex-col gap-1 px-3 py-0">
        <div className="flex items-center justify-between">
          <span className="text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
            {label}
          </span>
          {Icon && <Icon size={11} className={cn('opacity-80', toneClass)} />}
        </div>
        <span className={cn('num-mono text-lg font-extrabold leading-none', toneClass)}>{value}</span>
        {sub && <span className="text-[0.62rem] text-muted-foreground">{sub}</span>}
      </CardContent>
    </Card>
  );
}
