/**
 * Top-right account controls: a Help button (replays the onboarding
 * walkthrough), an API-keys settings button, and Clerk's UserButton (account /
 * sign-out). Renders nothing when auth is off, so the dormant app is unchanged.
 *
 * Exception: the screenshot pipeline (scripts/capture-onboarding.mjs) runs with
 * auth off but needs the Settings dialog on screen for the `08-settings` slide.
 * `VITE_CAPTURE_MODE=1` renders the Help/Settings buttons without Clerk's
 * UserButton (there is no session to render).
 */
import { UserButton } from '@clerk/clerk-react';
import { HelpCircle, Settings } from 'lucide-react';

import { AUTH_ENABLED } from '@/config/auth';
import { Button } from '@/components/ui/button';
import { useOnboarding } from '@/components/onboarding/use-onboarding';
import { ApiKeysSettings } from './api-keys-settings';

const CAPTURE_MODE = (import.meta.env.VITE_CAPTURE_MODE as string | undefined) === '1';

export function UserMenu() {
  const { openWelcome } = useOnboarding();
  if (!AUTH_ENABLED && !CAPTURE_MODE) return null;
  return (
    <div className="fixed right-3 top-[calc(env(safe-area-inset-top)+0.5rem)] z-30 flex items-center gap-1.5">
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
      {AUTH_ENABLED && <UserButton afterSignOutUrl="/" />}
    </div>
  );
}
