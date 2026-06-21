import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { previewInsightOrder } from '../api/endpoints';

export default function InsightOrderPreviewDialog({
  open,
  onOpenChange,
  draft,
  onConfirm,
}) {
  const [serverPreview, setServerPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);

  useEffect(() => {
    if (!open || !draft) {
      setServerPreview(null);
      setPreviewError(null);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    previewInsightOrder(draft)
      .then((body) => {
        if (!cancelled) setServerPreview(body);
      })
      .catch((err) => {
        if (!cancelled) setPreviewError(err?.message || 'Server preview unavailable');
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, draft]);

  if (!draft) return null;

  const notional = serverPreview?.notional ?? draft.notional;
  const quantity = serverPreview?.quantity ?? draft.quantity;
  const blocked = serverPreview?.allowed === false;
  const warnings = serverPreview?.warnings || [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Preview order from analyst</DialogTitle>
          <DialogDescription>
            Review before prefilling the order ticket. Nothing is submitted until you confirm in Order Entry.
          </DialogDescription>
        </DialogHeader>

        <dl className="space-y-2 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Symbol</dt>
            <dd className="font-semibold">{draft.symbol}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Side</dt>
            <dd className={cn('font-bold', draft.side === 'BUY' ? 'text-trading-up' : 'text-trading-down')}>
              {draft.side}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Quantity</dt>
            <dd className="num-mono">{quantity}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Est. notional</dt>
            <dd className="num-mono">~${Number(notional || 0).toLocaleString()}</dd>
          </div>
          {serverPreview?.market_price != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Market price</dt>
              <dd className="num-mono">{serverPreview.market_price}</dd>
            </div>
          )}
          {draft.sizeFactor != null && draft.sizeFactor !== 1 && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Size factor</dt>
              <dd className="num-mono">{Math.round(draft.sizeFactor * 100)}% ({draft.atrRegime || 'risk'})</dd>
            </div>
          )}
          {(serverPreview?.stop_loss_price ?? draft.stop_loss_price) != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Stop loss</dt>
              <dd className="num-mono">{serverPreview?.stop_loss_price ?? draft.stop_loss_price}</dd>
            </div>
          )}
          {(serverPreview?.take_profit_price ?? draft.take_profit_price) != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Take profit</dt>
              <dd className="num-mono">{serverPreview?.take_profit_price ?? draft.take_profit_price}</dd>
            </div>
          )}
          {serverPreview?.risk_reward_ratio != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Risk / reward</dt>
              <dd className="num-mono">{serverPreview.risk_reward_ratio}:1</dd>
            </div>
          )}
        </dl>

        {previewLoading && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            Validating with server…
          </p>
        )}
        {previewError && (
          <p className="text-xs text-trading-warn">{previewError}</p>
        )}
        {blocked && serverPreview?.block_reason && (
          <p className="text-xs text-trading-down">{serverPreview.block_reason}</p>
        )}
        {warnings.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-4 text-xs text-trading-warn">
            {warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={onConfirm} disabled={blocked}>
            Confirm → Order ticket
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
