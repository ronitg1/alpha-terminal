/**
 * Market News + Earnings Transcripts API client.
 *
 * Thin wrapper around the `/news/*` and `/transcripts/*` backend routes.
 */

import { ArticleSummary, NewsArticle, NewsFeed, TranscriptAnalysis } from '@/types/sleeves';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText} ${body}`);
  }
  return (await res.json()) as T;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`POST ${path} failed: ${res.status} ${res.statusText} ${text}`);
  }
  return (await res.json()) as T;
}

export const newsApi = {
  getFeed: (tickers: string[], hours = 168) =>
    getJSON<NewsFeed>(
      `/news/feed?tickers=${encodeURIComponent(tickers.join(','))}&hours=${hours}`,
    ),
  getTickerNews: (ticker: string, hours = 168) =>
    getJSON<{ ticker: string; articles: NewsArticle[] }>(
      `/news/ticker/${encodeURIComponent(ticker)}?hours=${hours}`,
    ),
  summarize: (article: { title: string; description?: string; related?: string | null }) =>
    postJSON<ArticleSummary>('/news/summarize', article),
};

export const transcriptsApi = {
  extractUrl: (url: string) =>
    postJSON<{ text: string; chars: number }>('/transcripts/extract-url', { url }),
  uploadPdf: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${API_BASE_URL}/transcripts/upload`, { method: 'POST', body: form });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Upload failed: ${res.status} ${text}`);
    }
    return (await res.json()) as { text: string; chars: number; filename: string };
  },
  analyze: (body: {
    ticker: string;
    transcript: string;
    current_thesis?: string | null;
    report_date?: string | null;
  }) => postJSON<TranscriptAnalysis>('/transcripts/analyze', body),
};
