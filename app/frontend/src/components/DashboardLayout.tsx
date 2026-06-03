/**
 * DashboardLayout — Google Finance-style 3-pane layout.
 *
 * Replaces the VS Code IDE shell (Layout.tsx) with:
 *   Left  (240px fixed) : LeftNav — watchlist, sleeves, sectors, section nav
 *   Center (fluid)      : MainContent — Market / Screening / Portfolio sections
 *   Right  (320px)      : RightChatPanel — AI research assistant (toggleable)
 *
 * A single SleevesProvider wraps the whole layout so all sections share the
 * same watchlist/config/scan state (the inner SleevesProvider wrappers in
 * SleevesTab / OptionsTab / BacktestTab / StocksTab have been removed).
 */

import { DashboardProvider } from '@/contexts/dashboard-context';
import { SleevesProvider } from '@/contexts/sleeves-context';
import { LeftNav } from './dashboard/left-nav';
import { MainContent } from './dashboard/main-content';
import { RightChatPanel } from './dashboard/right-chat-panel';

function DashboardShell() {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {/* Left navigation panel */}
      <div className="w-60 flex-shrink-0 overflow-hidden">
        <LeftNav />
      </div>

      {/* Main content */}
      <MainContent />

      {/* Right AI chat panel (conditionally rendered by chatOpen state) */}
      <RightChatPanel />
    </div>
  );
}

export function DashboardLayout() {
  return (
    <SleevesProvider>
      <DashboardProvider>
        <DashboardShell />
      </DashboardProvider>
    </SleevesProvider>
  );
}
