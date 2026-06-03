/**
 * TranscriptsView — Earnings Transcripts tab.
 *
 * Three input modes (paste text / paste URL / upload PDF) converge on one
 * analysis that returns a 9-section structured read: sentiment vs prior
 * quarter, tone delta, key themes w/ quotes, hedging flags, dodged questions,
 * competitive + regulatory mentions, an explicit thesis-impact verdict, and
 * watch-next-quarter items.
 */

import { transcriptsApi } from '@/services/news-api';
import { TranscriptAnalysis } from '@/types/sleeves';
import { cn } from '@/lib/utils';
import { FileText, Link as LinkIcon, Upload, Sparkles } from 'lucide-react';
import { useRef, useState } from 'react';
import { AnalysisCards } from './analysis-cards';

type InputMode = 'text' | 'url' | 'pdf';

export function TranscriptsView() {
  const [mode, setMode] = useState<InputMode>('text');
  const [ticker, setTicker] = useState('');
  const [transcript, setTranscript] = useState('');
  const [url, setUrl] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<TranscriptAnalysis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const extractUrl = async () => {
    if (!url.trim()) return;
    setExtracting(true);
    setErr(null);
    try {
      const r = await transcriptsApi.extractUrl(url.trim());
      setTranscript(r.text);
      setMode('text');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setExtracting(false);
    }
  };

  const uploadPdf = async (file: File) => {
    setExtracting(true);
    setErr(null);
    try {
      const r = await transcriptsApi.uploadPdf(file);
      setTranscript(r.text);
      setMode('text');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setExtracting(false);
    }
  };

  const analyze = async () => {
    if (transcript.trim().length < 500) {
      setErr('Need at least 500 characters of transcript to analyze.');
      return;
    }
    setAnalyzing(true);
    setErr(null);
    try {
      const r = await transcriptsApi.analyze({ ticker: ticker.trim().toUpperCase(), transcript });
      setResult(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-5">
        <div className="flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">Earnings Call Analysis</h1>
        </div>
        <p className="text-xs text-muted-foreground -mt-3">
          Paste a transcript, a URL, or upload a PDF. The analysis flags tone shifts, hedging,
          dodged questions, competitive + policy mentions, and the thesis impact.
        </p>

        {/* Input mode tabs */}
        <div className="inline-flex rounded-md border border-border overflow-hidden text-xs">
          <ModeTab icon={FileText} label="Paste text" active={mode === 'text'} onClick={() => setMode('text')} />
          <ModeTab icon={LinkIcon} label="From URL" active={mode === 'url'} onClick={() => setMode('url')} />
          <ModeTab icon={Upload} label="Upload PDF" active={mode === 'pdf'} onClick={() => setMode('pdf')} />
        </div>

        {/* Input area */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="e.g. FSLR"
              className="bg-background border border-border rounded px-2 py-1 text-xs font-mono uppercase w-28 outline-none focus:border-primary"
            />
          </div>

          {mode === 'url' && (
            <div className="flex items-center gap-2">
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.fool.com/earnings/call-transcripts/…"
                className="flex-1 bg-background border border-border rounded px-2 py-1.5 text-xs outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => void extractUrl()}
                disabled={extracting || !url.trim()}
                className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:bg-primary/80 disabled:opacity-50 transition-colors"
              >
                {extracting ? 'Extracting…' : 'Extract'}
              </button>
            </div>
          )}

          {mode === 'pdf' && (
            <div
              onClick={() => fileRef.current?.click()}
              className="border border-dashed border-border rounded-md px-4 py-6 text-center cursor-pointer hover:border-foreground/30 transition-colors"
            >
              <Upload className="h-5 w-5 mx-auto text-muted-foreground mb-1" />
              <p className="text-xs text-muted-foreground">
                {extracting ? 'Extracting…' : 'Click to choose a PDF transcript'}
              </p>
              <input
                ref={fileRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadPdf(f);
                }}
              />
            </div>
          )}

          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={
              mode === 'text'
                ? 'Paste the earnings call transcript here…'
                : 'Extracted transcript will appear here — review, then Analyze.'
            }
            rows={mode === 'text' ? 12 : 6}
            className="w-full bg-background border border-border rounded-md px-3 py-2 text-xs leading-relaxed font-mono outline-none focus:border-primary resize-y"
          />

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void analyze()}
              disabled={analyzing || transcript.trim().length < 500}
              className="inline-flex items-center gap-1.5 text-sm px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/80 disabled:opacity-50 transition-colors"
            >
              <Sparkles className="h-4 w-4" />
              {analyzing ? 'Analyzing…' : 'Analyze transcript'}
            </button>
            <span className="text-[10px] text-muted-foreground">
              {transcript.trim().length.toLocaleString()} chars
            </span>
          </div>

          {err && (
            <div className="text-xs text-rose-500 italic px-2 py-2 rounded border border-rose-500/30 bg-rose-500/5">
              {err}
            </div>
          )}
        </div>

        {result && <AnalysisCards analysis={result} />}
      </div>
    </div>
  );
}

function ModeTab({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 border-r border-border last:border-r-0 transition-colors',
        active ? 'bg-foreground/5 text-foreground' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}
