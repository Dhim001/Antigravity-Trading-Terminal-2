import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * Catches render errors in heavy widgets (charts, dock) without crashing the whole terminal.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, remountKey: 0 };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error(`[ErrorBoundary:${this.props.name || 'widget'}]`, error, info);
  }

  handleRetry = () => {
    this.setState((prev) => ({
      error: null,
      remountKey: prev.remountKey + 1,
    }));
    this.props.onRetry?.();
  };

  render() {
    const { error, remountKey } = this.state;
    if (!error) {
      return (
        <React.Fragment key={remountKey}>
          {this.props.children}
        </React.Fragment>
      );
    }

    const label = this.props.name || 'This panel';

    return (
      <div className="flex min-h-[120px] flex-col items-center justify-center gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-6 text-center">
        <AlertTriangle className="size-8 text-destructive" aria-hidden />
        <div>
          <p className="text-sm font-semibold text-foreground">{label} failed to render</p>
          <p className="mt-1 max-w-md text-xs text-muted-foreground">
            {error.message || 'An unexpected error occurred.'}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={this.handleRetry}>
          <RefreshCw data-icon="inline-start" aria-hidden />
          Retry
        </Button>
      </div>
    );
  }
}
