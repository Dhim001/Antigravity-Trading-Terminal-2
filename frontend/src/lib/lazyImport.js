import { lazy } from 'react';

/**
 * React.lazy wrapper that retries once on Vite chunk/HMR fetch failures.
 */
export function lazyImport(importFn, label = 'panel') {
  return lazy(async () => {
    try {
      return await importFn();
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
