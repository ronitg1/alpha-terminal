/**
 * SleeveThesisBar — inline LLM thesis generator for a single sleeve.
 *
 * Embedded inside SleeveSection. Click "Generate sleeve memo" → calls
 * POST /sleeves/thesis/sleeve/{name}. Result cached server-side by the
 * sleeve's row signature, so re-clicks are free.
 */
import { Button } from '@/components/ui/button';
import { sleevesApi } from '@/services/sleeves-api';
import type { Thesis } from '@/types/sleeves';
import { ChevronDown, ChevronUp, RefreshCw, Wand2 } from 'lucide-react';
import { useState } from 'react';

interface SleeveThesisBarProps {
  sleeveName: string;
}

export function SleeveThesisBar({ sleeveName }: SleeveThesisBarProps) {
  const [thesis, setThesis] = useState<Thesis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await sleevesApi.getSleeveThesis(sleeveName);
      setThesis(r);
      setExpanded(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="px-4 py-2.5 bg-muted/10 border-b border-border/40">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Sleeve Memo
          {thesis && (
            <span className="ml-2 normal-case tracking-normal font-mono text-[10px] text-muted-foreground/70">
              · LLM bias {thesis.bias}
              {thesis.top_long ? ` · long ${thesis.top_long}` : ''}
              {thesis.top_short ? ` · short ${thesis.top_short}` : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant={thesis ? 'ghost' : 'outline'}
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={handleGenerate}
            disabled={loading}
            title="Synthesize a sleeve-scoped PM memo (~$0.05)"
          >
            {loading ? (
              <RefreshCw className="h-3.5 w-3.5 mr-1 animate-spin" />
            ) : (
              <Wand2 className="h-3.5 w-3.5 mr-1" />
            )}
            {thesis ? 'Refresh' : 'Generate memo'}
          </Button>
          {thesis && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="mt-1.5 text-[11px] px-2 py-1 rounded border border-rose-500/30 bg-rose-500/5 text-rose-700 dark:text-rose-400">
          Thesis call failed: {error}
        </div>
      )}

      {thesis && (
        <div className="mt-2 text-sm leading-relaxed">
          <p className="italic">"{thesis.condensed}"</p>
          {expanded && (
            <div className="mt-3 pt-3 border-t border-border/40 whitespace-pre-wrap text-[13px] leading-relaxed">
              {thesis.full}
              <div className="text-[10px] text-muted-foreground italic mt-2">
                Generated {new Date(thesis.generated_at).toLocaleString()}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
