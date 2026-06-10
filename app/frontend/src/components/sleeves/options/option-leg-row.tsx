/**
 * OptionLegRow — one contract row inside the chain viewer.
 *
 * Click anywhere on the row to copy a trade-readable string to the
 * clipboard, e.g. `MSFT 2026-06-06 470C @ $2.45`. Matches the dashboard's
 * "signals only, no order entry" rule — copying the leg keeps the user in
 * their broker workflow.
 *
 * ATM rows get a subtle highlight passed via prop (the viewer computes
 * which strike is closest to spot and tags exactly one row).
 */

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { pnlApi } from '@/services/pnl-api';
import { OptionContract } from '@/types/sleeves';
import { Copy, PlusCircle, Star } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

interface OptionLegRowProps {
  contract: OptionContract;
  underlying: string;
  atm?: boolean;
  /** When set, render this row as a leg of the recommended structure:
   *  'long' = bought (emerald BUY tag + star), 'short' = sold (rose SELL tag).
   *  Wins over ``atm`` styling. A single-leg recommendation passes 'long'. */
  highlight?: 'long' | 'short';
}

export function OptionLegRow({ contract, underlying, atm, highlight }: OptionLegRowProps) {
  const [copied, setCopied] = useState(false);
  const [tracking, setTracking] = useState(false);
  const isLong = highlight === 'long';
  const isShort = highlight === 'short';

  const entryPx = (): number | null =>
    mid(contract.bid, contract.ask) ?? contract.last ?? contract.ask ?? contract.bid;

  const handleCopy = async () => {
    const code = contract.type === 'call' ? 'C' : 'P';
    const px =
      contract.last ??
      mid(contract.bid, contract.ask) ??
      contract.ask ??
      contract.bid;
    const pxStr = px !== null && px !== undefined ? `@ $${px.toFixed(2)}` : '@ —';
    const text = `${underlying} ${contract.expiration} ${contract.strike}${code} ${pxStr}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail under non-secure context; quiet failure is fine.
    }
  };

  const handleTrack = async () => {
    const px = entryPx();
    if (px == null) {
      toast.error('No quote on this contract to use as an entry price.');
      return;
    }
    setTracking(true);
    try {
      await pnlApi.createPosition({
        kind: 'option',
        ticker: underlying,
        side: isShort ? 'short' : 'long',
        qty: 1,
        option: {
          type: contract.type,
          strike: contract.strike,
          expiration: contract.expiration,
          contract_ticker: contract.ticker ?? null,
        },
        entry_price: Number(px.toFixed(2)),
        entry_date: new Date().toISOString().slice(0, 10),
        source: 'screener',
        real: false,
        notes: `Tracked from ${underlying} chain @ mid`,
      });
      toast.success(
        `Tracking ${underlying} $${contract.strike}${contract.type === 'call' ? 'C' : 'P'} ${contract.expiration} @ $${px.toFixed(2)} — see the P&L tab`,
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setTracking(false);
    }
  };

  return (
    <tr
      className={cn(
        'border-b border-border/40 last:border-0 hover:bg-muted/30 cursor-pointer text-[11px] font-mono',
        atm && !highlight && 'bg-amber-500/5',
        isLong && 'bg-emerald-500/15 ring-1 ring-inset ring-emerald-500/40 font-semibold',
        isShort && 'bg-rose-500/15 ring-1 ring-inset ring-rose-500/40 font-semibold',
      )}
      onClick={handleCopy}
      title={
        isLong
          ? 'Long leg (buy) — click to copy'
          : isShort
            ? 'Short leg (sell) — click to copy'
            : 'Click to copy'
      }
    >
      <td className="px-2 py-1 font-semibold whitespace-nowrap">
        {isLong && (
          <Star className="h-3 w-3 inline-block mr-1 text-emerald-500 fill-emerald-500 -mt-0.5" />
        )}
        {isShort && (
          <span className="inline-block mr-1 px-1 rounded-sm bg-rose-500/20 text-rose-600 dark:text-rose-400 text-[8px] font-bold align-middle">
            SELL
          </span>
        )}
        {isLong && (
          <span className="inline-block mr-1 px-1 rounded-sm bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-[8px] font-bold align-middle">
            BUY
          </span>
        )}
        {contract.strike.toFixed(2)}
      </td>
      <td className="px-2 py-1 tabular-nums">{formatNum(contract.last)}</td>
      <td className="px-2 py-1 tabular-nums">
        {formatNum(contract.bid)}/{formatNum(contract.ask)}
      </td>
      <td className="px-2 py-1 tabular-nums">{formatPct(contract.iv)}</td>
      <td className="px-2 py-1 tabular-nums">{formatNum(contract.delta, 2)}</td>
      <td className="px-2 py-1 tabular-nums">{formatInt(contract.volume)}</td>
      <td className="px-2 py-1 tabular-nums">{formatInt(contract.open_interest)}</td>
      <td className="px-1 py-1 text-right w-12 whitespace-nowrap">
        <Button
          variant="ghost"
          size="icon"
          className="h-5 w-5"
          disabled={tracking}
          onClick={(e) => {
            e.stopPropagation();
            void handleTrack();
          }}
          aria-label="Track this contract in the P&L tab"
          title="Track in P&L (paper, 1 contract @ mid)"
        >
          <PlusCircle className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-5 w-5"
          onClick={(e) => {
            e.stopPropagation();
            void handleCopy();
          }}
          aria-label="Copy trade detail"
        >
          <Copy className={cn('h-3 w-3', copied && 'text-emerald-500')} />
        </Button>
      </td>
    </tr>
  );
}

function mid(a: number | null, b: number | null): number | null {
  if (a === null || b === null) return null;
  return (a + b) / 2;
}

function formatNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

function formatInt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

function formatPct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  // Polygon ships IV as a fraction (0.32) — render as percent.
  return `${(n * 100).toFixed(1)}%`;
}
