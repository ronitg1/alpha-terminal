/**
 * BYOK API-key settings (Phase 3). Lets a signed-in user add/replace/remove
 * their own provider keys. DeepSeek is required by default for LLM scans/thesis
 * /chat and is billed per use; OpenRouter can replace it when selected. Massive
 * (market data) and Finnhub (news) are optional — the app falls back to the
 * shared keys for those.
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
import { ScheduledScansSettings } from './scheduled-scans-settings';

type Provider = 'deepseek' | 'openrouter' | 'massive' | 'finnhub';
type LlmProvider = 'DeepSeek' | 'OpenRouter';

const PROVIDERS: { id: Provider; label: string; required: boolean; help: string }[] = [
  { id: 'deepseek', label: 'DeepSeek', required: true, help: 'Default LLM key for AI scans, theses, and chat; not needed when OpenRouter is selected.' },
  { id: 'openrouter', label: 'OpenRouter', required: false, help: 'Optional; enables OpenRouter model selection and bills LLM usage to your key.' },
  { id: 'massive', label: 'Massive (Polygon)', required: false, help: 'Market data. Approved accounts use the shared key; otherwise add your own.' },
  { id: 'finnhub', label: 'Finnhub', required: false, help: 'News & fundamentals. Approved accounts use the shared key; otherwise add your own.' },
];

interface KeySummary { provider: string; has_key: boolean }
interface AccessInfo { is_owner: boolean; shared_data_approved: boolean; request_status: string | null }
interface AccessReq { id: number; user_id: string; email: string | null; status: string; note: string | null }
interface ModelPreference { model_provider: LlmProvider; model_name: string; preference_saved: boolean }
interface LanguageModel { display_name: string; model_name: string; provider: string }

const DEEPSEEK_MODELS: LanguageModel[] = [
  { display_name: 'DeepSeek R1 (deepseek-reasoner)', model_name: 'deepseek-reasoner', provider: 'DeepSeek' },
  { display_name: 'DeepSeek V3 (deepseek-chat)', model_name: 'deepseek-chat', provider: 'DeepSeek' },
  { display_name: 'DeepSeek V4 Pro (deepseek-v4-pro)', model_name: 'deepseek-v4-pro', provider: 'DeepSeek' },
];

export function ApiKeysSettings({ trigger }: { trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState<Set<string>>(new Set());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [access, setAccess] = useState<AccessInfo | null>(null);
  const [requests, setRequests] = useState<AccessReq[]>([]);
  const [modelProvider, setModelProvider] = useState<LlmProvider>('DeepSeek');
  const [modelName, setModelName] = useState('deepseek-reasoner');
  const [modelBusy, setModelBusy] = useState(false);
  const [openRouterModels, setOpenRouterModels] = useState<LanguageModel[]>([]);

  const loadAccess = useCallback(async () => {
    try {
      const me: AccessInfo = await (await fetch(`${API_BASE_URL}/access/me`)).json();
      setAccess(me);
      if (me.is_owner) {
        const rows: AccessReq[] = await (await fetch(`${API_BASE_URL}/access/requests`)).json();
        setRequests(rows);
      }
    } catch {
      /* access info is best-effort; ignore */
    }
  }, []);

  async function requestAccess() {
    try {
      const res = await fetch(`${API_BASE_URL}/access/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('Request sent — the owner will review it.');
      await loadAccess();
    } catch (e) {
      toast.error(`Couldn't send request: ${e instanceof Error ? e.message : e}`);
    }
  }

  async function decide(id: number, action: 'approve' | 'deny') {
    try {
      const res = await fetch(`${API_BASE_URL}/access/requests/${id}/${action}`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success(`Request ${action === 'approve' ? 'approved' : 'denied'}.`);
      await loadAccess();
    } catch (e) {
      toast.error(`Couldn't update request: ${e instanceof Error ? e.message : e}`);
    }
  }

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

  const loadModelSettings = useCallback(async () => {
    try {
      const prefRes = await fetch(`${API_BASE_URL}/user-settings/model`);
      if (!prefRes.ok) throw new Error(`HTTP ${prefRes.status}`);
      const pref: ModelPreference = await prefRes.json();
      setModelProvider(pref.model_provider);
      setModelName(pref.model_name);
    } catch (e) {
      toast.error(`Could not load model preference: ${e instanceof Error ? e.message : e}`);
    }

    try {
      const modelsRes = await fetch(`${API_BASE_URL}/language-models/`);
      if (!modelsRes.ok) throw new Error(`HTTP ${modelsRes.status}`);
      const payload: { models: LanguageModel[] } = await modelsRes.json();
      setOpenRouterModels((payload.models || []).filter((m) => m.provider === 'OpenRouter'));
    } catch {
      setOpenRouterModels([]);
    }
  }, []);

  useEffect(() => {
    if (open) {
      void refresh();
      void loadAccess();
      void loadModelSettings();
    }
  }, [open, refresh, loadAccess, loadModelSettings]);

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

  async function saveModelPreference() {
    const name = modelName.trim();
    if (!name) return;
    setModelBusy(true);
    try {
      const res = await fetch(`${API_BASE_URL}/user-settings/model`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_provider: modelProvider, model_name: name }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`;
        throw new Error(detail);
      }
      const pref: ModelPreference = await res.json();
      setModelProvider(pref.model_provider);
      setModelName(pref.model_name);
      toast.success('Model preference saved.');
    } catch (e) {
      toast.error(`Couldn't save model preference: ${e instanceof Error ? e.message : e}`);
    } finally {
      setModelBusy(false);
    }
  }

  function onModelProviderChange(next: LlmProvider) {
    setModelProvider(next);
    if (next === 'DeepSeek' && !DEEPSEEK_MODELS.some((m) => m.model_name === modelName)) {
      setModelName('deepseek-reasoner');
    }
    if (next === 'OpenRouter' && (!modelName || DEEPSEEK_MODELS.some((m) => m.model_name === modelName))) {
      setModelName(openRouterModels[0]?.model_name ?? 'openai/gpt-5.2');
    }
  }

  const hasOpenRouterKey = present.has('openrouter');
  const selectedProviderLocked = modelProvider === 'OpenRouter' && !hasOpenRouterKey;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>API keys</DialogTitle>
          <DialogDescription>
            Bring your own provider keys. Keys are encrypted and never shown again after saving.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {PROVIDERS.map((p) => {
            const isSet = present.has(p.id);
            const isRequired =
              p.id === 'deepseek'
                ? modelProvider !== 'OpenRouter'
                : p.id === 'openrouter'
                  ? modelProvider === 'OpenRouter'
                  : p.required;
            // Finnhub is free-tier: all signed-in users use the shared key by default.
            // Massive is approved-only: shared only when access.shared_data_approved.
            const usingShared =
              !isSet &&
              !isRequired &&
              access != null &&
              (p.id === 'finnhub' || (p.id === 'massive' && access.shared_data_approved));
            return (
              <div key={p.id} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{p.label}</span>
                  {isRequired && <Badge variant="secondary">Required</Badge>}
                  {isSet ? (
                    <Badge className="ml-auto" variant="success">Set</Badge>
                  ) : usingShared ? (
                    <Badge className="ml-auto" variant="secondary">Using shared key</Badge>
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

          <div className="space-y-3 rounded-md border border-border p-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">LLM model</span>
              <Badge className="ml-auto" variant={modelProvider === 'OpenRouter' ? 'success' : 'secondary'}>
                {modelProvider}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              OpenRouter accepts any model id. Saved choices apply to scans, theses, chat, news summaries, and transcript analysis.
            </p>
            <div className="grid gap-2 sm:grid-cols-[150px_1fr_auto]">
              <select
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                value={modelProvider}
                onChange={(e) => onModelProviderChange(e.target.value as LlmProvider)}
                disabled={modelBusy}
              >
                <option value="DeepSeek">DeepSeek</option>
                <option value="OpenRouter">OpenRouter</option>
              </select>
              {modelProvider === 'DeepSeek' ? (
                <select
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  disabled={modelBusy}
                >
                  {DEEPSEEK_MODELS.map((m) => (
                    <option key={m.model_name} value={m.model_name}>{m.display_name}</option>
                  ))}
                </select>
              ) : (
                <>
                  <Input
                    list="openrouter-model-options"
                    placeholder="openai/gpt-5.2"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    disabled={modelBusy || !hasOpenRouterKey}
                  />
                  <datalist id="openrouter-model-options">
                    {openRouterModels.slice(0, 500).map((m) => (
                      <option key={m.model_name} value={m.model_name}>{m.display_name}</option>
                    ))}
                  </datalist>
                </>
              )}
              <Button
                onClick={saveModelPreference}
                disabled={modelBusy || selectedProviderLocked || !modelName.trim()}
              >
                {modelBusy ? 'Saving…' : 'Save'}
              </Button>
            </div>
            {selectedProviderLocked && (
              <p className="text-xs text-muted-foreground">
                Add and verify an OpenRouter key above before selecting OpenRouter models.
              </p>
            )}
          </div>

          {/* Request free access to the owner's shared market-data keys. */}
          {access && !access.is_owner && !access.shared_data_approved && (
            <div className="rounded-md border border-border p-3">
              {access.request_status === 'pending' ? (
                <p className="text-xs text-muted-foreground">
                  Your request for free market-data access is pending the owner's review.
                </p>
              ) : access.request_status === 'denied' ? (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">Your access request was declined.</p>
                  <Button size="sm" variant="link" className="h-auto p-0" onClick={requestAccess}>
                    Request again
                  </Button>
                </div>
              ) : (
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">
                    Don't want to add Massive/Finnhub keys?
                  </p>
                  <Button size="sm" variant="link" className="h-auto p-0" onClick={requestAccess}>
                    Request free market-data access from the owner →
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* Owner-only: review access requests (always shown to the owner). */}
          {access?.is_owner && (
            <div className="space-y-2 rounded-md border border-border p-3">
              <p className="text-sm font-medium">Access requests</p>
              {requests.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No requests yet. When someone asks for shared market-data access, they'll appear here to approve.
                </p>
              )}
              {requests.map((r) => (
                <div key={r.id} className="flex items-center gap-2 text-xs">
                  <span className="truncate">{r.email ?? r.user_id}</span>
                  <Badge variant={r.status === 'approved' ? 'success' : r.status === 'denied' ? 'destructive' : 'outline'} className="ml-auto">
                    {r.status}
                  </Badge>
                  {r.status !== 'approved' && (
                    <Button size="sm" variant="outline" onClick={() => decide(r.id, 'approve')}>Approve</Button>
                  )}
                  {r.status !== 'denied' && (
                    <Button size="sm" variant="ghost" onClick={() => decide(r.id, 'deny')}>Deny</Button>
                  )}
                </div>
              ))}
            </div>
          )}

          <ScheduledScansSettings />
        </div>
      </DialogContent>
    </Dialog>
  );
}
