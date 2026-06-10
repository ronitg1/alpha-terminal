/**
 * PortfolioSection — Portfolio Pulse page.
 *
 * Layout (top → bottom):
 *   1. Portfolio metrics bar (scan date, position counts, avg conviction, bias breakdown)
 *   2. High conviction names (top 5 across all sleeves)
 *   3. LLM portfolio memo (on-demand button + display)
 *   4. Per-sleeve accordion groups (each with signal mini-bar)
 */

import { useSleevesContext } from '@/contexts/sleeves-context';
import { postSse, sleevesApi } from '@/services/sleeves-api';
import { cn } from '@/lib/utils';
import { PerAgentVerdict, Quote, Thesis, TickerRow, TickerThesis } from '@/types/sleeves';
import { FinnhubSnapshot } from '@/components/dashboard/finnhub-snapshot';
import {
  AlertTriangle,
  BarChart2,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  Minus,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
  Users,
  Zap,
} from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';

// ─── Saved theses (hydration) ────────────────────────────────────────────────
// Loaded once by PortfolioSection from GET /sleeves/thesis/saved. Keys:
// 'portfolio' | 'sleeve:<name>' | 'ticker:<SYMBOL>:<quick|deep>'. Components
// seed their state from here so a refresh doesn't lose paid-for analyses.
const SavedThesesContext = createContext<Record<string, Thesis | TickerThesis>>({});

// ─── Helpers ─────────────────────────────────────────────────────────────────

function signalColor(consensus: string) {
  if (consensus === 'bullish') return 'text-emerald-500';
  if (consensus === 'bearish') return 'text-rose-500';
  return 'text-yellow-500';
}

function signalBg(consensus: string) {
  if (consensus === 'bullish') return 'bg-emerald-500/10 border-emerald-500/30';
  if (consensus === 'bearish') return 'bg-rose-500/10 border-rose-500/30';
  return 'bg-yellow-500/10 border-yellow-500/30';
}

function SignalIcon({ consensus }: { consensus: string }) {
  if (consensus === 'bullish') return <TrendingUp className="h-3 w-3" />;
  if (consensus === 'bearish') return <TrendingDown className="h-3 w-3" />;
  return <Minus className="h-3 w-3" />;
}

function fmtPrice(p: number | null | undefined): string {
  if (p == null) return '—';
  return '$' + p.toFixed(2);
}

function fmtPct(p: number | null | undefined): string {
  if (p == null) return '';
  return (p >= 0 ? '+' : '') + p.toFixed(2) + '%';
}

function shortThesis(text: string | null | undefined): string {
  if (!text) return '';
  const first = text.split(/[.!?\n]/)[0]?.trim() ?? '';
  return first.length > 120 ? first.slice(0, 120) + '…' : first;
}

function prettyAgent(key: string): string {
  return key
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

// ─── Overall conviction + recommendation ─────────────────────────────────────

type Verdict = {
  /** Action call: Strong Buy / Buy / Accumulate / Hold / Trim / Sell / Strong Sell. */
  label: string;
  tone: 'bull' | 'bear' | 'neutral';
  /** 0–100 conviction strength (magnitude of the weighted agent agreement). */
  score: number;
  blurb: string;
};

/**
 * Collapse the agent panel into a single actionable call for one name.
 *
 * `weighted_score` is a signed −100..100 blend (sign = direction, magnitude =
 * how strongly the *weighted* agents agree). Its magnitude is the conviction:
 * a lone dissenting agent cancels weight and pulls the score toward zero, so a
 * split book lands on "Hold" with low conviction even if each agent is
 * individually confident. The label tiers off the signed score; the score
 * shown is the magnitude.
 */
function deriveVerdict(row: TickerRow): Verdict {
  const ws = Math.max(-100, Math.min(100, row.weighted_score ?? 0));
  const score = Math.round(Math.abs(ws));
  const n = row.per_agent?.length ?? 0;
  const conf = Math.round(row.avg_confidence ?? 0);

  let label: string;
  let tone: 'bull' | 'bear' | 'neutral';
  if (ws >= 65) {
    label = 'Strong Buy';
    tone = 'bull';
  } else if (ws >= 35) {
    label = 'Buy';
    tone = 'bull';
  } else if (ws >= 12) {
    label = 'Accumulate';
    tone = 'bull';
  } else if (ws > -12) {
    label = 'Hold';
    tone = 'neutral';
  } else if (ws > -35) {
    label = 'Trim';
    tone = 'bear';
  } else if (ws > -65) {
    label = 'Sell';
    tone = 'bear';
  } else {
    label = 'Strong Sell';
    tone = 'bear';
  }

  const blurb =
    n > 0
      ? `Weighted blend of ${n} agent${n === 1 ? '' : 's'} · avg confidence ${conf}%`
      : 'No agent analysis yet — run a scan to score this name.';
  return { label, tone, score, blurb };
}

function verdictTextCls(tone: Verdict['tone']): string {
  if (tone === 'bull') return 'text-emerald-600 dark:text-emerald-400';
  if (tone === 'bear') return 'text-rose-600 dark:text-rose-400';
  return 'text-amber-600 dark:text-amber-400';
}

function verdictBorderCls(tone: Verdict['tone']): string {
  if (tone === 'bull') return 'border-emerald-500/40 bg-emerald-500/10';
  if (tone === 'bear') return 'border-rose-500/40 bg-rose-500/10';
  return 'border-amber-500/40 bg-amber-500/10';
}

function verdictBarCls(tone: Verdict['tone']): string {
  if (tone === 'bull') return 'bg-emerald-500';
  if (tone === 'bear') return 'bg-rose-500';
  return 'bg-amber-500';
}

/** Compact recommendation chip for the collapsed ticker row. */
function VerdictPill({ verdict }: { verdict: Verdict }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border flex-shrink-0',
        verdictBorderCls(verdict.tone),
        verdictTextCls(verdict.tone),
      )}
      title={`Overall recommendation · conviction ${verdict.score}/100`}
    >
      {verdict.label}
      <span className="font-mono tabular-nums opacity-80">{verdict.score}</span>
    </span>
  );
}

/** Headline verdict card shown at the top of a name's expanded detail. */
function ConvictionSummary({ row }: { row: TickerRow }) {
  const v = deriveVerdict(row);
  return (
    <div className="rounded-lg border border-border/60 bg-card p-3">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div>
          <div className="text-[9px] uppercase tracking-wider text-muted-foreground font-semibold">
            Overall recommendation
          </div>
          <div className={cn('text-lg font-bold leading-tight', verdictTextCls(v.tone))}>{v.label}</div>
        </div>
        <div className="text-right">
          <div className="text-[9px] uppercase tracking-wider text-muted-foreground font-semibold">Conviction</div>
          <div className="flex items-baseline gap-0.5 justify-end">
            <span className={cn('text-2xl font-bold font-mono tabular-nums leading-none', verdictTextCls(v.tone))}>
              {v.score}
            </span>
            <span className="text-[11px] text-muted-foreground">/100</span>
          </div>
        </div>
      </div>
      <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
        <div className={cn('h-full rounded-full', verdictBarCls(v.tone))} style={{ width: `${v.score}%` }} />
      </div>
      <p className="text-[11px] text-muted-foreground mt-2">{v.blurb}</p>
    </div>
  );
}

function agentSignalCls(signal: string): string {
  if (signal === 'bullish') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400';
  if (signal === 'bearish') return 'border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-400';
  return 'border-border bg-muted/40 text-muted-foreground';
}

/** Read a raw agent field as a clean string, treating n/a-style values as absent. */
function rawStr(raw: Record<string, unknown> | undefined, key: string): string | null {
  const v = raw?.[key];
  if (v == null) return null;
  // A structured (dict/array) value can't be rendered as a sentence — bail out
  // so callers fall back to a structured renderer instead of "[object Object]".
  if (typeof v === 'object') return null;
  const s = String(v).trim();
  if (!s || s === '[object Object]' || /^(n\/?_?a|none|null|skip|no edge.*|n_a)$/i.test(s)) return null;
  return s;
}

/**
 * Some agents (notably `fundamentals_analyst`) store `reasoning` as a dict of
 * category signals — `{ profitability_signal: { signal, details }, ... }` —
 * rather than a sentence. Flatten that into renderable rows. Returns null when
 * `reasoning` is absent or is a plain string (handled by `rawStr`).
 */
type ReasoningPart = { label: string; signal: string | null; details: string };

function reasoningParts(raw: Record<string, unknown> | undefined): ReasoningPart[] | null {
  const v = raw?.['reasoning'];
  if (v == null || typeof v !== 'object' || Array.isArray(v)) return null;
  const parts: ReasoningPart[] = [];
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (val == null) continue;
    const label = k.replace(/_signal$/i, '').replace(/_/g, ' ').trim();
    if (typeof val === 'object' && !Array.isArray(val)) {
      const o = val as Record<string, unknown>;
      const signal = o.signal != null ? String(o.signal).trim() : null;
      const details = o.details != null ? String(o.details).trim() : '';
      if (!details && !signal) continue;
      parts.push({ label, signal, details });
    } else {
      const details = String(val).trim();
      if (!details || details === '[object Object]') continue;
      parts.push({ label, signal: null, details });
    }
  }
  return parts.length ? parts : null;
}

function SectionLabel({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5 mb-2">
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {children}
      </span>
    </div>
  );
}

// ─── Per-agent verdict card (the deep-dive focus) ────────────────────────────

function AgentVerdictCard({ agent }: { agent: PerAgentVerdict }) {
  const raw = agent.raw;
  const reasoning = rawStr(raw, 'reasoning');
  const reasoningRows = reasoningParts(raw);
  const edge = rawStr(raw, 'variant_perception');
  const killSwitch = rawStr(raw, 'kill_switch');
  const catalysts = [
    rawStr(raw, 'catalyst_near_term'),
    rawStr(raw, 'catalyst_medium_term'),
  ].filter((c): c is string => !!c);
  const meta = [
    rawStr(raw, 'conviction') && `conviction: ${rawStr(raw, 'conviction')}`,
    rawStr(raw, 'position_type') && `${rawStr(raw, 'position_type')!.replace(/_/g, ' ')}`,
    rawStr(raw, 'pair_with') && `pair: ${rawStr(raw, 'pair_with')}`,
    rawStr(raw, 'hold_period') && `hold: ${rawStr(raw, 'hold_period')!.replace(/_/g, ' ')}`,
  ].filter((m): m is string => !!m);

  const confColor =
    agent.confidence >= 70 ? 'bg-emerald-500' : agent.confidence >= 50 ? 'bg-amber-500' : 'bg-rose-500/60';

  return (
    <div className="rounded-md border border-border/60 bg-card p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <span className={cn('text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border', agentSignalCls(agent.signal))}>
          {agent.signal}
        </span>
        <span className="text-xs font-semibold">{prettyAgent(agent.agent)}</span>
        <div className="flex-1" />
        <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
          {agent.confidence.toFixed(0)}%
        </span>
      </div>
      <div className="h-1 w-full bg-muted rounded-full overflow-hidden mb-2">
        <div className={cn('h-full rounded-full', confColor)} style={{ width: `${Math.min(100, agent.confidence)}%` }} />
      </div>

      {reasoningRows ? (
        <div className="space-y-1">
          {reasoningRows.map((r) => (
            <div key={r.label} className="flex items-start gap-1.5 text-[11px] leading-relaxed">
              {r.signal && (
                <span
                  className={cn(
                    'mt-0.5 h-1.5 w-1.5 rounded-full flex-shrink-0',
                    r.signal === 'bullish'
                      ? 'bg-emerald-500'
                      : r.signal === 'bearish'
                        ? 'bg-rose-500'
                        : 'bg-muted-foreground/50',
                  )}
                />
              )}
              <span className="text-foreground/85">
                <span className="font-semibold capitalize">{r.label}: </span>
                {r.details}
              </span>
            </div>
          ))}
        </div>
      ) : reasoning ? (
        <p className="text-[11px] leading-relaxed text-foreground/85">{reasoning}</p>
      ) : (
        <p className="text-[11px] italic text-muted-foreground">No detailed reasoning recorded.</p>
      )}

      {edge && (
        <p className="text-[11px] leading-relaxed mt-1.5">
          <span className="text-amber-600 dark:text-amber-400 font-semibold">Edge: </span>
          <span className="text-foreground/85">{edge}</span>
        </p>
      )}

      {catalysts.length > 0 && (
        <div className="flex items-start gap-1.5 mt-1.5">
          <Target className="h-3 w-3 text-sky-500 flex-shrink-0 mt-0.5" />
          <p className="text-[11px] text-foreground/80 leading-relaxed">{catalysts.join(' · ')}</p>
        </div>
      )}

      {killSwitch && (
        <div className="flex items-start gap-1.5 mt-1.5">
          <AlertTriangle className="h-3 w-3 text-rose-500 flex-shrink-0 mt-0.5" />
          <p className="text-[11px] text-foreground/80 leading-relaxed">
            <span className="text-rose-500 font-medium">Kill switch: </span>
            {killSwitch}
          </p>
        </div>
      )}

      {meta.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {meta.map((m) => (
            <span key={m} className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-border/60 text-muted-foreground">
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Confidence bar ──────────────────────────────────────────────────────────

function ConfidenceBar({ value, label }: { value: number; label?: string }) {
  const color =
    value >= 70 ? 'bg-emerald-500' : value >= 50 ? 'bg-amber-500' : 'bg-rose-500/60';
  return (
    <div className="flex items-center gap-2">
      {label && (
        <span className="text-[10px] text-muted-foreground w-28 shrink-0 truncate">{label}</span>
      )}
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={cn('h-full rounded-full', color)} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
      <span className="text-[10px] font-mono tabular-nums w-8 text-right">{value.toFixed(0)}%</span>
    </div>
  );
}

// ─── Portfolio metrics bar ───────────────────────────────────────────────────

function MetricsBar({ rows }: { rows: TickerRow[] }) {
  if (rows.length === 0) return null;
  const bull = rows.filter((r) => r.consensus === 'bullish').length;
  const bear = rows.filter((r) => r.consensus === 'bearish').length;
  const neut = rows.length - bull - bear;
  const avgConf = rows.reduce((s, r) => s + r.avg_confidence, 0) / rows.length;

  const metrics = [
    { label: 'Positions', value: String(rows.length), color: 'text-foreground' },
    { label: 'Bullish', value: String(bull), color: 'text-emerald-500' },
    { label: 'Bearish', value: String(bear), color: 'text-rose-500' },
    { label: 'Neutral', value: String(neut), color: 'text-yellow-500' },
    { label: 'Avg Conviction', value: `${avgConf.toFixed(0)}%`, color: avgConf >= 70 ? 'text-emerald-500' : avgConf >= 50 ? 'text-amber-500' : 'text-rose-500' },
  ];

  return (
    <div className="grid grid-cols-5 gap-3 rounded-lg border border-border/60 bg-card p-4">
      {metrics.map((m) => (
        <div key={m.label} className="text-center">
          <div className={cn('text-xl font-bold font-mono tabular-nums', m.color)}>{m.value}</div>
          <div className="text-[10px] text-muted-foreground mt-0.5">{m.label}</div>
        </div>
      ))}
    </div>
  );
}

// ─── High conviction names ────────────────────────────────────────────────────

function HighConvictionSection({
  rows,
  quotes,
}: {
  rows: TickerRow[];
  quotes: Record<string, Quote>;
}) {
  const top = [...rows]
    .filter((r) => r.consensus === 'bullish' || r.consensus === 'bearish')
    .sort((a, b) => b.avg_confidence - a.avg_confidence)
    .slice(0, 5);

  if (top.length === 0) return null;

  return (
    <div>
      <h2 className="text-sm font-semibold mb-3 text-muted-foreground uppercase tracking-wider text-xs">
        High Conviction
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-5 gap-2">
        {top.map((r) => {
          const q = quotes[r.ticker];
          return (
            <div
              key={r.ticker}
              className={cn(
                'rounded-lg border p-3 space-y-1.5',
                signalBg(r.consensus),
              )}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono font-bold text-sm">{r.ticker}</span>
                <span className={cn('text-[10px] font-medium', signalColor(r.consensus))}>
                  {r.consensus}
                </span>
              </div>
              <div className="text-xs font-mono text-muted-foreground">
                {fmtPrice(q?.last)}
                {q?.pct_change != null && (
                  <span className={cn('ml-1.5', q.pct_change >= 0 ? 'text-emerald-500' : 'text-rose-500')}>
                    {fmtPct(q.pct_change)}
                  </span>
                )}
              </div>
              <ConfidenceBar value={r.avg_confidence} />
              <p className="text-[10px] text-muted-foreground truncate">
                {r.sleeve.replace(/_/g, ' ')}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── LLM Portfolio Memo ───────────────────────────────────────────────────────

function PortfolioMemo() {
  const saved = useContext(SavedThesesContext);
  const [thesis, setThesis] = useState<Thesis | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Hydrate the last saved portfolio memo (unless the user already re-ran).
  useEffect(() => {
    const persisted = saved['portfolio'] as Thesis | undefined;
    if (persisted) setThesis((cur) => cur ?? persisted);
  }, [saved]);

  const generate = async () => {
    if (loading) return;
    setLoading(true);
    setErr(null);
    try {
      const t = await sleevesApi.getPortfolioThesis();
      setThesis(t);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const biasColor = thesis?.bias === 'bullish'
    ? 'text-emerald-500'
    : thesis?.bias === 'bearish'
    ? 'text-rose-500'
    : 'text-yellow-500';

  return (
    <div className="rounded-lg border border-border/60 bg-card">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40">
        <div>
          <h2 className="text-sm font-semibold">Portfolio Thesis</h2>
          <p className="text-[10px] text-muted-foreground">Full LLM analysis across every sleeve</p>
        </div>
        <button
          type="button"
          onClick={() => void generate()}
          disabled={loading}
          className={cn(
            'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border transition-colors',
            thesis
              ? 'text-sky-300 border-sky-500/40 hover:bg-sky-500/10'
              : 'text-primary border-primary/40 bg-primary/5 hover:bg-primary/10',
          )}
        >
          <Sparkles className="h-3 w-3" />
          {loading ? 'Analyzing…' : thesis ? 'Refresh' : 'Run full thesis'}
        </button>
      </div>
      {err && (
        <div className="px-4 py-3 text-xs text-rose-400">{err}</div>
      )}
      {!thesis && !err && (
        <div className="px-4 py-3 text-xs text-muted-foreground italic">
          Run a whole-portfolio thesis — an LLM synthesis of the book across all sleeves, with bias, top long/short, and the reasoning behind it.
        </div>
      )}
      {thesis && (
        <div className="px-4 py-4 space-y-3">
          <div className="flex items-center gap-3">
            <span className={cn('text-xs font-semibold uppercase', biasColor)}>{thesis.bias}</span>
            <span className="text-[10px] text-muted-foreground">·</span>
            <span className="text-[10px] text-muted-foreground">{thesis.scan_date}</span>
            {thesis.top_long && (
              <>
                <span className="text-[10px] text-muted-foreground">·</span>
                <span className="text-[10px] text-emerald-500">Top long: {thesis.top_long}</span>
              </>
            )}
            {thesis.top_short && (
              <>
                <span className="text-[10px] text-muted-foreground">·</span>
                <span className="text-[10px] text-rose-500">Top short: {thesis.top_short}</span>
              </>
            )}
          </div>
          <p className="text-sm font-medium leading-relaxed">{thesis.condensed}</p>
          {thesis.full && thesis.full !== thesis.condensed && (
            <p className="text-xs text-foreground/80 leading-relaxed whitespace-pre-wrap">{thesis.full}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Expanded ticker detail ───────────────────────────────────────────────────

function TickerDetail({ row, ticker }: { row: TickerRow; ticker: string }) {
  // A fresh per-name scan (if run) overrides the row's saved verdicts.
  const [scannedRow, setScannedRow] = useState<TickerRow | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<string>('');
  const [scanErr, setScanErr] = useState<string | null>(null);

  const agents = (scannedRow ?? row).per_agent ?? [];

  const runScan = useCallback(async () => {
    if (scanning) return;
    setScanning(true);
    setScanErr(null);
    setScanProgress('Starting…');
    try {
      await postSse(`/sleeves/scan/ticker/${encodeURIComponent(ticker)}`, {}, (event, data) => {
        if (event === 'progress') {
          const d = data as { agent?: string; status?: string };
          setScanProgress(d.agent ? `${d.agent.replace(/_/g, ' ')}: ${d.status ?? ''}` : (d.status ?? ''));
        } else if (event === 'complete') {
          // CompleteEvent serializes its payload under a nested `data` key.
          const payload = ((data as { data?: { row?: TickerRow } }).data ?? data) as { row?: TickerRow };
          if (payload.row) setScannedRow(payload.row);
          setScanning(false);
        } else if (event === 'error') {
          setScanErr((data as { message?: string }).message ?? 'Scan failed');
          setScanning(false);
        }
      });
    } catch (e) {
      setScanErr(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }, [ticker, scanning]);

  return (
    <div className="space-y-5 pt-3">
      {/* Headline verdict — overall recommendation + conviction for this name,
          derived from the (possibly freshly re-run) agent panel. */}
      <ConvictionSummary row={scannedRow ?? row} />

      {/* Snapshot — the headline fundamentals you need to know (Finnhub). */}
      <FinnhubSnapshot ticker={ticker} />

      {/* Agent verdicts — the focus: each agent's signal, conviction, thesis. */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <SectionLabel icon={Users}>
            Agent verdicts{agents.length > 0 ? ` (${agents.length})` : ''}
          </SectionLabel>
          <button
            type="button"
            onClick={() => void runScan()}
            disabled={scanning}
            title="Run this name's sleeve agents now (does not overwrite the saved morning scan)"
            className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary transition-colors disabled:opacity-50"
          >
            <Sparkles className="h-3 w-3" />
            {scanning ? 'Running…' : agents.length > 0 ? 'Re-run agents' : 'Run agents'}
          </button>
        </div>

        {scanning && (
          <p className="text-[11px] text-muted-foreground italic mb-2">{scanProgress || 'Running agents…'}</p>
        )}
        {scanErr && <p className="text-[11px] text-rose-500 italic mb-2">{scanErr}</p>}

        {agents.length > 0 ? (
          <div className="space-y-2">
            {agents.map((a) => (
              <AgentVerdictCard key={a.agent} agent={a} />
            ))}
          </div>
        ) : (
          !scanning && (
            <p className="text-xs text-muted-foreground italic">
              No agent analysis yet — click <strong>Run agents</strong> to score this name now, or run a full morning scan.
            </p>
          )
        )}
      </div>

      {/* Idea synthesis — LLM thesis grounded in the agents + fundamentals. */}
      <div>
        <SectionLabel icon={Lightbulb}>Idea synthesis</SectionLabel>
        <RunAnalysis ticker={ticker} />
      </div>
    </div>
  );
}

// ─── Run analysis (Quick take / Deep analysis) ──────────────────────────────

const BIAS_CLS: Record<string, string> = {
  bullish: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  bearish: 'border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-400',
  neutral: 'border-border bg-muted/40 text-muted-foreground',
};

/** Render a small subset of markdown (**bold** + line breaks) cleanly. */
function MarkdownLite({ text }: { text: string }) {
  return (
    <div className="text-xs leading-relaxed text-foreground/90 space-y-1.5">
      {text.split('\n').filter((l) => l.trim()).map((line, i) => {
        const parts = line.split(/(\*\*[^*]+\*\*)/g);
        return (
          <p key={i}>
            {parts.map((p, j) =>
              p.startsWith('**') && p.endsWith('**') ? (
                <strong key={j} className="text-foreground">{p.slice(2, -2)}</strong>
              ) : (
                <span key={j}>{p}</span>
              ),
            )}
          </p>
        );
      })}
    </div>
  );
}

function RunAnalysis({ ticker }: { ticker: string }) {
  const saved = useContext(SavedThesesContext);
  const [result, setResult] = useState<TickerThesis | null>(null);
  const [loading, setLoading] = useState<'quick' | 'deep' | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Hydrate the last saved analysis for this name — deep beats quick.
  useEffect(() => {
    const persisted = (saved[`ticker:${ticker}:deep`] ?? saved[`ticker:${ticker}:quick`]) as
      | TickerThesis
      | undefined;
    if (persisted) setResult((cur) => cur ?? persisted);
  }, [saved, ticker]);

  const run = useCallback(
    async (depth: 'quick' | 'deep') => {
      setLoading(depth);
      setErr(null);
      try {
        const r = await sleevesApi.getTickerThesis(ticker, depth);
        setResult(r);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(null);
      }
    },
    [ticker],
  );

  return (
    <div className="rounded-md border border-primary/20 bg-primary/[0.03] p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-muted-foreground">
          Synthesize an idea from the agents + fundamentals:
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => void run('quick')}
          disabled={loading !== null}
          className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded border border-border hover:border-foreground/40 hover:text-foreground text-muted-foreground transition-colors disabled:opacity-50"
        >
          <Zap className="h-3 w-3" />
          {loading === 'quick' ? 'Thinking…' : 'Quick take'}
        </button>
        <button
          type="button"
          onClick={() => void run('deep')}
          disabled={loading !== null}
          className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary transition-colors disabled:opacity-50"
        >
          <Sparkles className="h-3 w-3" />
          {loading === 'deep' ? 'Analyzing…' : 'Deep analysis'}
        </button>
      </div>

      {err && <p className="text-[11px] text-rose-500 italic mt-2">Failed: {err}</p>}

      {result && (
        <div className="mt-3 space-y-2 border-t border-border/40 pt-3">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded border',
                BIAS_CLS[result.bias] ?? BIAS_CLS.neutral,
              )}
            >
              {result.bias}
            </span>
            <span className="text-[10px] text-muted-foreground">
              {result.depth === 'deep' ? 'Deep analysis' : 'Quick take'}
            </span>
          </div>
          {result.condensed && (
            <p className="text-xs font-medium text-foreground">{result.condensed}</p>
          )}
          {result.full && <MarkdownLite text={result.full} />}
        </div>
      )}
    </div>
  );
}

// ─── Single ticker row ────────────────────────────────────────────────────────

function TickerPulseRow({
  row,
  quote,
  allocationPct,
}: {
  row: TickerRow;
  quote: Quote | undefined;
  allocationPct: number | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const thesis = shortThesis(row.variant_perception);

  return (
    <div className={cn('border border-border/60 rounded-lg overflow-hidden', expanded && 'border-border')}>
      {/* Header row */}
      <button
        type="button"
        onClick={() => setExpanded((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/20 text-left transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
        )}

        {/* Signal pill */}
        <span
          className={cn(
            'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border flex-shrink-0',
            signalBg(row.consensus),
            signalColor(row.consensus),
          )}
        >
          <SignalIcon consensus={row.consensus} />
          {row.consensus}
        </span>

        {/* Ticker */}
        <span className="font-mono font-bold text-sm w-16 flex-shrink-0">{row.ticker}</span>

        {/* Thesis snippet */}
        <span className="flex-1 text-xs text-muted-foreground truncate hidden sm:block min-w-0">
          {thesis || <span className="italic">No thesis — run a scan.</span>}
        </span>

        {/* Overall recommendation + conviction */}
        <VerdictPill verdict={deriveVerdict(row)} />

        {/* Allocation */}
        {allocationPct != null && allocationPct > 0 && (
          <span className="text-[10px] font-mono text-muted-foreground flex-shrink-0">
            {allocationPct.toFixed(1)}%
          </span>
        )}

        {/* Price */}
        <div className="flex flex-col items-end flex-shrink-0 w-20">
          <span className="font-mono text-sm">{fmtPrice(quote?.last)}</span>
          {quote?.pct_change != null && (
            <span
              className={cn(
                'text-[10px] font-mono',
                quote.pct_change >= 0 ? 'text-emerald-500' : 'text-rose-500',
              )}
            >
              {fmtPct(quote.pct_change)}
            </span>
          )}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-border/50">
          <TickerDetail row={row} ticker={row.ticker} />
        </div>
      )}
    </div>
  );
}

// ─── Sleeve group ─────────────────────────────────────────────────────────────

function SleeveGroup({
  sleeveName,
  tickers,
  rows,
  quotes,
  portfolioSettings,
}: {
  sleeveName: string;
  tickers: string[];
  rows: TickerRow[];
  quotes: Record<string, Quote>;
  portfolioSettings: Record<string, { allocation_pct: number }>;
}) {
  const [open, setOpen] = useState(true);
  const saved = useContext(SavedThesesContext);
  const [thesis, setThesis] = useState<Thesis | null>(null);
  const [thesisLoading, setThesisLoading] = useState(false);
  const [thesisErr, setThesisErr] = useState<string | null>(null);

  // Hydrate the last saved sleeve thesis (unless the user already re-ran).
  useEffect(() => {
    const persisted = saved[`sleeve:${sleeveName}`] as Thesis | undefined;
    if (persisted) setThesis((cur) => cur ?? persisted);
  }, [saved, sleeveName]);

  // Sleeve-wide agent run — a filtered live scan over just this sleeve.
  // Results stream in via the shared SSE handler (sleeve_complete merges
  // rows into latestScan) and persist merged into today's saved scan.
  const { runScan, scanStatus, liveActivity } = useSleevesContext();
  const [scanningSleeve, setScanningSleeve] = useState(false);

  const runAgents = useCallback(async () => {
    if (scanningSleeve || scanStatus === 'running') return;
    setScanningSleeve(true);
    setOpen(true);
    try {
      await runScan({ sleeves: [sleeveName] });
    } finally {
      setScanningSleeve(false);
    }
  }, [runScan, scanningSleeve, scanStatus, sleeveName]);

  // Latest progress line while THIS sleeve's scan is running.
  const lastActivity = scanningSleeve && liveActivity.length > 0
    ? liveActivity[liveActivity.length - 1]
    : null;

  const sleeveRows = rows.filter((r) => r.sleeve === sleeveName);
  const rowMap = Object.fromEntries(sleeveRows.map((r) => [r.ticker, r]));

  const runThesis = useCallback(async () => {
    if (thesisLoading) return;
    setThesisLoading(true);
    setThesisErr(null);
    try {
      setThesis(await sleevesApi.getSleeveThesis(sleeveName));
      setOpen(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setThesisErr(
        /404/.test(msg)
          ? 'No scan data for this sleeve yet — run a morning scan first.'
          : msg,
      );
    } finally {
      setThesisLoading(false);
    }
  }, [sleeveName, thesisLoading]);

  const bullish = sleeveRows.filter((r) => r.consensus === 'bullish').length;
  const bearish = sleeveRows.filter((r) => r.consensus === 'bearish').length;
  const neutral = sleeveRows.length - bullish - bearish;
  const total = sleeveRows.length;

  const totalAlloc = tickers.reduce(
    (s, t) => s + (portfolioSettings[t]?.allocation_pct ?? 0),
    0,
  );

  return (
    <div className="space-y-2">
      {/* Sleeve header */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-3 text-left group flex-1 min-w-0"
        >
          {open ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          )}
          <h2 className="font-semibold text-base capitalize">{sleeveName.replace(/_/g, ' ')}</h2>
          <span className="text-xs text-muted-foreground">{tickers.length} positions</span>

          {/* Signal summary */}
          {total > 0 && (
            <div className="flex items-center gap-2 text-[10px]">
              {bullish > 0 && <span className="text-emerald-500">{bullish} bullish</span>}
              {bearish > 0 && <span className="text-rose-500">{bearish} bearish</span>}
              {neutral > 0 && <span className="text-muted-foreground">{neutral} neutral</span>}
            </div>
          )}
        </button>
        {totalAlloc > 0 && (
          <span className="text-xs text-muted-foreground font-mono flex-shrink-0">{totalAlloc.toFixed(1)}% alloc</span>
        )}
        <button
          type="button"
          onClick={() => void runAgents()}
          disabled={scanningSleeve || scanStatus === 'running'}
          title={`Run the agent panel on all ${tickers.length} names in this sleeve (updates today's saved scan)`}
          className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary transition-colors disabled:opacity-50 flex-shrink-0"
        >
          <Zap className="h-3 w-3" />
          {scanningSleeve ? 'Running agents…' : 'Run agents'}
        </button>
        <button
          type="button"
          onClick={() => void runThesis()}
          disabled={thesisLoading}
          title="Synthesize an LLM thesis across this sleeve's scanned names"
          className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary transition-colors disabled:opacity-50 flex-shrink-0"
        >
          <Sparkles className="h-3 w-3" />
          {thesisLoading ? 'Analyzing…' : thesis ? 'Refresh thesis' : 'Run thesis'}
        </button>
      </div>

      {/* Live agent progress while this sleeve's scan runs */}
      {scanningSleeve && (
        <div className="ml-7 text-[11px] text-muted-foreground italic">
          {lastActivity
            ? `${lastActivity.agent.replace(/_/g, ' ')}${lastActivity.ticker ? ` · ${lastActivity.ticker}` : ''}: ${lastActivity.status}`
            : 'Starting agent panel…'}
        </div>
      )}

      {/* Sleeve thesis result */}
      {thesisErr && (
        <div className="ml-7 text-[11px] text-rose-500 italic">{thesisErr}</div>
      )}
      {thesis && open && (
        <div className="ml-7 rounded-md border border-primary/20 bg-primary/[0.03] p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className={cn('text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded border', BIAS_CLS[thesis.bias] ?? BIAS_CLS.neutral)}>
              {thesis.bias}
            </span>
            <span className="text-[10px] text-muted-foreground">Sleeve thesis · scan {thesis.scan_date}</span>
            {thesis.top_long && <span className="text-[10px] text-emerald-500">▲ {thesis.top_long}</span>}
            {thesis.top_short && <span className="text-[10px] text-rose-500">▼ {thesis.top_short}</span>}
          </div>
          {thesis.condensed && <p className="text-xs font-medium text-foreground">{thesis.condensed}</p>}
          {thesis.full && thesis.full !== thesis.condensed && <MarkdownLite text={thesis.full} />}
        </div>
      )}

      {/* Signal mini-bar */}
      {open && total > 0 && (
        <div className="flex h-1 rounded-full overflow-hidden gap-px ml-7">
          {bullish > 0 && (
            <div className="bg-emerald-500" style={{ width: `${(bullish / total) * 100}%` }} />
          )}
          {neutral > 0 && (
            <div className="bg-yellow-500/50" style={{ width: `${(neutral / total) * 100}%` }} />
          )}
          {bearish > 0 && (
            <div className="bg-rose-500" style={{ width: `${(bearish / total) * 100}%` }} />
          )}
        </div>
      )}

      {/* Ticker rows */}
      {open && (
        <div className="space-y-1.5 ml-7">
          {tickers.map((t) => (
            <TickerPulseRow
              key={t}
              row={
                rowMap[t] ?? {
                  ticker: t,
                  sleeve: sleeveName,
                  consensus: 'neutral',
                  weighted_score: 0,
                  avg_confidence: 0,
                  highlight: 'neutral',
                  position_type: '',
                  hold_period: '',
                  has_variant_perception: false,
                  variant_perception: '',
                  per_agent: [],
                }
              }
              quote={quotes[t]}
              allocationPct={portfolioSettings[t]?.allocation_pct ?? null}
            />
          ))}
          {tickers.length === 0 && (
            <p className="text-xs text-muted-foreground italic">No tickers in this sleeve.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export function PortfolioSection() {
  const { config, latestScan, portfolioSettings } = useSleevesContext();
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [savedTheses, setSavedTheses] = useState<Record<string, Thesis | TickerThesis>>({});

  const allTickers = config?.sleeves.flatMap((s) => s.tickers) ?? [];

  const fetchQuotes = useCallback(async () => {
    if (allTickers.length === 0) return;
    try {
      const { quotes: q } = await sleevesApi.getQuotes(allTickers);
      setQuotes(q);
    } catch {
      /* non-fatal */
    }
  }, [allTickers.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    void fetchQuotes();
  }, [fetchQuotes]);

  // Hydrate previously-run analyses (portfolio/sleeve/ticker theses) so a
  // refresh doesn't blank out work the user already paid LLM credits for.
  useEffect(() => {
    let cancelled = false;
    sleevesApi
      .getSavedTheses()
      .then(({ theses }) => { if (!cancelled) setSavedTheses(theses); })
      .catch(() => { /* non-fatal — components just start empty */ });
    return () => { cancelled = true; };
  }, []);

  const rows = latestScan?.rows ?? [];

  return (
    <SavedThesesContext.Provider value={savedTheses}>
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-8">
        {/* Header */}
        <div className="flex items-center gap-3">
          <BarChart2 className="h-5 w-5 text-muted-foreground" />
          <div>
            <h1 className="text-xl font-semibold">Portfolio Pulse</h1>
            <p className="text-xs text-muted-foreground">
              {latestScan
                ? `Scan: ${latestScan.date} · ${latestScan.row_count} signals`
                : 'No scan data — run a morning scan to populate.'}
            </p>
          </div>
        </div>

        {/* Portfolio metrics */}
        <MetricsBar rows={rows} />

        {/* High conviction names */}
        {rows.length > 0 && <HighConvictionSection rows={rows} quotes={quotes} />}

        {/* LLM memo */}
        <PortfolioMemo />

        {(!config || config.sleeves.length === 0) && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center">
            <p className="text-sm text-muted-foreground italic">
              No sleeves configured. Add sleeves in the Market → Sleeves management panel.
            </p>
          </div>
        )}

        {/* Divider before per-sleeve breakdown */}
        {config && config.sleeves.length > 0 && rows.length > 0 && (
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border/60" />
            <span className="text-[10px] text-muted-foreground uppercase tracking-wider">By Sleeve</span>
            <div className="flex-1 h-px bg-border/60" />
          </div>
        )}

        {/* One group per sleeve */}
        {config?.sleeves.map((sleeve) => (
          <SleeveGroup
            key={sleeve.name}
            sleeveName={sleeve.name}
            tickers={sleeve.tickers}
            rows={rows}
            quotes={quotes}
            portfolioSettings={portfolioSettings[sleeve.name] ?? {}}
          />
        ))}
      </div>
    </div>
    </SavedThesesContext.Provider>
  );
}
