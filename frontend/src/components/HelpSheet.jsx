import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

const GLOSSARY = [
  { term: 'Chart Analyst', def: 'Rule-based signal engine on closed bars. Optional LLM adds narrative only — never changes BUY/SELL.' },
  { term: 'sub_reports', def: 'Trend, momentum, and risk breakdown inside each insight (v2 envelope).' },
  { term: 'Scanner', def: 'Batch-ranks your watchlist by analyst score. Filter client-side after one scan.' },
  { term: 'HITL', def: 'Human-in-the-loop — preview an order from an insight before prefilling the ticket.' },
  { term: 'CHART_AGENT', def: 'Bot strategy that trades on Chart Analyst signals at bar close.' },
  { term: 'REST fallback', def: 'When WebSocket is down, the UI loads snapshots via HTTP and shows a stale-data banner.' },
];

const WORKFLOWS = [
  { title: 'Day trade', steps: ['⌘K pick symbol', 'Read analyst badge', 'Preview order → ticket', 'Submit with pre-trade check'] },
  { title: 'Scan opportunities', steps: ['Open Scanner dock tab', 'Scan watchlist', 'Click row → Analyst tab', 'Preview or deploy bot'] },
  { title: 'Bot ops', steps: ['⌘B Algo tab', 'Deploy CHART_AGENT or MACD_RSI', 'Bot logs → Why? links', 'Drawer → Why we entered on trades'] },
];

export default function HelpSheet({ open, onOpenChange }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>Help & glossary</DialogTitle>
          <DialogDescription>
            Quick reference for terminal features and workflows.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="workflows" className="min-h-0 flex-1 flex flex-col">
          <TabsList className="w-full shrink-0">
            <TabsTrigger value="workflows" className="flex-1">Workflows</TabsTrigger>
            <TabsTrigger value="glossary" className="flex-1">Glossary</TabsTrigger>
            <TabsTrigger value="shortcuts" className="flex-1">Shortcuts</TabsTrigger>
          </TabsList>
          <TabsContent value="workflows" className="mt-3 min-h-0 flex-1 overflow-y-auto space-y-4">
            {WORKFLOWS.map((w) => (
              <div key={w.title} className="rounded-md border border-border/60 p-3">
                <h4 className="mb-2 text-sm font-semibold">{w.title}</h4>
                <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
                  {w.steps.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
              </div>
            ))}
          </TabsContent>
          <TabsContent value="glossary" className="mt-3 min-h-0 flex-1 overflow-y-auto space-y-2">
            {GLOSSARY.map((g) => (
              <div key={g.term} className="border-b border-border/40 py-2 last:border-0">
                <dt className="text-sm font-semibold">{g.term}</dt>
                <dd className="mt-0.5 text-xs text-muted-foreground">{g.def}</dd>
              </div>
            ))}
          </TabsContent>
          <TabsContent value="shortcuts" className="mt-3 min-h-0 flex-1 overflow-y-auto">
            <ul className="space-y-2 text-sm">
              {[
                ['⌘K', 'Command palette'],
                ['⌘1 / ⌘2', 'Single / multi chart'],
                ['⌘B', 'Algo bot tab'],
                ['⌘I', 'Analyst tab'],
                ['⌘,', 'Preferences'],
                ['?', 'Keyboard shortcuts sheet'],
              ].map(([k, a]) => (
                <li key={k} className="flex justify-between gap-4 border-b border-border/40 py-1.5">
                  <span className="text-muted-foreground">{a}</span>
                  <kbd className="rounded border border-border bg-muted px-2 py-0.5 font-mono text-[0.65rem]">{k}</kbd>
                </li>
              ))}
            </ul>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
