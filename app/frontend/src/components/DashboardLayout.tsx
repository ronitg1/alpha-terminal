/**
 * DashboardLayout — Google Finance-style 3-pane layout, responsive down to phones.
 *
 * Desktop (md+): unchanged three columns —
 *   Left  (240px fixed) : LeftNav — watchlist, sleeves, sectors, section nav
 *   Center (fluid)      : MainContent — Market / Screening / Portfolio sections
 *   Right  (320px)      : RightChatPanel — AI research assistant (toggleable)
 *
 * Mobile (< md): single column. The left nav collapses to a slide-in drawer
 * opened from a thin top bar's ☰ button; the chat panel becomes a full-screen
 * overlay (see right-chat-panel.tsx). Height uses .app-vh (100dvh) so the iOS
 * Safari toolbar never hides the bottom of the content.
 *
 * A single SleevesProvider wraps the whole layout so all sections share the
 * same watchlist/config/scan state.
 */

import { useState } from 'react';
import { Menu } from 'lucide-react';

import { DashboardProvider, useDashboard } from '@/contexts/dashboard-context';
import { SleevesProvider } from '@/contexts/sleeves-context';
import { PortfolioProvider } from '@/contexts/portfolio-context';
import { cn } from '@/lib/utils';
import type { DashboardSection } from '@/types/sleeves';
import { LeftNav } from './dashboard/left-nav';
import { MainContent } from './dashboard/main-content';
import { RightChatPanel } from './dashboard/right-chat-panel';

const SECTION_LABELS: Record<DashboardSection, string> = {
  market: 'Market',
  screening: 'Screening',
  portfolio: 'Portfolio',
  pnl: 'P&L',
  news: 'News',
  transcripts: 'Calls',
};

/** Thin top bar shown only on phones: ☰ opens the nav drawer + section title. */
function MobileTopBar({ onMenu }: { onMenu: () => void }) {
  const { section } = useDashboard();
  return (
    <div className="md:hidden safe-top sticky top-0 z-30 flex h-12 flex-shrink-0 items-center gap-1 border-b border-border bg-background/95 px-2 backdrop-blur">
      <button
        type="button"
        onClick={onMenu}
        aria-label="Open menu"
        className="rounded-md p-2 text-foreground/80 transition-colors hover:text-foreground"
      >
        <Menu className="h-5 w-5" />
      </button>
      {/* pr keeps the title clear of the floating account controls (top-right). */}
      <span className="truncate pr-28 text-sm font-semibold">{SECTION_LABELS[section]}</span>
    </div>
  );
}

function DashboardShell() {
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="app-vh flex w-screen overflow-hidden bg-background">
      {/* Drawer backdrop — mobile only, when open */}
      {navOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Left navigation: off-canvas drawer on mobile, static column on md+ */}
      <div
        className={cn(
          'w-60 flex-shrink-0 overflow-hidden bg-background',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50 max-md:shadow-xl',
          'max-md:transition-transform max-md:duration-200 max-md:ease-out',
          navOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full',
        )}
      >
        <LeftNav onNavigate={() => setNavOpen(false)} />
      </div>

      {/* Center column: mobile top bar + main content */}
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileTopBar onMenu={() => setNavOpen(true)} />
        <div className="min-h-0 flex-1">
          <MainContent />
        </div>
      </div>

      {/* Right AI chat panel (full-screen overlay on mobile; side panel on md+) */}
      <RightChatPanel />
    </div>
  );
}

export function DashboardLayout() {
  return (
    <SleevesProvider>
      <DashboardProvider>
        <PortfolioProvider>
          <DashboardShell />
        </PortfolioProvider>
      </DashboardProvider>
    </SleevesProvider>
  );
}
