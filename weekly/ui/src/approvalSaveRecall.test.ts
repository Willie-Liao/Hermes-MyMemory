/**
 * Integration tests for saveApprovalBatch / recallApprovalBatch / recallApprovalCard.
 * Must set HERMES_HOME before importing serverHelpers (module caches paths at load).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { RECALL_LIMIT_MESSAGE } from './approvalRecall.ts';
import { splitHotEntries } from './hotMemory.ts';

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'approval-save-'));
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;

const memories = path.join(hermesHome, 'memories');
const staging = path.join(memories, 'staging');
const daily = path.join(staging, 'daily');
fs.mkdirSync(daily, { recursive: true });
fs.mkdirSync(path.join(hermesHome, 'plugins', 'MyMemory', 'weekly'), { recursive: true });

fs.writeFileSync(
  path.join(memories, 'MEMORY.md'),
  `# Hermes Core Memory\n\n§ Existing memory entry (as of 2026-07-01)\n`,
);
fs.writeFileSync(
  path.join(memories, 'USER.md'),
  `# User Profile\n\n§ Existing user entry (as of 2026-07-01)\n`,
);
fs.writeFileSync(
  path.join(daily, '2026-07-11.md'),
  `---
id: mem-2026-07-11-short
type: fact
confidence: high
status: candidate
sources: ["session test"]
valid_from: 2026-07-11
valid_to: open
---
Keep replies short.

---
id: mem-2026-07-11-delete-me
type: fact
confidence: medium
status: candidate
sources: ["session test2"]
---
Temporary fact to delete.

---
id: mem-2026-07-11-keep-tail
type: fact
confidence: high
status: candidate
sources: ["session test3"]
---
Tail block stays last.
`,
);
const until = new Date(Date.now() + 60 * 60e3).toISOString();
fs.writeFileSync(
  path.join(staging, '.weekly-state.json'),
  JSON.stringify({
    presentation: {},
    completed_weeks: [],
    tidy_pending: [],
    instant_until: until,
    hot_promotion_allowed: true,
  }, null, 2),
);

const {
  saveApprovalBatch,
  recallApprovalBatch,
  recallApprovalCard,
  readAllMemoryBlocks,
  readGateState,
} = await import('./serverHelpers.ts');
const { readHotFile } = await import('./hotMemory.ts');
const { loadApprovalRecallStore } = await import('./approvalRecallStore.ts');

const weekKey = '2026-W28';

assert.equal(readGateState().hot_promotion_allowed, true);

const saveResult = saveApprovalBatch(weekKey, [
  {
    blockId: 'mem-2026-07-11-short',
    recordId: 'F1',
    action: 'memory',
    bulletText: 'Keep replies short.',
    validFrom: '2026-07-11',
    validTo: 'open',
  },
  {
    blockId: 'mem-2026-07-11-delete-me',
    recordId: 'F2',
    action: 'delete',
    bulletText: '',
  },
]);
assert.equal(saveResult.success, true);
assert.ok(saveResult.batch);
assert.equal(saveResult.batch!.operations.length, 2);

const memoryEntries = splitHotEntries('MEMORY.md', readHotFile('MEMORY.md').content).entries;
assert.ok(memoryEntries.some((e) => e.includes('Keep replies short.') && e.includes('valid_from: 2026-07-11')));

const blocksAfterSave = readAllMemoryBlocks();
assert.equal(blocksAfterSave.find((b) => b.id === 'mem-2026-07-11-short')?.status, 'approved');
assert.equal(blocksAfterSave.find((b) => b.id === 'mem-2026-07-11-delete-me'), undefined);
assert.equal(
  saveResult.batch!.operations.find((o) => o.action === 'delete')?.blockIndex,
  1,
);

const storeAfterSave = loadApprovalRecallStore(weekKey);
assert.equal(storeAfterSave.batches.length, 1);

const cardRecall = recallApprovalCard(weekKey, 'F2');
assert.equal(cardRecall.success, true);
assert.ok(readAllMemoryBlocks().find((b) => b.id === 'mem-2026-07-11-delete-me'));
// Restore into original slot (between short and keep-tail), not appended at end.
const order = [...fs.readFileSync(path.join(daily, '2026-07-11.md'), 'utf8')
  .matchAll(/^id:\s*(\S+)/gm)].map((m) => m[1]);
assert.deepEqual(order, [
  'mem-2026-07-11-short',
  'mem-2026-07-11-delete-me',
  'mem-2026-07-11-keep-tail',
]);
assert.equal(loadApprovalRecallStore(weekKey).batches[0].operations.length, 1);

const batchRecall = recallApprovalBatch(weekKey);
assert.equal(batchRecall.success, true);
const memoryAfterRecall = splitHotEntries('MEMORY.md', readHotFile('MEMORY.md').content).entries;
assert.equal(memoryAfterRecall.some((e) => e.includes('Keep replies short.')), false);
assert.equal(readAllMemoryBlocks().find((b) => b.id === 'mem-2026-07-11-short')?.status, 'candidate');
assert.equal(loadApprovalRecallStore(weekKey).batches.length, 0);

const emptyRecall = recallApprovalBatch(weekKey);
assert.equal(emptyRecall.success, false);
assert.equal(emptyRecall.error, RECALL_LIMIT_MESSAGE);

const lockedGate = new Date(Date.now() - 60e3).toISOString();
fs.writeFileSync(
  path.join(staging, '.weekly-state.json'),
  JSON.stringify({
    presentation: {},
    completed_weeks: [],
    tidy_pending: [],
    instant_until: lockedGate,
    hot_promotion_allowed: false,
  }, null, 2),
);
assert.equal(readGateState().hot_promotion_allowed, false);
const unlockedSave = saveApprovalBatch(weekKey, [
  {
    blockId: 'mem-2026-07-11-short',
    recordId: 'F1',
    action: 'memory',
    bulletText: 'Keep replies short again.',
  },
]);
assert.equal(unlockedSave.success, true, unlockedSave.error);
assert.equal(readGateState().hot_promotion_allowed, true);

const editSave = saveApprovalBatch(weekKey, [
  {
    blockId: 'mem-2026-07-11-keep-tail',
    recordId: 'E1',
    action: 'edit',
    bulletText: 'Tail block rewritten.',
    beforeBody: 'Tail block stays last.',
  },
]);
assert.equal(editSave.success, true, editSave.error);
assert.equal(editSave.batch!.operations[0].action, 'edit');
assert.equal(
  readAllMemoryBlocks().find((b) => b.id === 'mem-2026-07-11-keep-tail')?.body.trim(),
  'Tail block rewritten.',
);
const editRecall = recallApprovalBatch(weekKey);
assert.equal(editRecall.success, true);
assert.equal(
  readAllMemoryBlocks().find((b) => b.id === 'mem-2026-07-11-keep-tail')?.body.trim(),
  'Tail block stays last.',
);

if (original === undefined) {
  delete process.env.HERMES_HOME;
} else {
  process.env.HERMES_HOME = original;
}
fs.rmSync(hermesHome, { recursive: true, force: true });

console.log('approvalSaveRecall.test.ts: all assertions passed');
