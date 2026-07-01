import { cn } from '@/lib/utils';
import { snaptradeApi } from '@/services/snaptrade-api';
import type { SnapTradeAccount, SnapTradePortfolioResponse, SnapTradeStatus } from '@/types/snaptrade';
import { Link2, Link2Off, Landmark, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

function fmtMoney(v: number | null): string {
  if (v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

function fmtNum(v: number | null): string {
  return v === null || Number.isNaN(v) ? '—' : String(v);
}

function AccountCard({ account }: { account: SnapTradeAccount }) {
  const empty = account.positions.length === 0 && account.options.length === 0;
  return (
    <div className="rounded border border-border/60 bg-muted/20">
      <div className="px-2 py-1.5 border-b border-border/60 text-[11px] font-semibold text-foreground">
        {account.label}
      </div>
      {empty ? (
        <p className="px-2 py-2 text-[11px] text-muted-foreground italic">No positions in this account.</p>
      ) : (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-muted-foreground text-left">
              <th className="px-2 py-1 font-medium">Symbol</th>
              <th className="px-2 py-1 font-medium">Type</th>
              <th className="px-2 py-1 font-medium text-right">Units</th>
              <th className="px-2 py-1 font-medium text-right">Value</th>
            </tr>
          </thead>
          <tbody>
            {account.positions.map((p, i) => (
              <tr key={`s-${i}`} className="border-t border-border/40">
                <td className="px-2 py-1 font-mono">{p.symbol || '—'}</td>
                <td className="px-2 py-1 text-muted-foreground">stock</td>
                <td className="px-2 py-1 text-right">{fmtNum(p.units)}</td>
                <td className="px-2 py-1 text-right">{fmtMoney(p.market_value)}</td>
              </tr>
            ))}
            {account.options.map((o, i) => (
              <tr key={`o-${i}`} className="border-t border-border/40">
                <td className="px-2 py-1 font-mono">
                  {o.underlying}
                  {o.option_type ? (
                    <span className="ml-1 text-muted-foreground">
                      {o.option_type} {o.strike ?? ''} {o.expiration ?? ''}
                    </span>
                  ) : null}
                </td>
                <td className="px-2 py-1 text-muted-foreground">option</td>
                <td className="px-2 py-1 text-right">{fmtNum(o.units)}</td>
                <td className="px-2 py-1 text-right">{fmtMoney(o.market_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function SnapTradeConnect() {
  const [status, setStatus] = useState<SnapTradeStatus | null>(null);
  const [portfolio, setPortfolio] = useState<SnapTradePortfolioResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const loadStatus = async () => {
    try {
      setStatus(await snaptradeApi.getStatus());
    } catch {
      // status is best-effort; a failure just hides the card
      setStatus(null);
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  const connect = async () => {
    setBusy(true);
    try {
      const { redirect_uri } = await snaptradeApi.connect();
      window.open(redirect_uri, '_blank', 'noopener,noreferrer,width=520,height=720');
      toast.info('Complete the connection in the SnapTrade window, then click Refresh.');
      await loadStatus();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    try {
      setPortfolio(await snaptradeApi.getPortfolio());
      await loadStatus();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await snaptradeApi.disconnect();
      setPortfolio(null);
      await loadStatus();
      toast.success('Disconnected brokerage sync.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Dormant when the server has no SnapTrade credentials.
  if (!status || !status.configured) return null;

  return (
    <div className="rounded-lg border border-border/60 bg-card overflow-hidden">
      <div className="px-3 py-2 border-b border-border/60 flex items-center gap-2">
        <Landmark className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Fidelity (SnapTrade)
        </span>
        <div className="ml-auto flex items-center gap-1">
          {!status.connected ? (
            <button
              type="button"
              onClick={() => void connect()}
              disabled={busy || !status.approved}
              className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary disabled:opacity-50"
            >
              <Link2 className="h-3 w-3" /> Connect
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={busy}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
              >
                <RefreshCw className={cn('h-3 w-3', busy && 'animate-spin')} /> Refresh
              </button>
              <button
                type="button"
                onClick={() => void connect()}
                disabled={busy || !status.approved}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
              >
                <Link2 className="h-3 w-3" /> Add account
              </button>
              <button
                type="button"
                onClick={() => void disconnect()}
                disabled={busy}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
              >
                <Link2Off className="h-3 w-3" /> Disconnect
              </button>
            </>
          )}
        </div>
      </div>

      <div className="p-3 space-y-2">
        {!status.approved ? (
          <p className="text-[11px] text-muted-foreground italic">
            Brokerage sync isn't enabled for your account yet.
          </p>
        ) : !status.connected ? (
          <p className="text-[11px] text-muted-foreground">
            Connect your Fidelity account (read-only) to see stock and option positions here. SnapTrade
            handles the login — your brokerage credentials are never shared with this app.
          </p>
        ) : !portfolio ? (
          <p className="text-[11px] text-muted-foreground">
            Connected. Click <span className="font-medium">Refresh</span> to load your positions.
          </p>
        ) : portfolio.accounts.length === 0 ? (
          <p className="text-[11px] text-muted-foreground italic">
            No linked accounts yet — click <span className="font-medium">Add account</span> and choose Fidelity.
          </p>
        ) : (
          <div className="max-h-80 overflow-auto space-y-2">
            {portfolio.accounts.map((a) => (
              <AccountCard key={a.id} account={a} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
