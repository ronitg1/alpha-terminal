/**
 * AnalysisCards — renders the 9-section transcript analysis output.
 */

import { TranscriptAnalysis } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { AlertTriangle, Quote, ShieldAlert, Swords, Target, TrendingUp } from 'lucide-react';

const RELEVANCE_CLS: Record<string, string> = {
  high: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  medium: 'border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400',
  low: 'border-border bg-muted/40 text-muted-foreground',
};
const SIGNAL_CLS: Record<string, string> = {
  bullish: 'text-emerald-500',
  bearish: 'text-rose-500',
  neutral: 'text-muted-foreground',
};
const DIRECTION_CLS: Record<string, string> = {
  confirms: 'border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400',
  strengthens: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  weakens: 'border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400',
  breaks: 'border-rose-500/40 bg-rose-500/10 text-rose-600 dark:text-rose-400',
};

export function AnalysisCards({ analysis: a }: { analysis: TranscriptAnalysis }) {
  const score = a.sentimentScore;
  const scoreCls = score > 2 ? 'text-emerald-500' : score < -2 ? 'text-rose-500' : 'text-amber-500';

  return (
    <div className="space-y-4 pt-2">
      {/* Headline: sentiment + thesis impact */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-4 flex flex-col items-center justify-center">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Sentiment vs prior Q
          </div>
          <div className={cn('text-3xl font-bold font-mono', scoreCls)}>
            {score > 0 ? '+' : ''}{score}
          </div>
          <div className="text-[10px] text-muted-foreground">scale −10 … +10</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4 md:col-span-2">
          <div className="flex items-center gap-2 mb-1.5">
            <Target className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Thesis impact</span>
            <span
              className={cn(
                'text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded border',
                DIRECTION_CLS[a.thesisImpact.direction] ?? DIRECTION_CLS.confirms,
              )}
            >
              {a.thesisImpact.direction}
            </span>
          </div>
          <p className="text-xs leading-relaxed text-foreground/90">{a.thesisImpact.narrative}</p>
        </div>
      </div>

      {/* Tone delta + guidance language */}
      <Section icon={TrendingUp} title="Tone delta">
        <p className="text-xs leading-relaxed text-foreground/90">{a.toneDelta}</p>
      </Section>

      {a.guidanceLanguage && (
        <Section icon={AlertTriangle} title="Guidance & hedging language">
          <p className="text-xs leading-relaxed text-foreground/90">{a.guidanceLanguage}</p>
        </Section>
      )}

      {/* Key themes */}
      {a.keyThemes.length > 0 && (
        <Section icon={Quote} title="Key themes">
          <div className="space-y-2">
            {a.keyThemes.map((t, i) => (
              <div key={i} className="border-l-2 border-border pl-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold">{t.topic}</span>
                  <span className={cn('text-[9px] font-semibold uppercase px-1 py-0.5 rounded border', RELEVANCE_CLS[t.bookRelevance] ?? RELEVANCE_CLS.low)}>
                    {t.bookRelevance}
                  </span>
                </div>
                {t.quote && <p className="text-[11px] italic text-muted-foreground mt-0.5">"{t.quote}"</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Dodged questions */}
      {a.dodgedQuestions.length > 0 && (
        <Section icon={ShieldAlert} title="Dodged questions">
          <div className="space-y-2.5">
            {a.dodgedQuestions.map((d, i) => (
              <div key={i} className="text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{d.analyst}</span>
                  <span className={cn('text-[9px] font-semibold uppercase px-1 py-0.5 rounded border', RELEVANCE_CLS[d.importance] ?? RELEVANCE_CLS.low)}>
                    {d.importance}
                  </span>
                </div>
                <p className="text-foreground/90 mt-0.5"><span className="text-muted-foreground">Q:</span> {d.question}</p>
                <p className="text-muted-foreground"><span className="text-muted-foreground/70">Pivot:</span> {d.pivot}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Competitive + policy mentions side by side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {a.competitiveMentions.length > 0 && (
          <Section icon={Swords} title="Competitive mentions">
            <div className="space-y-2">
              {a.competitiveMentions.map((c, i) => (
                <div key={i} className="text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold">{c.competitor}</span>
                    <span className={cn('text-[10px] font-semibold', SIGNAL_CLS[c.signal] ?? SIGNAL_CLS.neutral)}>
                      ({c.signal})
                    </span>
                  </div>
                  <p className="text-muted-foreground">{c.context}</p>
                </div>
              ))}
            </div>
          </Section>
        )}

        {a.policyMentions.length > 0 && (
          <Section icon={ShieldAlert} title="Policy / regulatory">
            <div className="space-y-2">
              {a.policyMentions.map((p, i) => (
                <div key={i} className="text-xs">
                  <span className="font-semibold">{p.topic}</span>
                  {p.quote && <p className="text-[11px] italic text-muted-foreground mt-0.5">"{p.quote}"</p>}
                  <p className="text-foreground/80">{p.interpretation}</p>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>

      {/* Watch next quarter */}
      {a.watchNextQuarter.length > 0 && (
        <Section icon={Target} title="Watch next quarter">
          <ul className="space-y-1">
            {a.watchNextQuarter.map((w, i) => (
              <li key={i} className="text-xs text-foreground/90 flex gap-1.5">
                <span className="text-primary flex-shrink-0">•</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
        <h3 className="text-xs font-semibold uppercase tracking-wide">{title}</h3>
      </div>
      {children}
    </div>
  );
}
