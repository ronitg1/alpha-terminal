/**
 * RightChatPanel — AI Research assistant panel (right side).
 *
 * Context-aware: knows the current section, selected ticker, and recent
 * screener/scan data (passed from the app-level state).  Each new ticker
 * selection resets the suggested prompts.
 *
 * Uses POST /sleeves/chat/stream (SSE) with DeepSeek V3.
 */

import { useDashboard } from '@/contexts/dashboard-context';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { streamChat } from '@/services/sleeves-api';
import { ChatMessage } from '@/types/sleeves';
import { Bot, ChevronRight, Send, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

// ─── Suggested prompts ──────────────────────────────────────────────────────

function getSuggestions(ticker: string | null, section: string): string[] {
  if (ticker) {
    return [
      `What's driving ${ticker}'s recent price movement?`,
      `What are the key risks for ${ticker} right now?`,
      `Summarise the latest news on ${ticker}.`,
      `What does the options flow say about ${ticker}?`,
    ];
  }
  if (section === 'screening') {
    return [
      'Which screener candidates have the highest conviction today?',
      'What patterns are dominating the scan results?',
      'Explain the top options screener signal.',
    ];
  }
  if (section === 'portfolio') {
    return [
      'What is the overall portfolio bias right now?',
      'Which portfolio has the strongest signals today?',
      'What are the top short candidates across my portfolios?',
    ];
  }
  return [
    "What's going on in the markets today?",
    'Which sectors are showing the most momentum?',
    'Summarise the morning scan results.',
  ];
}

// ─── Message bubble ──────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: ChatMessage & { streaming?: boolean } }) {
  const isUser = msg.role === 'user';
  return (
    <div className={cn('flex gap-2 mb-3', isUser && 'flex-row-reverse')}>
      {!isUser && (
        <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Bot className="h-3.5 w-3.5 text-primary" />
        </div>
      )}
      <div
        className={cn(
          'rounded-lg px-3 py-2 text-xs leading-relaxed max-w-[85%]',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted text-foreground',
        )}
      >
        {msg.content}
        {(msg as { streaming?: boolean }).streaming && (
          <span className="inline-block w-1 h-3 bg-current animate-pulse ml-0.5 align-middle" />
        )}
      </div>
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────

interface RightChatPanelProps {
  /** Optional screener snapshot passed from the screening section. */
  screenerSnapshot?: Record<string, unknown> | null;
  /** Optional pattern snapshot passed from the patterns section. */
  patternSnapshot?: Record<string, unknown> | null;
}

export function RightChatPanel({ screenerSnapshot, patternSnapshot }: RightChatPanelProps) {
  const { section, selectedTicker, chatOpen, toggleChat } = useDashboard();
  const { latestScan } = useSleevesContext();

  const [messages, setMessages] = useState<(ChatMessage & { streaming?: boolean })[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Reset conversation when ticker or section changes (keep it context-fresh)
  useEffect(() => {
    setMessages([]);
  }, [selectedTicker, section]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const buildScanSnapshot = useCallback(() => {
    if (!latestScan) return null;
    return {
      date: latestScan.date,
      rows: latestScan.rows.slice(0, 20).map((r) => ({
        ticker: r.ticker,
        consensus: r.consensus,
        weighted_score: r.weighted_score,
        avg_confidence: r.avg_confidence,
        sleeve: r.sleeve,
      })),
    };
  }, [latestScan]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      const userMsg: ChatMessage = { role: 'user', content: trimmed };
      const newHistory: ChatMessage[] = [...messages.filter((m) => !m.streaming), userMsg];
      setMessages([...newHistory, { role: 'assistant', content: '', streaming: true }]);
      setInput('');
      setLoading(true);

      abortRef.current?.abort();
      abortRef.current = new AbortController();

      try {
        await streamChat(
          newHistory,
          {
            section,
            selectedTicker,
            screenerSnapshot: screenerSnapshot ?? null,
            patternSnapshot: patternSnapshot ?? null,
            scanSnapshot: buildScanSnapshot() as Record<string, unknown> | null,
          },
          (token) => {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.streaming) {
                return [...prev.slice(0, -1), { ...last, content: last.content + token }];
              }
              return prev;
            });
          },
          () => {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.streaming) {
                return [...prev.slice(0, -1), { role: 'assistant', content: last.content }];
              }
              return prev;
            });
            setLoading(false);
          },
          abortRef.current.signal,
        );
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.streaming) {
            return [
              ...prev.slice(0, -1),
              { role: 'assistant', content: 'Connection error — check that the backend is running.' },
            ];
          }
          return prev;
        });
        setLoading(false);
      }
    },
    [loading, messages, section, selectedTicker, screenerSnapshot, patternSnapshot, buildScanSnapshot],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  };

  if (!chatOpen) return null;

  const suggestions = getSuggestions(selectedTicker, section);

  return (
    <div className="flex flex-col h-full w-80 border-l border-border bg-background flex-shrink-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <Bot className="h-4 w-4 text-primary flex-shrink-0" />
        <span className="text-sm font-semibold flex-1">Research</span>
        {selectedTicker && (
          <span className="text-xs font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
            {selectedTicker}
          </span>
        )}
        <button
          type="button"
          onClick={toggleChat}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Message thread */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              {selectedTicker
                ? `Ask anything about ${selectedTicker}`
                : 'Ask any financial question'}
            </p>
            <div className="space-y-1.5">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="w-full text-left text-xs flex items-center gap-2 px-3 py-2 rounded-lg border border-border hover:bg-muted/50 transition-colors group"
                >
                  <span className="flex-1 text-foreground/80">{s}</span>
                  <ChevronRight className="h-3 w-3 text-muted-foreground group-hover:text-foreground flex-shrink-0" />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div>
            {messages.map((m, i) => (
              <MessageBubble key={i} msg={m} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border p-3">
        {messages.length > 0 && (
          <button
            type="button"
            onClick={() => setMessages([])}
            className="text-[10px] text-muted-foreground hover:text-foreground mb-2 transition-colors"
          >
            New conversation
          </button>
        )}
        <div className="flex items-end gap-2 bg-muted rounded-lg px-3 py-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything…"
            rows={1}
            className="flex-1 bg-transparent text-xs resize-none outline-none placeholder-muted-foreground leading-relaxed max-h-24 overflow-y-auto"
            style={{ fieldSizing: 'content' } as React.CSSProperties}
          />
          <button
            type="button"
            onClick={() => void send(input)}
            disabled={!input.trim() || loading}
            className="flex-shrink-0 text-primary hover:text-primary/80 disabled:opacity-40 transition-colors"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="text-[10px] text-muted-foreground mt-1.5 text-center">
          DeepSeek V3 · ~$0.001/message
        </p>
      </div>
    </div>
  );
}
