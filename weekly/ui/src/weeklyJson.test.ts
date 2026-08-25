import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { formatSummaryLine, invalidatesBadgeSeq } from './weeklyJson.ts';

const root = dirname(fileURLToPath(import.meta.url));

describe('weekly JSON cite helpers', () => {
  it('badges former step when invalidates omits to_seq', () => {
    expect(
      invalidatesBadgeSeq({
        seq: 2,
        date: '2026-08-13',
        event_id: 'mem-b',
        text: 'later',
        via: 'invalidates',
      }),
    ).toBe(1);
  });

  it('badges seq 1 only when seq 4 invalidates with to_seq 1', () => {
    expect(
      invalidatesBadgeSeq({
        seq: 4,
        date: '2026-08-14',
        event_id: 'mem-d',
        text: 'retract',
        via: 'invalidates',
        to_seq: 1,
      }),
    ).toBe(1);
  });

  it('does not badge evolves', () => {
    expect(
      invalidatesBadgeSeq({
        seq: 2,
        date: '2026-08-13',
        event_id: 'mem-b',
        text: 'continue',
        via: 'evolves',
      }),
    ).toBeNull();
  });
});

describe('Chronicle source wiring', () => {
  it('formats summary weekdays in parentheses', () => {
    expect(
      formatSummaryLine({
        text: 'weekly ui has been updated to second version discarding legend and jump to',
        weekdays: ['Monday', 'Tuesday'],
      }),
    ).toBe(
      '- weekly ui has been updated to second version discarding legend and jump to (Monday, Tuesday)',
    );
  });

  it('FourPartWeeklyCard reads JSON summary, not Distill/Brief sections', () => {
    const card = readFileSync(join(root, 'components/FourPartWeeklyCard.tsx'), 'utf8');
    expect(card).toContain('payload.summary');
    expect(card).toContain('weekly-chronicle-summary');
    expect(card).toContain('formatSummaryLine');
    expect(card).toContain('No weekly summary yet');
    expect(card).not.toContain('- None.');
    expect(card).not.toContain('Cross-day-thread');
    expect(card).not.toContain('Intra-day-thread');
    expect(card).not.toContain('parseFourPartBrief');
    expect(card).not.toContain('Weekly Brief');
    expect(card).not.toContain('citeNForEventId');
    expect(card).not.toContain('onJumpApprovalCite');
  });

  it('queue is overdue-only with no Hypothesis Confirm/Delete', () => {
    const queue = readFileSync(
      join(root, 'components/MemoryApprovalActionQueue.tsx'),
      'utf8',
    );
    expect(queue).toContain('Possible overdue report');
    expect(queue).toContain('chronicle-overdue-queue');
    expect(queue).not.toContain('Hypothesis');
    expect(queue).not.toContain('onHypConfirm');
  });
});
