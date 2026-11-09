/**
 * ISO-8601 week key (YYYY-Www), matching Python date.isocalendar().
 * Parses YYYY-MM-DD as a calendar date (UTC), not locale-dependent Date parsing.
 */
export function getISOWeekCode(dateStr: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr.trim());
  if (!m) return '';
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (
    !Number.isInteger(year)
    || !Number.isInteger(month)
    || !Number.isInteger(day)
    || month < 1
    || month > 12
    || day < 1
    || day > 31
  ) {
    return '';
  }

  // Thursday of the week containing this date determines the ISO week-year.
  const utc = Date.UTC(year, month - 1, day);
  const date = new Date(utc);
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) {
    return '';
  }

  const dayNum = date.getUTCDay() || 7; // Mon=1 … Sun=7
  date.setUTCDate(date.getUTCDate() + 4 - dayNum);
  const isoYear = date.getUTCFullYear();
  const yearStart = Date.UTC(isoYear, 0, 1);
  const weekNo = Math.ceil((((date.getTime() - yearStart) / 86400000) + 1) / 7);
  return `${isoYear}-W${String(weekNo).padStart(2, '0')}`;
}

/** Current ISO week for a Date (local calendar day → YYYY-Www). */
export function getCurrentISOWeekCode(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return getISOWeekCode(`${y}-${m}-${d}`);
}

const MONTH_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

/** Monday UTC of ISO week YYYY-Www (week 1 contains Jan 4). */
export function mondayOfISOWeek(weekKey: string): Date | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(weekKey.trim());
  if (!m) return null;
  const isoYear = Number(m[1]);
  const weekNo = Number(m[2]);
  if (
    !Number.isInteger(isoYear)
    || !Number.isInteger(weekNo)
    || weekNo < 1
    || weekNo > 53
  ) {
    return null;
  }
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const dayNum = jan4.getUTCDay() || 7; // Mon=1 … Sun=7
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - dayNum + 1 + (weekNo - 1) * 7);
  return monday;
}

function formatUtcDay(d: Date): string {
  return `${MONTH_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/**
 * Exact calendar span for an ISO week (Mon–Sun), e.g. "Jun 15–21"
 * or "Jun 29–Jul 5" / "Dec 29–Jan 4" when the month (or year) changes.
 */
export function formatISOWeekDateRange(weekKey: string): string {
  const monday = mondayOfISOWeek(weekKey);
  if (!monday) return '';
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  const sameMonth =
    monday.getUTCFullYear() === sunday.getUTCFullYear()
    && monday.getUTCMonth() === sunday.getUTCMonth();
  if (sameMonth) {
    return `${MONTH_SHORT[monday.getUTCMonth()]} ${monday.getUTCDate()}–${sunday.getUTCDate()}`;
  }
  return `${formatUtcDay(monday)}–${formatUtcDay(sunday)}`;
}

/**
 * Default WeekReview selection: current ISO week if listed, else newest week key.
 * Avoids jumping to the oldest pending row (list sorts pending ascending).
 */
export function pickDefaultWeek<T extends { week: string }>(
  weeks: T[],
  now: Date = new Date(),
): T | undefined {
  if (weeks.length === 0) return undefined;
  const current = getCurrentISOWeekCode(now);
  const match = weeks.find((w) => w.week === current);
  if (match) return match;
  return [...weeks].sort((a, b) => b.week.localeCompare(a.week))[0];
}

/** Parse ISO week key → { year, week } or null. */
export function parseISOWeekKey(
  weekKey: string,
): { year: number; week: number } | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(weekKey.trim());
  if (!m) return null;
  const year = Number(m[1]);
  const week = Number(m[2]);
  if (
    !Number.isInteger(year)
    || !Number.isInteger(week)
    || week < 1
    || week > 53
  ) {
    return null;
  }
  return { year, week };
}

/**
 * Calendar month (1–12) of the week's Monday (UTC).
 * Cross-month weeks (e.g. Jun 29–Jul 5) bucket under Monday's month.
 */
export function monthOfISOWeekMonday(weekKey: string): number | null {
  const monday = mondayOfISOWeek(weekKey);
  if (!monday) return null;
  return monday.getUTCMonth() + 1;
}

export function listYearsFromWeeks(weekKeys: string[]): number[] {
  const years = new Set<number>();
  for (const key of weekKeys) {
    const parsed = parseISOWeekKey(key);
    if (parsed) years.add(parsed.year);
  }
  return [...years].sort((a, b) => b - a);
}

/** Months (1–12) that have at least one listed week in `year` (by Monday month). */
export function listMonthsForYear(weekKeys: string[], year: number): number[] {
  const months = new Set<number>();
  for (const key of weekKeys) {
    const parsed = parseISOWeekKey(key);
    if (!parsed || parsed.year !== year) continue;
    const month = monthOfISOWeekMonday(key);
    if (month != null) months.add(month);
  }
  return [...months].sort((a, b) => a - b);
}

export function filterWeeksByYearMonth(
  weekKeys: string[],
  year: number,
  month: number,
): string[] {
  return weekKeys
    .filter((key) => {
      const parsed = parseISOWeekKey(key);
      if (!parsed || parsed.year !== year) return false;
      return monthOfISOWeekMonday(key) === month;
    })
    .sort((a, b) => a.localeCompare(b));
}

export const MONTH_SELECT_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;
