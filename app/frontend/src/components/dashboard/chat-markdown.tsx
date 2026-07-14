/**
 * ChatMarkdown — renders assistant replies as markdown (GFM tables, bold, lists,
 * code) instead of raw text. Styled for the chat bubble and dark-mode safe;
 * tables scroll horizontally so wide pattern tables never overflow the panel.
 */
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';

const COMPONENTS: Components = {
  p: ({ children }) => <p className="whitespace-pre-wrap leading-relaxed">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => <ul className="list-disc space-y-0.5 pl-4">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-0.5 pl-4">{children}</ol>,
  li: ({ children }) => <li className="marker:text-muted-foreground">{children}</li>,
  h1: ({ children }) => <h3 className="mt-2 text-sm font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-2 text-sm font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-1.5 font-semibold">{children}</h4>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-primary underline">
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    const block = String(className || '').includes('language-');
    return block ? (
      <code className="block">{children}</code>
    ) : (
      <code className="rounded bg-background/70 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
    );
  },
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded bg-background/70 p-2 font-mono text-[0.85em]">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-border">{children}</thead>,
  th: ({ children }) => <th className="whitespace-nowrap px-2 py-1 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-t border-border/50 px-2 py-1 align-top">{children}</td>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-2 text-muted-foreground">{children}</blockquote>
  ),
  hr: () => <hr className="my-1 border-border/60" />,
};

export function ChatMarkdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn('space-y-2 break-words', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
