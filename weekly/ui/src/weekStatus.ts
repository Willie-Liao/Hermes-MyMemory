import type { WeekOverview } from './types';
import { formatISOWeekDateRange } from './isoWeek.ts';

/**
 * Normalize week lifecycle to slash / `_weeks_status_rows` vocabulary.
 * `current` ≡ open/pending, `completed` ≡ closed/reviewed.
 */
export function normalizeWeekStatus(status: string): WeekOverview['status'] {
  if (status === 're-review pending' || status === 're-review') return 'pending';
  if (status === 'reviewed' || status === 'completed') return 'reviewed';
  if (status === 'pending' || status === 'current') return 'pending';
  // Unknown legacy values: treat as pending so the dropdown stays usable.
  return 'pending';
}

/** Parse document-level ``week_status`` from weekly MD frontmatter. */
export function parseWeekStatusFromContent(fileContent: string): 'pending' | 'reviewed' | null {
  const match = /^---\r?\n([\s\S]*?)\r?\n---/.exec(fileContent);
  if (!match) return null;
  const statusMatch = /^week_status:\s*(\S+)/im.exec(match[1]);
  if (!statusMatch) return null;
  const raw = statusMatch[1].trim().toLowerCase();
  if (raw === 'reviewed') return 'reviewed';
  if (raw === 'pending') return 'pending';
  return null;
}

export function weekStatusesEquivalent(a: string, b: string): boolean {
  return normalizeWeekStatus(a) === normalizeWeekStatus(b);
}

/** UI badge / select status: OPEN (draft) · CLOSED (reviewed). */
export function weekLifecycleLabel(status: string): 'OPEN' | 'CLOSED' {
  const normalized = normalizeWeekStatus(status);
  if (normalized === 'reviewed') return 'CLOSED';
  return 'OPEN';
}

/** Fixed width for week+range so status column aligns in monospace `<select>`. */
const WEEK_OPTION_LEFT_WIDTH = 28;

/** Dropdown label: "2026-W25 · Jun 15–21              OPEN". */
export function formatWeekOptionLabel(weekKey: string, status: string): string {
  const range = formatISOWeekDateRange(weekKey);
  const left = range ? `${weekKey} · ${range}` : weekKey;
  const lifecycle = weekLifecycleLabel(status);
  return `${left.padEnd(WEEK_OPTION_LEFT_WIDTH)} ${lifecycle}`;
}

/**
 * Week select (year/month already chosen): "W25 · Jun 15–21 OPEN".
 */
export function formatWeekCascadeOptionLabel(weekKey: string, status: string): string {
  const parsed = /^(\d{4})-(W\d{2})$/.exec(weekKey.trim());
  const weekPart = parsed?.[2] ?? weekKey;
  const range = formatISOWeekDateRange(weekKey);
  const lifecycle = weekLifecycleLabel(status);
  const left = range ? `${weekPart} · ${range}` : weekPart;
  return `${left} ${lifecycle}`;
}
