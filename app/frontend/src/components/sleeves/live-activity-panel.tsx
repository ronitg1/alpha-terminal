/**
 * LiveActivityPanel — auto-scrolling log of progress events during a scan.
 *
 * Renders nothing when there's no activity AND no scan is running. During
 * a scan, shows up to MAX_VISIBLE events in reverse chronological order
 * (newest at top) so the user always sees what just happened.
 *
 * Phase 4 will graduate this to a real bottom-panel tab. For now an inline
 * collapsible card under the high-conviction strip is the right scope.
 */

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useSleevesContext } from '@/contexts/sleeves-context';
import { Activity } from 'lucide-react';
import { useEffect, useRef } from 'react';

const MAX_VISIBLE = 80;

function fmtTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour12: false });
}

export function LiveActivityPanel() {
  const { liveActivity, scanStatus } = useSleevesContext();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to bottom (latest event) while scanning.
  useEffect(() => {
    if (scrollRef.current && scanStatus === 'running') {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [liveActivity, scanStatus]);

  // Don't take up space when there's nothing to show.
  if (scanStatus !== 'running' && liveActivity.length === 0) {
    return null;
  }

  const visible = liveActivity.slice(-MAX_VISIBLE);

  return (
    <div className="px-6 py-4 border-b border-border">
      <div className="flex items-center gap-2 mb-2">
        <Activity className={`h-3.5 w-3.5 ${scanStatus === 'running' ? 'text-emerald-500 animate-pulse' : 'text-muted-foreground'}`} />
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          Live Activity
        </span>
        <Badge variant="outline" className="font-mono text-[10px]">
          {liveActivity.length}
        </Badge>
      </div>
      <Card className="bg-muted/30">
        <div
          ref={scrollRef}
          className="max-h-40 overflow-y-auto px-3 py-2 font-mono text-[11px] space-y-0.5"
        >
          {visible.length === 0 ? (
            <div className="text-muted-foreground italic">Waiting for first progress event…</div>
          ) : (
            visible.map((ev) => (
              <div key={ev.seq} className="flex gap-3">
                <span className="text-muted-foreground tabular-nums">{fmtTime(ev.ts)}</span>
                <span className="text-foreground/70 w-32 truncate">{ev.agent}</span>
                <span className="text-foreground/90 w-12 truncate">{ev.ticker ?? ''}</span>
                <span className="text-muted-foreground truncate flex-1">{ev.status}</span>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
