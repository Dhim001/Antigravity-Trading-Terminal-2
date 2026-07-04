/** In-process backtest job poll timer — shared by endpoints + dispatch. */

let _backtestPollTimer = null;

export function stopBacktestJobPolling() {
  if (_backtestPollTimer) {
    clearTimeout(_backtestPollTimer);
    _backtestPollTimer = null;
  }
}

export function scheduleBacktestJobPoll(fn, delayMs) {
  stopBacktestJobPolling();
  _backtestPollTimer = setTimeout(fn, delayMs);
}
