import PortfolioDashboard from '../../components/PortfolioDashboard';

export default function PortfolioStandaloneContent() {
  return (
    <div className="h-full min-h-0 overflow-hidden">
      <PortfolioDashboard open standalone onOpenChange={() => {}} />
    </div>
  );
}
