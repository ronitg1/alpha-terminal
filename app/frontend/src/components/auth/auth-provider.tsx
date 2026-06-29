/**
 * Wraps the app in Clerk's provider — but only when auth is enabled. When the
 * flag is off it renders children directly, so the dormant app never loads Clerk
 * and behaves exactly as before.
 */
import { ClerkProvider } from '@clerk/clerk-react';
import type { ReactNode } from 'react';

import { AUTH_ENABLED, CLERK_PUBLISHABLE_KEY } from '@/config/auth';

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!AUTH_ENABLED) return <>{children}</>;
  return (
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl="/">
      {children}
    </ClerkProvider>
  );
}
