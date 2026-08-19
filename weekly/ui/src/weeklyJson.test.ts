import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { citeNForEventId, invalidatesBadgeSeq } from './weeklyJson.ts';

const root = dirname(fileURLToPath(import.meta.url));

describe('weekly JSON cite helpers', () => {
  const legend = {
    '1': 'mem-a',
    '4': 'mem-wed',
  };

  it('paints step [4] from legend so Chronicle jumps to Approval Hub [4]', () => {
    expect(citeNForEventId(legend, 'mem-wed')).toBe(4);
  });

  it('omits a button when the event id is missing from legend', () => {
    expect(citeNForEventId(legend, 'mem-unknown')).toBeNull();
  });

  it('rejects a denormalized cite_n that does not match legend', () => {
    expect(citeNForEventId(legend, 'mem-wed', 1)).toBe(4);
  });

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
  it('FourPartWeeklyCard reads JSON keys, not Distill/Brief sections', () => {
    const card = readFileSync(join(root, 'components/FourPartWeeklyCard.tsx'), 'utf8');
    expect(card).toContain('cross-day-thread');
    expect(card).toContain('intra-day-thread');
    expect(card).not.toContain('parseFourPartBrief');
    expect(card).not.toContain('Weekly Brief');
    expect(card).not.toContain('Conflict');
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
