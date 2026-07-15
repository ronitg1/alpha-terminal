/**
 * PnlSection — the Paper Trading tab (a simulated $100k options account).
 *
 * Layout (top → bottom):
 *   1. Header: marks as-of stamp, Refresh, Reset, Add trade
 *   2. Paper account bar: equity / buying power / positions value / P&L
 *   3. Summary cards: realized / unrealized / total / win rate / open count
 *   4. Equity curve (cumulative realized, lightweight-charts)
 *   5. Open positions — live marks, unrealized P&L, close/delete
 *   6. Closed positions — realized P&L history
 *
 * Positions arrive from two places: this tab's Add form (manual) and the
 * one-click "Track"/"Add to Paper Trading" buttons on screener/pattern picks.
 * Everything here is simulated — nothing is a real trade.
 *
 * Both position lists render as a table on desktop (md+) and reflow into stacked
 * cards on phones (convention #8) so nothing is clipped on iOS.
 */

import { pnlApi, type PaperAccount } from '@/services/pnl-api';
import { cn } from '@/lib/utils';
import type { OptionLeg, PnlMark, PnlPosition, PnlSummary, PositionCreatePayload } from '@/types/pnl';
import { ColorType, createChart, LineStyle } from 'lightweight-charts';
import {
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
  Wallet,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function multiplier(p: PnlPosition): number {
  return p.kind === 'option' ? 100 : 1;
}

function direction(p: PnlPosition): number {
  return p.side === 'long' ? 1 : -1;
}

function unrealized(p: PnlPosition, mark: number | null | undefined): number | null {
  if (p.status !== 'open' || mark == null) return null;
  return (mark - p.entry_price) * p.qty * multiplier(p) * direction(p);
}

function realized(p: PnlPosition): number | null {
  if (p.status !== 'closed' || p.exit_price == null) return null;
  return (p.exit_price - p.entry_price) * p.qty * multiplier(p) * direction(p);
}

function fmtMoney(v: number | null | undefined, sign = true): string {
  if (v == null) return '—';
  const s = sign && v > 0 ? '+' : '';
  return `${s}$${Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(2)}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

function pnlColor(v: number | null | undefined): string {
  if (v == null) return 'text-muted-foreground';
  return v >= 0 ? 'text-emerald-500' : 'text-rose-500';
}

/** "NVDA $200C 7/17/26" for options, plain ticker for stock. */
function instrumentLabel(p: PnlPosition): string {
  if (p.kind === 'option' && p.option) {
    const [y, m, d] = p.option.expiration.split('-');
    return `${p.ticker} $${p.option.strike}${p.option.type === 'call' ? 'C' : 'P'} ${Number(m)}/${Number(d)}/${y.slice(2)}`;
  }
  return p.ticker;
}

// ─── Summary cards ───────────────────────────────────────────────────────────

function SummaryCards({ summary }: { summary: PnlSummary }) {
  const total = summary.realized_total + summary.unrealized_total;
  const cards = [
    { label: 'Realized', value: fmtMoney(summary.realized_total), color: pnlColor(summary.realized_total) },
    { label: 'Unrealized', value: fmtMoney(summary.unrealized_total), color: pnlColor(summary.unrealized_total) },
    { label: 'Total P&L', value: fmtMoney(total), color: pnlColor(total) },
    {
      label: 'Win rate',
      value: summary.win_rate != null ? `${summary.win_rate.toFixed(0)}%` : '—',
      color: summary.win_rate != null && summary.win_rate >= 50 ? 'text-emerald-500' : 'text-amber-500',
    },
    { label: 'Open / Closed', value: `${summary.n_open} / ${summary.n_closed}`, color: 'text-foreground' },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 rounded-lg border border-border/60 bg-card p-4 sm:grid-cols-5">
      {cards.map((c) => (
        <div key={c.label} className="text-center">
          <div className={cn('text-lg font-bold font-mono tabular-nums sm:text-xl', c.color)}>{c.value}</div>
          <div className="text-[10px] text-muted-foreground mt-0.5">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Equity curve ────────────────────────────────────────────────────────────

function EquityCurve({ points }: { points: { date: string; cum_realized: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || points.length < 2) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 160,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#9ca3af' },
      grid: { vertLines: { visible: false }, horzLines: { color: '#1f293744' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    const last = points[points.length - 1].cum_realized;
    const series = chart.addLineSeries({
      color: last >= 0 ? '#10b981' : '#f43f5e',
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
    });
    // Collapse same-day closes to the day's final cumulative value.
    const byDate = new Map<string, number>();
    for (const pt of points) byDate.set(pt.date, pt.cum_realized);
    series.setData([...byDate.entries()].map(([date, value]) => ({ time: date, value })));
    chart.timeScale().fitContent();

    const onResize = () => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
    };
  }, [points]);

  if (points.length < 2) return null;
  return (
    <div className="rounded-lg border border-border/60 bg-card p-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        Realized equity curve
      </div>
      <div ref={ref} />
    </div>
  );
}

// ─── Add-position form ───────────────────────────────────────────────────────

const inputCls =
  'bg-background border border-border rounded px-2 py-1 text-xs focus:outline-none focus:border-primary w-full';

function AddPositionForm({ onAdded, onCancel }: { onAdded: () => void; onCancel: () => void }) {
  const [kind, setKind] = useState<'option' | 'stock'>('option');
  const [ticker, setTicker] = useState('');
  const [side, setSide] = useState<'long' | 'short'>('long');
  const [qty, setQty] = useState('1');
  const [entryPrice, setEntryPrice] = useState('');
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10));
  const [optType, setOptType] = useState<'call' | 'put'>('call');
  const [strike, setStrike] = useState('');
  const [expiration, setExpiration] = useState('');
  const [real, setReal] = useState(false);
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const t = ticker.trim().toUpperCase();
    const q = parseFloat(qty);
    const ep = parseFloat(entryPrice);
    if (!t || !Number.isFinite(q) || q <= 0 || !Number.isFinite(ep) || ep < 0) {
      toast.error('Ticker, a positive quantity, and an entry price are required.');
      return;
    }
    let option: OptionLeg | null = null;
    if (kind === 'option') {
      const k = parseFloat(strike);
      if (!Number.isFinite(k) || k <= 0 || !/^\d{4}-\d{2}-\d{2}$/.test(expiration)) {
        toast.error('Options need a strike and an expiration (YYYY-MM-DD).');
        return;
      }
      option = { type: optType, strike: k, expiration };
    }
    const payload: PositionCreatePayload = {
      kind, ticker: t, side, qty: q, option,
      entry_price: ep, entry_date: entryDate || null,
      source: 'manual', real, notes,
    };
    setSaving(true);
    try {
      await pnlApi.createPosition(payload);
      toast.success(`Tracking ${t}`);
      onAdded();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        {/* Kind + side toggles */}
        <div className="flex rounded border border-border overflow-hidden">
          {(['option', 'stock'] as const).map((k) => (
            <button key={k} type="button" onClick={() => setKind(k)}
              className={cn('px-2 py-1 text-[11px]', kind === k ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground')}>
              {k}
            </button>
          ))}
        </div>
        <div className="flex rounded border border-border overflow-hidden">
          {(['long', 'short'] as const).map((s) => (
            <button key={s} type="button" onClick={() => setSide(s)}
              className={cn('px-2 py-1 text-[11px]', side === s ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground')}>
              {s}
            </button>
          ))}
        </div>
        <input className={cn(inputCls, 'w-20 font-mono uppercase')} placeholder="Ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} />
        <input className={cn(inputCls, 'w-16')} placeholder="Qty" value={qty} onChange={(e) => setQty(e.target.value)} />
        <input className={cn(inputCls, 'w-24')} placeholder={kind === 'option' ? 'Premium/share' : 'Price'} value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)} />
        <input className={cn(inputCls, 'w-32')} type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)} />
        {kind === 'option' && (
          <>
            <div className="flex rounded border border-border overflow-hidden">
              {(['call', 'put'] as const).map((t) => (
                <button key={t} type="button" onClick={() => setOptType(t)}
                  className={cn('px-2 py-1 text-[11px]', optType === t ? (t === 'call' ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white') : 'bg-background text-muted-foreground')}>
                  {t}
                </button>
              ))}
            </div>
            <input className={cn(inputCls, 'w-20')} placeholder="Strike" value={strike} onChange={(e) => setStrike(e.target.value)} />
            <input className={cn(inputCls, 'w-32')} type="date" value={expiration} onChange={(e) => setExpiration(e.target.value)} />
          </>
        )}
        <label className="flex items-center gap-1 text-[11px] text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={real} onChange={(e) => setReal(e.target.checked)} className="accent-primary" />
          real fill
        </label>
      </div>
      <div className="flex items-center gap-2">
        <input className={cn(inputCls, 'flex-1')} placeholder="Notes (thesis, source, exit plan…)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button type="button" onClick={() => void submit()} disabled={saving}
          className="px-3 py-1 rounded bg-primary text-primary-foreground text-xs font-semibold disabled:opacity-50">
          {saving ? 'Saving…' : 'Track'}
        </button>
        <button type="button" onClick={onCancel} className="p-1 text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

// ─── Position row ────────────────────────────────────────────────────────────

function SourceTag({ p }: { p: PnlPosition }) {
  return (
    <span className="flex gap-1">
      <span className={cn(
        'text-[9px] font-bold uppercase px-1 py-0.5 rounded border',
        p.real
          ? 'border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400'
          : 'border-border bg-muted/40 text-muted-foreground',
      )}>
        {p.real ? 'real' : 'paper'}
      </span>
      {p.source !== 'manual' && (
        <span className="text-[9px] uppercase px-1 py-0.5 rounded border border-border text-muted-foreground">
          {p.source}
        </span>
      )}
    </span>
  );
}

// Shared close/delete logic so the desktop row and the mobile card stay in sync.
function usePositionActions(p: PnlPosition, onChanged: () => void) {
  const [closing, setClosing] = useState(false);
  const [exitPrice, setExitPrice] = useState('');

  const doClose = async () => {
    const px = parseFloat(exitPrice);
    if (!Number.isFinite(px) || px < 0) {
      toast.error('Enter a valid exit price.');
      return;
    }
    try {
      await pnlApi.closePosition(p.id, px);
      toast.success(`Closed ${instrumentLabel(p)}`);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  };

  const doDelete = () => {
    toast(`Delete ${instrumentLabel(p)}?`, {
      action: {
        label: 'Delete',
        onClick: () => {
          void pnlApi.deletePosition(p.id).then(onChanged).catch((e) => toast.error(String(e)));
        },
      },
      cancel: { label: 'Cancel', onClick: () => {} },
    });
  };

  return { closing, setClosing, exitPrice, setExitPrice, doClose, doDelete };
}

/** Mobile card for an open position (md:hidden). Same data as OpenRow, stacked. */
function OpenCard({ p, mark, onChanged }: { p: PnlPosition; mark: PnlMark | undefined; onChanged: () => void }) {
  const { closing, setClosing, exitPrice, setExitPrice, doClose, doDelete } = usePositionActions(p, onChanged);
  const u = unrealized(p, mark?.mark);
  const basis = p.entry_price * p.qty * multiplier(p);
  const uPct = u != null && basis > 0 ? (u / basis) * 100 : null;

  return (
    <div className="p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-semibold" title={p.notes || undefined}>{instrumentLabel(p)}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {p.side} · {p.qty} @ ${p.entry_price.toFixed(2)}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cn('font-mono text-sm font-semibold', pnlColor(u))}>{fmtMoney(u)}</div>
          {uPct != null && <div className={cn('font-mono text-[11px]', pnlColor(u))}>{fmtPct(uPct)}</div>}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-muted-foreground" title={mark?.source}>
          Mark {mark?.mark != null ? `$${mark.mark.toFixed(2)}` : '—'}
        </span>
        <SourceTag p={p} />
        <div className="ml-auto flex items-center gap-1">
          {closing ? (
            <>
              <input
                className="w-20 rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[11px]"
                placeholder="Exit" value={exitPrice} autoFocus
                onChange={(e) => setExitPrice(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void doClose(); if (e.key === 'Escape') setClosing(false); }}
              />
              <button type="button" onClick={() => void doClose()} className="rounded bg-primary px-1.5 py-0.5 text-[10px] text-primary-foreground">OK</button>
              <button type="button" onClick={() => setClosing(false)} className="px-1 text-[10px] text-muted-foreground">✕</button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => { setExitPrice(mark?.mark != null ? String(mark.mark) : ''); setClosing(true); }}
                className="rounded border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
              >
                Close
              </button>
              <button type="button" onClick={doDelete} className="p-1 text-muted-foreground hover:text-rose-500">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function OpenRow({
  p, mark, onChanged,
}: {
  p: PnlPosition;
  mark: PnlMark | undefined;
  onChanged: () => void;
}) {
  const { closing, setClosing, exitPrice, setExitPrice, doClose, doDelete } = usePositionActions(p, onChanged);
  const u = unrealized(p, mark?.mark);
  const basis = p.entry_price * p.qty * multiplier(p);
  const uPct = u != null && basis > 0 ? (u / basis) * 100 : null;

  return (
    <tr className="border-b border-border/40 hover:bg-muted/20">
      <td className="py-1.5 px-2 font-mono text-xs font-semibold whitespace-nowrap" title={p.notes || undefined}>
        {instrumentLabel(p)}
      </td>
      <td className="py-1.5 px-2 text-xs">{p.side}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-right">{p.qty}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-right">${p.entry_price.toFixed(2)}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-right" title={mark?.source}>
        {mark?.mark != null ? `$${mark.mark.toFixed(2)}` : '—'}
      </td>
      <td className={cn('py-1.5 px-2 text-xs font-mono text-right whitespace-nowrap', pnlColor(u))}>
        {fmtMoney(u)} {uPct != null && <span className="opacity-70">({fmtPct(uPct)})</span>}
      </td>
      <td className="py-1.5 px-2"><SourceTag p={p} /></td>
      <td className="py-1.5 px-2 text-right whitespace-nowrap">
        {closing ? (
          <span className="inline-flex items-center gap-1">
            <input
              className="bg-background border border-border rounded px-1.5 py-0.5 text-[11px] w-20 font-mono"
              placeholder="Exit price" value={exitPrice} autoFocus
              onChange={(e) => setExitPrice(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void doClose(); if (e.key === 'Escape') setClosing(false); }}
            />
            <button type="button" onClick={() => void doClose()} className="text-[10px] px-1.5 py-0.5 rounded bg-primary text-primary-foreground">OK</button>
            <button type="button" onClick={() => setClosing(false)} className="text-[10px] px-1 text-muted-foreground">✕</button>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1">
            <button
              type="button"
              onClick={() => { setExitPrice(mark?.mark != null ? String(mark.mark) : ''); setClosing(true); }}
              className="text-[10px] px-1.5 py-0.5 rounded border border-border hover:bg-muted text-muted-foreground"
            >
              Close
            </button>
            <button type="button" onClick={doDelete} className="p-0.5 text-muted-foreground hover:text-rose-500">
              <Trash2 className="h-3 w-3" />
            </button>
          </span>
        )}
      </td>
    </tr>
  );
}

/** Mobile card for a closed position (md:hidden). */
function ClosedCard({ p, onChanged }: { p: PnlPosition; onChanged: () => void }) {
  const { doDelete } = usePositionActions(p, onChanged);
  const r = realized(p);
  const basis = p.entry_price * p.qty * multiplier(p);
  const rPct = r != null && basis > 0 ? (r / basis) * 100 : null;

  return (
    <div className="p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-semibold" title={p.notes || undefined}>{instrumentLabel(p)}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {p.side} · {p.qty} · ${p.entry_price.toFixed(2)} → {p.exit_price != null ? `$${p.exit_price.toFixed(2)}` : '—'}
          </div>
          {p.exit_date && <div className="text-[10px] text-muted-foreground">closed {p.exit_date}</div>}
        </div>
        <div className="shrink-0 text-right">
          <div className={cn('font-mono text-sm font-semibold', pnlColor(r))}>{fmtMoney(r)}</div>
          {rPct != null && <div className={cn('font-mono text-[11px]', pnlColor(r))}>{fmtPct(rPct)}</div>}
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <SourceTag p={p} />
        <button type="button" onClick={doDelete} className="ml-auto p-1 text-muted-foreground hover:text-rose-500">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function ClosedRow({ p, onChanged }: { p: PnlPosition; onChanged: () => void }) {
  const { doDelete } = usePositionActions(p, onChanged);
  const r = realized(p);
  const basis = p.entry_price * p.qty * multiplier(p);
  const rPct = r != null && basis > 0 ? (r / basis) * 100 : null;

  return (
    <tr className="border-b border-border/40 hover:bg-muted/20">
      <td className="py-1.5 px-2 font-mono text-xs font-semibold whitespace-nowrap" title={p.notes || undefined}>
        {instrumentLabel(p)}
      </td>
      <td className="py-1.5 px-2 text-xs">{p.side}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-right">{p.qty}</td>
      <td className="py-1.5 px-2 text-xs font-mono text-right whitespace-nowrap">
        ${p.entry_price.toFixed(2)} → {p.exit_price != null ? `$${p.exit_price.toFixed(2)}` : '—'}
      </td>
      <td className={cn('py-1.5 px-2 text-xs font-mono text-right whitespace-nowrap', pnlColor(r))}>
        {fmtMoney(r)} {rPct != null && <span className="opacity-70">({fmtPct(rPct)})</span>}
      </td>
      <td className="py-1.5 px-2 text-xs text-muted-foreground">{p.exit_date ?? '—'}</td>
      <td className="py-1.5 px-2"><SourceTag p={p} /></td>
      <td className="py-1.5 px-2 text-right">
        <button type="button" onClick={doDelete} className="p-0.5 text-muted-foreground hover:text-rose-500">
          <Trash2 className="h-3 w-3" />
        </button>
      </td>
    </tr>
  );
}

// ─── Paper account bar ───────────────────────────────────────────────────────

function PaperAccountBar({ account }: { account: PaperAccount }) {
  const tone = (v: number) => (v > 0 ? 'text-emerald-500' : v < 0 ? 'text-rose-500' : 'text-muted-foreground');
  const Stat = ({ label, value, cls }: { label: string; value: string; cls?: string }) => (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={cn('text-sm font-semibold tabular-nums', cls)}>{value}</div>
    </div>
  );
  return (
    <div className="rounded-lg border border-border/60 bg-card p-4">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Account value</div>
      <div className="text-2xl font-bold tabular-nums">{fmtMoney(account.equity, false)}</div>
      <div className={cn('text-xs', tone(account.total_pnl))}>
        {fmtMoney(account.total_pnl)} ({fmtPct(account.total_pnl_pct)}) all-time
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Buying power" value={fmtMoney(account.buying_power, false)} />
        <Stat label="Positions value" value={fmtMoney(account.positions_value, false)} />
        <Stat label="Unrealized" value={fmtMoney(account.unrealized)} cls={tone(account.unrealized)} />
        <Stat label="Realized" value={fmtMoney(account.realized)} cls={tone(account.realized)} />
        <div
          title={
            account.sharpe != null
              ? `Approximate: annualized from ${account.sharpe_days} weekdays of realized (closed-trade) equity; open positions' swings are not included.`
              : 'Needs more closed-trade history (5+ trade dates over 30+ days).'
          }
        >
          <Stat
            label="Sharpe (approx)"
            value={account.sharpe != null ? account.sharpe.toFixed(2) : '—'}
            cls={account.sharpe != null ? tone(account.sharpe) : undefined}
          />
        </div>
      </div>
    </div>
  );
}

// ─── Main section ────────────────────────────────────────────────────────────

export function PnlSection() {
  const [positions, setPositions] = useState<PnlPosition[]>([]);
  const [summary, setSummary] = useState<PnlSummary | null>(null);
  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);

  const reload = useCallback(async (withMarks = true) => {
    setLoading(true);
    try {
      const [pos, sum, acct] = await Promise.all([
        pnlApi.listPositions(),
        pnlApi.getSummary(withMarks),
        pnlApi.getAccount().catch(() => null),
      ]);
      setPositions(pos.positions);
      setSummary(sum);
      if (acct) setAccount(acct);
    } catch (e) {
      toast.error(`Paper trading load failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const resetAll = useCallback(async () => {
    if (!window.confirm('Reset paper trading? This deletes all your paper trades and restores full buying power.')) return;
    try {
      await pnlApi.resetAccount();
      toast.success('Paper trading reset.');
      void reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    }
  }, [reload]);

  useEffect(() => { void reload(); }, [reload]);

  const open = positions.filter((p) => p.status === 'open');
  const closed = positions.filter((p) => p.status === 'closed');
  const marks = summary?.marks ?? {};

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4 safe-bottom">
      {/* Header (pr reserves space for the fixed top-right account menu) */}
      <div className="flex flex-wrap items-center gap-2 pr-28">
        <Wallet className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">Paper Trading</h2>
        {account && (
          <span className="text-[10px] text-muted-foreground">as of {account.asof.replace('T', ' ')}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button" onClick={() => void reload()}
            disabled={loading}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} /> Refresh
          </button>
          <button
            type="button" onClick={() => void resetAll()}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground"
            title="Delete all paper trades and restore full buying power"
          >
            <RotateCcw className="h-3 w-3" /> Reset
          </button>
          <button
            type="button" onClick={() => setAdding((a) => !a)}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-primary/40 bg-primary/5 hover:bg-primary/10 text-primary"
          >
            <Plus className="h-3 w-3" /> Add trade
          </button>
        </div>
      </div>

      <p className="text-[11px] text-muted-foreground">
        A simulated options account. Add contracts manually or send them from the Pattern Scanner, and track P&amp;L against a
        ${account ? account.starting_cash.toLocaleString() : '100,000'} starting balance. Nothing here is a real trade.
      </p>

      {account && <PaperAccountBar account={account} />}

      {adding && <AddPositionForm onAdded={() => { setAdding(false); void reload(); }} onCancel={() => setAdding(false)} />}

      {summary && <SummaryCards summary={summary} />}
      {summary && <EquityCurve points={summary.equity_curve} />}

      {/* Open positions */}
      <div className="rounded-lg border border-border/60 bg-card overflow-hidden">
        <div className="px-3 py-2 border-b border-border/60 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Open positions ({open.length})
        </div>
        {open.length === 0 ? (
          <p className="px-3 py-4 text-xs text-muted-foreground italic">
            Nothing tracked yet — add a trade, or send a pick from the Pattern Scanner or screener.
          </p>
        ) : (
          <>
          {/* Mobile: stacked cards */}
          <div className="divide-y divide-border/40 md:hidden">
            {open.map((p) => (
              <OpenCard key={p.id} p={p} mark={marks[p.id]} onChanged={() => void reload()} />
            ))}
          </div>
          {/* Desktop: table */}
          <table className="hidden w-full text-left md:table">
            <thead>
              <tr className="text-[10px] uppercase text-muted-foreground border-b border-border/60">
                <th className="py-1.5 px-2 font-medium">Instrument</th>
                <th className="py-1.5 px-2 font-medium">Side</th>
                <th className="py-1.5 px-2 font-medium text-right">Qty</th>
                <th className="py-1.5 px-2 font-medium text-right">Entry</th>
                <th className="py-1.5 px-2 font-medium text-right">Mark</th>
                <th className="py-1.5 px-2 font-medium text-right">Unrealized</th>
                <th className="py-1.5 px-2 font-medium">Tags</th>
                <th className="py-1.5 px-2" />
              </tr>
            </thead>
            <tbody>
              {open.map((p) => (
                <OpenRow key={p.id} p={p} mark={marks[p.id]} onChanged={() => void reload()} />
              ))}
            </tbody>
          </table>
          </>
        )}
      </div>

      {/* Closed positions */}
      <div className="rounded-lg border border-border/60 bg-card overflow-hidden">
        <div className="px-3 py-2 border-b border-border/60 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Closed positions ({closed.length})
        </div>
        {closed.length === 0 ? (
          <p className="px-3 py-4 text-xs text-muted-foreground italic">No closed trades yet.</p>
        ) : (
          <>
          {/* Mobile: stacked cards */}
          <div className="divide-y divide-border/40 md:hidden">
            {closed.map((p) => (
              <ClosedCard key={p.id} p={p} onChanged={() => void reload()} />
            ))}
          </div>
          {/* Desktop: table */}
          <table className="hidden w-full text-left md:table">
            <thead>
              <tr className="text-[10px] uppercase text-muted-foreground border-b border-border/60">
                <th className="py-1.5 px-2 font-medium">Instrument</th>
                <th className="py-1.5 px-2 font-medium">Side</th>
                <th className="py-1.5 px-2 font-medium text-right">Qty</th>
                <th className="py-1.5 px-2 font-medium text-right">Entry → Exit</th>
                <th className="py-1.5 px-2 font-medium text-right">Realized</th>
                <th className="py-1.5 px-2 font-medium">Exit date</th>
                <th className="py-1.5 px-2 font-medium">Tags</th>
                <th className="py-1.5 px-2" />
              </tr>
            </thead>
            <tbody>
              {closed.map((p) => (
                <ClosedRow key={p.id} p={p} onChanged={() => void reload()} />
              ))}
            </tbody>
          </table>
          </>
        )}
      </div>
    </div>
  );
}
