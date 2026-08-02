import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearMlLabRequest,
  postMlLabRequest,
  takeMlLabRequest,
} from './mlLabRequests';

/** Minimal window/localStorage/CustomEvent stubs for the node test env. */
function makeWindowStub() {
  const listeners = new Map();
  const store = new Map();
  return {
    addEventListener: (type, fn) => {
      listeners.set(type, [...(listeners.get(type) || []), fn]);
    },
    removeEventListener: (type, fn) => {
      listeners.set(type, (listeners.get(type) || []).filter((f) => f !== fn));
    },
    dispatchEvent: (evt) => {
      for (const fn of listeners.get(evt.type) || []) fn(evt);
      return true;
    },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    location: { search: '' },
  };
}

class FakeCustomEvent {
  constructor(type, opts = {}) {
    this.type = type;
    this.detail = opts.detail;
  }
}

describe('mlLabRequests mailbox', () => {
  beforeEach(() => {
    vi.stubGlobal('window', makeWindowStub());
    vi.stubGlobal('CustomEvent', FakeCustomEvent);
    clearMlLabRequest();
  });

  afterEach(() => {
    clearMlLabRequest();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('dispatches a live CustomEvent and stores the pending request', () => {
    const seen = [];
    const listener = (e) => seen.push(e.detail);
    window.addEventListener('ml-lab-run-pipeline', listener);
    postMlLabRequest('ml-lab-run-pipeline', { strategy: 'LSTM_DIRECTION', mode: 'full' });
    window.removeEventListener('ml-lab-run-pipeline', listener);

    expect(seen).toHaveLength(1);
    expect(seen[0].strategy).toBe('LSTM_DIRECTION');

    const req = takeMlLabRequest(['ml-lab-run-pipeline']);
    expect(req?.detail?.strategy).toBe('LSTM_DIRECTION');
    // Consumed — a second take returns null.
    expect(takeMlLabRequest(['ml-lab-run-pipeline'])).toBeNull();
  });

  it('keeps the request when a live listener cleared nothing (Lab unmounted)', () => {
    postMlLabRequest('ml-lab-open-batch', { scope: 'stale' });
    const req = takeMlLabRequest(['ml-lab-open-batch', 'ml-lab-retrain']);
    expect(req?.type).toBe('ml-lab-open-batch');
    expect(req?.detail?.scope).toBe('stale');
  });

  it('does not return requests of a different type', () => {
    postMlLabRequest('ml-lab-retrain', { strategy: 'ML_SIGNAL_BOOST' });
    expect(takeMlLabRequest(['ml-lab-run-pipeline'])).toBeNull();
    // Still pending for the matching type.
    expect(takeMlLabRequest(['ml-lab-retrain'])?.type).toBe('ml-lab-retrain');
  });

  it('clearMlLabRequest drops the pending request (live handler processed it)', () => {
    postMlLabRequest('ml-lab-run-pipeline', { strategy: 'X' });
    clearMlLabRequest('ml-lab-run-pipeline');
    expect(takeMlLabRequest(['ml-lab-run-pipeline'])).toBeNull();
  });

  it('expires stale requests past the TTL', () => {
    vi.useFakeTimers();
    postMlLabRequest('ml-lab-run-pipeline', { strategy: 'X' });
    vi.advanceTimersByTime(16_000);
    expect(takeMlLabRequest(['ml-lab-run-pipeline'])).toBeNull();
  });

  it('a newer post overwrites an older pending request', () => {
    postMlLabRequest('ml-lab-open-batch', { scope: 'all' });
    postMlLabRequest('ml-lab-open-batch', { scope: 'untrained' });
    expect(takeMlLabRequest(['ml-lab-open-batch'])?.detail?.scope).toBe('untrained');
  });

  it('drains a persisted request from localStorage (cross-window realm)', () => {
    // Simulate a second realm: no module-level pending, only the LS copy.
    window.localStorage.setItem('ml-lab-request:pending', JSON.stringify({
      type: 'ml-lab-run-pipeline',
      detail: { strategy: 'TCN_MULTI_HORIZON' },
      ts: Date.now(),
    }));
    const req = takeMlLabRequest(['ml-lab-run-pipeline']);
    expect(req?.detail?.strategy).toBe('TCN_MULTI_HORIZON');
    expect(window.localStorage.getItem('ml-lab-request:pending')).toBeNull();
  });
});
