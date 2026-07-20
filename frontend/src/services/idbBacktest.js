/**
 * IndexedDB persistence for full backtest payloads (Tier 4).
 * Survives tab close and avoids sessionStorage quota limits.
 */

const DB_NAME = 'trading_terminal_backtest';
const STORE_NAME = 'runs';
/** v2: savedAt index for prune without materializing blobs (MEMORY #17). */
const DB_VERSION = 2;
const MAX_IDB_RUNS = 10;

function terminalProfile() {
  return import.meta.env.VITE_TERMINAL_PROFILE || 'default';
}

function storageKey(runId) {
  return `${terminalProfile()}:${runId}`;
}

let dbPromise = null;

function openDb() {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  if (!dbPromise) {
    dbPromise = new Promise((resolve) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (event) => {
        const db = req.result;
        let store;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          store = db.createObjectStore(STORE_NAME, { keyPath: 'key' });
        } else {
          store = req.transaction.objectStore(STORE_NAME);
        }
        if (store && !store.indexNames.contains('savedAt')) {
          store.createIndex('savedAt', 'savedAt', { unique: false });
        }
        // Ensure existing rows have savedAt for the index (best-effort on upgrade).
        if (event.oldVersion < 2 && store) {
          const cursorReq = store.openCursor();
          cursorReq.onsuccess = () => {
            const cursor = cursorReq.result;
            if (!cursor) return;
            const row = cursor.value;
            if (row && row.savedAt == null) {
              row.savedAt = Date.now();
              cursor.update(row);
            }
            cursor.continue();
          };
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    });
  }
  return dbPromise;
}

function withStore(mode, fn) {
  return openDb().then((db) => {
    if (!db) return null;
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, mode);
      const store = tx.objectStore(STORE_NAME);
      const out = fn(store);
      tx.oncomplete = () => resolve(out);
      tx.onerror = () => resolve(null);
      tx.onabort = () => resolve(null);
    });
  });
}

/** @returns {Promise<boolean>} */
export async function idbSaveBacktest(runId, results) {
  if (!runId || !results) return false;
  const key = storageKey(runId);
  const saved = await withStore('readwrite', (store) => {
    store.put({ key, runId, savedAt: Date.now(), results });
    return true;
  });
  if (saved) {
    await idbPruneBacktests(runId, MAX_IDB_RUNS);
  }
  return Boolean(saved);
}

/** @returns {Promise<object|null>} */
export async function idbLoadBacktest(runId) {
  if (!runId) return null;
  const key = storageKey(runId);
  const row = await withStore('readonly', (store) => new Promise((resolve) => {
    const req = store.get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => resolve(null);
  }));
  return row?.results ?? null;
}

/** @returns {Promise<void>} */
export async function idbClearBacktest(runId) {
  if (!runId) return;
  const key = storageKey(runId);
  await withStore('readwrite', (store) => {
    store.delete(key);
    return true;
  });
}

/**
 * Keep newest runs for this profile; always retain keepRunId.
 * Uses key + savedAt only — does not materialize `results` blobs (MEMORY #17).
 */
export async function idbPruneBacktests(keepRunId, maxRuns = MAX_IDB_RUNS) {
  const prefix = `${terminalProfile()}:`;
  const meta = await withStore('readonly', (store) => new Promise((resolve) => {
    const out = [];
    let req;
    try {
      if (store.indexNames.contains('savedAt')) {
        req = store.index('savedAt').openCursor(null, 'prev');
      } else {
        req = store.openCursor();
      }
    } catch (_) {
      resolve([]);
      return;
    }
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) {
        resolve(out);
        return;
      }
      const row = cursor.value;
      if (row?.key?.startsWith(prefix)) {
        // Only keep prune metadata — never retain results reference.
        out.push({ key: row.key, savedAt: row.savedAt ?? 0 });
      }
      cursor.continue();
    };
    req.onerror = () => resolve(out);
  }));
  if (!Array.isArray(meta) || meta.length <= maxRuns) return;

  const mine = [...meta].sort((a, b) => (b.savedAt ?? 0) - (a.savedAt ?? 0));
  const keepKey = storageKey(keepRunId);
  const keep = new Set([keepKey]);
  for (const row of mine) {
    if (keep.size >= maxRuns) break;
    keep.add(row.key);
  }
  const victims = mine.filter((r) => !keep.has(r.key));
  if (victims.length === 0) return;

  await withStore('readwrite', (store) => {
    for (const row of victims) {
      store.delete(row.key);
    }
    return true;
  });
}

/** @internal */
export async function idbClearAllForTests() {
  if (typeof indexedDB === 'undefined') return;
  if (dbPromise) {
    try {
      const db = await dbPromise;
      db.close();
    } catch (_) {
      /* ignore */
    }
    dbPromise = null;
  }
  await new Promise((resolve) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => resolve();
    req.onblocked = () => resolve();
  });
}
