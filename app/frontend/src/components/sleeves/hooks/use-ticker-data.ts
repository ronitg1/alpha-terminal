/**
 * Tiny in-memory cache + hook for /sleeves/ticker/{ticker}.
 *
 * The new dashboard renders many tickers at once (high-conviction tiles,
 * sleeve summary sparklines, rich row sparklines) and we don't want every
 * remount to re-fetch. The backend already caches for 5 minutes; this is a
 * client-side coalescer to keep duplicate requests from going out at all.
 *
 * Not using SWR/React Query — neither is installed and a Map keyed by
 * ticker is enough for this scope.
 */
import { sleevesApi } from '@/services/sleeves-api';
import type { TickerData } from '@/types/sleeves';
import { useEffect, useState } from 'react';

interface CacheEntry {
  data: TickerData | null;
  error: string | null;
  inFlight: Promise<TickerData> | null;
  fetchedAt: number;
}

// 4-minute browser-side TTL — slightly under the 5-min backend cache so we
// re-fetch right when it expires server-side rather than serving stale.
const TTL_MS = 4 * 60 * 1000;

const cache = new Map<string, CacheEntry>();

function getOrFetch(ticker: string): Promise<TickerData> {
  const key = ticker.toUpperCase();
  const now = Date.now();
  const existing = cache.get(key);
  if (existing) {
    if (existing.inFlight) return existing.inFlight;
    if (existing.data && now - existing.fetchedAt < TTL_MS) {
      return Promise.resolve(existing.data);
    }
  }
  const promise = sleevesApi.getTickerData(key);
  cache.set(key, {
    data: existing?.data ?? null,
    error: null,
    inFlight: promise,
    fetchedAt: existing?.fetchedAt ?? 0,
  });
  return promise
    .then((data) => {
      cache.set(key, { data, error: null, inFlight: null, fetchedAt: Date.now() });
      return data;
    })
    .catch((err) => {
      cache.set(key, {
        data: existing?.data ?? null,
        error: err instanceof Error ? err.message : String(err),
        inFlight: null,
        fetchedAt: existing?.fetchedAt ?? 0,
      });
      throw err;
    });
}

export interface UseTickerDataResult {
  data: TickerData | null;
  loading: boolean;
  error: string | null;
}

export function useTickerData(ticker: string | null | undefined): UseTickerDataResult {
  const [, force] = useState(0);
  const key = ticker?.toUpperCase() ?? null;

  useEffect(() => {
    if (!key) return;
    let live = true;
    getOrFetch(key)
      .then(() => {
        if (live) force((n) => n + 1);
      })
      .catch(() => {
        if (live) force((n) => n + 1);
      });
    return () => {
      live = false;
    };
  }, [key]);

  if (!key) return { data: null, loading: false, error: null };
  const entry = cache.get(key);
  return {
    data: entry?.data ?? null,
    loading: !entry?.data && !!entry?.inFlight,
    error: entry?.error ?? null,
  };
}
