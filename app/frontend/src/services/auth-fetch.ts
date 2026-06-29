/**
 * Global fetch interceptor that attaches the Clerk session token to backend
 * requests (Phase 3).
 *
 * The app makes backend calls from many plain (non-React) service modules and
 * consumes SSE via `fetch` + `getReader()` (the morning-scan and chat streams),
 * so wrapping `window.fetch` once is the single place that covers every call —
 * regular requests AND streams — without threading a token through dozens of
 * call sites. Requests not aimed at the backend origin are passed through
 * untouched.
 *
 * Installed only when auth is enabled (see main.tsx); a no-op otherwise, so the
 * dormant app is byte-for-byte unchanged.
 */
import { API_BASE_URL } from '@/lib/api-base';

// Clerk attaches a global `window.Clerk` once ClerkProvider mounts; getToken()
// returns the short-lived session JWT (refreshed by Clerk under the hood).
declare global {
  interface Window {
    Clerk?: { session?: { getToken: (opts?: { skipCache?: boolean }) => Promise<string | null> } };
  }
}

// Only attach the bearer token when the backend origin is an ABSOLUTE http(s)
// URL. If VITE_API_URL were ever empty or a bare path, `startsWith("")` would be
// true for every URL and we'd leak the token to third-party hosts — so refuse to
// match in that case.
const SAFE_BACKEND_ORIGIN: string | null = /^https?:\/\//.test(API_BASE_URL) ? API_BASE_URL : null;

let installed = false;

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  if (input instanceof Request) return input.url;
  return String(input);
}

export function installAuthFetch(): void {
  if (installed) return;
  installed = true;

  if (!SAFE_BACKEND_ORIGIN) {
    console.warn('[auth-fetch] VITE_API_URL is not an absolute http(s) URL; not attaching auth tokens.');
    return;
  }

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const isBackend = urlOf(input).startsWith(SAFE_BACKEND_ORIGIN);
    const session = window.Clerk?.session;

    if (!isBackend || !session) {
      return originalFetch(input, init);
    }

    let token: string | null = null;
    try {
      token = await session.getToken();
      // Transient null (stale cache / just-expired token): one forced refresh
      // before giving up, rather than firing an unauthenticated request.
      if (!token) token = await session.getToken({ skipCache: true });
    } catch {
      // If the token still can't be fetched, fall through unauthenticated — the
      // backend 401s and the UI can react, rather than hanging the request.
      token = null;
    }
    if (!token) {
      return originalFetch(input, init);
    }

    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set('Authorization', `Bearer ${token}`);
    // Never set Content-Type for multipart uploads — the browser must generate
    // the `multipart/form-data; boundary=...` header itself. Drop any stray one
    // so a FormData body (Fidelity CSV / transcript PDF) is never corrupted.
    if (init?.body instanceof FormData) {
      headers.delete('Content-Type');
    }
    return originalFetch(input, { ...init, headers });
  };
}
