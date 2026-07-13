/**
 * TRADE_COPILOT — conversational dock panel for the trading terminal.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MessageSquare, Send, Loader2, Trash2, Check, X, Mic, ShieldAlert, Leaf, RefreshCcw } from 'lucide-react';
import { toast } from 'sonner';
import { useStore } from '../../store/useStore';
import {
  copilotChat,
  copilotConfirm,
  copilotCancel,
  fetchCopilotHistory,
  clearCopilotSession,
} from '../../api/endpoints';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { DockScrollPanel } from '../WidgetShell';
import { cn } from '@/lib/utils';

const SESSION_KEY = 'trade_copilot_session_id';

const QUICK_PROMPTS = [
  'How are my bots doing?',
  'What market is ETHUSDT in on 5m?',
  'Portfolio status',
];

function loadSessionId() {
  try {
    return localStorage.getItem(SESSION_KEY) || null;
  } catch {
    return null;
  }
}

function saveSessionId(id) {
  try {
    if (id) localStorage.setItem(SESSION_KEY, id);
  } catch {
    /* ignore */
  }
}

function renderInline(text) {
  const nodes = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|_([^_]+)_)/g;
  let last = 0;
  let m;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] != null) {
      nodes.push(
        <strong key={`b${key++}`} className="font-semibold text-foreground">
          {m[2]}
        </strong>,
      );
    } else if (m[3] != null) {
      nodes.push(
        <code key={`c${key++}`} className="copilot-panel__code">
          {m[3]}
        </code>,
      );
    } else if (m[4] != null) {
      nodes.push(
        <em key={`i${key++}`} className="text-muted-foreground not-italic text-[0.92em]">
          {m[4]}
        </em>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function RichReply({ text }) {
  const lines = String(text || '').split('\n');
  return (
    <div className="copilot-panel__rich">
      {lines.map((line, i) => {
        const trimmed = line.trimStart();
        if (!trimmed) {
          return <div key={i} className="h-1" />;
        }
        if (trimmed.startsWith('- ')) {
          return (
            <div key={i} className="copilot-panel__bullet">
              <span className="copilot-panel__bullet-mark" aria-hidden>
                •
              </span>
              <span className="min-w-0 flex-1">{renderInline(trimmed.slice(2))}</span>
            </div>
          );
        }
        const numbered = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (numbered) {
          return (
            <div key={i} className="copilot-panel__bullet">
              <span className="copilot-panel__num">{numbered[1]}.</span>
              <span className="min-w-0 flex-1">{renderInline(numbered[2])}</span>
            </div>
          );
        }
        return (
          <div
            key={i}
            className={cn(trimmed.startsWith('  ') && 'pl-3 text-[0.9em] text-muted-foreground')}
          >
            {renderInline(line)}
          </div>
        );
      })}
    </div>
  );
}

function MessageBubble({ msg, onConfirm, onCancel, confirming }) {
  const isUser = msg.role === 'user';
  const pendingId = msg.pending_id || msg.payload?.pending_id;
  const pendingAction = msg.pending_action || msg.payload?.pending_action;
  const needsConfirm = Boolean(pendingId && pendingAction && msg.role === 'assistant');

  return (
    <div className={cn('copilot-panel__row', isUser ? 'copilot-panel__row--user' : 'copilot-panel__row--bot')}>
      <div className={cn('copilot-panel__bubble', isUser ? 'copilot-panel__bubble--user' : 'copilot-panel__bubble--bot')}>
        {!isUser && (
          <div className="flex items-center gap-2 mb-1.5">
            {msg.source_agent === 'RiskSentinel' && <ShieldAlert className="size-4 text-red-500" title="Risk Sentinel" />}
            {msg.source_agent === 'AlphaDecay' && <Leaf className="size-4 text-green-500" title="Alpha Decay" />}
            {msg.source_agent === 'RegimeRotation' && <RefreshCcw className="size-4 text-blue-500" title="Regime Rotation" />}
            
            {msg.intent && !msg.source_agent && (
              <Badge variant="outline" className="copilot-panel__intent m-0">
                {msg.intent}
              </Badge>
            )}
            {msg.source_agent && (
              <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/80">
                {msg.source_agent}
              </span>
            )}
          </div>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap">{msg.content}</div>
        ) : (
          <RichReply text={msg.content} />
        )}
        {needsConfirm && (
          <div className="copilot-panel__confirm">
            <Button
              size="sm"
              variant="default"
              disabled={confirming}
              onClick={() => onConfirm?.(pendingId)}
              className="h-7 gap-1"
            >
              {confirming ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={confirming}
              onClick={() => onCancel?.(pendingId)}
              className="h-7 gap-1"
            >
              <X className="size-3.5" />
              Cancel
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function CopilotTab() {
  const activeSymbol = useStore((s) => s.activeSymbol);
  const [sessionId, setSessionId] = useState(() => loadSessionId());
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  
  const copilotMessages = useStore((s) => s.copilotMessages);

  useEffect(() => {
    if (copilotMessages && copilotMessages.length > 0) {
      setMessages((prev) => [...prev, ...copilotMessages]);
      useStore.getState().clearCopilotMessages();
    }
  }, [copilotMessages]);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    });
  }, []);

  useEffect(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results)
          .map((result) => result[0].transcript)
          .join('');
        setInput(transcript);
      };
      recognition.onend = () => {
        setListening(false);
      };
      recognition.onerror = (event) => {
        console.error('Speech recognition error', event.error);
        setListening(false);
      };
      recognitionRef.current = recognition;
    }
  }, []);

  const toggleListening = () => {
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
    } else {
      if (!recognitionRef.current) {
        toast.error('Voice input is not supported in this browser.');
        return;
      }
      setInput('');
      recognitionRef.current.start();
      setListening(true);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!sessionId) return;
      try {
        const res = await fetchCopilotHistory(sessionId);
        if (!cancelled && res?.ok && Array.isArray(res.messages)) {
          setMessages(
            res.messages.map((m) => ({
              id: m.id,
              role: m.role,
              content: m.content,
              intent: m.intent,
              payload: m.payload,
              pending_id: m.payload?.pending_id,
              pending_action: m.payload?.pending_action,
            })),
          );
        }
      } catch {
        /* first load / empty table */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, busy, scrollToBottom]);

  const send = useCallback(async (overrideText) => {
    const text = (overrideText ?? input).trim();
    if (!text || busy) return;
    if (overrideText == null) setInput('');
    else setInput('');
    setBusy(true);
    const optimistic = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: text,
    };
    setMessages((prev) => [...prev, optimistic]);
    try {
      const res = await copilotChat({
        message: text,
        session_id: sessionId,
        active_symbol: activeSymbol,
      });
      if (!res?.ok) {
        toast.error(res?.error || 'Copilot failed');
        setMessages((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            role: 'assistant',
            content: res?.error || 'Something went wrong.',
            intent: 'error',
          },
        ]);
        return;
      }
      if (res.session_id) {
        setSessionId(res.session_id);
        saveSessionId(res.session_id);
      }
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== optimistic.id),
        { id: `u-${Date.now()}`, role: 'user', content: text },
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: res.reply || '(empty reply)',
          intent: res.intent,
          pending_id: res.pending_id,
          pending_action: res.pending_action,
          tool_results: res.tool_results,
        },
      ]);
    } catch (err) {
      toast.error(err?.message || 'Copilot request failed');
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'assistant',
          content: err?.message || 'Request failed.',
          intent: 'error',
        },
      ]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }, [activeSymbol, busy, input, sessionId]);

  const onConfirm = useCallback(async (pendingId) => {
    if (!pendingId || confirming) return;
    setConfirming(true);
    try {
      const res = await copilotConfirm(pendingId);
      if (!res?.ok) {
        toast.error(res?.error || 'Confirm failed');
        return;
      }
      toast.success('Action confirmed');
      setMessages((prev) => [
        ...prev,
        {
          id: `ok-${Date.now()}`,
          role: 'assistant',
          content: `Confirmed. ${JSON.stringify(res.result || {}, null, 0)}`,
          intent: 'action',
        },
      ]);
    } catch (err) {
      toast.error(err?.message || 'Confirm failed');
    } finally {
      setConfirming(false);
    }
  }, [confirming]);

  const onCancel = useCallback(async (pendingId) => {
    if (!pendingId || confirming) return;
    setConfirming(true);
    try {
      await copilotCancel(pendingId);
      toast.message('Action cancelled');
      setMessages((prev) => [
        ...prev,
        {
          id: `cancel-${Date.now()}`,
          role: 'assistant',
          content: 'Cancelled.',
          intent: 'action',
        },
      ]);
    } catch (err) {
      toast.error(err?.message || 'Cancel failed');
    } finally {
      setConfirming(false);
    }
  }, [confirming]);

  const clearChat = useCallback(async () => {
    try {
      if (sessionId) await clearCopilotSession(sessionId);
    } catch {
      /* ignore */
    }
    const next = crypto.randomUUID?.() || `sess-${Date.now()}`;
    setSessionId(next);
    saveSessionId(next);
    setMessages([]);
  }, [sessionId]);

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="dock-panel-tab copilot-panel flex h-full min-h-0 flex-col">
      <header className="dock-panel-tab__toolbar">
        <div className="dock-panel-tab__toolbar-lead">
          <div className="dock-panel-tab__toolbar-icon" aria-hidden>
            <MessageSquare className="size-3.5" />
          </div>
          <div className="dock-panel-tab__toolbar-copy">
            <span className="dock-panel-tab__toolbar-title">Copilot</span>
            <span className="dock-panel-tab__toolbar-subtitle">
              {activeSymbol ? (
                <>
                  Context <span className="num-mono text-foreground/80">{activeSymbol}</span>
                </>
              ) : (
                'Ask · analyze · deploy'
              )}
            </span>
          </div>
        </div>
        <div className="dock-panel-tab__toolbar-actions flex items-center gap-1.5">
          {activeSymbol && (
            <Badge variant="secondary" className="num-mono h-5 px-1.5 text-[10px]">
              {activeSymbol}
            </Badge>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1 px-2 text-xs"
            onClick={clearChat}
            title="Clear conversation"
          >
            <Trash2 className="size-3.5" />
            <span className="copilot-panel__clear-label">Clear</span>
          </Button>
        </div>
      </header>

      <DockScrollPanel className="copilot-panel__scroll min-h-0 flex-1">
        <div className="copilot-panel__thread">
          {messages.length === 0 && !busy && (
            <div className="copilot-panel__empty">
              <p className="copilot-panel__empty-title">Terminal assistant</p>
              <p className="copilot-panel__empty-copy">
                Portfolio, bots, regime analysis, and deploy/pause with confirmation.
              </p>
              <div className="copilot-panel__chips">
                {QUICK_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="copilot-panel__chip"
                    disabled={busy}
                    onClick={() => send(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="copilot-panel__messages">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                msg={msg}
                onConfirm={onConfirm}
                onCancel={onCancel}
                confirming={confirming}
              />
            ))}
            {busy && (
              <div className="copilot-panel__thinking">
                <Loader2 className="size-3.5 animate-spin" />
                Thinking…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>
      </DockScrollPanel>

      <div className="copilot-panel__composer">
        <div className="copilot-panel__composer-inner">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about bots, regime, or deploy…"
            className="copilot-panel__input min-h-[40px] max-h-24 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            rows={1}
            disabled={busy}
          />
          <Button
            size="icon"
            variant={listening ? 'destructive' : 'ghost'}
            className="size-9 shrink-0"
            onClick={toggleListening}
            disabled={busy || (!listening && !!input.trim())}
            title="Voice input"
          >
            <Mic className={cn('size-3.5', listening && 'animate-pulse')} />
          </Button>
          <Button
            size="icon"
            className="copilot-panel__send size-9 shrink-0"
            onClick={() => send()}
            disabled={busy || !input.trim()}
            title="Send"
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-3.5" />}
          </Button>
        </div>
        <p className="copilot-panel__hint">Enter to send · Shift+Enter for newline</p>
      </div>
    </div>
  );
}
