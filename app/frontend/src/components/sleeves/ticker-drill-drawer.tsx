/**
 * TickerDrillDrawer — right-side Sheet that opens when the user selects
 * a ticker from any sleeve table or high-conviction strip card.
 *
 * Renders:
 *   • Header: ticker, sleeve, consensus pill, position type, hold period.
 *   • Per-agent accordion (one item per agent that produced output).
 *     Each item header shows signal + confidence; expanded body shows the
 *     rich fields that agent emits (variant_perception, catalysts, IRA
 *     stack, FEOC traffic light, S-curve position, AI exposure, etc.).
 *
 * The drawer reads from SleevesContext.selectedTicker. Closing the
 * drawer calls selectTicker(null).
 *
 * For agents without rich output (legacy upstream agents that only emit
 * {signal, confidence, reasoning}), the drawer renders whatever fields
 * happen to be in `raw`. For CSV-derived rows (no raw available), we
 * surface a small note explaining that fresh-scan data is richer.
 */

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { PerAgentVerdict, TickerRow } from '@/types/sleeves';
import { Sparkles } from 'lucide-react';
import { AnalystChip } from './analyst-chip';
import { SignalPill } from './signal-pill';
import { TrafficLight } from './traffic-light';

/** Pull a short preview of an agent's reasoning so the user sees the gist
 *  without expanding the accordion. CSV-only verdicts have no raw fields;
 *  fall back to the variant_perception text on the row when present.
 */
function reasoningPreview(verdict: PerAgentVerdict, fallback?: string): string {
  const raw = verdict.raw ?? {};
  const r = (raw.reasoning as string | undefined) ?? fallback ?? '';
  if (!r) return '';
  // First sentence or first ~140 chars, whichever comes first.
  const firstSentence = r.match(/^[^.!?\n]+[.!?]?/)?.[0] ?? r;
  return firstSentence.length > 140 ? firstSentence.slice(0, 140).trimEnd() + '…' : firstSentence;
}

export function TickerDrillDrawer() {
  const { selectedTicker, selectTicker, latestScan } = useSleevesContext();
  const open = selectedTicker !== null;

  const row: TickerRow | undefined = open
    ? latestScan?.rows.find((r) => r.ticker === selectedTicker)
    : undefined;

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) selectTicker(null);
      }}
    >
      <SheetContent side="right" className="w-[480px] sm:w-[560px] overflow-y-auto">
        {row ? <DrawerBody row={row} /> : <DrawerEmpty ticker={selectedTicker} />}
      </SheetContent>
    </Sheet>
  );
}

function DrawerEmpty({ ticker }: { ticker: string | null }) {
  return (
    <>
      <SheetHeader>
        <SheetTitle className="font-mono">{ticker ?? '—'}</SheetTitle>
        <SheetDescription>No data in the latest scan for this ticker yet.</SheetDescription>
      </SheetHeader>
      <p className="text-sm text-muted-foreground mt-4">
        Run a scan that includes <span className="font-mono">{ticker}</span> to see per-agent
        verdicts here.
      </p>
    </>
  );
}

function DrawerBody({ row }: { row: TickerRow }) {
  return (
    <>
      <SheetHeader>
        <div className="flex items-center gap-3">
          <SheetTitle className="font-mono text-2xl">{row.ticker}</SheetTitle>
          {row.has_variant_perception && (
            <Sparkles className="h-5 w-5 text-amber-500" />
          )}
        </div>
        <SheetDescription className="flex items-center gap-3 pt-1">
          <span className="uppercase text-[10px] tracking-wide">{row.sleeve.replace(/_/g, ' ')}</span>
          <SignalPill signal={row.consensus} confidence={row.avg_confidence} />
          <span className="font-mono text-xs">
            score {row.weighted_score.toFixed(1)}
          </span>
        </SheetDescription>
      </SheetHeader>

      <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
        <KVRow label="Position" value={row.position_type.replace(/_/g, ' ')} />
        <KVRow label="Hold period" value={row.hold_period.replace(/_/g, ' ')} />
        <KVRow label="Highlight" value={row.highlight} />
        <KVRow label="Avg confidence" value={`${row.avg_confidence.toFixed(0)}%`} />
      </div>

      {row.variant_perception && (
        <div className="mt-4 p-3 rounded-md bg-amber-500/5 border border-amber-500/20">
          <div className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-400 mb-1 flex items-center gap-1.5">
            <Sparkles className="h-3 w-3" />
            Variant Perception
          </div>
          <div className="text-sm italic">"{row.variant_perception}"</div>
        </div>
      )}

      <Separator className="my-5" />

      <div className="text-xs uppercase tracking-wide text-muted-foreground mb-2">
        Per-Agent Verdicts · {row.per_agent.length}
      </div>

      {row.per_agent.length === 0 ? (
        <div className="text-sm text-muted-foreground italic">No agent verdicts.</div>
      ) : (
        <Accordion type="multiple" className="w-full">
          {row.per_agent.map((v) => {
            const preview = reasoningPreview(v, row.variant_perception);
            return (
              <AccordionItem key={v.agent} value={v.agent}>
                <AccordionTrigger className="hover:no-underline py-2">
                  <div className="flex flex-col flex-1 pr-3 gap-1.5">
                    <div className="flex items-center gap-3">
                      <AnalystChip agentKey={v.agent} variant="inline" className="text-sm" />
                      <div className="flex-1" />
                      <SignalPill signal={v.signal} confidence={v.confidence} compact />
                      <span className="font-mono text-xs text-muted-foreground tabular-nums w-8 text-right">
                        {Math.round(v.confidence)}
                      </span>
                    </div>
                    {preview && (
                      <div className="text-[11px] text-muted-foreground text-left leading-snug pr-2">
                        {preview}
                      </div>
                    )}
                  </div>
                </AccordionTrigger>
                <AccordionContent className="pt-2">
                  <AgentRichFields verdict={v} />
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
      )}
    </>
  );
}

function KVRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="font-mono">{value}</div>
    </div>
  );
}

// ─── Agent rich-field renderers ─────────────────────────────────────────────

function AgentRichFields({ verdict }: { verdict: PerAgentVerdict }) {
  const raw = verdict.raw ?? {};
  const hasRaw = Object.keys(raw).length > 0;

  if (!hasRaw) {
    return (
      <div className="text-xs text-muted-foreground italic">
        No rich output captured. Run a fresh scan to populate variant perception, catalysts, and
        agent-specific fields.
      </div>
    );
  }

  // Pull common fields first; everything else falls into KV pairs at the bottom.
  const variant = raw.variant_perception as string | undefined;
  const reasoning = raw.reasoning as string | undefined;
  const killSwitch = raw.kill_switch as string | undefined;
  const catalystNear = raw.catalyst_near_term as string | undefined;
  const catalystMedium = raw.catalyst_medium_term as string | undefined;
  const probWrong = raw.probability_wrong as string | undefined;

  // Energy transition specifics.
  const iraStack = raw.ira_credit_stack as string | undefined;
  const feocRisk = raw.feoc_risk as string | undefined;
  const subSector = raw.sub_sector as string | undefined;
  const unitEcon = raw.unit_economics_note as string | undefined;

  // Emerging tech specifics.
  const techCat = raw.tech_category as string | undefined;
  const moatType = raw.moat_type as string | undefined;
  const moatDur = raw.moat_durability as string | undefined;
  const sCurve = raw.s_curve_position as string | undefined;
  const aiExposure = raw.ai_exposure as string | undefined;
  const aiTailwind = raw.ai_tailwind as string | undefined;
  const valuation = raw.valuation_assessment as string | undefined;
  const competitors = raw.competitors_note as string | undefined;

  return (
    <div className="space-y-3 text-xs">
      {variant && variant.toLowerCase() !== 'no edge — skip' && (
        <Field label="Variant perception" value={`"${variant}"`} italic />
      )}

      {(iraStack || feocRisk || subSector || unitEcon) && (
        <div className="grid grid-cols-2 gap-2">
          {subSector && <KVChip label="Sub-sector" value={subSector} />}
          {iraStack && <KVChip label="IRA credit stack" value={iraStack} />}
          {feocRisk && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">FEOC risk</div>
              <TrafficLight status={feocRisk} field="feoc risk" />
            </div>
          )}
          {unitEcon && <KVChip label="Unit econ" value={unitEcon} span={2} />}
        </div>
      )}

      {(techCat || moatType || moatDur || sCurve || aiExposure || aiTailwind || valuation) && (
        <div className="grid grid-cols-2 gap-2">
          {techCat && <KVChip label="Tech category" value={techCat} />}
          {moatType && <KVChip label="Moat" value={`${moatType}${moatDur ? ` · ${moatDur}` : ''}`} />}
          {sCurve && <KVChip label="S-curve" value={sCurve} />}
          {aiExposure && <KVChip label="AI exposure" value={`${aiExposure}${aiTailwind ? ` · ${aiTailwind}` : ''}`} />}
          {valuation && <KVChip label="Valuation" value={valuation} span={2} />}
        </div>
      )}

      {competitors && <Field label="Competitors" value={competitors} />}

      {(catalystNear || catalystMedium) && (
        <div className="space-y-1">
          {catalystNear && <Field label="Catalyst (0-90d)" value={catalystNear} />}
          {catalystMedium && <Field label="Catalyst (90-365d)" value={catalystMedium} />}
        </div>
      )}

      {killSwitch && (
        <div className="p-2 rounded bg-rose-500/5 border border-rose-500/20">
          <div className="text-[10px] uppercase tracking-wide text-rose-700 dark:text-rose-400 mb-0.5">
            Kill switch
          </div>
          <div className="text-foreground/90">{killSwitch}</div>
        </div>
      )}

      {probWrong && <KVChip label="Probability wrong" value={probWrong} />}

      {reasoning && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
            Reasoning
          </div>
          <div className="leading-relaxed text-foreground/90 whitespace-pre-wrap">{reasoning}</div>
        </div>
      )}
    </div>
  );
}

function Field({ label, value, italic }: { label: string; value: string; italic?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">{label}</div>
      <div className={italic ? 'italic' : ''}>{value}</div>
    </div>
  );
}

function KVChip({ label, value, span }: { label: string; value: string; span?: number }) {
  return (
    <div className={span === 2 ? 'col-span-2' : ''}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-0.5">{label}</div>
      <Badge variant="outline" className="font-mono text-[11px]">
        {value}
      </Badge>
    </div>
  );
}
