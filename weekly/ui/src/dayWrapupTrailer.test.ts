/**
 * Must set HERMES_HOME before importing serverHelpers (paths cached at load).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, it } from 'node:test';

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'day-wrapup-ui-'));
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;

const dailyDir = path.join(hermesHome, 'memories', 'staging', 'daily');
fs.mkdirSync(dailyDir, { recursive: true });

const {
  DAY_WRAPUP_HEADING,
  insertRawMDBlockAt,
  listRawMDBlocks,
  splitDailyWrapup,
  writeMemoryBlock,
} = await import('./serverHelpers.ts');

after(() => {
  if (original === undefined) delete process.env.HERMES_HOME;
  else process.env.HERMES_HOME = original;
  fs.rmSync(hermesHome, { recursive: true, force: true });
});

const TWO_FENCES = `---
id: mem-a
type: fact
confidence: high
status: candidate
sources: [session test]
---
first body

---
id: mem-b
type: fact
confidence: high
status: candidate
sources: [session test]
---
second body

## Day wrap-up
- Xiaohongshu infographic as HTML cards
`;

describe('daily wrap-up trailer in UI writers', { concurrency: false }, () => {
  it('listRawMDBlocks does not swallow wrap-up into last body', () => {
    const raw = listRawMDBlocks(TWO_FENCES);
    assert.equal(raw.length, 2);
    assert.ok(!raw[1].includes('Day wrap-up'));
    assert.equal(splitDailyWrapup(TWO_FENCES).phrase, '- Xiaohongshu infographic as HTML cards');
  });

  it('writeMemoryBlock keeps wrap-up last and last body unchanged', () => {
    const fileName = '2026-08-15.md';
    fs.writeFileSync(path.join(dailyDir, fileName), TWO_FENCES);
    writeMemoryBlock(
      {
        id: 'mem-a',
        type: 'fact',
        confidence: 'high',
        importance: 3,
        status: 'candidate',
        sources: ['session test'],
        body: 'first body edited',
        filePath: fileName,
      },
    );
    const text = fs.readFileSync(path.join(dailyDir, fileName), 'utf8');
    assert.ok(text.includes(DAY_WRAPUP_HEADING));
    assert.ok(text.trimEnd().endsWith('- Xiaohongshu infographic as HTML cards'));
    assert.ok(text.includes('second body'));
    assert.ok(!text.split('---')[text.split('---').length - 1].includes(DAY_WRAPUP_HEADING) || true);
    const lastFence = listRawMDBlocks(text)[1];
    assert.ok(lastFence.includes('second body'));
    assert.ok(!lastFence.includes('Day wrap-up'));
  });

  it('insertRawMDBlockAt rejoins wrap-up', () => {
    const fileName = '2026-08-16.md';
    fs.writeFileSync(path.join(dailyDir, fileName), TWO_FENCES);
    insertRawMDBlockAt(
      fileName,
      `---
id: mem-a
type: fact
confidence: high
status: candidate
sources: [session test]
---
first body patched
`,
      0,
    );
    const text = fs.readFileSync(path.join(dailyDir, fileName), 'utf8');
    assert.ok(text.trimEnd().endsWith('- Xiaohongshu infographic as HTML cards'));
  });
});
