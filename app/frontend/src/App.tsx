import { AuthGate } from './components/auth/auth-gate';
import { UserMenu } from './components/auth/user-menu';
import { DashboardLayout } from './components/DashboardLayout';
import { OnboardingProvider } from './components/onboarding/use-onboarding';
import { Toaster } from './components/ui/sonner';

export default function App() {
  return (
    <>
      <AuthGate>
        <OnboardingProvider>
          <DashboardLayout />
          <UserMenu />
        </OnboardingProvider>
      </AuthGate>
      <Toaster />
    </>
  );
}
