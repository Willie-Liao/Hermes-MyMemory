/**
 * Pure helpers for Memory Approval Weekly Review action queue
 * (Hypothesis + Possible overdue). Browser-safe — no Node imports.
 */
import {
  citeTargetId,
  type FourPartBrief,
  type FourPartBullet,
} from './fourPartBrief';
import type { WeeklyReviewPendingOp } from './weeklyReviewOps';

export function hypothesisIdFor(
  item: FourPartBullet,
  citeMap: FourPartBrief['citeMap'],
): string {
  if (item.cite != null) {
    const entry = citeTargetId(citeMap, item.cite);
    if (entry?.kind === 'hypothesis' && entry.targetId) return entry.targetId;
  }
  const slug = item.text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 40);
  return `hyp-${slug || 'anonymous'}`;
}

export function findReviewMark(
  ops: WeeklyReviewPendingOp[],
  predicate: (op: WeeklyReviewPendingOp) => boolean,
): WeeklyReviewPendingOp | undefined {
  return ops.find(predicate);
}

export function hypMarkedIn(
  ops: WeeklyReviewPendingOp[],
  hypId: string,
): boolean {
  return ops.some(
    (op) =>
      (op.kind === 'hyp_confirm' || op.kind === 'hyp_delete')
      && op.hypothesisId === hypId,
  );
}

export function spanMarkedIn(
  ops: WeeklyReviewPendingOp[],
  blockId: string,
): boolean {
  return ops.some(
    (op) =>
      (op.kind === 'span_confirm'
        || op.kind === 'span_put_off'
        || op.kind === 'span_set_due_date')
      && op.blockId === blockId,
  );
}
