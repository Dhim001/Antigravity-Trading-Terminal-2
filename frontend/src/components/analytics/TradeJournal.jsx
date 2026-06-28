/**
 * TradeJournal — searchable, tagged trade annotations.
 */
import { useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { BookOpen, Plus, Trash2, Search } from 'lucide-react';
import { useJournal } from '@/hooks/useAnalytics';
import { compressScreenshotDataUrl } from '@/lib/analytics/helpers';
import { cn } from '@/lib/utils';

function JournalEditor({ entry, onSave, onCancel }) {
  const [form, setForm] = useState({
    id: entry?.id || '',
    symbol: entry?.symbol || '',
    tags: (entry?.tags || []).join(', '),
    note: entry?.note || '',
    lesson: entry?.lesson || '',
    trade_ref: entry?.trade_ref || '',
    order_id: entry?.order_id || '',
    bot_id: entry?.bot_id || '',
    screenshot_url: entry?.screenshot_url || '',
  });

  const onScreenshot = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const compressed = await compressScreenshotDataUrl(reader.result);
      setForm((f) => ({ ...f, screenshot_url: compressed }));
    };
    reader.readAsDataURL(file);
  };

  const submit = () => {
    onSave({
      ...form,
      tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
    });
  };

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border/50 bg-muted/20 p-3">
      <div className="grid grid-cols-2 gap-2">
        <Input placeholder="Symbol" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className="h-8 text-xs" />
        <Input placeholder="Tags (comma-separated)" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} className="h-8 text-xs" />
      </div>
      <Textarea placeholder="Trade notes…" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="min-h-[60px] text-xs" />
      <Textarea placeholder="Lessons learned…" value={form.lesson} onChange={(e) => setForm({ ...form, lesson: e.target.value })} className="min-h-[48px] text-xs" />
      <div className="flex flex-wrap items-center gap-2">
        <label className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
          <input type="file" accept="image/*" className="hidden" onChange={onScreenshot} />
          Attach screenshot
        </label>
        {form.screenshot_url && (
          <img src={form.screenshot_url} alt="" className="h-12 w-20 rounded object-cover" />
        )}
        <div className="ml-auto flex gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button size="sm" onClick={submit}>Save</Button>
        </div>
      </div>
    </div>
  );
}

export default function TradeJournal({ className = '', seedEntry = null, enabled = true }) {
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const { entries, saveEntry, deleteEntry } = useJournal(
    { query: query || undefined, limit: 100 },
    { enabled },
  );

  const filtered = useMemo(() => {
    if (!query.trim()) return entries;
    const q = query.toLowerCase();
    return entries.filter((e) =>
      (e.note || '').toLowerCase().includes(q)
      || (e.lesson || '').toLowerCase().includes(q)
      || (e.symbol || '').toLowerCase().includes(q)
      || (e.tags || []).some((t) => t.toLowerCase().includes(q)),
    );
  }, [entries, query]);

  const startNew = () => {
    setEditing(seedEntry || {});
    setShowNew(true);
  };

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div className="flex items-center gap-2">
        <BookOpen size={14} className="text-muted-foreground" />
        <span className="text-xs font-semibold">Trade Journal</span>
        <div className="relative ml-auto flex-1 max-w-[200px]">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-7 pl-7 text-xs"
          />
        </div>
        <Button variant="outline" size="sm" onClick={startNew}>
          <Plus size={12} /> New
        </Button>
      </div>

      {showNew && (
        <JournalEditor
          entry={editing}
          onSave={(entry) => { saveEntry(entry); setShowNew(false); setEditing(null); }}
          onCancel={() => { setShowNew(false); setEditing(null); }}
        />
      )}

      <div className="max-h-[280px] space-y-2 overflow-auto">
        {filtered.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">No journal entries yet</p>
        ) : filtered.map((entry) => (
          <div key={entry.id} className="rounded-md border border-border/40 p-2 text-xs">
            <div className="mb-1 flex items-center gap-2">
              <span className="font-semibold num-mono">{entry.symbol || '—'}</span>
              {(entry.tags || []).map((t) => (
                <Badge key={t} variant="secondary" className="h-4 px-1 text-[0.6rem]">{t}</Badge>
              ))}
              <Button variant="ghost" size="icon" className="ml-auto h-6 w-6" onClick={() => deleteEntry(entry.id)}>
                <Trash2 size={12} />
              </Button>
            </div>
            {entry.note && <p className="text-foreground/90">{entry.note}</p>}
            {entry.lesson && <p className="mt-1 text-muted-foreground italic">{entry.lesson}</p>}
            {entry.screenshot_url && (
              <img src={entry.screenshot_url} alt="" className="mt-2 max-h-24 rounded object-contain" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
