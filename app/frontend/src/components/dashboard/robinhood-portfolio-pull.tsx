import { cn } from '@/lib/utils';
import { robinhoodApi } from '@/services/robinhood-api';
import type { RobinhoodPortfolioResponse } from '@/types/robinhood';
import { RefreshCw, Wallet } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

export function RobinhoodPortfolioPull() {
  const [loading, setLoading] = useState(false);
  const [portfolio, setPortfolio] = useState<RobinhoodPortfolioResponse | null>(null);

  const pullPortfolio = async () => {
    setLoading(true);
    try {
      const payload = await robinhoodApi.getPortfolio();
      setPortfolio(payload);
      toast.success('Pulled Robinhood portfolio snapshot.');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border border-border/60 bg-card overflow-hidden">
      <div className="px-3 py-2 border-b border-border/60 flex items-center gap-2">
        <Wallet className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Robinhood portfolio
        </span>
        {portfolio && (
          <span className="ml-auto text-[10px] text-muted-foreground">
            as of {portfolio.asof.replace('T', ' ')}
          </span>
        )}
        <button
          type="button"
          onClick={() => void pullPortfolio()}
          disabled={loading}
          className={cn(
            'inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted text-muted-foreground disabled:opacity-50',
            !portfolio && 'ml-auto',
          )}
        >
          <RefreshCw className={cn('h-3 w-3', loading && 'animate-spin')} />
          {loading ? 'Pulling' : portfolio ? 'Refresh' : 'Pull snapshot'}
        </button>
      </div>
      {portfolio && (
        <div className="max-h-80 overflow-auto p-3 space-y-3">
          {portfolio.tools.map((tool) => (
            <div key={tool.tool} className="space-y-1">
              <div className="text-[11px] font-mono text-muted-foreground">{tool.tool}</div>
              <pre className="text-[11px] leading-relaxed bg-muted/40 border border-border/60 rounded p-2 overflow-auto">
                {JSON.stringify(tool.data, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
