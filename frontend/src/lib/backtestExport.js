/**
 * Export backtest report as downloadable PDF.
 */

import {
  buildChartSectionHtml,
  resolveBacktestPdfCandles,
} from './backtestPdfChart';

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function fmtTradeTime(sec) {
  if (sec == null || sec === '') return '—';
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return '—';
  const ms = n > 1e11 ? n : n * 1000;
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return String(sec);
  }
}

function resolveSummary(results) {
  const s = results?.summary ?? {};
  return {
    total_pnl: s.total_pnl ?? results?.total_pnl,
    win_rate: s.win_rate ?? results?.win_rate,
    total_trades: s.total_trades ?? results?.trade_count,
    max_drawdown: s.max_drawdown ?? results?.max_drawdown,
    sharpe_ratio: s.sharpe_ratio,
    sortino_ratio: s.sortino_ratio,
    profit_factor: s.profit_factor,
    return_pct: s.return_pct,
    expectancy: s.expectancy,
  };
}

function buildReportHtml({
  results,
  symbol,
  strategy,
  days,
  timeframe,
  trades = [],
  chartSection = '',
}) {
  const summary = resolveSummary(results);
  const meta = results?.meta ?? {};
  const rows = (trades.length ? trades : results?.trades ?? []).slice(0, 250);
  const reasoning = results?.reasoning;
  const reasoningRows = (reasoning?.trades ?? []).slice(0, 50);

  const runKind = reasoning?.run_kind
    ?? (results?.walk_forward || meta?.walk_forward
      ? 'walk_forward'
      : results?.sweep
        ? 'sweep'
        : 'single');

  const runKindLabel = reasoning?.run_kind_label
    ?? (runKind === 'walk_forward'
      ? 'Walk-forward OOS'
      : runKind === 'sweep'
        ? 'Parameter sweep (best config)'
        : 'Standard backtest');

  const statRows = [
    ['Total PnL', summary.total_pnl != null ? `$${Number(summary.total_pnl).toFixed(2)}` : '—'],
    ['Return', summary.return_pct != null ? `${Number(summary.return_pct).toFixed(2)}%` : '—'],
    ['Win rate', summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'],
    ['Trades', String(summary.total_trades ?? '—')],
    ['Max drawdown', summary.max_drawdown != null ? `${Number(summary.max_drawdown).toFixed(2)}%` : '—'],
    ['Sharpe', summary.sharpe_ratio != null ? Number(summary.sharpe_ratio).toFixed(2) : '—'],
    ['Sortino', summary.sortino_ratio != null ? Number(summary.sortino_ratio).toFixed(2) : '—'],
    ['Profit factor', summary.profit_factor != null ? Number(summary.profit_factor).toFixed(2) : '—'],
    ['Expectancy', summary.expectancy != null ? `$${Number(summary.expectancy).toFixed(2)}` : '—'],
    ['Sim mode', results?.sim_mode ?? 'live_aligned'],
    ['Run type', runKindLabel],
  ];

  const tradeRows = rows.map((t) => `
    <tr>
      <td>${escHtml(fmtTradeTime(t.time))}</td>
      <td>${escHtml(t.side)}${t.is_exit ? ' exit' : ''}</td>
      <td>${escHtml(t.quantity)}</td>
      <td>${t.price != null ? escHtml(Number(t.price).toFixed(4)) : '—'}</td>
      <td>${t.pnl != null ? escHtml(Number(t.pnl).toFixed(2)) : '—'}</td>
      <td>${escHtml(t.reason ?? (t.is_exit ? 'EXIT' : 'ENTRY'))}</td>
    </tr>
  `).join('');

  const reasoningSection = reasoningRows.length > 0 ? `
    <h2>LLM trade explanations (${reasoningRows.length})</h2>
    <p class="meta">${escHtml(reasoning?.scope ?? 'Post-hoc entry narration')}</p>
    <table>
      <thead><tr><th>Time</th><th>Side</th><th>Price</th><th>Explanation</th></tr></thead>
      <tbody>
        ${reasoningRows.map((r) => `
          <tr>
            <td>${escHtml(fmtTradeTime(r.bar_time ?? r.time))}</td>
            <td>${escHtml(r.side)}</td>
            <td>${r.price != null ? escHtml(Number(r.price).toFixed(4)) : '—'}</td>
            <td>${escHtml(r.narrative ?? '—')}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  ` : '';

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Backtest ${escHtml(symbol)} ${escHtml(strategy)}</title>
  <style>
    body { font-family: system-ui, sans-serif; font-size: 11px; color: #111; margin: 24px; }
    h1 { font-size: 16px; margin: 0 0 4px; }
    h2 { font-size: 13px; margin: 20px 0 6px; }
    .meta { color: #555; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; vertical-align: top; }
    th { background: #f0f0f0; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }
    .stat { border: 1px solid #ddd; padding: 8px; border-radius: 4px; }
    .stat label { display: block; font-size: 9px; color: #666; text-transform: uppercase; }
    .stat strong { font-size: 13px; }
    .charts { margin: 16px 0 20px; page-break-inside: avoid; }
    .charts h3 { font-size: 12px; margin: 0 0 6px; font-weight: 700; }
    .chart-wrap { border: 1px solid #ddd; border-radius: 6px; overflow: hidden; margin-bottom: 14px; background: #fff; }
    .chart-wrap--equity { margin-bottom: 0; }
    .chart-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 9px; color: #555; margin-bottom: 6px; }
    .chart-legend span { display: inline-flex; align-items: center; gap: 5px; }
    .lg { display: inline-block; width: 10px; height: 10px; }
    .lg-entry { background: #2563eb; clip-path: polygon(50% 100%, 0 0, 100% 0); }
    .lg-exit-win { background: #d97706; border-radius: 50%; }
    .lg-exit-loss { background: #dc2626; border-radius: 50%; }
    @media print {
      body { margin: 12mm; }
      h2 { page-break-before: auto; }
      tr { page-break-inside: avoid; }
    }
  </style>
</head>
<body>
  <h1>Backtest Report — ${escHtml(symbol)} · ${escHtml(strategy)}</h1>
  <p class="meta">${escHtml(days)}-day · ${escHtml(timeframe)} · ${escHtml(meta.count ?? '')} bars · Run ${escHtml(results?.run_id ?? '').slice(0, 8)}</p>
  <div class="stats">
    ${statRows.map(([k, v]) => `<div class="stat"><label>${escHtml(k)}</label><strong>${escHtml(v)}</strong></div>`).join('')}
  </div>
  ${chartSection}
  <h2>Trades (${rows.length}${results?.trades_total > rows.length ? ` of ${results.trades_total}` : ''})</h2>
  <table>
    <thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>PnL</th><th>Reason</th></tr></thead>
    <tbody>${tradeRows || '<tr><td colspan="6">No trades</td></tr>'}</tbody>
  </table>
  ${reasoningSection}
</body>
</html>`;
}

function backtestPdfFilename({ symbol, strategy, results }) {
  const sym = String(symbol ?? results?.meta?.symbol ?? 'sym').replace(/[^\w.-]+/g, '_');
  const strat = String(strategy ?? results?.meta?.strategy ?? 'strategy').replace(/[^\w.-]+/g, '_');
  const date = new Date().toISOString().slice(0, 10);
  return `backtest_${sym}_${strat}_${date}.pdf`;
}

async function buildBacktestReportHtml({
  results,
  symbol,
  strategy,
  days,
  timeframe,
  trades = [],
}) {
  const sym = symbol ?? results?.meta?.symbol;
  const tf = timeframe ?? results?.meta?.timeframe ?? '1m';
  const chartTrades = (trades.length ? trades : results?.trades ?? []);

  let chartSection = '';
  try {
    const { candles, bucketSecs } = await resolveBacktestPdfCandles(sym, results?.meta, tf);
    if (candles.length) {
      chartSection = buildChartSectionHtml({
        candles,
        trades: chartTrades,
        equityCurve: results?.equity_curve ?? [],
        bucketSecs,
        symbol: sym,
        timeframe: tf,
        escHtml,
      });
    }
  } catch (err) {
    console.warn('[exportBacktestPdf] chart section skipped:', err);
  }

  return buildReportHtml({
    results,
    symbol: sym,
    strategy,
    days,
    timeframe: tf,
    trades,
    chartSection,
  });
}

async function renderHtmlToPdf(html) {
  const [{ jsPDF }, { default: html2canvas }] = await Promise.all([
    import('jspdf'),
    import('html2canvas'),
  ]);

  const iframe = document.createElement('iframe');
  iframe.setAttribute('title', 'Backtest PDF render');
  iframe.style.cssText = 'position:fixed;left:-10000px;top:0;width:794px;border:0;visibility:hidden';
  document.body.appendChild(iframe);

  const doc = iframe.contentDocument ?? iframe.contentWindow?.document;
  if (!doc) {
    iframe.remove();
    throw new Error('Could not create PDF render frame');
  }

  doc.open();
  doc.write(html);
  doc.close();

  await new Promise((resolve) => setTimeout(resolve, 500));

  const body = doc.body;
  const canvas = await html2canvas(body, {
    scale: 2,
    useCORS: true,
    logging: false,
    backgroundColor: '#ffffff',
    width: body.scrollWidth,
    height: body.scrollHeight,
    windowWidth: body.scrollWidth,
    windowHeight: body.scrollHeight,
  });

  iframe.remove();

  const pdf = new jsPDF({ orientation: 'p', unit: 'mm', format: 'a4', compress: true });
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pageHeight = pdf.internal.pageSize.getHeight();
  const imgWidth = pageWidth;
  const imgHeight = (canvas.height * imgWidth) / canvas.width;
  const imgData = canvas.toDataURL('image/jpeg', 0.92);

  let heightLeft = imgHeight;
  let position = 0;

  pdf.addImage(imgData, 'JPEG', 0, position, imgWidth, imgHeight);
  heightLeft -= pageHeight;

  while (heightLeft > 0) {
    position = heightLeft - imgHeight;
    pdf.addPage();
    pdf.addImage(imgData, 'JPEG', 0, position, imgWidth, imgHeight);
    heightLeft -= pageHeight;
  }

  return pdf;
}

/**
 * Build report HTML and download as PDF.
 * @returns {Promise<{ ok: boolean, error?: string, filename?: string }>}
 */
export async function exportBacktestPdf({
  results,
  symbol,
  strategy,
  days,
  timeframe,
  trades = [],
}) {
  if (!results) {
    return { ok: false, error: 'No backtest results to export' };
  }

  try {
    const html = await buildBacktestReportHtml({
      results,
      symbol,
      strategy,
      days,
      timeframe,
      trades,
    });
    const pdf = await renderHtmlToPdf(html);
    const filename = backtestPdfFilename({ symbol, strategy, results });
    pdf.save(filename);
    return { ok: true, filename };
  } catch (err) {
    console.error('[exportBacktestPdf]', err);
    return { ok: false, error: err?.message || 'Could not generate PDF' };
  }
}

const SWEEP_CSV_COLUMNS = [
  'label', 'total_pnl', 'trade_count', 'win_rate', 'sharpe_ratio', 'profit_factor', 'max_drawdown',
];

function csvEscape(val) {
  const s = val == null ? '' : String(val);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/**
 * Export sweep result rows to CSV.
 */
export function exportSweepCsv({ results, symbol, strategy, objective = 'total_pnl' }) {
  const rows = results?.sweep?.results;
  if (!rows?.length) {
    return { ok: false, error: 'No sweep results to export' };
  }
  const header = [...SWEEP_CSV_COLUMNS, 'config_json'].join(',');
  const body = rows.map((row) => {
    const summary = row.summary ?? {};
    const vals = [
      row.label ?? '',
      row.total_pnl ?? summary.total_pnl ?? '',
      row.trade_count ?? summary.total_trades ?? '',
      summary.win_rate ?? '',
      summary.sharpe_ratio ?? '',
      summary.profit_factor ?? '',
      summary.max_drawdown ?? '',
      JSON.stringify(row.config ?? {}),
    ];
    return vals.map(csvEscape).join(',');
  });
  const blob = new Blob([header + '\n' + body.join('\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `sweep_${symbol}_${strategy}_${objective}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  return { ok: true };
}
