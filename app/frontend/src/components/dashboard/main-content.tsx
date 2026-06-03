/**
 * MainContent — routes between Market, Screening, and Portfolio sections.
 *
 * Each section renders either a new purpose-built view or an existing tab
 * component.  The Screening section adds a sub-nav for Pattern Scanner,
 * Options Screener, and Backtest.
 */

import { useDashboard } from '@/contexts/dashboard-context';
import { cn } from '@/lib/utils';
import { MarketView } from './market-view';
import { PortfolioSection } from './portfolio-section';
import { NewsView } from '@/components/news/news-view';
import { TranscriptsView } from '@/components/transcripts/transcripts-view';
import { Component, ReactNode } from 'react';

// Existing tab content components (inner functions extracted for reuse)
import { OptionsTabContent } from '@/components/sleeves/options/options-tab';
import { BacktestTabContent } from '@/components/sleeves/backtest/backtest-tab';
import { PatternsTab as PatternsTabContent } from '@/components/patterns/patterns-tab';

import { ScreeningSubTab } from '@/types/sleeves';

// Simple error boundary so a screener card crash never kills the whole app
class SectionErrorBoundary extends Component<
  { children: ReactNode },
  { error: string | null }
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(err: Error) {
    return { error: err.message };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex items-center justify-center h-full text-center px-8">
          <div className="space-y-2 max-w-md">
            <div className="text-sm font-medium text-rose-500">Section failed to render</div>
            <div className="text-xs text-muted-foreground font-mono break-all">{this.state.error}</div>
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="text-xs text-primary underline"
            >
              Retry
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const SUB_TABS: { id: ScreeningSubTab; label: string }[] = [
  { id: 'options', label: 'Options Screener' },
  { id: 'patterns', label: 'Pattern Scanner' },
  { id: 'backtest', label: 'Backtest' },
];

function ScreeningSection() {
  const { screeningSubTab, setScreeningSubTab } = useDashboard();

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tab nav */}
      <div className="flex items-center gap-1 border-b border-border px-6 pt-4 pb-0 flex-shrink-0">
        {SUB_TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setScreeningSubTab(id)}
            className={cn(
              'px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors',
              screeningSubTab === id
                ? 'border-foreground text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Sub-section content */}
      <div className="flex-1 overflow-hidden">
        {screeningSubTab === 'options' && <OptionsTabContent />}
        {screeningSubTab === 'patterns' && <PatternsTabContent />}
        {screeningSubTab === 'backtest' && <BacktestTabContent />}
      </div>
    </div>
  );
}

export function MainContent() {
  const { section } = useDashboard();

  return (
    <div className="flex-1 h-full overflow-hidden">
      <SectionErrorBoundary>
        {section === 'market' && <MarketView />}
        {section === 'screening' && <ScreeningSection />}
        {section === 'portfolio' && <PortfolioSection />}
        {section === 'news' && <NewsView />}
        {section === 'transcripts' && <TranscriptsView />}
      </SectionErrorBoundary>
    </div>
  );
}
