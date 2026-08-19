/** Shared weekly JSON types so Chronicle and tests paint the same [N] as Approval Hub. */

export type ThreadVia = 'evolves' | 'invalidates';

export type WeeklyThreadStep = {
  seq: number;
  date: string;
  event_id: string;
  text: string;
  cite_n?: number | null;
  via?: ThreadVia | null;
  to_seq?: number | null;
};

export type WeeklyCrossDayThread = {
  id: string;
  label: string;
  start_date?: string;
  end_date?: string;
  entity_keys?: string[];
  steps: WeeklyThreadStep[];
  outcome?: { state?: string; text?: string } | null;
};

export type WeeklyIntraDayThread = {
  date: string;
  weekday?: string;
  source_field?: string;
  text: string;
  empty?: boolean;
};

export type WeeklyJsonPayload = {
  week_key?: string;
  legend?: Record<string, string>;
  'cross-day-thread'?: WeeklyCrossDayThread[];
  'intra-day-thread'?: WeeklyIntraDayThread[];
  entities?: unknown[];
};

/** Look up week-global [N] from legend so a thread step cannot mint a local index. */
export function citeNForEventId(
  legend: Record<string, string> | undefined,
  eventId: string,
  denorm?: number | null,
): number | null {
  if (denorm != null && legend) {
    const mapped = legend[String(denorm)];
    if (mapped === eventId) return denorm;
  }
  if (!legend) return denorm ?? null;
  for (const [n, mem] of Object.entries(legend)) {
    if (mem === eventId) {
      const parsed = Number(n);
      return Number.isFinite(parsed) ? parsed : null;
    }
  }
  return null;
}

/** Badge the superseded step (to_seq, else former seq) only when via is invalidates. */
export function invalidatesBadgeSeq(step: WeeklyThreadStep): number | null {
  if (step.via !== 'invalidates') return null;
  if (step.to_seq != null) return step.to_seq;
  return step.seq > 1 ? step.seq - 1 : null;
}
