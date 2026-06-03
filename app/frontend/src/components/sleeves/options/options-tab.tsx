/**
 * OptionsTab — top-level container for the options screener.
 *
 * Strategies are discovered server-side via GET /sleeves/options/strategies
 * so the pill row + explainer adapt automatically when a strategy is added
 * or renamed on the backend. The screener endpoint is then hit with the
 * selected (sleeve, strategy) pair.
 *
 * Wrapped in a tab-scoped SleevesProvider so the sleeve picker can read
 * config.sleeves without depending on the SleevesTab subtree.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { sleevesApi } from '@/services/sleeves-api';
import { OptionsScreenerResponse, OptionsStrategyMeta } from '@/types/sleeves';
import {
  Activity,
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  ChevronUp,
  Compass,
  Crosshair,
  Flame,
  Info,
  RefreshCw,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Waves,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { OptionsScreenerCard } from './options-screener-card';

const DEFAULT_SLEEVE = 'mega_tech';
const DEFAULT_STRATEGY = 'weakness';
const EXPLAINER_KEY = 'options-explainer-open';

// Icon registry — backend strategies key into this for the pill glyph. New
// strategies fall through to a neutral dot.
const STRATEGY_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  weakness: TrendingDown,
  strength: TrendingUp,
  momentum: Activity,
  mean_reversion: Waves,
  breakout: ArrowUpToLine,
  breakdown: ArrowDownToLine,
  volume_spike: Zap,
  pullback: Crosshair,
  trend_bias: Compass,
  vol_expansion: Flame,
  unusual_options_activity: Sparkles,
};

export function OptionsTab() {
  return <OptionsTabContent />;
}

export function OptionsTabContent() {
  const { config } = useSleevesContext();
  const [strategies, setStrategies] = useState<OptionsStrategyMeta[]>([]);
  const [sleeve, setSleeve] = useState<string>(DEFAULT_SLEEVE);
  const [strategy, setStrategy] = useState<string>(DEFAULT_STRATEGY);
  const [data, setData] = useState<OptionsScreenerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Explainer collapse state, persisted across reloads.
  const [explainerOpen, setExplainerOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(EXPLAINER_KEY);
      return v === null ? true : v === 'true';
    } catch {
      return true;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(EXPLAINER_KEY, String(explainerOpen));
    } catch {
      // Private mode etc. — silent ignore.
    }
  }, [explainerOpen]);

  // Load the strategy registry once on mount.
  // Filter out any chart-pattern strategies (pattern_* keys) — these are
  // pure technical screener strategies only.
  useEffect(() => {
    void (async () => {
      try {
        const resp = await sleevesApi.getOptionsStrategies();
        setStrategies(resp.strategies.filter((s) => !s.key.startsWith('pattern_')));
      } catch (err) {
        console.error('Failed to load strategies catalog:', err);
      }
    })();
  }, []);

  const load = useCallback(async (slv: string, strat: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await sleevesApi.getOptionsScreener(slv, strat);
      setData(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(sleeve, strategy);
  }, [load, sleeve, strategy]);

  const activeMeta = useMemo(
    () => strategies.find((s) => s.key === strategy),
    [strategies, strategy]
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 max-w-5xl mx-auto">
        <header className="mb-4">
          <h1 className="text-xl font-semibold">Options Screener</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {activeMeta?.description ??
              'Pick a strategy, pick a sleeve, then explore option chains for the ranked candidates.'}
          </p>
        </header>

        {/* Strategy grid — one regular box per strategy, lays out as a wrap
            grid in the normal page flow. */}
        <StrategyGrid
          strategies={strategies}
          activeKey={strategy}
          onPick={setStrategy}
        />

        <Explainer
          open={explainerOpen}
          onToggle={() => setExplainerOpen((o) => !o)}
          strategies={strategies}
          activeKey={strategy}
        />

        <div className="flex items-center gap-3 mb-4 mt-4 flex-wrap">
          <label className="text-xs text-muted-foreground">Sleeve</label>
          <select
            value={sleeve}
            onChange={(e) => setSleeve(e.target.value)}
            className="bg-background border border-border rounded px-2 py-1 text-sm font-mono"
          >
            {(config?.sleeves ?? [{ name: DEFAULT_SLEEVE } as { name: string }]).map((s) => (
              <option key={s.name} value={s.name}>
                {s.name.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load(sleeve, strategy)}
            disabled={loading}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          {data?.generated_at && (
            <span className="text-[10px] text-muted-foreground">
              generated {new Date(data.generated_at).toLocaleTimeString()}
            </span>
          )}
          <div className="flex-1" />
          <ConvictionLegend />
        </div>

        {error && (
          <div className="text-xs text-rose-500 italic mb-3">Failed to load screener: {error}</div>
        )}

        {loading && !data && (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-12 rounded bg-muted-foreground/10 animate-pulse" />
            ))}
          </div>
        )}

        {data && data.candidates.length === 0 && !loading && (
          <div className="text-xs text-muted-foreground italic px-2 py-3 rounded border border-dashed">
            No candidates returned for this sleeve.
          </div>
        )}

        {data && (
          <div className="space-y-2">
            {data.candidates.map((c, i) => (
              <OptionsScreenerCard key={c.ticker} candidate={c} defaultOpen={i === 0} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Strategy grid — regular boxes ──────────────────────────────────────────

function StrategyGrid({
  strategies,
  activeKey,
  onPick,
}: {
  strategies: OptionsStrategyMeta[];
  activeKey: string;
  onPick: (k: string) => void;
}) {
  if (strategies.length === 0) {
    return (
      <div className="text-[10px] text-muted-foreground italic mb-4">
        loading strategies…
      </div>
    );
  }
  return (
    <div className="mb-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
      {strategies.map((s) => {
        const Icon = STRATEGY_ICONS[s.key];
        const active = s.key === activeKey;
        return (
          <button
            key={s.key}
            type="button"
            onClick={() => onPick(s.key)}
            title={s.description}
            className={cn(
              'rounded-md border px-3 py-2 text-left text-xs transition-colors flex items-start gap-2',
              active
                ? 'border-foreground/60 bg-foreground/5 text-foreground'
                : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted/30 hover:border-foreground/30'
            )}
          >
            {Icon ? (
              <Icon className="h-4 w-4 flex-shrink-0 mt-0.5" />
            ) : (
              <span className="h-4 w-4 flex-shrink-0" />
            )}
            <div className="min-w-0 flex-1">
              <div className="font-medium leading-tight">{s.label}</div>
              <div className="text-[10px] opacity-70 leading-snug mt-0.5 truncate">
                {s.subtitle}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ─── Explainer (collapsible) ────────────────────────────────────────────────

function Explainer({
  open,
  onToggle,
  strategies,
  activeKey,
}: {
  open: boolean;
  onToggle: () => void;
  strategies: OptionsStrategyMeta[];
  activeKey: string;
}) {
  return (
    <div className="rounded-md border border-sky-500/30 bg-sky-500/5">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-sky-500/5"
      >
        <Info className="h-3.5 w-3.5 text-sky-600 dark:text-sky-400" />
        <span className="text-xs font-medium text-sky-700 dark:text-sky-300">
          How to read this
        </span>
        <div className="flex-1" />
        {open ? (
          <ChevronUp className="h-3.5 w-3.5 text-sky-600 dark:text-sky-400" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-sky-600 dark:text-sky-400" />
        )}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3 text-xs leading-relaxed text-foreground/90 border-t border-sky-500/20 pt-3">
          <Section title="What this does">
            For every ticker in the selected sleeve, the active strategy runs
            three short-term signals. Each fired signal is magnitude-weighted
            (how far past the threshold?) to produce a{' '}
            <Strong>conviction %</Strong> score (0–100%). Tickers ranked
            highest-conviction first. Candidates scoring ≥40% receive expiry
            tier recommendations; click a tier pill to jump to that DTE in
            the chain viewer.
          </Section>

          <Section title="Strategies">
            {strategies.length === 0 ? (
              <span className="text-muted-foreground italic">loading…</span>
            ) : (
              <ul className="list-disc pl-5 space-y-1">
                {strategies.map((s) => (
                  <li key={s.key}>
                    <Strong>{s.label}</Strong>
                    {s.key === activeKey && (
                      <span className="text-sky-700 dark:text-sky-300"> (active)</span>
                    )}
                    {' — '}
                    {s.description}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-muted-foreground">
              Switch with the Strategy pills above. The sleeve picker is
              independent — all strategies work on any sleeve.
            </p>
          </Section>

          <Section title="Signal chips">
            Each card shows three small chips for the active strategy's
            signals (e.g. <Mono>20d vs QQQ</Mono>, <Mono>Z-score</Mono>,{' '}
            <Mono>RSI extreme</Mono>). The chip text shows the current value;
            it turns <span className="text-amber-700 dark:text-amber-400">amber</span>{' '}
            when the rule fires. Hover any chip for the exact threshold and
            current reading.
          </Section>

          <Section title="How to use it">
            <ol className="list-decimal pl-5 space-y-1">
              <li>Pick a strategy + sleeve.</li>
              <li>Scan the list — high-conviction names rank to the top.</li>
              <li>
                Click a card to open its option chain — calls on the left, puts
                on the right. The strike closest to spot is highlighted. Use
                the <Strong>Expiry</Strong> dropdown to switch between near-term
                weeklies and longer-dated contracts.
              </li>
              <li>
                Click any contract row to copy a paste-ready trade ticker (e.g.{' '}
                <Mono>MSFT 2026-05-30 410C @ $2.81</Mono>). The dashboard is
                signals-only — paste into your broker.
              </li>
            </ol>
          </Section>

          <Section title="Chain table glossary">
            <ul className="list-disc pl-5 space-y-1">
              <li><Strong>Strike</Strong>: the price at which the option can be exercised.</li>
              <li><Strong>Last</Strong>: most recent traded price (per share — × 100 for the per-contract premium).</li>
              <li><Strong>Bid / Ask</Strong>: what buyers are paying / what sellers are asking. The gap is the spread.</li>
              <li><Strong>IV</Strong>: Implied Volatility — the market's annualized forecast of underlying movement. Higher = more expensive option.</li>
              <li><Strong>Δ (Delta)</Strong>: how much the option price moves per $1 of underlying. Calls 0→1; puts 0→−1.</li>
              <li><Strong>Vol / OI</Strong>: today's volume / total open interest. Higher = more liquid contract.</li>
            </ul>
          </Section>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-sky-700 dark:text-sky-300 mb-1">
        {title}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Strong({ children }: { children: React.ReactNode }) {
  return <span className="font-semibold text-foreground">{children}</span>;
}

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <code className="font-mono text-[10px] bg-muted px-1 py-0.5 rounded">{children}</code>
  );
}

// ─── Conviction legend ──────────────────────────────────────────────────────

function ConvictionLegend() {
  return (
    <div className="hidden md:flex items-center gap-1.5 text-[10px] text-muted-foreground">
      <span className="mr-1">Conviction:</span>
      <LegendChip label="≥80%" cls="border-emerald-500/60 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" />
      <LegendChip label="60–79%" cls="border-amber-500/60 bg-amber-500/10 text-amber-700 dark:text-amber-400" />
      <LegendChip label="40–59%" cls="border-yellow-500/40 bg-yellow-500/5 text-yellow-700 dark:text-yellow-400" />
      <LegendChip label="<40%" cls="opacity-60" />
    </div>
  );
}

function LegendChip({ label, cls }: { label: string; cls: string }) {
  return (
    <Badge variant="outline" className={cn('text-[10px] font-mono px-1.5 py-0', cls)}>
      {label}
    </Badge>
  );
}
