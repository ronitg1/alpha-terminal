/**
 * SleeveManagerDialog — create / edit / delete sleeves.
 *
 * One dialog covers the whole sleeves config so the user can rebalance
 * allocations across sleeves atomically (the backend rejects writes whose
 * totals don't sum to 100%). Each sleeve is a card with editable fields;
 * a "+ New sleeve" affordance adds a draft slot pre-filled with sensible
 * defaults.
 *
 * Agent weights are auto-distributed equally across the selected agents.
 * Advanced per-agent weight tuning is intentionally not exposed here —
 * power users edit portfolio_config.py directly.
 *
 * Save uses the bulk ``PUT /sleeves/config`` endpoint. Delete is also done
 * by re-saving the whole config without the removed sleeve, in the same
 * round-trip as any allocation rebalance.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { cn } from '@/lib/utils';
import { sleevesApi } from '@/services/sleeves-api';
import { AlertCircle, Plus, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

const SLEEVE_NAME_RE = /^[a-z][a-z0-9_]{0,30}$/;
const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;
const ALLOC_TOLERANCE = 1e-6;

interface SleeveManagerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface DraftSleeve {
  name: string;
  allocation_pct: number;
  agents: string[];
  tickers: string[];
  /** Pre-existing sleeves carry their original name so we know what to
   *  serialise as the dict key (vs. new sleeves where the name is the
   *  user-typed value). */
  isNew: boolean;
}

export function SleeveManagerDialog({ open, onOpenChange }: SleeveManagerDialogProps) {
  const { config, analystMeta, refresh } = useSleevesContext();

  const [drafts, setDrafts] = useState<DraftSleeve[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Hydrate from live config every time the dialog opens.
  useEffect(() => {
    if (!open || !config) return;
    setError(null);
    setDrafts(
      config.sleeves.map((s) => ({
        name: s.name,
        allocation_pct: s.allocation_pct,
        agents: [...s.agents],
        tickers: [...s.tickers],
        isNew: false,
      })),
    );
  }, [open, config]);

  const totalAlloc = useMemo(
    () => drafts.reduce((sum, d) => sum + (d.allocation_pct || 0), 0),
    [drafts],
  );
  const allocOk = Math.abs(totalAlloc - 100) < ALLOC_TOLERANCE;
  const availableAgents = useMemo(() => {
    return Object.values(analystMeta).sort((a, b) => a.order - b.order);
  }, [analystMeta]);

  const addNewSleeve = () => {
    setDrafts((prev) => [
      ...prev,
      {
        name: '',
        allocation_pct: 0,
        agents: ['alpha_seeker'],
        tickers: [],
        isNew: true,
      },
    ]);
  };

  const removeSleeve = (idx: number) => {
    setDrafts((prev) => prev.filter((_, i) => i !== idx));
  };

  const update = (idx: number, patch: Partial<DraftSleeve>) => {
    setDrafts((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  };

  const validateAll = (): string | null => {
    if (drafts.length === 0) return 'Need at least one sleeve.';
    const seenNames = new Set<string>();
    for (const d of drafts) {
      const name = d.name.trim();
      if (!name) return 'Every sleeve needs a name.';
      if (!SLEEVE_NAME_RE.test(name)) {
        return `Bad name "${name}". Use lowercase letters, digits, underscores; start with a letter.`;
      }
      if (seenNames.has(name)) return `Duplicate sleeve name: ${name}.`;
      seenNames.add(name);
      if (d.allocation_pct < 0 || d.allocation_pct > 100) {
        return `${name}: allocation_pct must be 0..100.`;
      }
      if (!d.agents.length) return `${name}: pick at least one agent.`;
      for (const t of d.tickers) {
        if (!TICKER_RE.test(t)) return `${name}: bad ticker "${t}".`;
      }
    }
    if (!allocOk) {
      return `Allocations must sum to 100%; currently ${totalAlloc.toFixed(2)}%.`;
    }
    return null;
  };

  const save = async () => {
    const err = validateAll();
    if (err) {
      setError(err);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body: Record<
        string,
        { allocation_pct: number; agents: string[]; agent_weights: Record<string, number>; tickers: string[] }
      > = {};
      for (const d of drafts) {
        const evenWeight = 1 / d.agents.length;
        const weights: Record<string, number> = {};
        d.agents.forEach((a, i) => {
          weights[a] = i === d.agents.length - 1 ? 1 - evenWeight * (d.agents.length - 1) : evenWeight;
        });
        body[d.name.trim()] = {
          allocation_pct: d.allocation_pct,
          agents: d.agents,
          agent_weights: weights,
          tickers: d.tickers,
        };
      }
      await sleevesApi.replaceAllSleeves(body);
      // Refresh the SleevesContext so all dropdowns elsewhere update.
      await refresh();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Manage sleeves</DialogTitle>
          <DialogDescription>
            Edit existing sleeves or add new ones. Allocations across sleeves must sum to 100%. Saves
            atomically — re-balancing two sleeves at once is one click.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 my-2">
          {drafts.map((d, i) => (
            <SleeveCard
              key={i}
              draft={d}
              availableAgents={availableAgents}
              onChange={(patch) => update(i, patch)}
              onRemove={() => removeSleeve(i)}
            />
          ))}

          <Button variant="outline" size="sm" onClick={addNewSleeve} className="w-full">
            <Plus className="h-3.5 w-3.5 mr-1.5" />
            Add sleeve
          </Button>
        </div>

        <div className="text-xs flex items-center gap-2 mt-2">
          <span className="text-muted-foreground">Total allocation:</span>
          <Badge
            variant="outline"
            className={cn(
              'font-mono',
              allocOk
                ? 'border-emerald-500/50 text-emerald-700 dark:text-emerald-400'
                : 'border-rose-500/50 text-rose-700 dark:text-rose-400',
            )}
          >
            {totalAlloc.toFixed(2)}%
          </Badge>
          {!allocOk && (
            <span className="text-rose-600 dark:text-rose-400">
              must equal 100% to save
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 text-xs text-rose-600 dark:text-rose-400 mt-2 p-2 rounded border border-rose-500/30 bg-rose-500/5">
            <AlertCircle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving || !allocOk}>
            {saving ? 'Saving…' : 'Save sleeves'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Single sleeve card ─────────────────────────────────────────────────────

function SleeveCard({
  draft,
  availableAgents,
  onChange,
  onRemove,
}: {
  draft: DraftSleeve;
  availableAgents: { key: string; display_name: string; description: string }[];
  onChange: (patch: Partial<DraftSleeve>) => void;
  onRemove: () => void;
}) {
  // Local state is the raw textarea content (preserves typing freedom — trailing
  // commas, double spaces while the user is mid-word). Initialised once from
  // draft.tickers; no useEffect sync, otherwise typing "NVDA, " would get
  // normalised back to "NVDA" on every keystroke and lose the trailing comma.
  const [tickerInput, setTickerInput] = useState(() => draft.tickers.join(', '));

  /** Parse the raw textarea content into a deduped uppercase ticker list. */
  const parseTickers = (raw: string): string[] => {
    const parsed = raw
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of parsed) {
      if (!seen.has(t)) {
        seen.add(t);
        out.push(t);
      }
    }
    return out;
  };

  const handleTickerChange = (raw: string) => {
    setTickerInput(raw);
    // Commit to parent state on every keystroke so a fast Save click can't
    // race the textarea's blur event. Previously this only happened in
    // onBlur, which meant typing tickers and immediately clicking Save would
    // persist the OLD ticker list — the bug the user reported.
    onChange({ tickers: parseTickers(raw) });
  };

  const toggleAgent = (agentKey: string) => {
    const has = draft.agents.includes(agentKey);
    onChange({ agents: has ? draft.agents.filter((a) => a !== agentKey) : [...draft.agents, agentKey] });
  };

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="sleeve_name (lowercase + underscores)"
          value={draft.name}
          onChange={(e) => onChange({ name: e.target.value.toLowerCase() })}
          disabled={!draft.isNew}
          className="font-mono text-sm flex-1"
        />
        <div className="flex items-center gap-1">
          <Input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={draft.allocation_pct}
            onChange={(e) => onChange({ allocation_pct: Number(e.target.value) })}
            className="w-20 font-mono text-sm"
          />
          <span className="text-xs text-muted-foreground">%</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-rose-500 hover:bg-rose-500/10 hover:text-rose-600"
          onClick={onRemove}
          title="Remove sleeve"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Agents ({draft.agents.length} selected · equal weights)
        </div>
        <div className="grid grid-cols-2 gap-1 max-h-32 overflow-y-auto pr-1">
          {availableAgents.map((a) => (
            <label
              key={a.key}
              className="flex items-center gap-2 text-xs cursor-pointer py-0.5 hover:bg-muted/30 rounded px-1"
              title={a.description}
            >
              <Checkbox
                checked={draft.agents.includes(a.key)}
                onCheckedChange={() => toggleAgent(a.key)}
              />
              <span className="truncate">{a.display_name}</span>
            </label>
          ))}
        </div>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
          Tickers ({draft.tickers.length})
        </div>
        <textarea
          value={tickerInput}
          onChange={(e) => handleTickerChange(e.target.value)}
          placeholder="NVDA, MSFT, GOOGL, …"
          className="w-full bg-background border border-border rounded px-2 py-1.5 text-sm font-mono min-h-[60px]"
        />
        <div className="text-[10px] text-muted-foreground mt-1">
          Comma or space separated. Uppercase; A–Z, digits, dot, hyphen.
        </div>
      </div>
    </div>
  );
}
