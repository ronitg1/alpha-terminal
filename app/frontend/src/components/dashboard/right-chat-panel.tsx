/**
 * RightChatPanel — AI Research assistant panel (right side).
 *
 * Context-aware: knows the current section, selected ticker, and recent
 * screener/scan data (passed from the app-level state).  Each new ticker
 * selection resets the suggested prompts.
 *
 * Uses POST /sleeves/chat/agent/stream (typed SSE, tool-calling agent) and
 * falls back to POST /sleeves/chat/stream (plain DeepSeek V3) when the agent
 * endpoint is unreachable. Tool activity renders as inline chips inside the
 * streaming assistant bubble.
 */

import { useDashboard } from '@/contexts/dashboard-context';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { streamAgentChat, streamChat, type ChatContext } from '@/services/sleeves-api';
import { ChatMessage } from '@/types/sleeves';
import { Bot, ChevronRight, Maximize2, Minimize2, Send, Wrench, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { ChatMarkdown } from './chat-markdown';

// ─── Local message shape (adds streaming + tool activity) ───────────────────

interface ToolStatus {
  name: string;
  done: boolean;
  ok?: boolean;
}

type PanelMessage = ChatMessage & { streaming?: boolean; tools?: ToolStatus[] };

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

function MessageBubble({ msg, expanded }: { msg: PanelMessage; expanded: boolean }) {
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
          'rounded-lg px-3 py-2 leading-relaxed min-w-0',
          expanded ? 'text-sm max-w-[90%]' : 'text-xs max-w-[85%]',
          isUser ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground',
        )}
      >
        {!isUser && msg.tools && msg.tools.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-1.5">
            {msg.tools.map((t, i) => (
              <span
                key={`${t.name}-${i}`}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border border-border bg-background/60 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground',
                  !t.done && 'animate-pulse',
                  t.done && t.ok === false && 'opacity-60 line-through',
                )}
                title={t.done ? (t.ok === false ? `${t.name} failed` : `used ${t.name}`) : `using ${t.name}`}
              >
                <Wrench className="h-2.5 w-2.5 flex-shrink-0" />
                {t.name}
              </span>
            ))}
          </div>
        )}
        {isUser ? (
          <span className="whitespace-pre-wrap">{msg.content}</span>
        ) : (
          msg.content && <ChatMarkdown content={msg.content} />
        )}
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

  const [messages, setMessages] = useState<PanelMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
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

  /** Immutable update applied to the trailing streaming assistant message. */
  const patchStreaming = useCallback((fn: (last: PanelMessage) => PanelMessage) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last?.streaming) return prev;
      return [...prev.slice(0, -1), fn(last)];
    });
  }, []);

  /** Freeze the trailing streaming message (drop the cursor, keep tool chips). */
  const finalizeStreaming = useCallback(() => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last?.streaming) return prev;
      return [...prev.slice(0, -1), { role: 'assistant', content: last.content, tools: last.tools }];
    });
    setLoading(false);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      const userMsg: ChatMessage = { role: 'user', content: trimmed };
      const newHistory: ChatMessage[] = [
        ...messages.filter((m) => !m.streaming).map((m) => ({ role: m.role, content: m.content })),
        userMsg,
      ];
      setMessages([...newHistory, { role: 'assistant', content: '', streaming: true }]);
      setInput('');
      setLoading(true);

      abortRef.current?.abort();
      abortRef.current = new AbortController();
      const signal = abortRef.current.signal;

      const context: ChatContext = {
        section,
        selectedTicker,
        screenerSnapshot: screenerSnapshot ?? null,
        patternSnapshot: patternSnapshot ?? null,
        scanSnapshot: buildScanSnapshot() as Record<string, unknown> | null,
      };

      // Legacy plain-chat stream — used when the agent endpoint is unreachable.
      const fallbackToPlainChat = async () => {
        await streamChat(
          newHistory,
          context,
          (token) => patchStreaming((last) => ({ ...last, content: last.content + token })),
          finalizeStreaming,
          signal,
        );
      };

      let gotOutput = false;
      try {
        await streamAgentChat(
          newHistory,
          context,
          {
            onToken: (token) => {
              gotOutput = true;
              patchStreaming((last) => ({ ...last, content: last.content + token }));
            },
            onToolCall: (name) => {
              gotOutput = true;
              patchStreaming((last) => ({
                ...last,
                tools: [...(last.tools ?? []), { name, done: false }],
              }));
            },
            onToolResult: (name, ok) => {
              patchStreaming((last) => {
                const tools = [...(last.tools ?? [])];
                const idx = tools.findIndex((t) => t.name === name && !t.done);
                if (idx >= 0) tools[idx] = { ...tools[idx], done: true, ok };
                return { ...last, tools };
              });
            },
            onError: (message) => {
              patchStreaming((last) => ({
                ...last,
                content: last.content || message,
              }));
              gotOutput = true;
            },
            onDone: finalizeStreaming,
          },
          signal,
        );
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        // Agent endpoint failed before producing anything — fall back to the
        // plain (non-agent) chat stream so the panel keeps working.
        if (!gotOutput) {
          try {
            await fallbackToPlainChat();
            return;
          } catch (fallbackErr) {
            if ((fallbackErr as Error).name === 'AbortError') return;
          }
        }
        patchStreaming((last) => ({
          ...last,
          content: last.content || 'Connection error — check that the backend is running.',
        }));
        finalizeStreaming();
      }
    },
    [
      loading,
      messages,
      section,
      selectedTicker,
      screenerSnapshot,
      patternSnapshot,
      buildScanSnapshot,
      patchStreaming,
      finalizeStreaming,
    ],
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
    <div
      className={cn(
        'flex flex-col bg-background',
        expanded
          ? 'fixed inset-0 z-50 safe-top'
          : 'h-full w-full md:w-80 md:border-l md:border-border md:flex-shrink-0 max-md:fixed max-md:inset-0 max-md:z-50 max-md:safe-top',
      )}
    >
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
          onClick={() => setExpanded((e) => !e)}
          className="hidden md:inline-flex text-muted-foreground hover:text-foreground transition-colors"
          title={expanded ? 'Collapse chat' : 'Expand chat to full screen'}
          aria-label={expanded ? 'Collapse chat' : 'Expand chat'}
        >
          {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          onClick={toggleChat}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Message thread */}
      <div className={cn('flex-1 overflow-y-auto px-3 py-3', expanded && 'w-full max-w-3xl mx-auto')}>
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
              <MessageBubble key={i} msg={m} expanded={expanded} />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className={cn('border-t border-border p-3 safe-bottom w-full', expanded && 'max-w-3xl mx-auto')}>
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
          Agent with live data tools · DeepSeek
        </p>
      </div>
    </div>
  );
}
