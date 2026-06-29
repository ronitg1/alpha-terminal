/**
 * Login gate. When auth is enabled, an unauthenticated visitor sees a centered
 * Clerk sign-in card; a signed-in user sees the app. When auth is off, children
 * render directly (dormant — no gate).
 */
import { SignIn, SignedIn, SignedOut } from '@clerk/clerk-react';
import type { ReactNode } from 'react';

import { AUTH_ENABLED } from '@/config/auth';

export function AuthGate({ children }: { children: ReactNode }) {
  if (!AUTH_ENABLED) return <>{children}</>;
  return (
    <>
      <SignedIn>{children}</SignedIn>
      <SignedOut>
        <div className="flex h-screen w-screen flex-col items-center justify-center gap-6 bg-background">
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Alpha Terminal</h1>
            <p className="text-sm text-muted-foreground">Sign in to access your research desk</p>
          </div>
          {/* routing="virtual" lets the component embed without a router. */}
          <SignIn routing="virtual" />
        </div>
      </SignedOut>
    </>
  );
}
