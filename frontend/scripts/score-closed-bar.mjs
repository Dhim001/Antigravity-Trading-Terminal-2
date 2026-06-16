/**
 * Score the closed bar (exclude forming candle) for backend parity tests.
 * Usage: node score-closed-bar.mjs <candles.json>
 */
import { readFileSync } from 'node:fs';
import { generateSignal } from '../src/utils/indicators.js';

const path = process.argv[2];
if (!path) {
  console.error('Usage: node score-closed-bar.mjs <candles.json>');
  process.exit(1);
}

const candles = JSON.parse(readFileSync(path, 'utf8'));
if (!Array.isArray(candles) || candles.length < 3) {
  console.error('Need at least 3 candles');
  process.exit(1);
}

// Backend scores iloc[-2]; frontend scores last candle — drop forming bar.
const closed = candles.slice(0, -1);
const result = generateSignal(closed);
process.stdout.write(JSON.stringify({
  score: result.score,
  signal: result.signal,
  reasons: result.reasons,
}));
