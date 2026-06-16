import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

const SHORTCUTS = [
  { keys: '⌘K / Ctrl+K', action: 'Open command palette' },
  { keys: '⌘1 / Ctrl+1', action: 'Single chart view' },
  { keys: '⌘2 / Ctrl+2', action: 'Multi-chart grid' },
  { keys: '⌘B / Ctrl+B', action: 'Algo bot tab' },
  { keys: '⌘I / Ctrl+I', action: 'Chart Analyst history tab' },
  { keys: '⌘, / Ctrl+,', action: 'Preferences' },
  { keys: '⌘[ / Ctrl+[', action: 'Toggle watchlist sidebar' },
  { keys: '?', action: 'This shortcuts sheet' },
  { keys: 'B / S', action: 'Focus Buy / Sell (order entry, when not typing)' },
];

export default function ShortcutsSheet({ open, onOpenChange }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Power-user bindings for navigation and trading workflows.
          </DialogDescription>
        </DialogHeader>
        <ul className="space-y-2 text-sm">
          {SHORTCUTS.map((row) => (
            <li key={row.keys} className="flex items-center justify-between gap-4 border-b border-border/40 py-1.5 last:border-0">
              <span className="text-muted-foreground">{row.action}</span>
              <kbd className="shrink-0 rounded border border-border bg-muted px-2 py-0.5 font-mono text-[0.65rem]">
                {row.keys}
              </kbd>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
