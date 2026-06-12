/**
 * OrderEntryWidget.jsx — order ticket with unified terminal tokens
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { toast } from 'sonner';
import { useStore } from '../store/useStore';
import { sendAction } from '../api/transport';
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
  const buyBtnRef  = useRef(null);
  const sellBtnRef = useRef(null);
  const formRef    = useRef(null);

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
    if (orderResult.status === 'success') toast.success(orderResult.message);
    else toast.error(orderResult.message);
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
      scrollable
      scrollPad={false}
      contentClassName="p-3 pb-2"
      headerRight={
        <div className="icon-label">
          <span className={cn('text-[0.62rem] font-bold tracking-wide', isBuy ? 'text-trading-up' : 'text-trading-down')}>
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
        <ToggleGroup
          type="single"
          value={side}
          onValueChange={(v) => v && setSide(v)}
          className="mb-2 grid w-full grid-cols-2 gap-1"
          spacing={0}
        >
          <ToggleGroupItem
            ref={buyBtnRef}
            value="BUY"
            variant="buy"
            className="w-full font-extrabold tracking-wide"
          >
            <TrendingUp data-icon="inline-start" />
            BUY <span className="text-[0.62rem] font-medium opacity-60">[B]</span>
          </ToggleGroupItem>
          <ToggleGroupItem
            ref={sellBtnRef}
            value="SELL"
            variant="sell"
            className="w-full font-extrabold tracking-wide"
          >
            <TrendingDown data-icon="inline-start" />
            SELL <span className="text-[0.62rem] font-medium opacity-60">[S]</span>
          </ToggleGroupItem>
        </ToggleGroup>

        <ToggleGroup
          type="single"
          value={orderType}
          onValueChange={(v) => v && setOrderType(v)}
          variant="outline"
          className="mb-2 grid w-full grid-cols-2 rounded-md bg-muted/30 p-0.5"
          spacing={0}
        >
          {['LIMIT', 'MARKET'].map(t => (
            <ToggleGroupItem key={t} value={t} size="sm" className="font-bold">
              {t}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <form ref={formRef} onSubmit={handlePlaceOrder}>
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
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[0.62rem] text-muted-foreground">
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
                <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[0.62rem] text-muted-foreground">
                  {base}
                </span>
              </div>
            </Field>
          </FieldGroup>

          <div className="mb-2 grid grid-cols-4 gap-1">
            {[25, 50, 75, 100].map(pct => (
              <Button key={pct} type="button" variant="outline" size="sm" onClick={() => fillQty(pct)}>
                {pct}%
              </Button>
            ))}
          </div>

          <div className="mb-1 flex justify-between text-xs text-muted-foreground">
            <span>Available {isBuy ? quote : base}:</span>
            <span className="num-mono font-bold text-foreground">
              {isBuy ? `${quoteAvailable.toFixed(2)} ${quote}` : `${Math.abs(basePosition).toFixed(4)} ${base}`}
            </span>
          </div>

          <div className="mb-2 flex justify-between text-xs text-muted-foreground">
            <span>Est. Order Value:</span>
            <span className={cn('num-mono font-bold', estCost > quoteAvailable && isBuy ? 'text-trading-down' : 'text-foreground')}>
              {estCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {quote}
            </span>
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
                      <ToggleGroupItem key={m} value={m} size="sm" className="px-2 text-[0.62rem] font-bold">{m}</ToggleGroupItem>
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
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[0.62rem] text-trading-down">
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
                      <ToggleGroupItem key={m} value={m} size="sm" className="px-2 text-[0.62rem] font-bold">{m}</ToggleGroupItem>
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
                  <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[0.62rem] text-trading-up">
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

          <Button
            type="submit"
            variant={isBuy ? 'buy' : 'sell'}
            size="lg"
            className="w-full font-extrabold tracking-wide"
          >
            {isBuy ? <TrendingUp data-icon="inline-start" /> : <TrendingDown data-icon="inline-start" />}
            Place {side} {orderType}
          </Button>
        </form>

        {errorMsg && (
          <Alert variant="destructive" className="mt-2 py-2">
            <ShieldAlert data-icon="inline-start" />
            <AlertDescription>{errorMsg}</AlertDescription>
          </Alert>
        )}
    </WidgetShell>
  );
}
