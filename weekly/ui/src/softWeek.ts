/** Client-safe soft empty-week helpers (no Node imports). */

import { getCurrentISOWeekCode, pickDefaultWeek } from './isoWeek';
import type { WeeklyProposal, WeekOverview } from './types';

/** Soft-load payload when draft/reviewed weekly MD is missing (visitable empty week). */
export function emptyWeekSoftLoadPayload(week: string): WeekOverview & {
  empty_digests: boolean;
} {
  return {
    week,
    status: 'pending',
    tidyState: 'none',
    filePath: `${week}.md`,
    fileContent: '',
    decisions: [] as WeeklyProposal[],
    empty_digests: true,
  };
}

/** Blank pending WeekOverview for bootstrap when the weeks list is empty. */
export function blankPendingWeekOverview(week: string): WeekOverview {
  const soft = emptyWeekSoftLoadPayload(week);
  const { empty_digests: _empty, ...overview } = soft;
  return overview;
}

/**
 * Always returns a week to open: listed default, else a blank current ISO week.
 */
export function resolveDefaultWeekSelection(
  weeks: WeekOverview[],
  now: Date = new Date(),
): WeekOverview {
  return pickDefaultWeek(weeks, now)
    ?? blankPendingWeekOverview(getCurrentISOWeekCode(now));
}

/** Generate/update outcomes that mean no usable dailies (purged or never written). */
export function isEmptyDigestGenerateOutcome(outcome: unknown): boolean {
  return outcome === 'no_daily'
    || outcome === 'empty_digests'
    || outcome === 'empty_week';
}

/** Soft empty week response for update/rescan when digests are gone — same as a new week. */
export function purgedWeekSoftLoadResult(
  week: string,
  outcome: string = 'no_daily',
) {
  return {
    ...emptyWeekSoftLoadPayload(week),
    outcome,
  };
}
