/**
 * BacktestTab — top-level container with sub-tab toggle for Sleeves vs
 * Options backtests.
 *
 * Deliberately not using a Shadcn Tabs primitive — that would require
 * adding a new shared component for two pills. Inline button toggle is
 * simpler and keeps the UI footprint flat.
 */

import { cn } from '@/lib/utils';
import { useState } from 'react';
import { OptionsBacktestPanel } from './options-backtest-panel';
import { SleevesBacktestPanel } from './sleeves-backtest-panel';

type SubTab = 'sleeves' | 'options';

// Wraps in SleevesProvider so the sub-panels can read `config.sleeves` for
// the sleeve picker. Tab-scoped — same pattern as SleevesTab + OptionsTab.
export function BacktestTab() {
  return <BacktestTabContent />;
}

export function BacktestTabContent() {
  const [sub, setSub] = useState<SubTab>('options');

  return (
    <div className="h-full overflow-y-auto p-6 max-w-5xl mx-auto">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Backtest</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Two engines.{' '}
          <strong>Strategy</strong> backtests any of the 10 technical screener strategies
          via Black-Scholes pricing — fast, deterministic, free per run.{' '}
          <strong>Sleeves</strong> backtests the real LLM agent panel making
          long/short equity decisions per trading day — slower and costs LLM credits.
        </p>
      </header>

      <div className="flex items-center gap-1 mb-5 border-b border-border">
        <TabBtn active={sub === 'options'} onClick={() => setSub('options')}>
          Strategy
        </TabBtn>
        <TabBtn active={sub === 'sleeves'} onClick={() => setSub('sleeves')}>
          Sleeves (LLM agents)
        </TabBtn>
      </div>

      {sub === 'options' ? <OptionsBacktestPanel /> : <SleevesBacktestPanel />}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'px-3 py-1.5 text-sm border-b-2 -mb-px transition-colors',
        active
          ? 'border-foreground text-foreground'
          : 'border-transparent text-muted-foreground hover:text-foreground'
      )}
    >
      {children}
    </button>
  );
}
