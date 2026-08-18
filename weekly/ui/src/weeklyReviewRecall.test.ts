/**
 * Must set HERMES_HOME before importing weeklyReviewRecall (serverHelpers caches paths at load).
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'weekly-review-recall-'));
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;

const dailyDir = path.join(hermesHome, 'memories', 'staging', 'daily');
const weeklyDir = path.join(hermesHome, 'memories', 'staging', 'weekly');
fs.mkdirSync(dailyDir, { recursive: true });
fs.mkdirSync(weeklyDir, { recursive: true });
fs.mkdirSync(path.join(hermesHome, 'plugins', 'MyMemory', 'weekly'), { recursive: true });

const {
  applyWeeklyReviewRecall,
  applyWeeklyReviewSave,
  weeklyReviewRecallAvailable,
  WEEKLY_REVIEW_RECALL_FILE,
  WEEKLY_REVIEW_RECALL_MAX,
} = await import('./weeklyReviewRecall.ts');

const WEEK = '2026-W31';

function weeklyFixture(hypBullet: string, hypId: string, cite: number): string {
  return `# Weekly distill ${WEEK}

## Distill

---
id: ${hypId}
type: hypothesis
confidence: medium
status: candidate
sources: [session test]
related: [mem-2026-07-27-kickoff]
---
${hypBullet}

## Brief

Weekly Brief — ${WEEK}

Monday — 2026-07-27 · Events [1]
Kickoff. [1]

Hypothesis
- ${hypBullet} [${cite}]

Possible overdue report
- None.

Cite map
- [1] event mem-2026-07-27-kickoff
- [${cite}] hypothesis ${hypId}
`;
}

function writeWeekly(content: string): string {
  const p = path.join(weeklyDir, `${WEEK}.md`);
  fs.writeFileSync(p, content);
  return p;
}

function writeDaily(fileName: string, content: string): void {
  fs.writeFileSync(path.join(dailyDir, fileName), content);
}

function recallStorePath(): string {
  return path.join(
    hermesHome,
    'memories',
    'staging',
    '.memory-3-step-recall',
    WEEKLY_REVIEW_RECALL_FILE,
  );
}

afterAll(() => {
  if (original === undefined) delete process.env.HERMES_HOME;
  else process.env.HERMES_HOME = original;
  fs.rmSync(hermesHome, { recursive: true, force: true });
});

describe('weeklyReviewRecall', () => {
  it('hyp_confirm writes fact + strips weekly; recall restores', () => {
    const hypId = 'hyp-parallel-latency';
    const hypText = 'Workers may reduce latency without changing cites';
    writeWeekly(weeklyFixture(hypText, hypId, 7));

    const saved = applyWeeklyReviewSave([
      {
        kind: 'hyp_confirm',
        weekKey: WEEK,
        hypothesisId: hypId,
        text: hypText,
        cite: 7,
        related: ['mem-2026-07-27-kickoff'],
        factId: 'fact-from-hyp-parallel-latency',
      },
    ]);
    expect(saved).toEqual({ ok: true, count: 1 });
    expect(weeklyReviewRecallAvailable()).toBe(true);
    expect(fs.existsSync(recallStorePath())).toBe(true);

    const daily = fs.readFileSync(path.join(dailyDir, '2026-07-27.md'), 'utf8');
    expect(daily).toMatch(/id: fact-from-hyp-parallel-latency/);
    expect(daily).toMatch(/type: fact/);
    expect(daily).toMatch(/Workers may reduce latency without changing cites/);

    const weekly = fs.readFileSync(path.join(weeklyDir, `${WEEK}.md`), 'utf8');
    expect(weekly).not.toMatch(/id: hyp-parallel-latency/);
    expect(weekly).not.toMatch(/Workers may reduce latency without changing cites/);
    expect(weekly).toMatch(/Hypothesis\n- None\./);

    const recalled = applyWeeklyReviewRecall();
    expect(recalled).toEqual({ ok: true, count: 1 });
    expect(weeklyReviewRecallAvailable()).toBe(false);

    const restoredWeekly = fs.readFileSync(path.join(weeklyDir, `${WEEK}.md`), 'utf8');
    expect(restoredWeekly).toMatch(/id: hyp-parallel-latency/);
    expect(restoredWeekly).toMatch(/Workers may reduce latency without changing cites/);

    const dailyAfter = fs.existsSync(path.join(dailyDir, '2026-07-27.md'))
      ? fs.readFileSync(path.join(dailyDir, '2026-07-27.md'), 'utf8')
      : '';
    expect(dailyAfter).not.toMatch(/fact-from-hyp-parallel-latency/);
  });

  it('hyp_delete strips weekly only; recall restores', () => {
    const hypId = 'hyp-delete-me';
    const hypText = 'Delete this hypothesis only';
    writeWeekly(weeklyFixture(hypText, hypId, 9));
    writeDaily(
      '2026-07-28.md',
      `---
id: mem-keep
type: fact
confidence: high
status: candidate
sources: [session test]
---
keep me
`,
    );

    const saved = applyWeeklyReviewSave([
      {
        kind: 'hyp_delete',
        weekKey: WEEK,
        hypothesisId: hypId,
        text: hypText,
        cite: 9,
      },
    ]);
    expect(saved).toEqual({ ok: true, count: 1 });

    const weekly = fs.readFileSync(path.join(weeklyDir, `${WEEK}.md`), 'utf8');
    expect(weekly).not.toMatch(/id: hyp-delete-me/);
    expect(weekly).not.toMatch(/Delete this hypothesis only/);
    expect(weekly).toMatch(/## Brief/);
    expect(weekly).toMatch(/Hypothesis\n- None\./);
    expect(fs.readFileSync(path.join(dailyDir, '2026-07-28.md'), 'utf8')).toMatch(/keep me/);

    const recalled = applyWeeklyReviewRecall();
    expect(recalled).toEqual({ ok: true, count: 1 });
    const restored = fs.readFileSync(path.join(weeklyDir, `${WEEK}.md`), 'utf8');
    expect(restored).toMatch(/id: hyp-delete-me/);
    expect(restored).toMatch(/Delete this hypothesis only/);
  });

  it('each span action applies valid_to; recall restores prior', () => {
    writeDaily(
      '2026-07-29.md',
      `---
id: span-a
type: fact
confidence: high
status: candidate
valid_from: 2026-07-01
valid_to: open
sources: [session test]
---
span a
`,
    );
    writeDaily(
      '2026-07-30.md',
      `---
id: span-b
type: fact
confidence: high
status: candidate
valid_from: 2026-07-01
valid_to: 2026-08-01
sources: [session test]
---
span b
`,
    );
    writeDaily(
      '2026-07-31.md',
      `---
id: span-c
type: fact
confidence: high
status: candidate
valid_from: 2026-07-01
valid_to: 2026-08-10
sources: [session test]
---
span c
`,
    );

    const saved = applyWeeklyReviewSave([
      {
        kind: 'span_confirm',
        weekKey: WEEK,
        blockId: 'span-a',
        proposed_valid_to: '2026-08-02',
      },
      {
        kind: 'span_put_off',
        weekKey: WEEK,
        blockId: 'span-b',
        interval: '7d',
      },
      {
        kind: 'span_set_due_date',
        weekKey: WEEK,
        blockId: 'span-c',
        due_date: '2026-09-15',
      },
    ]);
    expect(saved).toEqual({ ok: true, count: 3 });

    expect(fs.readFileSync(path.join(dailyDir, '2026-07-29.md'), 'utf8')).toMatch(
      /valid_to: 2026-08-02/,
    );
    expect(fs.readFileSync(path.join(dailyDir, '2026-07-30.md'), 'utf8')).toMatch(
      /valid_to: 2026-08-08/,
    );
    expect(fs.readFileSync(path.join(dailyDir, '2026-07-31.md'), 'utf8')).toMatch(
      /valid_to: 2026-09-15/,
    );

    const recalled = applyWeeklyReviewRecall();
    expect(recalled).toEqual({ ok: true, count: 3 });

    expect(fs.readFileSync(path.join(dailyDir, '2026-07-29.md'), 'utf8')).toMatch(
      /valid_to: open/,
    );
    expect(fs.readFileSync(path.join(dailyDir, '2026-07-30.md'), 'utf8')).toMatch(
      /valid_to: 2026-08-01/,
    );
    expect(fs.readFileSync(path.join(dailyDir, '2026-07-31.md'), 'utf8')).toMatch(
      /valid_to: 2026-08-10/,
    );
  });

  it('prunes to max 3 batches and errors on empty recall', () => {
    writeDaily(
      '2026-08-01.md',
      `---
id: span-prune
type: fact
confidence: high
status: candidate
valid_to: 2026-08-01
sources: [session test]
---
prune
`,
    );

    for (let i = 0; i < WEEKLY_REVIEW_RECALL_MAX + 1; i++) {
      const due = `2026-08-${String(2 + i).padStart(2, '0')}`;
      const saved = applyWeeklyReviewSave([
        {
          kind: 'span_set_due_date',
          weekKey: WEEK,
          blockId: 'span-prune',
          due_date: due,
        },
      ]);
      expect(saved.ok).toBe(true);
    }

    const store = JSON.parse(fs.readFileSync(recallStorePath(), 'utf8')) as {
      batches: unknown[];
    };
    expect(store.batches).toHaveLength(WEEKLY_REVIEW_RECALL_MAX);

    // Pop all remaining batches
    for (let i = 0; i < WEEKLY_REVIEW_RECALL_MAX; i++) {
      expect(applyWeeklyReviewRecall().ok).toBe(true);
    }
    expect(weeklyReviewRecallAvailable()).toBe(false);
    const empty = applyWeeklyReviewRecall();
    expect(empty.ok).toBe(false);
    if ('error' in empty) {
      expect(empty.error).toMatch(/Nothing to recall/i);
    }
  });
});
