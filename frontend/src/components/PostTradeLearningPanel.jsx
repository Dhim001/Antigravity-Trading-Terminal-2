import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Layers,
  RefreshCw,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { fetchPostTradeStatus, trainCopilotIntentLora } from '../api/endpoints';

const POLL_MS = 30000;

function Chip({ ok, label, title }) {
  return (
    <Badge
      variant={ok ? 'secondary' : 'outline'}
      className={ok ? 'text-trading-up border-trading-up/30' : 'text-muted-foreground'}
      title={title}
    >
      {label}
    </Badge>
  );
}

function BotRow({ bot }) {
  const stacking = bot.stacking;
  const conformal = bot.conformal;
  return (
    <div className="ptl-panel__bot">
      <div className="ptl-panel__bot-head">
        <span className="num-mono text-foreground/90">{bot.symbol || '—'}</span>
        <span className="text-muted-foreground">{bot.strategy}</span>
        {bot.regime_warning && (
          <Badge variant="outline" className="border-trading-warn/40 text-trading-warn">
            <ShieldAlert className="mr-1 size-3" /> regime warning
          </Badge>
        )}
      </div>
      <div className="ptl-panel__chips">
        <Chip
          ok={!!conformal}
          label={conformal ? `gate ${conformal.threshold.toFixed(2)}` : 'no gate'}
          title={conformal ? `Conformal q_hat=${conformal.q_hat}` : 'No conformal calibration yet'}
        />
        <Chip
          ok={!!stacking}
          label={stacking ? `stack ${stacking.mode}` : 'no stacking'}
          title={
            stacking
              ? `weights: ${stacking.base_names.map((n, i) => `${n}=${stacking.weights[i]}`).join(', ')} (n=${stacking.n_oos})`
              : 'No stacking model fitted'
          }
        />
        <Chip
          ok={bot.isotonic_calibrated}
          label={bot.isotonic_calibrated ? 'isotonic' : 'raw P(win)'}
          title={bot.isotonic_calibrated ? 'Meta-label probabilities are isotonic-calibrated' : 'No isotonic calibrator'}
        />
        <Chip
          ok={bot.rl_replay_transitions > 0}
          label={`replay ${bot.rl_replay_transitions}`}
          title="RL replay buffer transitions for this symbol"
        />
        <Chip
          ok={bot.posttrade_labels > 0}
          label={`labels ${bot.posttrade_labels}`}
          title="Closed-loop post-trade labels recorded"
        />
      </div>
    </div>
  );
}

export default function PostTradeLearningPanel() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [training, setTraining] = useState(false);
  const [notice, setNotice] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchPostTradeStatus();
      if (res?.ok) setData(res);
    } catch {
      /* keep last snapshot */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [open, refresh]);

  const onTrain = async () => {
    setTraining(true);
    setNotice(null);
    try {
      const res = await trainCopilotIntentLora();
      if (res?.ok) {
        setNotice({ ok: true, text: `Router trained — acc ${(res.train_accuracy * 100).toFixed(0)}% on ${res.sample_count} pairs` });
      } else {
        setNotice({ ok: false, text: res?.error || 'Training failed' });
      }
      await refresh();
    } catch (exc) {
      setNotice({ ok: false, text: String(exc?.message || exc) });
    } finally {
      setTraining(false);
    }
  };

  const router = data?.copilot_intent_router;
  const bots = data?.bots || [];

  return (
    <div className="ptl-panel border-b border-border/60">
      <button
        type="button"
        className="ptl-panel__toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Sparkles className="size-3.5 text-trading-up" />
        <span>Post-Trade Learning</span>
        {router && (
          <span className="text-muted-foreground text-[10px]">
            {router.trained ? 'router on' : `${router.training_pairs} pairs logged`}
          </span>
        )}
      </button>

      {open && (
        <div className="ptl-panel__body">
          <div className="ptl-panel__section">
            <div className="ptl-panel__section-title">
              <BrainCircuit className="size-3.5" />
              <span>Copilot intent router</span>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto h-6 gap-1 px-2 text-[11px]"
                onClick={refresh}
                disabled={loading}
                title="Refresh"
              >
                <RefreshCw className={`size-3 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
            {router ? (
              <div className="ptl-panel__router">
                <Chip
                  ok={router.trained}
                  label={router.trained ? 'LoRA trained' : 'untrained'}
                  title={router.trained ? `Intents: ${router.labels.join(', ')}` : 'Needs ≥1000 logged queries'}
                />
                <span className="text-muted-foreground text-[11px] num-mono">
                  {router.training_pairs} pairs
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 gap-1 px-2 text-[11px]"
                  onClick={onTrain}
                  disabled={training || router.training_pairs < 2}
                  title="Fine-tune LoRA adapters on logged copilot queries"
                >
                  <SlidersHorizontal className="size-3" />
                  {training ? 'Training…' : 'Train'}
                </Button>
              </div>
            ) : (
              <p className="text-muted-foreground text-[11px]">Loading…</p>
            )}
            {notice && (
              <p className={`text-[11px] ${notice.ok ? 'text-trading-up' : 'text-destructive'}`}>
                {notice.text}
              </p>
            )}
          </div>

          <div className="ptl-panel__section">
            <div className="ptl-panel__section-title">
              <Layers className="size-3.5" />
              <span>Bots</span>
              <span className="text-muted-foreground text-[10px]">{bots.length}</span>
            </div>
            {bots.length === 0 ? (
              <p className="text-muted-foreground text-[11px]">
                <Activity className="mr-1 inline size-3" /> No active bots.
              </p>
            ) : (
              bots.map((b) => <BotRow key={b.bot_id} bot={b} />)
            )}
          </div>
        </div>
      )}
    </div>
  );
}
