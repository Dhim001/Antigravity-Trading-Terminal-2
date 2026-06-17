/**
 * OrderEntryWidget.jsx — order ticket with unified terminal tokens
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
import { apiAction } from '../api/client';
import { Action } from '../api/protocol';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldGroup, FieldLabel, FieldDescription } from '@/components/ui/field';
import { WidgetShell } from './WidgetShell';
import { cn } from '@/lib/utils';
import { PlusCircle, ShieldAlert, Target, TrendingDown, TrendingUp, ChevronDown } from 'lucide-react';

const fmtDec = (n, dec) => n?.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });

export default function OrderEntryWidget() {
  const activeSymbol = useStore(state => state.activeSymbol);
  const ticker       = useStore(state => state.tickerData[activeSymbol]);
  const balances     = useStore(state => state.balances);
  const positions    = useStore(state => state.positions);
  const orderResult  = useStore(state => state.orderResult);
  const orderPrefill = useStore(state => state.orderPrefill);
  const clearOrderPrefill = useStore(state => state.clearOrderPrefill);

  const [side,      setSide]      = useState('BUY');
  const [orderType, setOrderType] = useState('LIMIT');
  const [price,     setPrice]     = useState('');
  const [quantity,  setQuantity]  = useState('');
  const [slPrice,   setSlPrice]   = useState('');
  const [tpPrice,   setTpPrice]   = useState('');
  const [slMode,    setSlMode]    = useState('%');
  const [tpMode,    setTpMode]    = useState('%');
  const [errorMsg,  setErrorMsg]  = useState(null);
  const [showSLTP,  setShowSLTP]  = useState(false);
  const [preview,   setPreview]   = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [lastFillExplain, setLastFillExplain] = useState(null);
  const buyBtnRef  = useRef(null);
  const sellBtnRef = useRef(null);
  const formRef    = useRef(null);
  const previewRef = useRef(null);

  useEffect(() => {
    if (!orderPrefill || orderPrefill.symbol !== activeSymbol) return;
    if (orderPrefill.side) setSide(orderPrefill.side);
    if (orderPrefill.orderType) setOrderType(orderPrefill.orderType);
    if (orderPrefill.quantity) setQuantity(String(orderPrefill.quantity));
    if (orderPrefill.stop_loss_price != null) {
      setShowSLTP(true);
      setSlMode('$');
      setSlPrice(String(orderPrefill.stop_loss_price));
    }
    if (orderPrefill.take_profit_price != null) {
      setShowSLTP(true);
      setTpMode('$');
      setTpPrice(String(orderPrefill.take_profit_price));
    }
    clearOrderPrefill();
  }, [orderPrefill, activeSymbol, clearOrderPrefill]);

  useEffect(() => {
    const handler = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === 'b' || e.key === 'B') { setSide('BUY');  buyBtnRef.current?.focus(); }
      if (e.key === 's' || e.key === 'S') { setSide('SELL'); sellBtnRef.current?.focus(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  useEffect(() => {
    if (ticker) setPrice(ticker.price.toString());
    else setPrice('');
    setSlPrice(''); setTpPrice(''); setQuantity('');
    setErrorMsg(null);
  }, [activeSymbol, ticker === undefined]);

  useEffect(() => {
    if (!orderResult) return;
    if (orderResult.status === 'success') {
      toast.success(orderResult.message);
      if (previewRef.current?.allowed) {
        const p = previewRef.current;
        setLastFillExplain({
          side: p.side,
          quantity: p.quantity,
          notional: p.notional,
          quote: p.quote,
          stop_loss_price: p.stop_loss_price,
          take_profit_price: p.take_profit_price,
          risk_reward_ratio: p.risk_reward_ratio,
        });
      }
    } else {
      toast.error(orderResult.message);
    }
  }, [orderResult]);

  const isCrypto = activeSymbol.includes('USDT');
  const base  = isCrypto ? activeSymbol.replace('USDT', '') : activeSymbol;
  const quote = isCrypto ? 'USDT' : 'USD';

  const quoteBalance   = balances[quote]?.balance  ?? 0;
  const quoteLocked    = balances[quote]?.locked    ?? 0;
  const quoteAvailable = quoteBalance - quoteLocked;
  const basePosition   = positions[activeSymbol]?.size ?? 0;

  const orderPrice = orderType === 'LIMIT' ? parseFloat(price) || 0 : (ticker?.price ?? 0);
  const qty        = parseFloat(quantity) || 0;
  const estCost    = orderPrice * qty;

  const computeSlAbs = () => {
    if (!slPrice) return null;
    if (slMode === '$') return parseFloat(slPrice);
    const pct = parseFloat(slPrice);
    if (!pct) return null;
    return side === 'BUY' ? orderPrice * (1 - pct / 100) : orderPrice * (1 + pct / 100);
  };
  const computeTpAbs = () => {
    if (!tpPrice) return null;
    if (tpMode === '$') return parseFloat(tpPrice);
    const pct = parseFloat(tpPrice);
    if (!pct) return null;
    return side === 'BUY' ? orderPrice * (1 + pct / 100) : orderPrice * (1 - pct / 100);
  };

  const slAbs = computeSlAbs();
  const tpAbs = computeTpAbs();

  const rrRatio = useMemo(() => {
    if (!slAbs || !tpAbs || !orderPrice) return null;
    const risk   = Math.abs(orderPrice - slAbs);
    const reward = Math.abs(tpAbs - orderPrice);
    if (risk === 0) return null;
    return (reward / risk).toFixed(2);
  }, [slAbs, tpAbs, orderPrice]);

  useEffect(() => {
    const q = parseFloat(quantity);
    if (!activeSymbol || isNaN(q) || q <= 0) {
      setPreview(null);
      return;
    }
    const timer = setTimeout(async () => {
      setPreviewLoading(true);
      const payload = {
        symbol: activeSymbol,
        type: orderType,
        side,
        quantity: q,
      };
      if (orderType === 'LIMIT') {
        const lp = parseFloat(price);
        if (!isNaN(lp) && lp > 0) payload.price = lp;
      }
      if (showSLTP && slAbs) payload.stop_loss_price = parseFloat(slAbs.toFixed(8));
      if (showSLTP && tpAbs) payload.take_profit_price = parseFloat(tpAbs.toFixed(8));
      try {
        const body = await apiAction('/api/v1/orders/preview', { method: 'POST', body: payload });
        const previewMsg = body.messages?.find((m) => m.type === 'order_preview');
        setPreview(previewMsg?.data ?? body.data ?? null);
      } catch {
        setPreview(null);
      } finally {
        setPreviewLoading(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [activeSymbol, side, orderType, quantity, price, showSLTP, slAbs, tpAbs]);

  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);

  const fillQty = (pct) => {
    if (!orderPrice) return;
    if (side === 'BUY') {
      const budget = quoteAvailable * (pct / 100);
      setQuantity((budget / orderPrice).toFixed(6));
    } else {
      setQuantity((Math.abs(basePosition) * (pct / 100)).toFixed(6));
    }
  };

  const handlePlaceOrder = async (e) => {
    e.preventDefault();
    setErrorMsg(null);
    const q = parseFloat(quantity);
    if (isNaN(q) || q <= 0) {
      setErrorMsg('Quantity must be > 0');
      return;
    }
    let lp = null;
    if (orderType === 'LIMIT') {
      lp = parseFloat(price);
      if (isNaN(lp) || lp <= 0) {
        setErrorMsg('Price must be > 0');
        return;
      }
    }
    const val = (lp || ticker?.price || 0) * q;
    if (val > 50000) {
      setErrorMsg('Order value exceeds $50,000 risk limit');
      return;
    }
    if (side === 'BUY' && val > quoteAvailable) {
      setErrorMsg(`Insufficient funds. Available: ${quoteAvailable.toFixed(2)} ${quote}`);
      return;
    }
    if (side === 'SELL' && q > basePosition) {
      setErrorMsg(`Insufficient holdings. Owned: ${basePosition} ${base}`);
      return;
    }

    const payload = { symbol: activeSymbol, type: orderType, side, price: lp, quantity: q };
    if (showSLTP) {
      if (slAbs) payload.stop_loss_price = parseFloat(slAbs.toFixed(8));
      if (tpAbs) payload.take_profit_price = parseFloat(tpAbs.toFixed(8));
    }
    const { ok } = await sendAction(Action.PLACE_ORDER, payload);
    if (ok) { setQuantity(''); setSlPrice(''); setTpPrice(''); }
    else {
      setErrorMsg('Order dispatch failed — backend unreachable.');
      toast.error('Order dispatch failed — backend unreachable.');
    }
  };

  const priceDec  = ticker ? ((activeSymbol.includes('XRP') || activeSymbol.includes('ADA') || activeSymbol.includes('DOGE') || ticker.price < 2) ? 4 : 2) : 2;
  const isBuy = side === 'BUY';

  return (
    <WidgetShell
      className="border-b border-border"
      icon={PlusCircle}
      title="Order Entry"
      contentClassName="order-entry-shell p-0"
      headerRight={
        <div className="icon-label">
          <span className={cn('text-xs font-bold tracking-wide', isBuy ? 'text-trading-up' : 'text-trading-down')}>
            {activeSymbol}
          </span>
          {ticker && (
            <span className={cn('num-mono text-xs font-bold', ticker.change_24h >= 0 ? 'text-trading-up' : 'text-trading-down')}>
              {fmtDec(ticker.price, priceDec)}
            </span>
          )}
        </div>
      }
    >
      <div className="order-entry-scroll">
        <ToggleGroup
          type="single"
          value={side}
          onValueChange={(v) => v && setSide(v)}
          className="order-entry-side-toggle mb-3 grid w-full grid-cols-2 gap-1"
          spacing={0}
        >
          <ToggleGroupItem
            ref={buyBtnRef}
            value="BUY"
            variant="buy"
            className="w-full font-extrabold tracking-wide"
          >
            <TrendingUp data-icon="inline-start" />
            BUY <span className="order-entry-hotkey">[B]</span>
          </ToggleGroupItem>
          <ToggleGroupItem
            ref={sellBtnRef}
            value="SELL"
            variant="sell"
            className="w-full font-extrabold tracking-wide"
          >
            <TrendingDown data-icon="inline-start" />
            SELL <span className="order-entry-hotkey">[S]</span>
          </ToggleGroupItem>
        </ToggleGroup>

        <ToggleGroup
          type="single"
          value={orderType}
          onValueChange={(v) => v && setOrderType(v)}
          variant="outline"
          className="order-entry-type-toggle mb-3 grid w-full grid-cols-2 rounded-md p-0.5"
          spacing={0}
        >
          {['LIMIT', 'MARKET'].map(t => (
            <ToggleGroupItem key={t} value={t} size="sm" className="font-bold">
              {t}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <form ref={formRef} id="order-entry-form" onSubmit={handlePlaceOrder}>
          <FieldGroup className="gap-2.5">
            {orderType === 'LIMIT' && (
              <Field data-invalid={!!errorMsg && errorMsg.includes('Price')}>
                <FieldLabel htmlFor="limit-price">Limit Price</FieldLabel>
                <div className="relative">
                  <Input
                    id="limit-price"
                    type="number"
                    step="any"
                    value={price}
                    onChange={e => setPrice(e.target.value)}
                    className="num-mono pr-12"
                    required
                  />
                  <span className="order-entry-input-suffix">
                    {quote}
                  </span>
                </div>
              </Field>
            )}

            <Field data-invalid={!!errorMsg && (errorMsg.includes('Quantity') || errorMsg.includes('Insufficient'))}>
              <FieldLabel htmlFor="order-qty">Quantity</FieldLabel>
              <div className="relative">
                <Input
                  id="order-qty"
                  type="number"
                  step="any"
                  value={quantity}
                  onChange={e => setQuantity(e.target.value)}
                  placeholder="0.00"
                  className="num-mono pr-12"
                  aria-invalid={!!errorMsg}
                  required
                />
                <span className="order-entry-input-suffix">
                  {base}
                </span>
              </div>
            </Field>
          </FieldGroup>

          <div className="order-entry-qty-presets mb-3 grid grid-cols-4 gap-1">
            {[25, 50, 75, 100].map(pct => (
              <Button key={pct} type="button" variant="outline" size="sm" onClick={() => fillQty(pct)}>
                {pct}%
              </Button>
            ))}
          </div>

          <div className="order-entry-metrics mb-3 flex flex-col gap-1.5">
            <div className="order-entry-metric-row">
              <span>Available {isBuy ? quote : base}</span>
              <span className="num-mono font-bold text-foreground">
                {isBuy ? `${quoteAvailable.toFixed(2)} ${quote}` : `${Math.abs(basePosition).toFixed(4)} ${base}`}
              </span>
            </div>
            <div className="order-entry-metric-row">
              <span>Est. Order Value</span>
              <span className={cn('num-mono font-bold', estCost > quoteAvailable && isBuy ? 'text-trading-down' : 'text-foreground')}>
                {estCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {quote}
              </span>
            </div>
          </div>

          <Collapsible open={showSLTP} onOpenChange={setShowSLTP}>
            <CollapsibleTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className={cn('mb-2 w-full justify-between', showSLTP && 'border-primary/30 bg-primary/10 text-trading-accent')}
              >
                <span className="icon-label">
                  <Target data-icon="inline-start" />
                  Stop Loss / Take Profit
                </span>
                <ChevronDown className={cn('size-3.5 transition-transform', showSLTP && 'rotate-180')} />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="mb-2 flex flex-col gap-2 rounded-md border border-border bg-muted/20 p-2">
              <Field>
                <div className="mb-1 flex items-center justify-between">
                  <FieldLabel className="text-trading-down">Stop Loss</FieldLabel>
                  <ToggleGroup type="single" value={slMode} onValueChange={(v) => { if (v) { setSlMode(v); setSlPrice(''); } }} spacing={0} className="h-6">
                    {['%', '$'].map(m => (
                      <ToggleGroupItem key={m} value={m} size="sm" className="px-2 text-xs font-bold">{m}</ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </div>
                <div className="relative">
                  <Input
                    type="number"
                    step="any"
                    value={slPrice}
                    onChange={e => setSlPrice(e.target.value)}
                    placeholder={slMode === '%' ? '1.5' : orderPrice ? orderPrice.toFixed(priceDec) : '0'}
                    className="num-mono border-[color-mix(in_srgb,var(--color-down)_30%,transparent)] pr-10"
                  />
                  <span className="order-entry-input-suffix order-entry-input-suffix--down">
                    {slMode === '%' ? '%' : quote}
                  </span>
                </div>
                {slAbs && (
                  <FieldDescription className="num-mono text-trading-down">→ ${slAbs.toFixed(priceDec)}</FieldDescription>
                )}
              </Field>

              <Field>
                <div className="mb-1 flex items-center justify-between">
                  <FieldLabel className="text-trading-up">Take Profit</FieldLabel>
                  <ToggleGroup type="single" value={tpMode} onValueChange={(v) => { if (v) { setTpMode(v); setTpPrice(''); } }} spacing={0} className="h-6">
                    {['%', '$'].map(m => (
                      <ToggleGroupItem key={m} value={m} size="sm" className="px-2 text-xs font-bold">{m}</ToggleGroupItem>
                    ))}
                  </ToggleGroup>
                </div>
                <div className="relative">
                  <Input
                    type="number"
                    step="any"
                    value={tpPrice}
                    onChange={e => setTpPrice(e.target.value)}
                    placeholder={tpMode === '%' ? '3.0' : orderPrice ? orderPrice.toFixed(priceDec) : '0'}
                    className="num-mono border-[color-mix(in_srgb,var(--color-up)_30%,transparent)] pr-10"
                  />
                  <span className="order-entry-input-suffix order-entry-input-suffix--up">
                    {tpMode === '%' ? '%' : quote}
                  </span>
                </div>
                {tpAbs && (
                  <FieldDescription className="num-mono text-trading-up">→ ${tpAbs.toFixed(priceDec)}</FieldDescription>
                )}
              </Field>

              {rrRatio && (
                <div className="flex items-center justify-center rounded-sm border border-border bg-muted/30 py-1.5 text-xs">
                  <span className="text-muted-foreground">Risk / Reward:&nbsp;</span>
                  <span className={cn(
                    'num-mono font-extrabold',
                    parseFloat(rrRatio) >= 2 ? 'text-trading-up' : parseFloat(rrRatio) >= 1 ? 'text-trading-warn' : 'text-trading-down'
                  )}>
                    1 : {rrRatio}
                  </span>
                </div>
              )}
            </CollapsibleContent>
          </Collapsible>
        </form>
      </div>

      <div className="order-entry-footer">
        {lastFillExplain && (
          <div className="mb-2 rounded-md border border-border/60 bg-muted/30 px-2.5 py-2 text-xs">
            <div className="mb-1 font-semibold text-muted-foreground">Last fill</div>
            <p className="num-mono">
              {lastFillExplain.side} {lastFillExplain.quantity} · ~
              {lastFillExplain.notional?.toLocaleString()} {lastFillExplain.quote}
              {lastFillExplain.stop_loss_price != null && <> · SL {lastFillExplain.stop_loss_price}</>}
              {lastFillExplain.take_profit_price != null && <> · TP {lastFillExplain.take_profit_price}</>}
              {lastFillExplain.risk_reward_ratio != null && <> · R:R 1:{lastFillExplain.risk_reward_ratio}</>}
            </p>
          </div>
        )}
        {(preview || previewLoading) && (
          <div className={cn(
            'mb-2 rounded-md border px-2.5 py-2 text-xs',
            preview?.allowed ? 'border-trading-up/30 bg-trading-up/5' : 'border-trading-down/30 bg-trading-down/5',
          )}>
            <div className="mb-1 flex items-center justify-between font-semibold">
              <span>Pre-trade preview</span>
              {previewLoading && <span className="text-muted-foreground">Updating…</span>}
            </div>
            {preview && (
              <>
                {preview.block_reason ? (
                  <p className="text-trading-down">{preview.block_reason}</p>
                ) : (
                  <p className="text-trading-up">Ready to submit</p>
                )}
                <p className="mt-1 num-mono text-muted-foreground">
                  Notional {preview.notional?.toLocaleString()} {preview.quote}
                  {preview.stop_loss_price != null && (
                    <> · SL {preview.stop_loss_price}</>
                  )}
                  {preview.take_profit_price != null && (
                    <> · TP {preview.take_profit_price}</>
                  )}
                  {preview.risk_reward_ratio != null && (
                    <> · R:R 1:{preview.risk_reward_ratio}</>
                  )}
                </p>
                {preview.warnings?.length > 0 && (
                  <p className="mt-1 text-trading-warn">{preview.warnings.join(' · ')}</p>
                )}
              </>
            )}
          </div>
        )}
        <Button
          type="submit"
          form="order-entry-form"
          variant={isBuy ? 'buy' : 'sell'}
          size="lg"
          className="w-full font-extrabold tracking-wide"
          disabled={preview && !preview.allowed}
        >
          {isBuy ? <TrendingUp data-icon="inline-start" /> : <TrendingDown data-icon="inline-start" />}
          Place {side} {orderType}
        </Button>

        {errorMsg && (
          <Alert variant="destructive" className="mt-2 py-2">
            <ShieldAlert data-icon="inline-start" />
            <AlertDescription>{errorMsg}</AlertDescription>
          </Alert>
        )}
      </div>
    </WidgetShell>
  );
}
