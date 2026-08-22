import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  RECALL_DIR_NAME,
  RECALL_MAX_BATCHES,
  applyRecallBatch,
  hotRecallSibling,
  popLinkedBatch,
  pruneExpiredBatches,
  pushRecallBatch,
  resolveRecallPath,
} from './hotRecall.ts';
import { loadRecallStore, saveRecallStore } from './hotRecallStore.ts';

const old = {
  savedAt: new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(),
  deletes: [{ index: 0, text: 'gone' }],
  edits: [],
};
const fresh = {
  savedAt: new Date().toISOString(),
  deletes: [{ index: 1, text: 'recent' }],
  edits: [{ index: 0, before: 'old' }],
};
assert.deepEqual(pruneExpiredBatches([old, fresh]).map((b) => b.deletes[0]?.text), ['recent']);

let stack = [];
for (let i = 0; i < 4; i++) {
  stack = pushRecallBatch(stack, {
    savedAt: new Date().toISOString(),
    deletes: [{ index: i, text: `d${i}` }],
    edits: [],
  });
}
assert.equal(stack.length, RECALL_MAX_BATCHES);
assert.equal(stack[0].deletes[0].text, 'd1'); // oldest dropped

const restored = applyRecallBatch(['a', 'c'], {
  savedAt: new Date().toISOString(),
  edits: [{ index: 0, before: 'A' }],
  deletes: [{ index: 1, text: 'b' }],
});
assert.deepEqual(restored, ['A', 'b', 'c']);

// Undo append (USER side of a move) then restore delete (MEMORY side).
const afterMove = applyRecallBatch(['keep', 'moved-in'], {
  savedAt: new Date().toISOString(),
  edits: [],
  deletes: [],
  appends: [{ index: 1, text: 'moved-in' }],
});
assert.deepEqual(afterMove, ['keep']);

const memoryRestored = applyRecallBatch(['other'], {
  savedAt: new Date().toISOString(),
  edits: [],
  deletes: [{ index: 0, text: 'moved-out' }],
  linkId: 'link-1',
});
assert.deepEqual(memoryRestored, ['moved-out', 'other']);

assert.equal(hotRecallSibling('MEMORY.md'), 'USER.md');
assert.equal(hotRecallSibling('USER.md'), 'MEMORY.md');
const linked = popLinkedBatch(
  [
    { savedAt: 'a', deletes: [], edits: [], linkId: 'x' },
    { savedAt: 'b', deletes: [], edits: [], linkId: 'y' },
  ],
  'y',
);
assert.equal(linked.batch?.linkId, 'y');
assert.equal(linked.remaining.length, 1);
assert.equal(linked.remaining[0]?.linkId, 'x');

const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'hot-recall-'));
const staging = path.join(hermesHome, 'memories', 'staging');
fs.mkdirSync(staging, { recursive: true });
const recallPath = path.join(staging, RECALL_DIR_NAME, 'memory.json');
assert.equal(
  resolveRecallPath(hermesHome, 'MEMORY.md'),
  `${hermesHome}/memories/staging/${RECALL_DIR_NAME}/memory.json`,
);
const original = process.env.HERMES_HOME;
process.env.HERMES_HOME = hermesHome;
try {
  fs.mkdirSync(path.dirname(recallPath), { recursive: true });
  fs.writeFileSync(recallPath, JSON.stringify({
    file: 'MEMORY.md',
    ui: {
      batches: [
        { savedAt: new Date(Date.now() - 25 * 3600e3).toISOString(), deletes: [{ index: 0, text: 'old' }], edits: [] },
        { savedAt: new Date().toISOString(), deletes: [{ index: 0, text: 'new' }], edits: [] },
      ],
    },
    chat: {
      batches: [
        { savedAt: new Date().toISOString(), deletes: [{ index: 9, text: 'legacy-chat' }], edits: [] },
      ],
    },
  }));
  const loaded = loadRecallStore('MEMORY.md');
  assert.equal(loaded.batches.length, 1);
  assert.equal(loaded.batches[0].deletes[0].text, 'new');
  const onDisk = JSON.parse(fs.readFileSync(recallPath, 'utf8'));
  assert.equal(onDisk.ui.batches.length, 1);
  assert.equal(onDisk.ui.batches[0].deletes[0].text, 'new');
  assert.equal('chat' in onDisk, false);

  saveRecallStore({ file: 'MEMORY.md', batches: [] });
  assert.equal(fs.existsSync(recallPath), false);
} finally {
  if (original === undefined) {
    delete process.env.HERMES_HOME;
  } else {
    process.env.HERMES_HOME = original;
  }
  fs.rmSync(hermesHome, { recursive: true, force: true });
}

console.log('hotRecall.test.ts: all assertions passed');
