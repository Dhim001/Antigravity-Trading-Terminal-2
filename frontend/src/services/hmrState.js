/** Shared Vite HMR persistence bucket — survives hot module replacement. */

export function getHmrData() {
  if (!import.meta.hot) return null;
  import.meta.hot.data ??= {};
  return import.meta.hot.data;
}

export function isHmrReload() {
  return Boolean(getHmrData()?.hmrActive);
}

export function markHmrActive() {
  const data = getHmrData();
  if (data) data.hmrActive = true;
}

export function setupHmrAccept() {
  if (import.meta.hot) {
    import.meta.hot.accept();
  }
}
