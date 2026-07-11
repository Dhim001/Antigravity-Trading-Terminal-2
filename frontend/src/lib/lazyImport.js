import { lazy } from 'react';

/**
 * React.lazy wrapper that retries once on Vite chunk/HMR fetch failures.
 * After a recycle, the open renderer can hold a stale module graph; a single
 * reload usually restores dynamic imports.
 */
export function lazyImport(importFn, label = 'panel') {
  return lazy(async () => {
    try {
      const mod = await importFn();
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(`lazy-import-retry:${label}`);
      }
      return mod;
    } catch (err) {
      const message = String(err?.message || err || '');
      const isChunkLoad = /fetch dynamically imported module|Loading chunk|Failed to fetch/i.test(message);
      if (!isChunkLoad) throw err;

      const retryKey = `lazy-import-retry:${label}`;
      if (typeof sessionStorage !== 'undefined' && !sessionStorage.getItem(retryKey)) {
        sessionStorage.setItem(retryKey, '1');
        window.location.reload();
        return new Promise(() => {});
      }
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(retryKey);
      }
      throw err;
    }
  });
}
