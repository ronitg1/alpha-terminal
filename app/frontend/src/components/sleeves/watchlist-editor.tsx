/**
 * WatchlistEditor — dialog for adding/removing/commenting tickers in the
 * opportunistic sleeve's watchlist (src/config/watchlist.py).
 *
 * Opens via a controlled prop. Loads current watchlist on open. Local
 * edits stay in component state until Save, which calls
 * SleevesContext.saveWatchlist (PUT /sleeves/watchlist) and closes.
 *
 * Validation mirrors the backend: tickers must match ^[A-Z][A-Z0-9.\-]{0,9}$.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { WatchlistEntry } from '@/types/sleeves';
import { Plus, X } from 'lucide-react';
import { useEffect, useState } from 'react';

const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

interface WatchlistEditorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function WatchlistEditor({ open, onOpenChange }: WatchlistEditorProps) {
  const { watchlist, loadWatchlist, saveWatchlist } = useSleevesContext();

  const [draft, setDraft] = useState<WatchlistEntry[]>([]);
  const [newTicker, setNewTicker] = useState('');
  const [newComment, setNewComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Hydrate draft from server state every time the dialog opens.
  useEffect(() => {
    if (open) {
      setError(null);
      setNewTicker('');
      setNewComment('');
      void loadWatchlist().then(() => setDraft(watchlist));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep draft in sync when watchlist refreshes (after save).
  useEffect(() => {
    if (open) setDraft(watchlist);
  }, [watchlist, open]);

  const addTicker = () => {
    const t = newTicker.trim().toUpperCase();
    if (!t) return;
    if (!TICKER_RE.test(t)) {
      setError(`Invalid ticker: ${t}`);
      return;
    }
    if (draft.some((e) => e.ticker === t)) {
      setError(`${t} already in watchlist`);
      return;
    }
    setDraft((prev) => [...prev, { ticker: t, comment: newComment.trim() }]);
    setNewTicker('');
    setNewComment('');
    setError(null);
  };

  const removeTicker = (ticker: string) => {
    setDraft((prev) => prev.filter((e) => e.ticker !== ticker));
  };

  const updateComment = (ticker: string, comment: string) => {
    setDraft((prev) =>
      prev.map((e) => (e.ticker === ticker ? { ...e, comment } : e))
    );
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveWatchlist(draft);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Opportunistic Watchlist</DialogTitle>
          <DialogDescription>
            Ad-hoc tickers scanned by the opportunistic sleeve (alpha_seeker +
            michael_burry). Writes to <span className="font-mono">src/config/watchlist.py</span> —
            comments survive saves.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex gap-2">
            <Input
              placeholder="TICKER"
              value={newTicker}
              onChange={(e) => setNewTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter') addTicker();
              }}
              className="font-mono w-32"
              maxLength={10}
            />
            <Input
              placeholder="why (optional)"
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') addTicker();
              }}
              className="flex-1"
            />
            <Button variant="outline" size="sm" onClick={addTicker}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>

          {error && (
            <div className="text-xs text-rose-700 dark:text-rose-400">{error}</div>
          )}

          <div className="border border-border rounded-md max-h-72 overflow-y-auto divide-y divide-border">
            {draft.length === 0 ? (
              <div className="p-4 text-sm text-muted-foreground italic text-center">
                Watchlist is empty. Add tickers above.
              </div>
            ) : (
              draft.map((e) => (
                <div key={e.ticker} className="flex items-center gap-2 p-2">
                  <Badge variant="outline" className="font-mono w-20 justify-center">
                    {e.ticker}
                  </Badge>
                  <Input
                    placeholder="why"
                    value={e.comment}
                    onChange={(ev) => updateComment(e.ticker, ev.target.value)}
                    className="flex-1 h-8 text-xs"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeTicker(e.ticker)}
                    aria-label={`Remove ${e.ticker}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))
            )}
          </div>

          <div className="text-[11px] text-muted-foreground">
            {draft.length} {draft.length === 1 ? 'ticker' : 'tickers'} · saved to disk on confirm
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void handleSave()} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
