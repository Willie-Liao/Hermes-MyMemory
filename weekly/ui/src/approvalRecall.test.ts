import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  formatMemoryBullet,
  removeHotEntryAt,
  revertOperation,
  removeOperationFromBatch,
  resolveApprovalRecallPath,
  type ApprovalBatch,
} from './approvalRecall.ts';
import {
  loadApprovalRecallStore,
  saveApprovalRecallStore,
} from './approvalRecallStore.ts';

assert.equal(
  formatMemoryBullet('Keep replies short.', '2026-07-11', 'open'),
  'Keep replies short.\nvalid_from: 2026-07-11\nvalid_to: open',
);

const entries = ['a', 'b', 'c'];
assert.deepEqual(removeHotEntryAt(entries, 1, 'b'), ['a', 'c']);

const op = {
  blockId: 'mem-x',
  recordId: 'r1',
  action: 'memory' as const,
  hotFile: 'MEMORY.md' as const,
  hotIndex: 2,
  hotText: 'bullet\nvalid_from: 2026-07-11\nvalid_to: open',
  blockStatusBefore: 'candidate',
};
// Re-inserts hotText at hotIndex (plan snippet had input/expected swapped).
assert.deepEqual(revertOperation(['a', 'b'], op), ['a', 'b', op.hotText!]);

const batch: ApprovalBatch = {
  savedAt: new Date().toISOString(),
  operations: [op, { ...op, recordId: 'r2', blockId: 'mem-y' }],
};
const trimmed = removeOperationFromBatch(batch, 'r1');
assert.ok(trimmed);
assert.equal(trimmed!.operations.length, 1);
assert.equal(trimmed!.operations[0].recordId, 'r2');

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'approval-recall-'));
const staging = path.join(hermesHome, 'memories', 'staging');
fs.mkdirSync(staging, { recursive: true });
const weekKey = '2026-W28';
const recallPath = resolveApprovalRecallPath(hermesHome, weekKey);
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;
try {
  fs.writeFileSync(recallPath, JSON.stringify({
    week: weekKey,
    batches: [
      {
        savedAt: new Date(Date.now() - 25 * 3600e3).toISOString(),
        operations: [{ ...op, recordId: 'old' }],
      },
      {
        savedAt: new Date().toISOString(),
        operations: [{ ...op, recordId: 'new' }],
      },
    ],
  }));
  const loaded = loadApprovalRecallStore(weekKey);
  assert.equal(loaded.week, weekKey);
  assert.equal(loaded.batches.length, 1);
  assert.equal(loaded.batches[0].operations[0].recordId, 'new');
  const onDisk = JSON.parse(fs.readFileSync(recallPath, 'utf8'));
  assert.equal(onDisk.batches.length, 1);

  saveApprovalRecallStore({ week: weekKey, batches: [] });
  assert.equal(fs.existsSync(recallPath), false);
} finally {
  if (original === undefined) {
    delete process.env.HERMES_HOME;
  } else {
    process.env.HERMES_HOME = original;
  }
  fs.rmSync(hermesHome, { recursive: true, force: true });
}

console.log('approvalRecall.test.ts: all assertions passed');
