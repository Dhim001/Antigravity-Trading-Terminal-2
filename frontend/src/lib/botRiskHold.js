import { useEffect, useState } from 'react';

/** @typedef {{ kind: 'cooloff' | 'streak_limit', reason?: string, remaining_sec?: number, cooloff_until?: string, consecutive_losses?: number, max_consecutive_losses?: number }} BotRiskHold */

export function formatCooloffRemaining(totalSec) {
  const sec = Math.max(0, Math.floor(Number(totalSec) || 0));
  if (sec >= 3600) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return `${h}h ${m}m`;
  }
  if (sec >= 60) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }
  return `${sec}s`;
}

export function remainingCooloffSec(hold) {
  if (!hold || hold.kind !== 'cooloff') return 0;
  if (hold.cooloff_until) {
    const until = Date.parse(hold.cooloff_until);
    if (!Number.isNaN(until)) {
      return Math.max(0, Math.ceil((until - Date.now()) / 1000));
    }
  }
  return Math.max(0, Number(hold.remaining_sec) || 0);
}

/** Live countdown for cooloff holds (ticks every second). */
export function useRiskHoldRemaining(hold) {
  const [remaining, setRemaining] = useState(() => remainingCooloffSec(hold));

  useEffect(() => {
    if (!hold || hold.kind !== 'cooloff') {
      setRemaining(0);
      return undefined;
    }
    const tick = () => setRemaining(remainingCooloffSec(hold));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [hold?.kind, hold?.cooloff_until, hold?.remaining_sec]);

  return remaining;
}

export function riskHoldBadgeLabel(hold, remainingSec = null) {
  if (!hold?.kind) return null;
  if (hold.kind === 'cooloff') {
    const rem = remainingSec ?? remainingCooloffSec(hold);
    if (rem <= 0) return null;
    return `COOLING OFF · ${formatCooloffRemaining(rem)}`;
  }
  if (hold.kind === 'streak_limit') {
    const cur = hold.consecutive_losses ?? '?';
    const max = hold.max_consecutive_losses ?? '?';
    return `LOSS STREAK · ${cur}/${max}`;
  }
  return hold.reason || null;
}

export function riskHoldDetailMessage(hold, remainingSec = null) {
  if (!hold?.kind) return null;
  if (hold.kind === 'cooloff') {
    const rem = remainingSec ?? remainingCooloffSec(hold);
    if (rem <= 0) return null;
    const losses = hold.consecutive_losses ?? 0;
    return `Cooling off after ${losses} consecutive loss${losses === 1 ? '' : 'es'}. New entries resume in ${formatCooloffRemaining(rem)}.`;
  }
  if (hold.kind === 'streak_limit') {
    return hold.block_reason
      || `Loss streak limit reached (${hold.consecutive_losses}/${hold.max_consecutive_losses}). Resume manually when ready.`;
  }
  return hold.reason || null;
}
