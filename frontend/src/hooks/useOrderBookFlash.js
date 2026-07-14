import { useEffect, useRef, useState } from 'react';
import { useStore } from '../store/useStore';

function sideFlash(prev, next) {
  if (!prev || !next) return null;
  if (next[0] > prev[0]) return 'up';
  if (next[0] < prev[0]) return 'down';
  if (next[1] > prev[1]) return 'up';
  if (next[1] < prev[1]) return 'down';
  return null;
}

/** Flash best bid/ask when price or top-of-book size changes. */
export function useOrderBookFlash(symbol) {
  const ob = useStore((state) => state.orderBooks[symbol]);
  const prevRef = useRef({ bid: null, ask: null });
  const [flash, setFlash] = useState({ bid: null, ask: null, key: 0 });

  useEffect(() => {
    const bid = ob?.bids?.[0];
    const ask = ob?.asks?.[0];
    const nextBid = bid ? [bid[0], bid[1]] : null;
    const nextAsk = ask ? [ask[0], ask[1]] : null;
    const bidFlash = sideFlash(prevRef.current.bid, nextBid);
    const askFlash = sideFlash(prevRef.current.ask, nextAsk);
    if (bidFlash || askFlash) {
      setFlash({ bid: bidFlash, ask: askFlash, key: Date.now() });
    }
    prevRef.current = { bid: nextBid, ask: nextAsk };
  }, [ob]);

  return flash;
}

export function flashClass(dir) {
  if (dir === 'up') return 'animate-flash-buy';
  if (dir === 'down') return 'animate-flash-sell';
  return '';
}
