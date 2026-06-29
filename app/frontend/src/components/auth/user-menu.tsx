/**
 * Top-right account controls: an API-keys settings button + Clerk's UserButton
 * (account / sign-out). Renders nothing when auth is off, so the dormant app is
 * unchanged.
 */
import { UserButton } from '@clerk/clerk-react';
import { Settings } from 'lucide-react';

import { AUTH_ENABLED } from '@/config/auth';
import { Button } from '@/components/ui/button';
import { ApiKeysSettings } from './api-keys-settings';

export function UserMenu() {
  if (!AUTH_ENABLED) return null;
  return (
    <div className="fixed right-3 top-2 z-50 flex items-center gap-1.5">
      <ApiKeysSettings
        trigger={
          <Button variant="ghost" size="icon" title="API keys">
            <Settings className="h-4 w-4" />
          </Button>
        }
      />
      <UserButton afterSignOutUrl="/" />
    </div>
  );
}
