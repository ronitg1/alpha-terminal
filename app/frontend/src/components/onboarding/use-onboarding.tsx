/**
 * Onboarding provider — owns first-login detection, the "already seen it" flag,
 * and the interactive tour.
 *
 * First-login detection is frontend-only: we store a flag in localStorage,
 * namespaced by the Clerk user id, so it is per-account on a given browser. This
 * deliberately avoids a backend/DB change (zero risk to the live schema). The
 * one trade-off: clearing browser data, or signing in on a brand-new browser,
 * shows the welcome once more. A backend onboarding flag would make it
 * bulletproof-per-account — left as a follow-up.
 *
 * The interactive tour uses driver.js, which spotlights the real UI elements
 * tagged with `data-tour` attributes, so it cannot drift from the layout.
 */
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';

import { AUTH_ENABLED } from '@/config/auth';
import { WelcomeDialog } from './welcome-dialog';
import { TOUR_STEPS } from './onboarding-steps';

const STORAGE_PREFIX = 'alpha-onboarding-v1';

/** Read the current Clerk user id from the global Clerk object, if present. */
function clerkUserId(): string | null {
  const clerk = (window as unknown as { Clerk?: { user?: { id?: string } } }).Clerk;
  return clerk?.user?.id ?? null;
}

/** localStorage key for the onboarding flag, namespaced per user. */
function storageKey(): string {
  const uid = clerkUserId();
  return uid ? `${STORAGE_PREFIX}:${uid}` : STORAGE_PREFIX;
}

function hasSeenOnboarding(): boolean {
  try {
    return localStorage.getItem(storageKey()) === 'done';
  } catch {
    return false;
  }
}

function markOnboardingSeen(): void {
  try {
    localStorage.setItem(storageKey(), 'done');
  } catch {
    /* ignore — a private-mode browser just re-shows the welcome next time */
  }
}

interface OnboardingContextType {
  /** Open the welcome walkthrough popup (used by the Help button). */
  openWelcome: () => void;
  /** Launch the interactive spotlight tour directly. */
  startTour: () => void;
}

const OnboardingContext = createContext<OnboardingContextType | null>(null);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const [welcomeOpen, setWelcomeOpen] = useState(false);

  // Auto-open on first login only. Clerk may attach `window.Clerk.user`
  // slightly after mount, so poll briefly for the user id before deciding.
  useEffect(() => {
    if (!AUTH_ENABLED) return;
    let cancelled = false;
    let tries = 0;
    const check = () => {
      if (cancelled) return;
      if (clerkUserId()) {
        if (!hasSeenOnboarding()) setWelcomeOpen(true);
        return;
      }
      if (tries++ < 20) setTimeout(check, 250); // up to ~5s
    };
    check();
    return () => {
      cancelled = true;
    };
  }, []);

  const startTour = useCallback(() => {
    const d = driver({
      showProgress: true,
      allowClose: true,
      overlayColor: 'rgba(0, 0, 0, 0.65)',
      nextBtnText: 'Next',
      prevBtnText: 'Back',
      doneBtnText: 'Done',
      steps: TOUR_STEPS,
      onDestroyed: () => markOnboardingSeen(),
    });
    // Let the welcome dialog finish closing before the spotlight paints.
    setTimeout(() => d.drive(), 250);
  }, []);

  const closeWelcome = useCallback(() => {
    markOnboardingSeen();
    setWelcomeOpen(false);
  }, []);

  const openWelcome = useCallback(() => setWelcomeOpen(true), []);

  return (
    <OnboardingContext.Provider value={{ openWelcome, startTour }}>
      {children}
      <WelcomeDialog open={welcomeOpen} onClose={closeWelcome} onStartTour={startTour} />
    </OnboardingContext.Provider>
  );
}

export function useOnboarding(): OnboardingContextType {
  const ctx = useContext(OnboardingContext);
  if (!ctx) throw new Error('useOnboarding must be used inside OnboardingProvider');
  return ctx;
}
