/**
 * Must set HERMES_HOME before importing stagingUiRecall (serverHelpers caches paths at load).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, describe, it } from 'node:test';
import type { MemoryBlock } from './types.ts';

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'staging-ui-recall-'));
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;

const dailyDir = path.join(hermesHome, 'memories', 'staging', 'daily');
fs.mkdirSync(dailyDir, { recursive: true });

const {
  applyStagingUiRecall,
  applyStagingUiSave,
  stagingUiRecallAvailable,
  STAGING_UI_RECALL_FILE,
} = await import('./stagingUiRecall.ts');

function block(partial: Partial<MemoryBlock> & Pick<MemoryBlock, 'id' | 'body' | 'filePath'>): MemoryBlock {
  return {
    type: 'fact',
    confidence: 'high',
    importance: 3,
    status: 'candidate',
    sources: ['session test'],
    ...partial,
  };
}

function writeDaily(fileName: string, content: string): void {
  fs.writeFileSync(path.join(dailyDir, fileName), content);
}

after(() => {
  if (original === undefined) delete process.env.HERMES_HOME;
  else process.env.HERMES_HOME = original;
  fs.rmSync(hermesHome, { recursive: true, force: true });
});

describe('stagingUiRecall', { concurrency: false }, () => {
  it('saves edit then recalls previous body', () => {
    writeDaily(
      '2026-07-11.md',
      `---
id: b1
type: fact
confidence: high
status: candidate
sources: [session test]
---
hello
`,
    );
    const before = block({ id: 'b1', body: 'hello', filePath: '2026-07-11.md' });
    const after = { ...before, body: 'hello world' };
    const saved = applyStagingUiSave([{ kind: 'edit', before, after }]);
    assert.equal(saved.ok, true);
    assert.equal(stagingUiRecallAvailable(), true);
    assert.ok(fs.existsSync(path.join(
      hermesHome,
      'memories',
      'staging',
      '.memory-3-step-recall',
      STAGING_UI_RECALL_FILE,
    )));

    const daily = fs.readFileSync(path.join(dailyDir, '2026-07-11.md'), 'utf8');
    assert.match(daily, /hello world/);

    const recalled = applyStagingUiRecall();
    assert.equal(recalled.ok, true);
    const restored = fs.readFileSync(path.join(dailyDir, '2026-07-11.md'), 'utf8');
    assert.match(restored, /^hello$/m);
    assert.equal(stagingUiRecallAvailable(), false);
  });

  it('saves delete then recalls restored block', () => {
    writeDaily(
      '2026-07-12.md',
      `---
id: b2
type: fact
confidence: high
status: candidate
sources: [session test]
---
keep me
`,
    );
    const before = block({ id: 'b2', body: 'keep me', filePath: '2026-07-12.md' });
    const saved = applyStagingUiSave([{ kind: 'delete', before }]);
    assert.equal(saved.ok, true);
    const afterPath = path.join(dailyDir, '2026-07-12.md');
    if (fs.existsSync(afterPath)) {
      const afterDelete = fs.readFileSync(afterPath, 'utf8');
      assert.doesNotMatch(afterDelete, /keep me/);
    }

    const recalled = applyStagingUiRecall();
    assert.equal(recalled.ok, true);
    const restored = fs.readFileSync(afterPath, 'utf8');
    assert.match(restored, /keep me/);
    assert.ok(!fs.existsSync(path.join(
      hermesHome,
      'memories',
      'staging',
      '.memory-3-step-recall',
      STAGING_UI_RECALL_FILE,
    )));
    assert.ok(!fs.existsSync(path.join(hermesHome, 'memories', 'staging', STAGING_UI_RECALL_FILE)));
  });
});
