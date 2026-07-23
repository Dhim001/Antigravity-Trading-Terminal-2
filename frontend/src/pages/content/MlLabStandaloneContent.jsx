import ModelTrainingDashboard from '../../components/dock/ModelTrainingDashboard';

/** @param {{ onReattach?: () => void }} props */
export default function MlLabStandaloneContent({ onReattach }) {
  return (
    <div className="h-full min-h-0 overflow-hidden">
      <ModelTrainingDashboard detached onAttach={onReattach} />
    </div>
  );
}
