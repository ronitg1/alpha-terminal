interface ConfidenceBadgeProps {
  confidence: number;
}

export function ConfidenceBadge({ confidence }: ConfidenceBadgeProps) {
  const pct = Math.round(confidence);

  const { label, barColor, badgeClass } =
    pct >= 76
      ? { label: 'High', barColor: 'bg-emerald-500', badgeClass: 'bg-emerald-900/40 text-emerald-400 border-emerald-700/50' }
      : pct >= 51
      ? { label: 'Med', barColor: 'bg-amber-500', badgeClass: 'bg-amber-900/40 text-amber-400 border-amber-700/50' }
      : { label: 'Low', barColor: 'bg-red-500', badgeClass: 'bg-red-900/40 text-red-400 border-red-700/50' };

  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-400 w-8 text-right">{pct}</span>
      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${badgeClass}`}>{label}</span>
    </div>
  );
}
