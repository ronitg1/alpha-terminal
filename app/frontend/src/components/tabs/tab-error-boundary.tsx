/**
 * TabErrorBoundary — class component wrapper around <TabContent>.
 *
 * Addresses HANDOFF gotcha #2: when a tab mount crashes, React unmounts
 * the entire tree (no main element, blank screen). With this boundary, a
 * crash is caught and the tab shows a "Tab failed to render — Reset"
 * fallback instead, while other tabs and the chrome stay alive.
 *
 * Resetting clears localStorage tab persistence and reloads — the same
 * recovery the gotcha note prescribes manually.
 */
import { Button } from '@/components/ui/button';
import { AlertTriangle } from 'lucide-react';
import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class TabErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // Log to the console so the developer still sees the stack.
    console.error('Tab render crashed:', error, info.componentStack);
  }

  handleReset = () => {
    try {
      localStorage.removeItem('ai-hedge-fund-tabs');
      localStorage.removeItem('ai-hedge-fund-active-tab');
    } catch {
      // ignore — non-blocking
    }
    location.reload();
  };

  handleRetry = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="h-full w-full flex items-center justify-center p-8 bg-background">
          <div className="max-w-md text-center space-y-3">
            <AlertTriangle className="h-10 w-10 text-amber-500 mx-auto" />
            <div className="text-base font-medium">
              This tab failed to render
            </div>
            <div className="text-xs text-muted-foreground font-mono break-all px-3">
              {this.state.error.message}
            </div>
            <div className="flex items-center justify-center gap-2 pt-1">
              <Button variant="outline" size="sm" onClick={this.handleRetry}>
                Try again
              </Button>
              <Button size="sm" onClick={this.handleReset}>
                Reset tabs & reload
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground italic">
              "Reset" clears persisted tab state and reloads the page. Other
              tabs and your scan data are safe — this only resets which tabs
              are open.
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
