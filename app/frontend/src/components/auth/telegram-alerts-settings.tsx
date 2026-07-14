/**
 * TelegramAlertsSettings — connect a personal Telegram bot and get pushed a
 * message when a scheduled scan surfaces a high-confidence signal. The user
 * creates their own bot (BotFather) and pastes its token (stored encrypted); the
 * token is never shown back. Pairing captures their chat_id via a code they send
 * to the bot. Rendered in the Settings dialog. Mobile-friendly (convention #8).
 */
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Bell, Send } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { alertsApi, type AlertSettings } from '@/services/alerts-api';

const TF_CHOICES: { value: string; label: string }[] = [
  { value: 'day', label: 'Daily' },
  { value: '1h', label: '1h' },
  { value: 'week', label: 'Weekly' },
  { value: '15m', label: '15m' },
];

export function TelegramAlertsSettings() {
  const [settings, setSettings] = useState<AlertSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState('');
  // A one-time verification code the user sends to their bot so we bind THEIR chat.
  const code = useMemo(() => String(Math.floor(100000 + Math.random() * 900000)), []);

  const load = async () => {
    try {
      setSettings(await alertsApi.getSettings());
    } catch (e) {
      toast.error(`Couldn't load alert settings: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const saveToken = async () => {
    if (token.trim().length < 10) {
      toast.error('That doesn’t look like a bot token.');
      return;
    }
    setBusy(true);
    try {
      await alertsApi.setToken(token.trim());
      setToken('');
      toast.success('Bot token saved. Now pair your chat below.');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save token');
    } finally {
      setBusy(false);
    }
  };

  const verifyPairing = async () => {
    setBusy(true);
    try {
      const r = await alertsApi.pair(code);
      if (r.paired) {
        toast.success('Connected! A confirmation was sent to your Telegram.');
        await load();
      } else {
        toast.error(r.error || 'Pairing not detected yet.');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Pairing failed');
    } finally {
      setBusy(false);
    }
  };

  const patch = async (p: { enabled?: boolean; min_confidence?: number; timeframes?: string[] }) => {
    try {
      setSettings(await alertsApi.saveSettings(p));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to update');
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await alertsApi.disconnect();
      toast.success('Disconnected.');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    try {
      await alertsApi.test();
      toast.success('Test alert sent — check Telegram.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Test failed');
    }
  };

  if (loading) return <p className="text-xs text-muted-foreground">Loading…</p>;
  if (!settings) return null;

  const paired = Boolean(settings.chat_id);
  const inputCls = 'w-full bg-background border border-border rounded px-2 py-1.5 text-xs';

  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        <Bell className="h-4 w-4 text-muted-foreground" />
        <p className="text-sm font-medium">Telegram alerts</p>
        {paired && settings.enabled && <Badge className="text-[10px]">on</Badge>}
        {paired && !settings.enabled && <Badge variant="outline" className="text-[10px]">off</Badge>}
      </div>
      <p className="text-xs text-muted-foreground">
        Get a push to your phone when a scheduled scan finds a signal at or above your confidence
        threshold. Alerts fire from your scheduled scans (Scheduled scans tab).
      </p>

      {/* Step 1 — bot token */}
      {!settings.has_token ? (
        <div className="space-y-2">
          <p className="text-xs font-medium">1. Create a bot &amp; paste its token</p>
          <ol className="ml-4 list-decimal space-y-0.5 text-[11px] text-muted-foreground">
            <li>In Telegram, message <span className="font-mono">@BotFather</span> → <span className="font-mono">/newbot</span>.</li>
            <li>Copy the token it gives you and paste it here.</li>
          </ol>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="123456:ABC-DEF…"
              className={inputCls + ' font-mono max-w-xs'}
            />
            <Button size="sm" onClick={() => void saveToken()} disabled={busy}>Save token</Button>
          </div>
        </div>
      ) : !paired ? (
        /* Step 2 — pairing */
        <div className="space-y-2">
          <p className="text-xs font-medium">2. Pair your chat</p>
          <p className="text-[11px] text-muted-foreground">
            Open your bot in Telegram, press <span className="font-mono">Start</span>, then send it this code:
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-muted px-3 py-1 font-mono text-sm font-bold tracking-widest">{code}</span>
            <Button size="sm" onClick={() => void verifyPairing()} disabled={busy}>
              {busy ? 'Checking…' : 'I’ve sent it'}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void disconnect()} disabled={busy}>
              Reset token
            </Button>
          </div>
        </div>
      ) : (
        /* Step 3 — connected: rules */
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-emerald-500">✓ Connected</span>
            <div className="ml-auto flex items-center gap-1">
              <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => void sendTest()}>
                <Send className="mr-1 h-3.5 w-3.5" /> Test
              </Button>
              <Button size="sm" variant="ghost" className="h-7 px-2 text-muted-foreground hover:text-rose-500" onClick={() => void disconnect()} disabled={busy}>
                Disconnect
              </Button>
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={settings.enabled}
              onChange={(e) => void patch({ enabled: e.target.checked })}
              className="accent-primary"
            />
            Alerts enabled
          </label>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-muted-foreground">Min confidence</span>
            <input
              type="number"
              min={50}
              max={100}
              step={1}
              value={Math.round(settings.min_confidence)}
              onChange={(e) => void patch({ min_confidence: Number(e.target.value) })}
              className={inputCls + ' w-20'}
            />
            <span className="text-muted-foreground">%</span>
          </div>

          <div className="space-y-1 text-xs">
            <span className="text-muted-foreground">Alert on these timeframes</span>
            <div className="flex flex-wrap gap-3">
              {TF_CHOICES.map((tf) => {
                const on = settings.timeframes.includes(tf.value);
                return (
                  <label key={tf.value} className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...settings.timeframes, tf.value]
                          : settings.timeframes.filter((t) => t !== tf.value);
                        void patch({ timeframes: next });
                      }}
                      className="accent-primary"
                    />
                    {tf.label}
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
