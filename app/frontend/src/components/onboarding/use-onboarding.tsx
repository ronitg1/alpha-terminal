/**
 * Onboarding provider — owns first-login detection, the "already seen it" flag,
 * and the interactive tour.
 *
 * First-login detection is backed by a per-user server flag
 * (`GET /auth/me -> onboarding_completed`, set via `POST /auth/onboarding-complete`),
 * so it's truly once-per-account: it survives a localStorage clear or a sign-in
 * on a new device. A localStorage flag (namespaced by Clerk user id) is kept as
 * a fast-path cache — it avoids a flash of the popup before `/auth/me` resolves
 * and as an offline fallback if the backend call fails.
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
import { authApi } from '@/services/auth-api';
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

/** Record completion in BOTH the local cache and the server source of truth. */
function persistOnboardingComplete(): void {
  markOnboardingSeen();
  void authApi.markOnboardingComplete().catch(() => {
    /* backend unreachable — the localStorage cache still suppresses re-open */
  });
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
  // slightly after mount, so poll briefly for the user id, then ask the backend
  // (source of truth). The localStorage cache short-circuits the network call
  // and is the fallback if the backend is unreachable.
  useEffect(() => {
    if (!AUTH_ENABLED) return;
    let cancelled = false;
    let tries = 0;
    const check = async () => {
      if (cancelled) return;
      if (!clerkUserId()) {
        if (tries++ < 20) setTimeout(check, 250); // up to ~5s for Clerk to attach
        return;
      }
      if (hasSeenOnboarding()) return; // already done on this browser — skip the call
      try {
        const me = await authApi.getMe();
        if (cancelled) return;
        if (me.onboarding_completed) markOnboardingSeen(); // cache the server truth
        else setWelcomeOpen(true);
      } catch {
        // Backend unreachable / older API: fall back to localStorage-only.
        if (!cancelled && !hasSeenOnboarding()) setWelcomeOpen(true);
      }
    };
    void check();
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
      onDestroyed: () => persistOnboardingComplete(),
    });
    // Let the welcome dialog finish closing before the spotlight paints.
    setTimeout(() => d.drive(), 250);
  }, []);

  const closeWelcome = useCallback(() => {
    persistOnboardingComplete();
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
