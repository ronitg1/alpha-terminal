/**
 * useMyStocks — small hook owning the user's editable My-Stocks list.
 *
 * Persisted in localStorage under "my-stocks" as a JSON array of upper-case
 * tickers. Independent from the sleeve watchlist (which is server-side and
 * scoped to the opportunistic sleeve) — this is purely a UI watchlist that
 * lets the user pin any name on a charts dashboard.
 *
 * Returns add / remove / set helpers + the current ordered list.
 */
import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'my-stocks';

const DEFAULT_TICKERS = ['NVDA', 'MSFT', 'AAPL', 'GOOGL', 'META', 'AMZN'];

function loadFromStorage(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_TICKERS;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed
        .map((t) => String(t).trim().toUpperCase())
        .filter(Boolean);
    }
  } catch {
    // Corrupted entry — fall through to defaults.
  }
  return DEFAULT_TICKERS;
}

function saveToStorage(tickers: string[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tickers));
  } catch {
    // localStorage can fail in private mode; non-fatal.
  }
}

export function useMyStocks() {
  const [tickers, setTickers] = useState<string[]>(() => loadFromStorage());

  useEffect(() => {
    saveToStorage(tickers);
  }, [tickers]);

  const add = useCallback((symbol: string) => {
    const t = symbol.trim().toUpperCase();
    if (!t) return false;
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(t)) return false;
    setTickers((prev) => (prev.includes(t) ? prev : [...prev, t]));
    return true;
  }, []);

  const remove = useCallback((symbol: string) => {
    const t = symbol.toUpperCase();
    setTickers((prev) => prev.filter((s) => s !== t));
  }, []);

  const move = useCallback((symbol: string, direction: 'up' | 'down') => {
    setTickers((prev) => {
      const i = prev.indexOf(symbol.toUpperCase());
      if (i < 0) return prev;
      const j = direction === 'up' ? i - 1 : i + 1;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }, []);

  const clear = useCallback(() => setTickers([]), []);

  return { tickers, add, remove, move, clear };
}
