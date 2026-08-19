import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  findReviewMark,
  hypMarkedIn,
  hypothesisIdFor,
  resolveReviewCiteJump,
  spanMarkedIn,
} from './memoryApprovalActionQueue.ts';
import { PUT_OFF_OPTIONS, putOffLabels } from './overdueActions.ts';
import {
  clearReviewPendingTarget,
  reviewPendingButtonLabel,
  reviewRecallButtonLabel,
  type WeeklyReviewPendingOp,
} from './weeklyReviewOps.ts';
import type { CiteMapEntry } from './fourPartBrief.ts';
import type { MemoryBlock } from './types.ts';

const root = path.dirname(fileURLToPath(import.meta.url));

describe('memoryApprovalActionQueue helpers', () => {
  const citeMap: CiteMapEntry[] = [
    { n: 1, kind: 'event', targetId: 'evt-ship' },
    { n: 2, kind: 'hypothesis', targetId: 'hyp-abc' },
    { n: 3, kind: 'span', targetId: 'span-x' },
  ];

  it('resolves hypothesis id from cite map when present', () => {
    expect(
      hypothesisIdFor({ text: 'Maybe X', cite: 2 }, citeMap),
    ).toBe('hyp-abc');
  });

  it('falls back to slug id without hypothesis cite', () => {
    expect(
      hypothesisIdFor({ text: 'Maybe Launch Pipeline!', cite: null }, citeMap),
    ).toBe('hyp-maybe-launch-pipeline');
  });

  it('routes event cites to daily block when mem-id exists', () => {
    const block = { id: 'evt-ship' } as MemoryBlock;
    const jump = resolveReviewCiteJump(1, citeMap, [block]);
    expect(jump).toEqual({ kind: 'daily', block });
  });

  it('routes missing event / non-event cites to approval', () => {
    expect(resolveReviewCiteJump(1, citeMap, []).kind).toBe('approval');
    expect(resolveReviewCiteJump(2, citeMap, []).kind).toBe('approval');
    expect(resolveReviewCiteJump(3, citeMap, []).kind).toBe('approval');
  });

  it('exposes exact four put-off values', () => {
    expect(putOffLabels()).toEqual(['1 day', '7 days', '2 weeks', '1 month']);
    expect(PUT_OFF_OPTIONS.map((o) => o.label)).toEqual([
      '1 day',
      '7 days',
      '2 weeks',
      '1 month',
    ]);
  });

  it('labels pending and saved review ops', () => {
    const pending: WeeklyReviewPendingOp[] = [
      { kind: 'hyp_confirm', weekKey: '2026-W31', hypothesisId: 'h1', text: 't' },
      { kind: 'hyp_delete', weekKey: '2026-W31', hypothesisId: 'h2' },
      {
        kind: 'span_confirm',
        weekKey: '2026-W31',
        blockId: 'b1',
        proposed_valid_to: '2026-08-01',
      },
      {
        kind: 'span_put_off',
        weekKey: '2026-W31',
        blockId: 'b2',
        interval: '7d',
      },
      {
        kind: 'span_set_due_date',
        weekKey: '2026-W31',
        blockId: 'b3',
        due_date: '2026-08-10',
      },
    ];
    expect(pending.map(reviewPendingButtonLabel)).toEqual([
      'Confirm · pending',
      'Delete · pending',
      'Confirm · pending',
      'Put off · pending',
      'Set due · pending',
    ]);
    expect(pending.map(reviewRecallButtonLabel)).toEqual([
      'Recall confirm',
      'Recall delete',
      'Recall confirm',
      'Recall put off',
      'Recall set due date',
    ]);
  });

  it('finds pending/saved marks without writing', () => {
    const ops: WeeklyReviewPendingOp[] = [
      { kind: 'hyp_confirm', weekKey: '2026-W31', hypothesisId: 'h1', text: 't' },
      {
        kind: 'span_put_off',
        weekKey: '2026-W31',
        blockId: 'span-x',
        interval: '1d',
      },
    ];
    expect(hypMarkedIn(ops, 'h1')).toBe(true);
    expect(hypMarkedIn(ops, 'h2')).toBe(false);
    expect(spanMarkedIn(ops, 'span-x')).toBe(true);
    const putOff = findReviewMark(ops, (op) => op.kind === 'span_put_off');
    expect(putOff && putOff.kind === 'span_put_off' ? putOff.blockId : null).toBe(
      'span-x',
    );
  });

  it('clears local pending on second click (same hyp/span target)', () => {
    const ops: WeeklyReviewPendingOp[] = [
      { kind: 'hyp_confirm', weekKey: '2026-W31', hypothesisId: 'h1', text: 't' },
      {
        kind: 'span_put_off',
        weekKey: '2026-W31',
        blockId: 'span-x',
        interval: '1d',
      },
      {
        kind: 'span_confirm',
        weekKey: '2026-W31',
        blockId: 'span-y',
        proposed_valid_to: '2026-08-01',
      },
    ];
    const clearedSpan = clearReviewPendingTarget(ops, ops[1]);
    expect(clearedSpan.map((o) => o.kind)).toEqual(['hyp_confirm', 'span_confirm']);
    const clearedHyp = clearReviewPendingTarget(ops, ops[0]);
    expect(clearedHyp.map((o) => ('blockId' in o ? o.blockId : o.hypothesisId))).toEqual([
      'span-x',
      'span-y',
    ]);
  });
});

describe('Memory Approval action queue placement (source fixture)', () => {
  it('renders overdue queue inside Chronicle, not Memory Approval', () => {
    const weekReview = fs.readFileSync(
      path.join(root, 'components/WeekReview.tsx'),
      'utf8',
    );
    const chronicle = fs.readFileSync(
      path.join(root, 'components/FourPartWeeklyCard.tsx'),
      'utf8',
    );
    const queue = fs.readFileSync(
      path.join(root, 'components/MemoryApprovalActionQueue.tsx'),
      'utf8',
    );

    expect(queue).not.toContain('Hypothesis');
    expect(queue).toContain('Possible overdue report');
    expect(queue).toContain('id="chronicle-overdue-queue"');

    expect(chronicle).toContain('cross-day-thread');
    expect(chronicle).toContain('intra-day-thread');
    expect(chronicle).not.toContain('Weekly Brief');
    expect(chronicle).not.toContain('Conflict');

    const chronicleCall = weekReview.match(
      /<FourPartWeeklyCard[\s\S]*?<\/FourPartWeeklyCard>/,
    )?.[0] ?? '';
    expect(chronicleCall).toContain('payload={weeklyJson}');
    expect(chronicleCall).toContain('<MemoryApprovalActionQueue');

    const approvalIdx = weekReview.indexOf('id="memory-approval-section"');
    const queueIdx = weekReview.indexOf('<MemoryApprovalActionQueue');
    const saveIdx = weekReview.indexOf('<span>Memory Approval Save / Recall</span>');
    expect(queueIdx).toBeGreaterThan(-1);
    expect(queueIdx).toBeLessThan(approvalIdx);
    expect(saveIdx).toBeGreaterThan(approvalIdx);
  });
});
