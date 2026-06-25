/** LIVE_MASSIVE helpers — US equity session and watchlist badges. */

/** Regular US equity session Mon–Fri 9:30–16:00 ET. */
export function usEquitySessionOpen(date = new Date()) {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      weekday: 'short',
      hour: 'numeric',
      minute: 'numeric',
      hour12: false,
    }).formatToParts(date);
    const weekday = parts.find((p) => p.type === 'weekday')?.value ?? '';
    if (weekday === 'Sat' || weekday === 'Sun') return false;
    const hour = Number(parts.find((p) => p.type === 'hour')?.value ?? 0);
    const minute = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
    const mins = hour * 60 + minute;
    return mins >= 9 * 60 + 30 && mins < 16 * 60;
  } catch {
    return true;
  }
}

export function isLiveMassiveMode(terminalMode) {
  return terminalMode === 'LIVE_MASSIVE';
}

/** Paper ledger (Sim or Massive) — no broker reconcile workflow. */
export function isPaperExecutionMode(terminalMode, executionMode) {
  if (executionMode === 'paper' || executionMode === 'simulated') return true;
  return terminalMode === 'SIMULATED' || terminalMode === 'LIVE_MASSIVE';
}

/** Row badge: closed | poll | null */
export function massiveWatchlistBadge(symbol, terminalMode, massiveHealth) {
  if (!isLiveMassiveMode(terminalMode) || !massiveHealth) return null;
  const isCryptoSym = String(symbol).includes('USDT');
  if (massiveHealth.poll_fallback || massiveHealth.stocks_mode === 'poll' || massiveHealth.crypto_mode === 'poll') {
    if (isCryptoSym && massiveHealth.crypto_mode === 'poll') return 'poll';
    if (!isCryptoSym && massiveHealth.stocks_mode === 'poll') return 'poll';
  }
  if (!isCryptoSym && !usEquitySessionOpen()) return 'closed';
  return null;
}
