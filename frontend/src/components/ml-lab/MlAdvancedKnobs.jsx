import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { DEEP_ML_STRATEGIES, FEATURE_SCHEME_OPTIONS, persistTrainInit } from '@/components/ml-lab/MlLabConstants';

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
        {strategy !== 'RL_PPO_AGENT' && strategy !== 'VAE_REGIME_DETECTOR' && (
          <>
            <label className="ml-training__advanced-field">
              <span>event_filter</span>
              <Select
                value={advanced.eventFilter || 'cusum'}
                onValueChange={(value) => setAdvanced((a) => ({ ...a, eventFilter: value }))}
              >
                <SelectTrigger size="sm" className="h-7 w-full min-w-0 text-xs" aria-label="Event filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent position="popper" className="ml-training__advanced-menu">
                  <SelectItem value="cusum" className="text-xs">cusum</SelectItem>
                  <SelectItem value="all" className="text-xs">all bars</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="ml-training__advanced-field">
              <span>cusum_threshold</span>
              <Input
                type="number"
                min={0.1}
                max={5}
                step={0.05}
                className="h-7 text-xs"
                value={advanced.cusumThreshold}
                onChange={(e) => setAdvanced((a) => ({ ...a, cusumThreshold: e.target.value }))}
              />
            </label>
          </>
        )}
        <label className="ml-training__advanced-field">
          <span title="Zero families without changing ONNX width — use for v7 vs v8 A/B">
            feature_scheme
          </span>
          <Select
            value={advanced.featureScheme || 'v8'}
            onValueChange={(value) => setAdvanced((a) => ({ ...a, featureScheme: value }))}
          >
            <SelectTrigger size="sm" className="h-7 w-full min-w-0 text-xs" aria-label="Feature scheme">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper" className="ml-training__advanced-menu">
              {FEATURE_SCHEME_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="ml-training__advanced-field">
          <span title="Fine-tune resumes the live champion; from scratch uses random weights and the full budget">
            train_init
          </span>
          <Select
            value={advanced.trainInit === 'scratch' ? 'scratch' : 'warm'}
            onValueChange={(value) => {
              persistTrainInit(value);
              setAdvanced((a) => ({ ...a, trainInit: value }));
            }}
          >
            <SelectTrigger size="sm" className="h-7 w-full min-w-0 text-xs" aria-label="Train initialization">
              <SelectValue />
            </SelectTrigger>
            <SelectContent position="popper" className="ml-training__advanced-menu">
              <SelectItem value="warm" className="text-xs">fine-tune existing</SelectItem>
              <SelectItem value="scratch" className="text-xs">from scratch</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </div>
      <p className="ml-training__advanced-hint">
        Train uses GPU (CUDA) when PyTorch detects it; Validate stays lighter on CPU.
        Client waits up to ~90 min for PPO / ~60 min for deep models (plus a poll buffer).
        Live bots still infer via CPU ONNX. Retrain after changing hidden_dim / architecture.
      </p>
    </details>
  );
}
