/**
 * Onboarding content — the copy and structure for the first-login walkthrough.
 *
 * Two parallel data sets:
 *   WELCOME_SLIDES — the multi-step welcome popup (big-picture screenshots +
 *                    plain-English explanations). Shown automatically on a
 *                    user's first login, and replayable from the Help button.
 *   TOUR_STEPS     — the interactive driver.js tour that spotlights the REAL
 *                    UI elements (matched by their `data-tour` attribute), so
 *                    it never goes stale when the layout changes.
 *
 * Keep the copy plain-English: the audience is retail investors, not
 * developers. No emojis (repo convention).
 */
import type { ReactNode } from 'react';

/** One step in the welcome popup carousel. */
export interface WelcomeSlide {
  id: string;
  title: string;
  body: ReactNode;
  /**
   * Public path to a screenshot, served from `public/onboarding/`. Optional —
   * the dialog shows a neutral placeholder if the image is missing or fails to
   * load, so the walkthrough works before screenshots have been captured.
   */
  image?: string;
  imageAlt?: string;
}

export const WELCOME_SLIDES: WelcomeSlide[] = [
  {
    id: 'welcome',
    title: 'Welcome to Alpha Terminal',
    body: (
      <>
        <p>
          Alpha Terminal is your personal equity research desk. It scans for
          technical chart patterns, screens options, tracks your portfolio and
          P&amp;L, and has an AI assistant that can answer questions about any
          stock.
        </p>
        <p className="mt-2 text-muted-foreground">
          This quick tour walks through the main areas. It takes about a minute,
          and you can skip it at any time — there is a Skip button on every step,
          and a Help button in the top-right corner to replay it later.
        </p>
      </>
    ),
    image: '/onboarding/01-welcome.png',
    imageAlt: 'Alpha Terminal dashboard overview',
  },
  {
    id: 'layout',
    title: 'The layout',
    body: (
      <>
        <p>The screen has three parts:</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <strong>Left sidebar</strong> — your watchlists, portfolios, and
            sector list, plus the six section buttons (Market, Screening,
            Portfolio, P&amp;L, News, Calls).
          </li>
          <li>
            <strong>Center</strong> — the main workspace; its content changes
            with the section you pick.
          </li>
          <li>
            <strong>Right panel</strong> — the AI research assistant (you can
            hide or show it).
          </li>
        </ul>
        <p className="mt-2 text-muted-foreground">
          Click any ticker in the left sidebar to jump straight to its Market
          page.
        </p>
      </>
    ),
    image: '/onboarding/02-layout.png',
    imageAlt: 'Three-pane layout: left sidebar, center workspace, right assistant',
  },
  {
    id: 'market',
    title: 'Market — research any stock',
    body: (
      <>
        <p>
          The <strong>Market</strong> section is the deep-dive page for a single
          stock. Pick a ticker and you get its price chart, a company overview,
          the latest news, key financials, and a live quote snapshot — all in one
          place.
        </p>
        <p className="mt-2 text-muted-foreground">
          This is the best starting point when you want to understand one
          company before acting on it.
        </p>
      </>
    ),
    image: '/onboarding/03-market.png',
    imageAlt: 'Market view with price chart, overview, news and financials',
  },
  {
    id: 'patterns',
    title: 'Screening — Pattern Scanner',
    body: (
      <>
        <p>
          Under <strong>Screening &rarr; Pattern Scanner</strong>, the app reads
          price history and flags classic chart patterns — Bull Pennant, Double
          Bottom, Cup &amp; Handle, and more — each with a{' '}
          <strong>confidence score</strong>.
        </p>
        <p className="mt-2">
          Click any result to open the full chart, where you will see the
          detected pattern drawn on the candles: the trendlines, the key price
          levels (like the breakout trigger), and the signal markers.
        </p>
        <p className="mt-2 text-muted-foreground">
          The confidence score blends how cleanly price broke out, how strong the
          volume was, and how well it respected the trendlines.
        </p>
      </>
    ),
    image: '/onboarding/04-patterns.png',
    imageAlt: 'Pattern Scanner results and an annotated pattern chart',
  },
  {
    id: 'screening-more',
    title: 'Screening — Options & Backtest',
    body: (
      <>
        <p>The Screening section has two more tabs:</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <strong>Options Screener</strong> — surfaces options contracts that
            fit a pattern signal, with suggested entry, stop, and target levels.
          </li>
          <li>
            <strong>Backtest</strong> — replays how a pattern signal would have
            performed historically, so you can judge a strategy before risking
            anything.
          </li>
        </ul>
        <p className="mt-2 text-muted-foreground">
          Signals only — Alpha Terminal never places trades for you.
        </p>
      </>
    ),
    image: '/onboarding/05-screening.png',
    imageAlt: 'Options screener and backtest tabs',
  },
  {
    id: 'assistant',
    title: 'AI research assistant',
    body: (
      <>
        <p>
          The panel on the right is an <strong>AI assistant</strong> that knows
          about the stock you are looking at. Ask it things like &ldquo;what does
          this pattern mean?&rdquo;, &ldquo;summarise the latest earnings
          call&rdquo;, or &ldquo;what are the risks here?&rdquo;
        </p>
        <p className="mt-2 text-muted-foreground">
          Toggle it on or off with the chat icon at the top of the left sidebar.
        </p>
      </>
    ),
    image: '/onboarding/06-assistant.png',
    imageAlt: 'AI research assistant panel',
  },
  {
    id: 'portfolio-pnl',
    title: 'Portfolio & P&L',
    body: (
      <>
        <p>
          <strong>Portfolio</strong> lets you group tickers into themed
          &ldquo;sleeves&rdquo; and manage your watchlists. <strong>P&amp;L</strong>{' '}
          lets you import your Fidelity positions (via CSV) to track gains and
          losses over time.
        </p>
        <p className="mt-2 text-muted-foreground">
          News and Calls round things out: a live news feed and earnings-call
          transcripts for the names you follow.
        </p>
      </>
    ),
    image: '/onboarding/07-portfolio-pnl.png',
    imageAlt: 'Portfolio and P&L sections',
  },
  {
    id: 'settings',
    title: 'One setup step: your API keys',
    body: (
      <>
        <p>
          Alpha Terminal runs on a few data and AI providers. The{' '}
          <strong>Settings</strong> button (gear icon, top-right) is where you add
          your keys.
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>
            <strong>DeepSeek</strong> — required for the AI assistant. You bring
            your own key (it is stored encrypted, never shown back to you).
          </li>
          <li>
            <strong>Market data</strong> — News works for everyone out of the
            box. Deeper market data may need approval; you can request access
            right from Settings.
          </li>
        </ul>
        <p className="mt-2 text-muted-foreground">
          You are all set. Take the interactive tour next, or jump straight in.
        </p>
      </>
    ),
    image: '/onboarding/08-settings.png',
    imageAlt: 'API keys settings dialog',
  },
];

/**
 * Interactive tour steps. Each `element` selector matches a `data-tour`
 * attribute placed on a real UI element. `driver.js` spotlights the element and
 * shows the popover; if an element is not on screen the step is skipped.
 */
export interface TourStep {
  element: string;
  popover: {
    title: string;
    description: string;
    side?: 'top' | 'right' | 'bottom' | 'left';
    align?: 'start' | 'center' | 'end';
  };
}

export const TOUR_STEPS: TourStep[] = [
  {
    element: '[data-tour="app-logo"]',
    popover: {
      title: 'Your research desk',
      description:
        'This is Alpha Terminal. The left sidebar is your home base — everything starts here.',
      side: 'bottom',
      align: 'start',
    },
  },
  {
    element: '[data-tour="nav-sections"]',
    popover: {
      title: 'Move between sections',
      description:
        'Switch between Market (research one stock), Screening (find patterns and options), Portfolio, P&L, News, and Calls.',
      side: 'bottom',
      align: 'start',
    },
  },
  {
    element: '[data-tour="watchlists"]',
    popover: {
      title: 'Your watchlists',
      description:
        'Create lists of tickers you follow. Click any ticker to open its Market page. Use the + to add a new list.',
      side: 'right',
      align: 'start',
    },
  },
  {
    element: '[data-tour="ai-assistant"]',
    popover: {
      title: 'AI assistant',
      description:
        'Toggle the AI research assistant on the right. Ask it about any stock you are viewing.',
      side: 'bottom',
      align: 'center',
    },
  },
  {
    element: '[data-tour="settings"]',
    popover: {
      title: 'Add your API keys',
      description:
        'Open Settings to add your DeepSeek key (needed for the AI assistant) and request market-data access.',
      side: 'left',
      align: 'start',
    },
  },
  {
    element: '[data-tour="help"]',
    popover: {
      title: 'Replay anytime',
      description:
        'That is the tour. Click this Help button whenever you want to see the walkthrough again. Happy researching!',
      side: 'left',
      align: 'start',
    },
  },
];
