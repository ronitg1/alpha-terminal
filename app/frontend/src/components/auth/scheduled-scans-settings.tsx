/**
 * ScheduledScansSettings — manage the times a user wants their Pattern Scanner
 * pre-run in the background, so results are ready when they open it. Rendered
 * inside the Settings dialog. Times are stored in the user's local timezone.
 */
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Clock, Plus, Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { browserTimezone, scheduledApi, type ScanSchedule } from '@/services/scheduled-api';
import { SCAN_TIMEFRAMES, timeframeConfig } from '@/lib/scan-timeframes';

/** "15:30" -> "3:30 PM" for display. */
function fmt12(t: string): string {
  const [hStr, m] = t.split(':');
  const h = Number(hStr);
  const ampm = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${m} ${ampm}`;
}

const selectCls = 'bg-background border border-border rounded px-1.5 py-1 text-xs';

export function ScheduledScansSettings() {
  const [schedules, setSchedules] = useState<ScanSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTime, setNewTime] = useState('08:00');
  const [newTf, setNewTf] = useState('day');
  const [newLookback, setNewLookback] = useState(180);
  const [busy, setBusy] = useState(false);
  const tz = browserTimezone();

  // Picking a timeframe resets the lookback to that timeframe's default (the old
  // value is usually out of range for the new bar size).
  const selectNewTf = (tf: string) => {
    setNewTf(tf);
    setNewLookback(timeframeConfig(tf).defaultLookback);
  };

  const load = async () => {
    try {
      setSchedules(await scheduledApi.listSchedules());
    } catch (e) {
      toast.error(`Couldn't load scheduled scans: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const add = async () => {
    setBusy(true);
    try {
      await scheduledApi.addSchedule(newTime, tz, newTf, newLookback);
      toast.success(`Scan scheduled for ${fmt12(newTime)}`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add time');
    } finally {
      setBusy(false);
    }
  };

  const editConfig = async (s: ScanSchedule, timeframe: string, lookbackDays: number) => {
    try {
      await scheduledApi.updateSchedule(s.id, timeframe, lookbackDays);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to update scan');
    }
  };

  const toggle = async (s: ScanSchedule) => {
    try {
      await scheduledApi.toggleSchedule(s.id, !s.enabled);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed');
    }
  };

  const remove = async (s: ScanSchedule) => {
    try {
      await scheduledApi.deleteSchedule(s.id);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed');
    }
  };

  return (
    <div className="space-y-2 rounded-md border border-border p-3">
      <p className="text-sm font-medium">Scheduled scans</p>
      <p className="text-xs text-muted-foreground">
        Auto-run your Pattern Scanner in the background at set times so results are ready when you
        open it. Scans your watchlists. Times are in your timezone ({tz}).
      </p>

      {loading ? (
        <p className="text-xs text-muted-foreground">Loading…</p>
      ) : (
        <>
          {schedules.length === 0 && (
            <p className="text-xs italic text-muted-foreground">No scheduled times yet.</p>
          )}
          {schedules.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center gap-2 text-xs">
              <Clock className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-mono">{fmt12(s.time_of_day)}</span>
              {/* Per-schedule timeframe + lookback (editable inline). */}
              <select
                value={s.timeframe}
                onChange={(e) =>
                  void editConfig(s, e.target.value, timeframeConfig(e.target.value).defaultLookback)
                }
                className={selectCls}
                aria-label="Timeframe"
              >
                {SCAN_TIMEFRAMES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
              <select
                value={s.lookback_days}
                onChange={(e) => void editConfig(s, s.timeframe, Number(e.target.value))}
                className={selectCls}
                aria-label="Lookback"
              >
                {timeframeConfig(s.timeframe).lookbacks.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
              {!s.enabled && (
                <Badge variant="outline" className="text-[10px]">
                  off
                </Badge>
              )}
              <div className="ml-auto flex items-center gap-1">
                <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => toggle(s)}>
                  {s.enabled ? 'Disable' : 'Enable'}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2 text-muted-foreground hover:text-rose-500"
                  onClick={() => remove(s)}
                  aria-label="Remove time"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <input
              type="time"
              value={newTime}
              onChange={(e) => setNewTime(e.target.value)}
              className="bg-background border border-border rounded px-2 py-1 text-xs"
            />
            <select value={newTf} onChange={(e) => selectNewTf(e.target.value)} className={selectCls} aria-label="New timeframe">
              {SCAN_TIMEFRAMES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <select
              value={newLookback}
              onChange={(e) => setNewLookback(Number(e.target.value))}
              className={selectCls}
              aria-label="New lookback"
            >
              {timeframeConfig(newTf).lookbacks.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
            <Button size="sm" onClick={() => void add()} disabled={busy}>
              <Plus className="mr-1 h-3.5 w-3.5" />
              {busy ? 'Adding…' : 'Add'}
            </Button>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Each scheduled scan runs its own timeframe + lookback (e.g. a daily 2yr premarket scan
            and a 1h 30d intraday scan). The Pattern Scanner shows the saved pre-scan for whichever
            timeframe you select.
          </p>
        </>
      )}
    </div>
  );
}
