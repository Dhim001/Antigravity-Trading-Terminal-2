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
 *   rollingFolds?: number,
 *   strategy?: string,
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
  rollingFolds = 3,
  strategy = '',
  deferred = false,
} = {}) {
  const parsedDays = parseInt(String(days), 10) || 7;
  const symbolCount = portfolioSymbols?.length
    ? uniqueSymbols(portfolioSymbols).length
    : Math.max(0, Math.floor(Number(portfolioSymbolCount) || 0));

  let ms = getBacktestClientTimeoutMs({
    reasoning,
    metaLabelWalkForward,
    walkForward,
    rollingFolds,
    comboCount: sweepCombos,
    days: parsedDays,
    portfolioSymbolCount: symbolCount,
    strategy,
  });

  if (sweepCombos > 1 && !walkForward) {
    ms = Math.max(ms, ms * Math.min(sweepCombos, 12) * 0.35);
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

  const strat = String(opts.strategy || '').toUpperCase();
  if (strat === 'RL_PPO_AGENT') parts.push('RL');
  else if (strat.includes('LSTM') || strat.includes('TCN') || strat.includes('TRANSFORMER')
    || strat.includes('VAE') || strat.includes('GNN')) {
    parts.push('deep ML');
  }   else if (strat.startsWith('ML_')) {
    parts.push('ML');
  }

  if (opts.deferred || opts.portfolioSymbolCount >= 2 || opts.reasoning
    || opts.walkForward || opts.metaLabelWalkForward
    || strat === 'RL_PPO_AGENT' || strat.includes('LSTM') || strat.includes('TRANSFORMER')) {
    parts.push('background job');
  }

  return parts.join(' · ');
}
