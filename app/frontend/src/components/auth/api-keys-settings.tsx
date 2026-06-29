/**
 * BYOK API-key settings (Phase 3). Lets a signed-in user add/replace/remove
 * their own provider keys. DeepSeek is required (it powers every LLM scan/thesis
 * /chat and is billed per use); Massive (market data) and Finnhub (news) are
 * optional — the app falls back to the shared keys for those.
 *
 * Key values are write-only: the API never returns them, so the UI only shows
 * whether a key is set, never the value.
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { API_BASE_URL } from '@/lib/api-base';

type Provider = 'deepseek' | 'massive' | 'finnhub';

const PROVIDERS: { id: Provider; label: string; required: boolean; help: string }[] = [
  { id: 'deepseek', label: 'DeepSeek', required: true, help: 'Required — powers AI scans, theses, and chat (billed to your key).' },
  { id: 'massive', label: 'Massive (Polygon)', required: false, help: 'Market data. Approved accounts use the shared key; otherwise add your own.' },
  { id: 'finnhub', label: 'Finnhub', required: false, help: 'News & fundamentals. Approved accounts use the shared key; otherwise add your own.' },
];

interface KeySummary { provider: string; has_key: boolean }

export function ApiKeysSettings({ trigger }: { trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api-keys/`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows: KeySummary[] = await res.json();
      setPresent(new Set(rows.filter((r) => r.has_key).map((r) => r.provider)));
    } catch (e) {
      toast.error(`Could not load your keys: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  async function save(provider: Provider) {
    const key = (drafts[provider] ?? '').trim();
    if (!key) return;
    setBusy(provider);
    try {
      const res = await fetch(`${API_BASE_URL}/api-keys/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, key_value: key }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`;
        throw new Error(detail);
      }
      toast.success(`${provider} key saved and verified.`);
      setDrafts((d) => ({ ...d, [provider]: '' }));
      await refresh();
    } catch (e) {
      toast.error(`Couldn't save ${provider} key: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(null);
    }
  }

  async function remove(provider: Provider) {
    setBusy(provider);
    try {
      const res = await fetch(`${API_BASE_URL}/api-keys/${provider}`, { method: 'DELETE' });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      toast.success(`${provider} key removed.`);
      await refresh();
    } catch (e) {
      toast.error(`Couldn't remove ${provider} key: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>API keys</DialogTitle>
          <DialogDescription>
            Bring your own provider keys. Keys are encrypted and never shown again after saving.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {PROVIDERS.map((p) => {
            const isSet = present.has(p.id);
            return (
              <div key={p.id} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{p.label}</span>
                  {p.required && <Badge variant="secondary">Required</Badge>}
                  {isSet ? (
                    <Badge className="ml-auto" variant="success">Set</Badge>
                  ) : (
                    <Badge className="ml-auto" variant="outline">Not set</Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{p.help}</p>
                <div className="flex gap-2">
                  <Input
                    type="password"
                    placeholder={isSet ? 'Replace key…' : 'Paste key…'}
                    value={drafts[p.id] ?? ''}
                    onChange={(e) => setDrafts((d) => ({ ...d, [p.id]: e.target.value }))}
                    disabled={busy === p.id}
                  />
                  <Button onClick={() => save(p.id)} disabled={busy === p.id || !(drafts[p.id] ?? '').trim()}>
                    {busy === p.id ? 'Saving…' : 'Save'}
                  </Button>
                  {isSet && (
                    <Button variant="outline" onClick={() => remove(p.id)} disabled={busy === p.id}>
                      Remove
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
          {loading && <p className="text-xs text-muted-foreground">Loading…</p>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
