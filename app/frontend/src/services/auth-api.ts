/**
 * Auth API client — thin wrappers over the backend's /auth routes.
 *
 * Requests go to the backend origin, so the global fetch interceptor
 * (`auth-fetch.ts`) attaches the Clerk bearer token automatically.
 */
import { API_BASE_URL } from '@/lib/api-base';

export interface MeResponse {
  user_id: string;
  auth_enabled: boolean;
  /** Whether this account has finished/skipped the first-login walkthrough. */
  onboarding_completed: boolean;
}

export const authApi = {
  getMe: async (): Promise<MeResponse> => {
    const res = await fetch(`${API_BASE_URL}/auth/me`);
    if (!res.ok) throw new Error(`GET /auth/me failed: ${res.status}`);
    return res.json();
  },

  markOnboardingComplete: async (): Promise<void> => {
    const res = await fetch(`${API_BASE_URL}/auth/onboarding-complete`, { method: 'POST' });
    if (!res.ok) throw new Error(`POST /auth/onboarding-complete failed: ${res.status}`);
  },
};
