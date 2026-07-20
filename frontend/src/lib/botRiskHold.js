import { useEffect, useState } from 'react';

/** @typedef {{ kind: 'cooloff' | 'streak_limit' | 'drawdown', reason?: string, remaining_sec?: number, cooloff_until?: string, consecutive_losses?: number, max_consecutive_losses?: number, drawdown_pct?: number, max_drawdown_pct?: number, total_pnl?: number, block_reason?: string }} BotRiskHold */

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
  if (!hold || (hold.kind !== 'cooloff' && hold.kind !== 'streak_limit')) return 0;
  if (hold.cooloff_until) {
    const until = Date.parse(hold.cooloff_until);
    if (!Number.isNaN(until)) {
      return Math.max(0, Math.ceil((until - Date.now()) / 1000));
    }
  }
  return Math.max(0, Number(hold.remaining_sec) || 0);
}

/**
 * Drop expired cooloff holds client-side so row tint / badges clear without
 * waiting for the next bots_update.
 * @param {BotRiskHold | null | undefined} hold
 * @returns {BotRiskHold | null}
 */
export function effectiveRiskHold(hold) {
  if (!hold?.kind) return null;
  if (
    (hold.kind === 'cooloff' || hold.kind === 'streak_limit')
    && hold.cooloff_until
    && remainingCooloffSec(hold) <= 0
  ) {
    return null;
  }
  return hold;
}

/** Live countdown for cooloff holds (ticks every second). */
export function useRiskHoldRemaining(hold) {
  const [remaining, setRemaining] = useState(() => remainingCooloffSec(hold));

  useEffect(() => {
    if (!hold || (hold.kind !== 'cooloff' && hold.kind !== 'streak_limit')) {
      setRemaining(0);
      return undefined;
    }
    if (hold.kind === 'streak_limit' && !hold.cooloff_until && hold.remaining_sec == null) {
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

/**
 * Single hook for UI: effective hold + live remaining seconds.
 * @param {BotRiskHold | null | undefined} hold
 */
export function useEffectiveRiskHold(hold) {
  const timed = hold?.kind === 'cooloff' || (hold?.kind === 'streak_limit' && (hold.cooloff_until || hold.remaining_sec != null))
    ? hold
    : null;
  const remaining = useRiskHoldRemaining(timed);
  if (!hold?.kind) return { hold: null, remaining: 0 };
  if (timed && remaining <= 0 && (hold.kind === 'cooloff' || hold.cooloff_until)) {
    return { hold: null, remaining: 0 };
  }
  return { hold, remaining };
}

export function riskHoldBadgeLabel(hold, remainingSec = null) {
  const active = effectiveRiskHold(hold);
  if (!active?.kind) return null;
  if (active.kind === 'cooloff') {
    const rem = remainingSec ?? remainingCooloffSec(active);
    if (rem <= 0) return null;
    return `COOLING OFF · ${formatCooloffRemaining(rem)}`;
  }
  if (active.kind === 'streak_limit') {
    const rem = remainingSec ?? remainingCooloffSec(active);
    const cur = active.consecutive_losses ?? '?';
    const max = active.max_consecutive_losses ?? '?';
    if (rem > 0) return `LOSS STREAK · ${cur}/${max} · ${formatCooloffRemaining(rem)}`;
    return `LOSS STREAK · ${cur}/${max}`;
  }
  if (active.kind === 'drawdown') {
    const dd = active.drawdown_pct ?? '?';
    const max = active.max_drawdown_pct ?? '?';
    return `MAX DD · ${dd}%/${max}%`;
  }
  return active.reason || null;
}

export function riskHoldDetailMessage(hold, remainingSec = null) {
  const active = effectiveRiskHold(hold);
  if (!active?.kind) return null;
  if (active.kind === 'cooloff') {
    const rem = remainingSec ?? remainingCooloffSec(active);
    if (rem <= 0) return null;
    const losses = active.consecutive_losses ?? 0;
    return `Cooling off after ${losses} consecutive loss${losses === 1 ? '' : 'es'}. New entries resume in ${formatCooloffRemaining(rem)}.`;
  }
  if (active.kind === 'streak_limit') {
    const rem = remainingSec ?? remainingCooloffSec(active);
    if (rem > 0) {
      return active.block_reason
        || `Loss streak limit reached (${active.consecutive_losses}/${active.max_consecutive_losses}). Entries blocked for ${formatCooloffRemaining(rem)} — resume to clear early.`;
    }
    return active.block_reason
      || `Loss streak limit reached (${active.consecutive_losses}/${active.max_consecutive_losses}). Resume to clear the hold, or wait for cooloff.`;
  }
  if (active.kind === 'drawdown') {
    return active.block_reason
      || `Max drawdown reached (${active.drawdown_pct}% of allocation vs ${active.max_drawdown_pct}% limit). Resume alone will re-pause until PnL recovers or the limit is raised.`;
  }
  return active.reason || null;
}

/**
 * Compact activity chip next to RUNNING/PAUSED — held / cooling off / no signal.
 * Prefer risk holds over "no signal"; inventory is shown in the Position column.
 *
 * @param {{
 *   status?: string,
 *   last_signal_at?: string | number | null,
 *   risk_hold?: BotRiskHold | null,
 * }} bot
 * @param {{ remainingSec?: number, hold?: BotRiskHold | null }} [opts]
 * @returns {{ kind: 'cooling_off'|'held'|'no_signal', label: string, title?: string } | null}
 */
export function botRuntimeActivityHint(bot, opts = {}) {
  const status = String(bot?.status || '').toUpperCase();
  if (status !== 'RUNNING' && status !== 'PAUSED') return null;

  const hold = effectiveRiskHold(opts.hold ?? bot?.risk_hold);
  const remaining = opts.remainingSec ?? remainingCooloffSec(hold);

  if (hold?.kind === 'cooloff' && remaining > 0) {
    return {
      kind: 'cooling_off',
      label: `Cooling off · ${formatCooloffRemaining(remaining)}`,
      title: riskHoldDetailMessage(hold, remaining) || undefined,
    };
  }
  if (hold?.kind === 'streak_limit' || hold?.kind === 'drawdown') {
    const rem = remaining > 0 ? ` · ${formatCooloffRemaining(remaining)}` : '';
    const label = hold.kind === 'drawdown' ? `Held · DD${rem}` : `Held${rem}`;
    return {
      kind: 'held',
      label,
      title: riskHoldDetailMessage(hold, remaining) || hold.block_reason || hold.reason || undefined,
    };
  }

  // Only annotate "no signal" while the evaluator is armed and flat of holds.
  if (status === 'RUNNING' && (bot?.last_signal_at == null || bot?.last_signal_at === '')) {
    return {
      kind: 'no_signal',
      label: 'No signal',
      title: 'Bot is running but has not emitted a trade signal yet.',
    };
  }

  return null;
}
