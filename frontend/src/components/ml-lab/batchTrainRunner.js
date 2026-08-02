/**
 * Sequential batch-train queue runner (pure async helper for BatchTrainDialog + tests).
 */

/**
 * Run strategies one-by-one. Failures are isolated — the queue continues.
 *
 * @param {object} opts
 * @param {string[]} opts.queue
 * @param {(strategyId: string) => Promise<unknown>} opts.onTrainStrategy
 * @param {(strategyId: string) => Promise<unknown>} [opts.onValidateStrategy]
 * @param {boolean} [opts.autoValidate]
 * @param {() => boolean} [opts.shouldCancel]
 * @param {(p: { index: number, total: number, strategy: string }) => void} [opts.onProgress]
 * @param {(strategyId: string, err: Error) => void} [opts.onStrategyError]
 * @returns {Promise<{ ok: number, failed: number, cancelled: boolean, total: number, completed: string[] }>}
 */
export async function runBatchTrainQueue({
  queue,
  onTrainStrategy,
  onValidateStrategy,
  autoValidate = false,
  shouldCancel,
  onProgress,
  onStrategyError,
} = {}) {
  const list = Array.isArray(queue) ? queue : [];
  let ok = 0;
  let failed = 0;
  const completed = [];

  for (let i = 0; i < list.length; i += 1) {
    if (shouldCancel?.()) {
      return {
        ok,
        failed,
        cancelled: true,
        total: list.length,
        completed,
      };
    }
    const strategyId = list[i];
    onProgress?.({ index: i + 1, total: list.length, strategy: strategyId });
    try {
      await onTrainStrategy(strategyId);
      if (autoValidate && typeof onValidateStrategy === 'function' && !shouldCancel?.()) {
        await onValidateStrategy(strategyId);
      }
      ok += 1;
      completed.push(strategyId);
    } catch (err) {
      failed += 1;
      onStrategyError?.(strategyId, err instanceof Error ? err : new Error(String(err?.message || err)));
    }
  }

  return {
    ok,
    failed,
    cancelled: Boolean(shouldCancel?.()),
    total: list.length,
    completed,
  };
}

/**
 * Format the end-of-batch toast summary.
 * @param {{ ok: number, failed: number, cancelled: boolean, total: number }} summary
 */
export function formatBatchTrainSummary({ ok, failed, cancelled, total }) {
  if (cancelled) {
    return `Stopped early. Trained ${ok}/${total}. ${failed} failed.`;
  }
  return `Trained ${ok}/${total} strategies.${failed ? ` ${failed} failed.` : ''}`;
}
