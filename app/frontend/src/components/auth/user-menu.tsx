/**
 * Top-right account controls: a Help button (replays the onboarding
 * walkthrough), an API-keys settings button, and Clerk's UserButton (account /
 * sign-out). Renders nothing when auth is off, so the dormant app is unchanged.
 */
import { UserButton } from '@clerk/clerk-react';
import { HelpCircle, Settings } from 'lucide-react';

import { AUTH_ENABLED } from '@/config/auth';
import { Button } from '@/components/ui/button';
import { useOnboarding } from '@/components/onboarding/use-onboarding';
import { ApiKeysSettings } from './api-keys-settings';

export function UserMenu() {
  const { openWelcome } = useOnboarding();
  if (!AUTH_ENABLED) return null;
  return (
    <div className="fixed right-3 top-2 z-50 flex items-center gap-1.5">
      <Button
        variant="ghost"
        size="icon"
        title="Help & walkthrough"
        data-tour="help"
        onClick={openWelcome}
      >
        <HelpCircle className="h-4 w-4" />
      </Button>
      <ApiKeysSettings
        trigger={
          <Button variant="ghost" size="icon" title="API keys" data-tour="settings">
            <Settings className="h-4 w-4" />
          </Button>
        }
      />
      <UserButton afterSignOutUrl="/" />
    </div>
  );
}
