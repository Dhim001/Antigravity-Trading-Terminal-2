/**
 * Export backtest report as printable PDF (browser Save as PDF).
 */

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function exportBacktestPdf({ results, symbol, strategy, days, timeframe, trades = [] }) {
  if (!results) return;
  const summary = results.summary ?? {};
  const meta = results.meta ?? {};
  const rows = (trades.length ? trades : results.trades ?? []).slice(0, 200);

  const statRows = [
    ['Total PnL', summary.total_pnl != null ? `$${Number(summary.total_pnl).toFixed(2)}` : '—'],
    ['Win rate', summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'],
    ['Trades', String(summary.total_trades ?? results.trade_count ?? '—')],
    ['Max drawdown', summary.max_drawdown != null ? `${Number(summary.max_drawdown).toFixed(2)}%` : '—'],
    ['Sharpe', summary.sharpe_ratio ?? '—'],
    ['Sortino', summary.sortino_ratio ?? '—'],
    ['Profit factor', summary.profit_factor ?? '—'],
    ['Sim mode', results.sim_mode ?? 'live_aligned'],
  ];

  const tradeRows = rows.map((t) => `
    <tr>
      <td>${escHtml(t.time)}</td>
      <td>${escHtml(t.side)}${t.is_exit ? ' exit' : ''}</td>
      <td>${escHtml(t.quantity)}</td>
      <td>${escHtml(t.price)}</td>
      <td>${t.pnl != null ? escHtml(Number(t.pnl).toFixed(2)) : '—'}</td>
      <td>${escHtml(t.reason ?? '')}</td>
    </tr>
  `).join('');

  const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Backtest ${escHtml(symbol)} ${escHtml(strategy)}</title>
  <style>
    body { font-family: system-ui, sans-serif; font-size: 11px; color: #111; margin: 24px; }
    h1 { font-size: 16px; margin: 0 0 4px; }
    .meta { color: #555; margin-bottom: 16px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; }
    th { background: #f0f0f0; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }
    .stat { border: 1px solid #ddd; padding: 8px; border-radius: 4px; }
    .stat label { display: block; font-size: 9px; color: #666; text-transform: uppercase; }
    .stat strong { font-size: 13px; }
    @media print { body { margin: 12mm; } }
  </style>
</head>
<body>
  <h1>Backtest Report — ${escHtml(symbol)} · ${escHtml(strategy)}</h1>
  <p class="meta">${escHtml(days)}-day · ${escHtml(timeframe)} · ${escHtml(meta.count ?? '')} bars · Run ${escHtml(results.run_id ?? '').slice(0, 8)}</p>
  <div class="stats">
    ${statRows.map(([k, v]) => `<div class="stat"><label>${escHtml(k)}</label><strong>${escHtml(v)}</strong></div>`).join('')}
  </div>
  <h2>Trades (${rows.length}${results.trades_total > rows.length ? ` of ${results.trades_total}` : ''})</h2>
  <table>
    <thead><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>PnL</th><th>Reason</th></tr></thead>
    <tbody>${tradeRows || '<tr><td colspan="6">No trades</td></tr>'}</tbody>
  </table>
  <script>window.onload = () => { window.print(); };</script>
</body>
</html>`;

  const win = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
  if (!win) return;
  win.document.write(html);
  win.document.close();
}
