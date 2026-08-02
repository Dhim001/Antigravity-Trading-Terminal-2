import { Input } from '@/components/ui/input';
import { DEEP_ML_STRATEGIES } from '@/components/ml-lab/MlLabConstants';

export function MlAdvancedKnobs({ advanced, setAdvanced, strategy }) {
  return (
    <details className="ml-training__advanced">
      <summary>Advanced</summary>
      <div className="ml-training__advanced-grid">
        <label className="ml-training__advanced-field">
          <span>n_folds</span>
          <Input
            type="number"
            min={2}
            max={8}
            className="h-7 text-xs"
            value={advanced.nFolds}
            onChange={(e) => setAdvanced((a) => ({ ...a, nFolds: e.target.value }))}
          />
        </label>
        <label className="ml-training__advanced-field">
          <span>validate_max_bars</span>
          <Input
            type="number"
            min={200}
            max={20000}
            step={100}
            className="h-7 text-xs"
            value={advanced.validateMaxBars}
            onChange={(e) => setAdvanced((a) => ({ ...a, validateMaxBars: e.target.value }))}
          />
        </label>
        <label className="ml-training__advanced-field">
          <span>pbo_segments</span>
          <Input
            type="number"
            min={2}
            max={8}
            className="h-7 text-xs"
            disabled={strategy === 'RL_PPO_AGENT'}
            value={advanced.pboSegments}
            onChange={(e) => setAdvanced((a) => ({ ...a, pboSegments: e.target.value }))}
          />
        </label>
        <label className="ml-training__advanced-field">
          <span>pbo_max_combos</span>
          <Input
            type="number"
            min={1}
            max={16}
            className="h-7 text-xs"
            disabled={strategy === 'RL_PPO_AGENT'}
            value={advanced.pboMaxCombos}
            onChange={(e) => setAdvanced((a) => ({ ...a, pboMaxCombos: e.target.value }))}
          />
        </label>
        {strategy === 'RL_PPO_AGENT' && (
          <label className="ml-training__advanced-field">
            <span>total_timesteps</span>
            <Input
              type="number"
              min={256}
              max={500000}
              step={256}
              className="h-7 text-xs"
              value={advanced.totalTimesteps}
              onChange={(e) => setAdvanced((a) => ({ ...a, totalTimesteps: e.target.value }))}
            />
          </label>
        )}
        {(DEEP_ML_STRATEGIES.has(strategy) || strategy === 'RL_PPO_AGENT') && (
          <label className="ml-training__advanced-field">
            <span>hidden_dim</span>
            <Input
              type="number"
              min={32}
              max={1024}
              step={32}
              className="h-7 text-xs"
              value={advanced.hiddenDim}
              onChange={(e) => setAdvanced((a) => ({ ...a, hiddenDim: e.target.value }))}
            />
          </label>
        )}
        {DEEP_ML_STRATEGIES.has(strategy) && (
          <label className="ml-training__advanced-field">
            <span>train epochs</span>
            <Input
              type="number"
              min={1}
              max={500}
              className="h-7 text-xs"
              value={advanced.epochs}
              onChange={(e) => setAdvanced((a) => ({ ...a, epochs: e.target.value }))}
            />
          </label>
        )}
        {DEEP_ML_STRATEGIES.has(strategy) && (
          <label className="ml-training__advanced-field">
            <span title="Stop after this many epochs with no better validation loss">
              early-stop patience
            </span>
            <Input
              type="number"
              min={1}
              max={100}
              className="h-7 text-xs"
              value={advanced.earlyStopPatience}
              onChange={(e) => setAdvanced((a) => ({ ...a, earlyStopPatience: e.target.value }))}
            />
          </label>
        )}
        {strategy === 'ML_SIGNAL_BOOST' && (
          <>
            <label className="ml-training__advanced-field">
              <span>gbm_max_iter</span>
              <Input
                type="number"
                min={40}
                max={1000}
                step={10}
                className="h-7 text-xs"
                value={advanced.gbmMaxIter}
                onChange={(e) => setAdvanced((a) => ({ ...a, gbmMaxIter: e.target.value }))}
              />
            </label>
            <label className="ml-training__advanced-field">
              <span>gbm_max_depth</span>
              <Input
                type="number"
                min={3}
                max={12}
                className="h-7 text-xs"
                value={advanced.gbmMaxDepth}
                onChange={(e) => setAdvanced((a) => ({ ...a, gbmMaxDepth: e.target.value }))}
              />
            </label>
          </>
        )}
      </div>
      <p className="ml-training__advanced-hint">
        Train uses GPU (CUDA) when PyTorch detects it; Validate stays lighter on CPU.
        Client waits up to ~90 min for PPO / ~60 min for deep models (plus a poll buffer).
        Live bots still infer via CPU ONNX. Retrain after changing hidden_dim / architecture.
      </p>
    </details>
  );
}
