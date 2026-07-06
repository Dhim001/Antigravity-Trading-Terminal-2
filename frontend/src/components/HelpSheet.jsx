import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

const GLOSSARY = [
  { term: 'Deep reasoning', def: 'On-demand LLM enrichment (summary + risk notes). Metadata only — never changes BUY/SELL signal.' },
  { term: 'Chart vision', def: 'Optional LLM description of chart structure from a captured PNG (1H/4H). Not a trading signal.' },
  { term: 'Narrative vs deep reasoning', def: 'Narrative is auto-generated on analyze; deep reasoning is manual and adds structured risk critique from sub-reports.' },
  { term: 'Chart Analyst', def: 'Rule-based signal engine on closed bars. Optional LLM adds narrative only — never changes BUY/SELL.' },
  { term: 'sub_reports', def: 'Trend, momentum, and risk breakdown inside each insight (v2 envelope).' },
  { term: 'Scanner', def: 'Batch-ranks your watchlist by analyst score. Filter client-side after one scan.' },
  { term: 'HITL', def: 'Human-in-the-loop — preview an order from an insight before prefilling the ticket.' },
  { term: 'CHART_AGENT', def: 'Bot strategy that trades on Chart Analyst signals at bar close.' },
  { term: 'REST fallback', def: 'When WebSocket is down, the UI loads snapshots via HTTP and shows a stale-data banner.' },
  { term: 'Backtest sweep', def: 'Grid search over strategy parameters in Backtest Lab Optimizer tab — pick an objective (PnL, Sharpe, or profit factor) and optional min-trades filter.' },
  { term: 'Walk-forward', def: 'Out-of-sample validation in Backtest Lab Optimizer: single 70/30 split by default, or rolling mode (2–5 folds) that optimizes on each in-sample slice and aggregates OOS metrics.' },
  { term: 'Research mode', def: 'Backtest sim_mode that allows short positions (SELL opens shorts) without live risk gates — use for hypothesis testing only.' },
  { term: 'PDF export', def: 'Download a formatted backtest report (metrics, price chart with trade markers, equity curve, trade log) from Backtest Lab via the PDF button.' },
  { term: 'Persistent backtest jobs', def: 'Long runs are stored server-side; after refresh or restart the UI resumes polling the active job automatically.' },
  { term: 'Multi-chart link groups', def: 'In multi-chart view, link All syncs symbol/timeframe across panes; Focused links only the active pane you change.' },
  { term: 'VWAP session', def: 'Volume-weighted average price indicator resets at UTC midnight each day — one session per calendar day.' },
];

const WORKFLOWS = [
  { title: 'Day trade', steps: ['⌘K pick symbol', 'Read analyst badge', 'Preview order → ticket', 'Submit with pre-trade check'] },
  { title: 'LLM enrichment', steps: ['Expand an Analyst history row', 'Deep reasoning → summary + risk (signal unchanged)', 'Optional chart vision on 1H/4H captures', 'Requires AGENT_LLM_ENABLED + provider in Settings'] },
  { title: 'Scan opportunities', steps: ['⌘I Insights Hub → Scanner', 'Scan watchlist → click row for Analyst', 'Preview order from Scanner or Analyst row', 'Deploy CHART_AGENT bot from Analyst footer'] },
  { title: 'Bot ops', steps: ['⌘B Algo tab', 'Deploy CHART_AGENT or MACD_RSI', 'Bot logs → Why? links', 'Drawer → Why we entered on trades'] },
  { title: 'Backtest & optimize', steps: ['Algo tab → Run backtest or OPTIMIZE', 'Backtest Lab: Results | Optimizer | Jobs tabs', 'Pick objective + min trades in Optimizer', 'Export CSV/PDF from results'] },
  { title: 'Research (shorts)', steps: ['Algo tab → Sim mode: Research', 'Run backtest — SELL signals open shorts', 'Review equity in Backtest Lab', 'Switch to Live-aligned before deploy'] },
];

export default function HelpSheet({ open, onOpenChange }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="help-sheet sm:max-w-lg max-h-[85vh] overflow-hidden flex flex-col gap-0 p-0">
        <DialogHeader className="help-sheet__header">
          <DialogTitle className="help-sheet__title">Help & glossary</DialogTitle>
          <DialogDescription className="help-sheet__description">
            Quick reference for terminal features and workflows.
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="workflows" className="help-sheet__tabs min-h-0 flex-1 flex flex-col">
          <TabsList className="help-sheet__tablist shrink-0">
            <TabsTrigger value="workflows" className="help-sheet__tab">Workflows</TabsTrigger>
            <TabsTrigger value="glossary" className="help-sheet__tab">Glossary</TabsTrigger>
            <TabsTrigger value="shortcuts" className="help-sheet__tab">Shortcuts</TabsTrigger>
          </TabsList>

          <TabsContent value="workflows" className="help-sheet__panel mt-0">
            <div className="help-sheet__panel-scroll">
              {WORKFLOWS.map((w) => (
                <article key={w.title} className="help-sheet__workflow-card">
                  <h4 className="help-sheet__workflow-title">{w.title}</h4>
                  <ol className="help-sheet__workflow-steps">
                    {w.steps.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                </article>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="glossary" className="help-sheet__panel mt-0">
            <dl className="help-sheet__glossary help-sheet__panel-scroll">
              {GLOSSARY.map((g) => (
                <div key={g.term} className="help-sheet__glossary-item">
                  <dt className="help-sheet__glossary-term">{g.term}</dt>
                  <dd className="help-sheet__glossary-def">{g.def}</dd>
                </div>
              ))}
            </dl>
          </TabsContent>

          <TabsContent value="shortcuts" className="help-sheet__panel mt-0">
            <ul className="help-sheet__shortcuts help-sheet__panel-scroll">
              {[
                ['⌘K', 'Command palette'],
                ['⌘1 / ⌘2', 'Single / multi chart'],
                ['⌘B', 'Algo bot tab'],
                ['⌘I', 'Insights Hub (scanner + analyst)'],
                ['⌘,', 'Preferences'],
                ['?', 'Keyboard shortcuts sheet'],
              ].map(([k, a]) => (
                <li key={k} className="help-sheet__shortcut-row">
                  <span className="help-sheet__shortcut-label">{a}</span>
                  <kbd className="help-sheet__kbd">{k}</kbd>
                </li>
              ))}
            </ul>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
