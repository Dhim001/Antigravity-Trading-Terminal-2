/**
 * Off-main-thread backtest payload trim (MEMORY_CENTRIC_REVIEW #24).
 */
import {
  trimBacktestPayload,
  slimBacktestForDock,
  buildBacktestOverlay,
} from '../lib/backtestSlim.js';

self.onmessage = (event) => {
  const { id, op, payload } = event.data || {};
  try {
    let result;
    if (op === 'trim') {
      result = trimBacktestPayload(payload);
    } else if (op === 'slim') {
      result = slimBacktestForDock(payload);
    } else if (op === 'overlay') {
      result = buildBacktestOverlay(payload);
    } else {
      throw new Error(`unknown backtestSlim op: ${op}`);
    }
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({
      id,
      ok: false,
      error: String(err?.message || err),
    });
  }
};
