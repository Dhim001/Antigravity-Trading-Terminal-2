/**
 * Per-trade MAE/MFE excursion bar (TradingView-style).
 */
import { cn } from '@/lib/utils';

export default function BacktestExcursionBar({ mfePct, maePct, pnl, className }) {
  const mfe = Math.max(0, Number(mfePct) || 0);
  const mae = Math.max(0, Number(maePct) || 0);
  if (mfe <= 0 && mae <= 0) {
    return <span className="text-muted-foreground text-[0.55rem]">—</span>;
  }

  const maxSide = Math.max(mfe, mae, 0.01);
  const mfeW = Math.round((mfe / maxSide) * 50);
  const maeW = Math.round((mae / maxSide) * 50);
  const win = (pnl ?? 0) >= 0;

  return (
    <div
      className={cn('backtest-excursion', className)}
      title={`MFE ${mfe.toFixed(2)}% · MAE ${mae.toFixed(2)}%`}
      aria-label={`Favorable excursion ${mfe.toFixed(2)} percent, adverse ${mae.toFixed(2)} percent`}
    >
      <span
        className="backtest-excursion__fav"
        style={{ width: `${mfeW}%` }}
      />
      <span className="backtest-excursion__mid" />
      <span
        className={cn('backtest-excursion__adv', win && 'backtest-excursion__adv--muted')}
        style={{ width: `${maeW}%` }}
      />
    </div>
  );
}
