/**
 * Browser-safe Weekly Review pending-op types + UI mark labels.
 * Keep Node/fs logic in weeklyReviewRecall.ts (server-only).
 */
import type { PutOffInterval } from './overdueActions';
import type { MemoryBlock } from './types';

export type WeeklyReviewHypConfirmPending = {
  kind: 'hyp_confirm';
  weekKey: string;
  hypothesisId: string;
  text: string;
  cite?: number | null;
  related?: string[];
  /** Prefer this daily date when set (YYYY-MM-DD). */
  citeDate?: string;
  confidence?: MemoryBlock['confidence'];
  sources?: string[];
  /** Optional deterministic fact id (tests / clients). */
  factId?: string;
};

export type WeeklyReviewHypDeletePending = {
  kind: 'hyp_delete';
  weekKey: string;
  hypothesisId: string;
  text?: string;
  cite?: number | null;
};

export type WeeklyReviewSpanConfirmPending = {
  kind: 'span_confirm';
  weekKey: string;
  blockId: string;
  proposed_valid_to: string;
};

export type WeeklyReviewSpanPutOffPending = {
  kind: 'span_put_off';
  weekKey: string;
  blockId: string;
  interval: PutOffInterval;
  proposed_valid_to?: string;
};

export type WeeklyReviewSpanSetDuePending = {
  kind: 'span_set_due_date';
  weekKey: string;
  blockId: string;
  due_date: string;
};

export type WeeklyReviewPendingOp =
  | WeeklyReviewHypConfirmPending
  | WeeklyReviewHypDeletePending
  | WeeklyReviewSpanConfirmPending
  | WeeklyReviewSpanPutOffPending
  | WeeklyReviewSpanSetDuePending;

/** Stable UI key for a pending/saved review action mark. */
export function reviewOpMarkKey(op: WeeklyReviewPendingOp): string {
  if (op.kind === 'hyp_confirm' || op.kind === 'hyp_delete') {
    return `hyp:${op.hypothesisId}:${op.kind === 'hyp_confirm' ? 'confirm' : 'delete'}`;
  }
  const action =
    op.kind === 'span_confirm'
      ? 'confirm'
      : op.kind === 'span_put_off'
        ? 'put_off'
        : 'set_due_date';
  return `${op.blockId}:${action}`;
}

/** Same hyp id or span block — used to clear a local pending mark on second click. */
export function isSameReviewPendingTarget(
  op: WeeklyReviewPendingOp,
  target: WeeklyReviewPendingOp,
): boolean {
  const hyp =
    (op.kind === 'hyp_confirm' || op.kind === 'hyp_delete')
    && (target.kind === 'hyp_confirm' || target.kind === 'hyp_delete')
    && op.hypothesisId === target.hypothesisId;
  if (hyp) return true;
  const spanKinds = new Set([
    'span_confirm',
    'span_put_off',
    'span_set_due_date',
  ]);
  return (
    spanKinds.has(op.kind)
    && spanKinds.has(target.kind)
    && 'blockId' in op
    && 'blockId' in target
    && op.blockId === target.blockId
  );
}

/** Drop local pending ops that target the same hyp/span row. */
export function clearReviewPendingTarget(
  ops: WeeklyReviewPendingOp[],
  target: WeeklyReviewPendingOp,
): WeeklyReviewPendingOp[] {
  return ops.filter((op) => !isSameReviewPendingTarget(op, target));
}

export function reviewPendingButtonLabel(op: WeeklyReviewPendingOp): string {
  switch (op.kind) {
    case 'hyp_confirm':
      return 'Confirm · pending';
    case 'hyp_delete':
      return 'Delete · pending';
    case 'span_confirm':
      return 'Confirm · pending';
    case 'span_put_off':
      return 'Put off · pending';
    case 'span_set_due_date':
      return 'Set due · pending';
  }
}

/** Slot values for an event card; prefixes stay in formatEventBodyFromSlots, not here. */
export type EventBodySlots = {
  beginning: string;
  course: string;
  outcome: string;
};

const _EVENT_SEMI_RE =
  /^Beginning:\s*(.*?);\s*Course:\s*(.*?);\s*Outcome:\s*(.*)$/s;
const _EVENT_LOOSE_RE =
  /Beginning:\s*(.*?)\s*Course:\s*(.*?)\s*Outcome:\s*(.*)$/is;

function _trimSlot(value: string): string {
  return value.trim().replace(/;+$/, '').trim();
}

/** Split a daily event body so the editor can lock Beginning:/Course:/Outcome: as labels.

  A single textarea lets people delete those prefixes and save a card digest cannot re-parse.
  */
export function parseEventBodySlots(body: string): EventBodySlots {
  const text = String(body || '').trim();
  const match = text.match(_EVENT_SEMI_RE) || text.match(_EVENT_LOOSE_RE);
  if (match) {
    return {
      beginning: _trimSlot(match[1] || ''),
      course: _trimSlot(match[2] || ''),
      outcome: (match[3] || '').trim(),
    };
  }
  return { beginning: text, course: '', outcome: '' };
}

/** Join slot prose the same way digest_tools does so disk bodies stay machine-parseable.

  Labels live only in this join; the UI never puts them in the editable fields.
  */
export function formatEventBodyFromSlots(slots: EventBodySlots): string {
  return (
    `Beginning: ${slots.beginning.trim()}; ` +
    `Course: ${slots.course.trim()}; ` +
    `Outcome: ${slots.outcome.trim()}`
  );
}

/** After weekly review Save — per-row Recall labels. */
export function reviewRecallButtonLabel(op: WeeklyReviewPendingOp): string {
  switch (op.kind) {
    case 'hyp_confirm':
      return 'Recall confirm';
    case 'hyp_delete':
      return 'Recall delete';
    case 'span_confirm':
      return 'Recall confirm';
    case 'span_put_off':
      return 'Recall put off';
    case 'span_set_due_date':
      return 'Recall set due date';
  }
}
