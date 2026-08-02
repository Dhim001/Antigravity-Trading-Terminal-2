/**
 * Mailbox for ML Lab requests (run-pipeline / open-batch / retrain).
 *
 * Dispatchers open the Lab dock and fire a window CustomEvent in the same tick.
 * The Lab panel unmounts after being hidden (MountWhenVisible keep-alive), so a
 * remounting Lab misses the synchronous event — leaving pipeline runs stuck at
 * TRAINING or batch/retrain requests silently dropped. Posting through this
 * mailbox lets a freshly-mounted Lab drain the last pending request on mount.
 *
 * The request is also persisted to localStorage and broadcast on the ML Lab
 * standalone channel so a Lab detached into its own window (a separate JS
 * realm that window events cannot reach) still receives it.
 */
import { broadcastStandaloneEvent } from './standalonePanels';

/** Requests older than this are treated as stale (e.g. user navigated away). */
const REQUEST_TTL_MS = 15_000;
const LS_KEY = 'ml-lab-request:pending';

let pending = null;

function persist(req) {
  try {
    if (req) window.localStorage.setItem(LS_KEY, JSON.stringify(req));
    else window.localStorage.removeItem(LS_KEY);
  } catch {
    /* ignore */
  }
}

function readStoredRequest() {
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const req = JSON.parse(raw);
    return req?.type ? req : null;
  } catch {
    return null;
  }
}

/**
 * Store the request and notify a live Lab (if mounted in this window) plus a
 * detached standalone Lab (via the ml-lab BroadcastChannel).
 * @param {'ml-lab-run-pipeline'|'ml-lab-open-batch'|'ml-lab-retrain'} type
 * @param {object} [detail]
 */
export function postMlLabRequest(type, detail = {}) {
  pending = { type, detail, ts: Date.now() };
  persist(pending);
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(new CustomEvent(type, { detail }));
    } catch {
      /* ignore — non-DOM realm */
    }
  }
  try {
    broadcastStandaloneEvent('ml-lab', 'ml-lab-request', { requestType: type, detail });
  } catch {
    /* ignore */
  }
}

/**
 * Read and clear the pending request when it matches one of `types`.
 * Falls back to the localStorage copy so detached windows can drain too.
 * Returns null when nothing is pending or the request is stale.
 * @param {string[]} [types] — restrict to these event types
 * @returns {{ type: string, detail: object, ts: number } | null}
 */
export function takeMlLabRequest(types) {
  const req = pending || readStoredRequest();
  if (!req) return null;
  if (Date.now() - req.ts > REQUEST_TTL_MS) {
    pending = null;
    persist(null);
    return null;
  }
  if (types && !types.includes(req.type)) return null;
  pending = null;
  persist(null);
  return req;
}

/** Clear without consuming (live handlers use this after processing an event). */
export function clearMlLabRequest(type) {
  if (pending && (!type || pending.type === type)) pending = null;
  // Always drop the persisted copy when it matches — another realm may have
  // written it while this realm had nothing pending.
  const stored = readStoredRequest();
  if (stored && (!type || stored.type === type)) persist(null);
}
