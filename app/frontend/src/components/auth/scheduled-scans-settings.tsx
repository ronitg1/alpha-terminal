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

// Frequency options: 0 = classic once-daily at a time; 60/120/240 = recurring.
const FREQ_CHOICES: { value: number; label: string }[] = [
  { value: 0, label: 'Daily' },
  { value: 60, label: 'Every 1h' },
  { value: 120, label: 'Every 2h' },
  { value: 240, label: 'Every 4h' },
];

/** Short label for a schedule's frequency. */
function freqLabel(s: ScanSchedule): string {
  return s.interval_minutes ? `Every ${s.interval_minutes / 60}h` : fmt12(s.time_of_day);
}

export function ScheduledScansSettings() {
  const [schedules, setSchedules] = useState<ScanSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTime, setNewTime] = useState('08:00');
  const [newTf, setNewTf] = useState('day');
  const [newLookback, setNewLookback] = useState(180);
  const [newInterval, setNewInterval] = useState(0); // 0 = daily
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
      // Interval schedules use the time as a daily start anchor; default it so two
      // interval schedules don't collide on the unique (user, time) key.
      const interval = newInterval || null;
      const anchor = interval ? newTime : newTime;
      await scheduledApi.addSchedule(anchor, tz, newTf, newLookback, interval);
      toast.success(interval ? `Scan scheduled every ${interval / 60}h` : `Scan scheduled for ${fmt12(newTime)}`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to add scan');
    } finally {
      setBusy(false);
    }
  };

  const editConfig = async (
    s: ScanSchedule,
    opts: { timeframe?: string; lookbackDays?: number; intervalMinutes?: number | null } = {},
  ) => {
    try {
      await scheduledApi.updateSchedule(
        s.id,
        opts.timeframe ?? s.timeframe,
        opts.lookbackDays ?? s.lookback_days,
        opts.intervalMinutes !== undefined ? opts.intervalMinutes : s.interval_minutes,
      );
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
              <span className="font-mono min-w-[4.5rem]">{freqLabel(s)}</span>
              {/* Frequency (daily-at-time vs recurring), editable inline. */}
              <select
                value={s.interval_minutes ?? 0}
                onChange={(e) => void editConfig(s, { intervalMinutes: Number(e.target.value) || null })}
                className={selectCls}
                aria-label="Frequency"
              >
                {FREQ_CHOICES.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
              {/* Per-schedule timeframe + lookback (editable inline). */}
              <select
                value={s.timeframe}
                onChange={(e) =>
                  void editConfig(s, {
                    timeframe: e.target.value,
                    lookbackDays: timeframeConfig(e.target.value).defaultLookback,
                  })
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
                onChange={(e) => void editConfig(s, { lookbackDays: Number(e.target.value) })}
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
            <select
              value={newInterval}
              onChange={(e) => setNewInterval(Number(e.target.value))}
              className={selectCls}
              aria-label="New frequency"
            >
              {FREQ_CHOICES.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
            <input
              type="time"
              value={newTime}
              onChange={(e) => setNewTime(e.target.value)}
              className="bg-background border border-border rounded px-2 py-1 text-xs"
              title={newInterval ? 'Start from this time each day' : 'Run at this time'}
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
            Each scan runs its own frequency (once <strong>Daily</strong> at a time, or recurring{' '}
            <strong>Every 1h/2h/4h</strong> from that start time), timeframe, and lookback — e.g. a
            daily 2yr premarket scan plus an hourly 1h/30d intraday scan. The Pattern Scanner shows
            the saved pre-scan for whichever timeframe you select, and high-confidence hits can be
            pushed to your phone (Alerts tab).
          </p>
        </>
      )}
    </div>
  );
}
