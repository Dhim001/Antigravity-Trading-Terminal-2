/** Run time estimates shown before starting a backtest. */

import {
  formatBacktestTimeoutLabel,
  getBacktestClientTimeoutMs,
} from './backtestTimeouts';
import { formatPortfolioRunEstimate, uniqueSymbols } from './portfolioBacktest';

/**
 * @param {{
 *   days?: number | string,
 *   portfolioSymbolCount?: number,
 *   portfolioSymbols?: string[],
 *   reasoning?: boolean,
 *   metaLabelWalkForward?: boolean,
 *   sweepCombos?: number,
 *   walkForward?: boolean,
 *   deferred?: boolean,
 * }} opts
 */
export function estimateRunDurationMs({
  days = 7,
  portfolioSymbolCount = 0,
  portfolioSymbols,
  reasoning = false,
  metaLabelWalkForward = false,
  sweepCombos = 0,
  walkForward = false,
  deferred = false,
} = {}) {
  const parsedDays = parseInt(String(days), 10) || 7;
  const symbolCount = portfolioSymbols?.length
    ? uniqueSymbols(portfolioSymbols).length
    : Math.max(0, Math.floor(Number(portfolioSymbolCount) || 0));

  let ms = getBacktestClientTimeoutMs({
    reasoning,
    metaLabelWalkForward,
    days: parsedDays,
    portfolioSymbolCount: symbolCount,
  });

  if (sweepCombos > 1) {
    ms = Math.max(ms, ms * Math.min(sweepCombos, 12) * 0.35);
  }
  if (walkForward && !metaLabelWalkForward) {
    ms = Math.max(ms, ms * 1.8);
  }
  if (deferred || symbolCount >= 2 || parsedDays >= 30 || reasoning || walkForward) {
    ms = Math.max(ms, 60_000);
  }

  return Math.round(ms);
}

/**
 * Human label for the RUN button / footer.
 */
export function formatRunEstimate(opts = {}) {
  const portfolioLabel = opts.portfolioSymbols?.length
    ? formatPortfolioRunEstimate(opts.portfolioSymbols, { days: opts.days })
    : null;
  if (portfolioLabel) return portfolioLabel;

  const ms = estimateRunDurationMs(opts);
  const label = formatBacktestTimeoutLabel(ms);
  const parts = [`Est. ~${label}`];

  if (opts.reasoning) parts.push('LLM reasoning');
  else if (opts.metaLabelWalkForward) parts.push('meta-label WF');
  else if (opts.walkForward) parts.push('walk-forward');
  else if (opts.sweepCombos > 1) parts.push(`${opts.sweepCombos} combos`);
  else if ((parseInt(String(opts.days), 10) || 7) >= 30) parts.push(`${opts.days}d archive`);

  if (opts.deferred || opts.portfolioSymbolCount >= 2 || opts.reasoning
    || opts.walkForward || opts.metaLabelWalkForward) {
    parts.push('background job');
  }

  return parts.join(' · ');
}
