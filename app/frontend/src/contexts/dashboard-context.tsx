/**
 * DashboardContext — top-level UI state for the Google Finance-style layout.
 *
 * Owns:
 *   section          — which of the 3 main sections is active
 *   screeningSubTab  — active sub-tab within the Screening section
 *   selectedTicker   — the ticker whose detail is shown in the Market view
 *   chatOpen         — whether the right AI chat panel is visible
 */

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react';
import { DashboardSection, ScreeningSubTab } from '@/types/sleeves';

const STORAGE_KEY = 'dashboard-state-v1';

interface Persisted {
  section: DashboardSection;
  screeningSubTab: ScreeningSubTab;
  chatOpen: boolean;
}

function loadPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as Persisted;
  } catch { /* ignore */ }
  return { section: 'market', screeningSubTab: 'options', chatOpen: true };
}

interface DashboardContextType {
  section: DashboardSection;
  screeningSubTab: ScreeningSubTab;
  selectedTicker: string | null;
  chatOpen: boolean;
  setSection: (s: DashboardSection) => void;
  setScreeningSubTab: (t: ScreeningSubTab) => void;
  setSelectedTicker: (t: string | null) => void;
  toggleChat: () => void;
}

const DashboardContext = createContext<DashboardContextType | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const init = loadPersisted();
  const [section, setSection] = useState<DashboardSection>(init.section);
  const [screeningSubTab, setScreeningSubTab] = useState<ScreeningSubTab>(init.screeningSubTab);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(init.chatOpen);

  const toggleChat = useCallback(() => setChatOpen((o) => !o), []);

  // Navigate to market section when a ticker is selected from the left nav
  const selectTicker = useCallback((t: string | null) => {
    setSelectedTicker(t);
    if (t) setSection('market');
  }, []);

  // Persist section + sub-tab + chatOpen (not selectedTicker — stale ticker on reload is confusing)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ section, screeningSubTab, chatOpen }));
    } catch { /* ignore */ }
  }, [section, screeningSubTab, chatOpen]);

  return (
    <DashboardContext.Provider
      value={{
        section,
        screeningSubTab,
        selectedTicker,
        chatOpen,
        setSection,
        setScreeningSubTab,
        setSelectedTicker: selectTicker,
        toggleChat,
      }}
    >
      {children}
    </DashboardContext.Provider>
  );
}

export function useDashboard(): DashboardContextType {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error('useDashboard must be used inside DashboardProvider');
  return ctx;
}
