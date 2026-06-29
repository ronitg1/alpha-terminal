/**
 * Frontend auth configuration (Phase 3).
 *
 * Mirrors the backend `AUTH_ENABLED` flag. When `VITE_AUTH_ENABLED` is off (the
 * default), the whole Clerk layer is dormant: no provider, no login gate, no
 * token attachment — the app renders exactly as it does today. Flip
 * `VITE_AUTH_ENABLED=1` (and set `VITE_CLERK_PUBLISHABLE_KEY`) in the deploy env
 * to turn login on, in lockstep with the backend flag.
 */

const truthy = (v: unknown): boolean =>
  typeof v === 'string' && ['1', 'true', 'yes'].includes(v.trim().toLowerCase());

/** The Clerk publishable key (public), or empty string if unset. */
export const CLERK_PUBLISHABLE_KEY: string =
  (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined)?.trim() ?? '';

/**
 * Whether login is enforced in the UI. Requires both the flag AND a publishable
 * key — if the flag is on but the key is missing, we stay dormant rather than
 * crash the app with a misconfigured ClerkProvider.
 */
export const AUTH_ENABLED: boolean =
  truthy(import.meta.env.VITE_AUTH_ENABLED) && CLERK_PUBLISHABLE_KEY.length > 0;
