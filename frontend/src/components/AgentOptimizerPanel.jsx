/**
 * Agentic bot gate optimizer for Backtest Lab.
 */
import TaOptimizerPanel from './TaOptimizerPanel';
import SignalGateFunnel from './SignalGateFunnel';
import ConfidenceCalibrationChart from './ConfidenceCalibrationChart';
import { StatCard } from '@/components/StatCard';
import { getStrategyMeta } from '@/config/strategies';
import { useStore } from '@/store/useStore';
import { Bot, Sparkles } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

function AgentValidationFooter({ results }) {
  const am = results?.agent_metrics;
  const regimes = am?.regime_performance || [];

  if (!am?.gate_funnel?.length && !am?.confidence_calibration?.length && !regimes.length) {
    return (
      <section className="algo-backtest-sweep__card optimizer-panel__placeholder mt-3" aria-label="Agent visualizations">
        <h5 className="algo-backtest-sweep__card-title">Gate analysis</h5>
        <p className="text-xs text-muted-foreground">
          Signal gate funnel, confidence calibration, and regime performance matrix render here
          when backtest results include <code className="mx-1">agent_metrics</code>.
        </p>
      </section>
    );
  }

  return (
    <section className="algo-backtest-sweep__card mt-3 space-y-3" aria-label="Agent visualizations">
      <h5 className="algo-backtest-sweep__card-title">Gate analysis</h5>
      {am?.gate_funnel?.length > 0 && (
        <SignalGateFunnel stages={am.gate_funnel} />
      )}
      {am?.confidence_calibration?.length > 0 && (
        <div>
          <p className="text-[0.6rem] uppercase text-muted-foreground mb-1">Calibration</p>
          <ConfidenceCalibrationChart calibration={am.confidence_calibration} />
        </div>
      )}
      {regimes.length > 0 && (
        <div className="algo-backtest-stat-grid algo-backtest-stat-grid--compact">
          {regimes.map((r) => (
            <StatCard
              key={r.regime}
              label={String(r.regime)}
              value={r.win_rate != null ? `${(Number(r.win_rate) <= 1 ? Number(r.win_rate) * 100 : Number(r.win_rate)).toFixed(0)}%` : '—'}
              sub={`${r.trades ?? 0} trades`}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default function AgentOptimizerPanel(props) {
  const { symbol, strategy, days, timeframe, results } = props;
  const meta = getStrategyMeta(strategy);
  const agentLlmAvailable = useStore((s) => s.agentLlmAvailable);
  const botConfig = useStore((s) => s.botConfig);
  const agentMetrics = results?.agent_metrics;

  return (
    <div className="optimizer-panel optimizer-panel--agent">
      <section className="algo-backtest-sweep__card optimizer-panel__hero" aria-label="Agent config">
        <div className="optimizer-panel__hero-row">
          <div className="optimizer-panel__hero-copy">
            <h5 className="algo-backtest-sweep__card-title flex items-center gap-2">
              <Bot size={14} aria-hidden />
              {meta.label}
            </h5>
            <p className="text-xs text-muted-foreground mt-1">
              Tune signal gates, calibration, and regime routing — not indicator periods.
            </p>
            <p className="text-xs text-muted-foreground mt-1 flex flex-wrap items-center gap-2">
              <span className="algo-backtest-sweep__chip">{symbol}</span>
              <span className="algo-backtest-sweep__chip">{strategy}</span>
              <span className="algo-backtest-sweep__chip num-mono">{days}d · {timeframe}</span>
              <Badge variant="outline" className="text-[0.6rem] h-5 gap-1">
                <Sparkles size={10} aria-hidden />
                {agentLlmAvailable ? 'LLM online' : 'LLM offline'}
              </Badge>
            </p>
            <p className="text-xs text-muted-foreground mt-2 num-mono">
              conf ≥ {Number(botConfig?.min_confidence ?? 0.55).toFixed(2)}
              {' · '}
              score ≥ {botConfig?.min_score ?? '—'}
              {' · '}
              meta: {botConfig?.meta_label_model_mode ?? 'wilson'}
            </p>
            {agentMetrics?.signals_generated != null && (
              <p className="text-xs text-muted-foreground mt-1">
                Last run: {agentMetrics.signals_generated} signals → {agentMetrics.signals_executed ?? 0} executed
              </p>
            )}
          </div>
        </div>
      </section>

      <TaOptimizerPanel
        {...props}
        panelTitle="Agent gate optimizer"
        footerSlot={<AgentValidationFooter results={results} />}
      />
    </div>
  );
}
