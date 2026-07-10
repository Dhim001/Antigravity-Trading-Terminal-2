/**
 * Typed OHLCV storage for 1m candle buffers — ~5× smaller than object[].
 * Exposes a lazily-built object view for chart consumers (updated in place on live ticks).
 */

export class CompactBarSeries {
  constructor(length = 0) {
    this.length = length;
    this.time = new Float64Array(length);
    this.open = new Float64Array(length);
    this.high = new Float64Array(length);
    this.low = new Float64Array(length);
    this.close = new Float64Array(length);
    this.volume = new Float64Array(length);
    /** @type {Array<{time:number,open:number,high:number,low:number,close:number,volume:number}>|null} */
    this._view = null;
  }

  static fromCandles(candles) {
    const n = candles?.length ?? 0;
    const s = new CompactBarSeries(n);
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      s.time[i] = c.time;
      s.open[i] = c.open;
      s.high[i] = c.high;
      s.low[i] = c.low;
      s.close[i] = c.close;
      s.volume[i] = c.volume ?? 0;
    }
    return s;
  }

  static isSeries(v) {
    return v instanceof CompactBarSeries;
  }

  _invalidateView() {
    this._view = null;
  }

  _ensureView() {
    if (this._view && this._view.length === this.length) return this._view;
    const view = new Array(this.length);
    for (let i = 0; i < this.length; i++) {
      view[i] = {
        time: this.time[i],
        open: this.open[i],
        high: this.high[i],
        low: this.low[i],
        close: this.close[i],
        volume: this.volume[i],
      };
    }
    this._view = view;
    return view;
  }

  /** Chart-facing candle array (cached, mutated in place on live updates). */
  toArray() {
    return this._ensureView();
  }

  getLast() {
    if (this.length === 0) return null;
    const i = this.length - 1;
    return {
      time: this.time[i],
      open: this.open[i],
      high: this.high[i],
      low: this.low[i],
      close: this.close[i],
      volume: this.volume[i],
    };
  }

  /** Last n bars as plain objects (for HT aggregation). */
  sliceTail(count) {
    const start = Math.max(0, this.length - count);
    const out = [];
    for (let i = start; i < this.length; i++) {
      out.push({
        time: this.time[i],
        open: this.open[i],
        high: this.high[i],
        low: this.low[i],
        close: this.close[i],
        volume: this.volume[i],
      });
    }
    return out;
  }

  some(fn) {
    const view = this._ensureView();
    for (let i = 0; i < view.length; i++) {
      if (fn(view[i], i)) return true;
    }
    return false;
  }

  replaceFrom(candles) {
    const n = candles.length;
    this._growTo(n);
    this.length = n;
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      this.time[i] = c.time;
      this.open[i] = c.open;
      this.high[i] = c.high;
      this.low[i] = c.low;
      this.close[i] = c.close;
      this.volume[i] = c.volume ?? 0;
    }
    this._invalidateView();
  }

  updateLast(bar) {
    if (this.length === 0) return;
    const i = this.length - 1;
    this.time[i] = bar.time;
    this.open[i] = bar.open;
    this.high[i] = bar.high;
    this.low[i] = bar.low;
    this.close[i] = bar.close;
    this.volume[i] = bar.volume ?? 0;
    const view = this._view;
    if (view && view[i]) {
      const v = view[i];
      v.time = bar.time;
      v.open = bar.open;
      v.high = bar.high;
      v.low = bar.low;
      v.close = bar.close;
      v.volume = bar.volume ?? 0;
    }
  }

  patchLastFromPrice(price) {
    if (this.length === 0) return false;
    const i = this.length - 1;
    const live = Number(price);
    const prevClose = this.close[i];
    const prevHigh = this.high[i];
    const prevLow = this.low[i];
    const high = Math.max(prevHigh, live);
    const low = Math.min(prevLow, live);
    if (prevClose === live && prevHigh === high && prevLow === low) return false;
    this.close[i] = live;
    this.high[i] = high;
    this.low[i] = low;
    const view = this._view;
    if (view && view[i]) {
      view[i].close = live;
      view[i].high = high;
      view[i].low = low;
    }
    return true;
  }

  push(bar) {
    const i = this.length;
    this._growTo(i + 1);
    this.length = i + 1;
    this.time[i] = bar.time;
    this.open[i] = bar.open;
    this.high[i] = bar.high;
    this.low[i] = bar.low;
    this.close[i] = bar.close;
    this.volume[i] = bar.volume ?? 0;
    if (this._view) {
      this._view.push({
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume ?? 0,
      });
    }
  }

  shift() {
    if (this.length === 0) return;
    if (this.length === 1) {
      this.length = 0;
      this._invalidateView();
      return;
    }
    for (let i = 0; i < this.length - 1; i++) {
      this.time[i] = this.time[i + 1];
      this.open[i] = this.open[i + 1];
      this.high[i] = this.high[i + 1];
      this.low[i] = this.low[i + 1];
      this.close[i] = this.close[i + 1];
      this.volume[i] = this.volume[i + 1];
    }
    this.length -= 1;
    if (this._view) {
      this._view.shift();
    } else {
      this._invalidateView();
    }
  }

  _growTo(capacity) {
    if (capacity <= this.time.length) return;
    const next = Math.max(capacity, Math.ceil(this.time.length * 1.5) || 64);
    const grow = (arr) => {
      const n = new Float64Array(next);
      n.set(arr.subarray(0, this.length));
      return n;
    };
    this.time = grow(this.time);
    this.open = grow(this.open);
    this.high = grow(this.high);
    this.low = grow(this.low);
    this.close = grow(this.close);
    this.volume = grow(this.volume);
  }

}

/** Coerce legacy object[] or CompactBarSeries to plain candles. */
export function materializeBars(buf) {
  if (!buf) return [];
  if (CompactBarSeries.isSeries(buf)) return buf.toArray();
  return buf;
}

export function barCount(buf) {
  if (!buf) return 0;
  return CompactBarSeries.isSeries(buf) ? buf.length : buf.length;
}
