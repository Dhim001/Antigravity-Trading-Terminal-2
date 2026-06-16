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

export default function InsightOrderPreviewDialog({
  open,
  onOpenChange,
  draft,
  onConfirm,
}) {
  if (!draft) return null;

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
            <dd className="num-mono">{draft.quantity}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-muted-foreground">Est. notional</dt>
            <dd className="num-mono">~${draft.notional?.toLocaleString()}</dd>
          </div>
          {draft.sizeFactor != null && draft.sizeFactor !== 1 && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Size factor</dt>
              <dd className="num-mono">{Math.round(draft.sizeFactor * 100)}% ({draft.atrRegime || 'risk'})</dd>
            </div>
          )}
          {draft.stop_loss_price != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Stop loss</dt>
              <dd className="num-mono">{draft.stop_loss_price}</dd>
            </div>
          )}
          {draft.take_profit_price != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Take profit</dt>
              <dd className="num-mono">{draft.take_profit_price}</dd>
            </div>
          )}
        </dl>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={onConfirm}>Confirm → Order ticket</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
