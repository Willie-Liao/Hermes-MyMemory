/** Shared weekly JSON types for Chronicle summary paint. */

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

export type WeeklySummaryItem = {
  text: string;
  weekdays?: string[];
};

export type WeeklyJsonPayload = {
  week_key?: string;
  legend?: Record<string, string>;
  'cross-day-thread'?: WeeklyCrossDayThread[];
  'intra-day-thread'?: WeeklyIntraDayThread[];
  summary?: WeeklySummaryItem[];
  entities?: unknown[];
};

/** Paint `- text (Monday, Tuesday)` so Chronicle does not parse hops. */
export function formatSummaryLine(row: WeeklySummaryItem): string {
  const text = (row.text || '').trim();
  const days = (row.weekdays || []).filter(Boolean).join(', ');
  if (!text) return '- None.';
  return days ? `- ${text} (${days})` : `- ${text}`;
}

/** Badge the superseded step (to_seq, else former seq) only when via is invalidates. */
export function invalidatesBadgeSeq(step: WeeklyThreadStep): number | null {
  if (step.via !== 'invalidates') return null;
  if (step.to_seq != null) return step.to_seq;
  return step.seq > 1 ? step.seq - 1 : null;
}
