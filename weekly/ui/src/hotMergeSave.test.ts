import assert from 'node:assert/strict';
import {
  applyPendingMergeState,
  entriesAfterPendingRemovals,
  mergeIntentId,
  mergeSaveFileOrder,
  overlayEntriesWithPendingMerges,
  recallPatchesFromMergeIntents,
  resolveMergePeers,
  undoMergeIntentState,
  type MergeIntent,
} from './components/HotMemoryEditor.tsx';

assert.deepEqual(
  entriesAfterPendingRemovals(
    ['zero', 'one', 'two', 'three'],
    new Set([1, 3]),
  ),
  ['zero', 'two'],
);

assert.deepEqual(
  mergeSaveFileOrder(
    'USER.md',
    new Set(['MEMORY.md', 'HERMES.md']),
    new Set(['USER.md']),
  ),
  ['MEMORY.md', 'HERMES.md', 'USER.md'],
);

assert.deepEqual(
  mergeSaveFileOrder(
    'MEMORY.md',
    new Set(['MEMORY.md']),
    new Set(['USER.md']),
  ),
  ['MEMORY.md', 'USER.md'],
);

const pendingIntent: MergeIntent = {
  id: mergeIntentId({
    sourceFile: 'USER.md',
    sourceIndex: 0,
    peerFile: 'USER.md',
    peerIndex: 2,
    reason: 'overlap',
    actions: ['merge'],
  }),
  sourceFile: 'USER.md',
  sourceIndex: 0,
  peerFile: 'USER.md',
  peerIndex: 2,
  reason: 'overlap',
  actions: ['merge'],
  beforeSource: 'source-before',
  beforePeer: 'peer-before',
  beforePeers: [{ file: 'USER.md', index: 2, text: 'peer-before' }],
  mergedText: 'merged-result',
  status: 'pending',
};

assert.equal(
  pendingIntent.id,
  'USER.md:0->USER.md:2',
);

const overlaid = overlayEntriesWithPendingMerges(
  'USER.md',
  ['disk-0', 'disk-1', 'disk-2'],
  [pendingIntent],
);
assert.deepEqual(overlaid.entries, ['merged-result', 'disk-1', 'disk-2']);
assert.deepEqual([...overlaid.peerRemoves].sort(), [2]);
assert.equal(overlaid.pendingEdits.get(0), 'source-before');

const applied = applyPendingMergeState(
  { 'USER.md': ['merged-result', 'keep', 'peer-before'] },
  'USER.md',
  ['merged-result', 'keep', 'peer-before'],
  [pendingIntent],
);
assert.equal(applied.entriesByFile['USER.md']?.[0], 'merged-result');
assert.deepEqual([...(applied.pendingRemovesByFile['USER.md'] ?? [])].sort(), [2]);

assert.throws(
  () =>
    applyPendingMergeState(
      { 'USER.md': ['a', 'b'] },
      'USER.md',
      ['a', 'b'],
      [{ ...pendingIntent, status: 'merging' }],
    ),
  /Wait for merge/,
);

const patches = recallPatchesFromMergeIntents('USER.md', [pendingIntent]);
assert.equal(patches.editsByIndex.get(0), 'source-before');
assert.equal(patches.deleteTextByIndex.get(2), 'peer-before');

const undone = undoMergeIntentState(
  pendingIntent,
  { 'USER.md': ['merged-result', 'keep', 'peer-before'] },
  'USER.md',
  ['merged-result', 'keep', 'peer-before'],
  new Set([2]),
);
assert.deepEqual(undone.entries, ['source-before', 'keep', 'peer-before']);
assert.equal(undone.pendingRemove.has(2), false);
assert.equal(undone.entriesByFile['USER.md']?.[0], 'source-before');
assert.equal(undone.entriesByFile['USER.md']?.[2], 'peer-before');

const crossFile: MergeIntent = {
  ...pendingIntent,
  id: 'MEMORY.md:1->USER.md:0',
  sourceFile: 'MEMORY.md',
  sourceIndex: 1,
  peerFile: 'USER.md',
  peerIndex: 0,
  beforeSource: 'mem-before',
  beforePeer: 'user-peer-before',
  beforePeers: [{ file: 'USER.md', index: 0, text: 'user-peer-before' }],
  mergedText: 'mem-merged',
  status: 'pending',
};
const crossApplied = applyPendingMergeState(
  {
    'MEMORY.md': ['m0', 'mem-merged'],
    'USER.md': ['user-peer-before', 'u1'],
  },
  'MEMORY.md',
  ['m0', 'mem-merged'],
  [crossFile],
);
assert.equal(crossApplied.entriesByFile['MEMORY.md']?.[1], 'mem-merged');
assert.deepEqual([...(crossApplied.pendingRemovesByFile['USER.md'] ?? [])], [0]);

const crossPatchesMem = recallPatchesFromMergeIntents('MEMORY.md', [crossFile]);
assert.equal(crossPatchesMem.editsByIndex.get(1), 'mem-before');
assert.equal(crossPatchesMem.deleteTextByIndex.size, 0);
const crossPatchesUser = recallPatchesFromMergeIntents('USER.md', [crossFile]);
assert.equal(crossPatchesUser.deleteTextByIndex.get(0), 'user-peer-before');
assert.equal(crossPatchesUser.editsByIndex.size, 0);

const multiInput = {
  sourceFile: 'USER.md' as const,
  sourceIndex: 0,
  peerFile: 'USER.md' as const,
  peerIndex: 1,
  peers: [
    { file: 'USER.md' as const, index: 1 },
    { file: 'USER.md' as const, index: 2 },
    { file: 'USER.md' as const, index: 0 },
  ],
  reason: 'multi',
  actions: ['merge'],
};
assert.deepEqual(resolveMergePeers(multiInput), [
  { file: 'USER.md', index: 1 },
  { file: 'USER.md', index: 2 },
]);
assert.equal(mergeIntentId(multiInput), 'USER.md:0->USER.md:1+USER.md:2');

const multiIntent: MergeIntent = {
  ...multiInput,
  id: mergeIntentId(multiInput),
  beforeSource: 'p0',
  beforePeer: 'p1',
  beforePeers: [
    { file: 'USER.md', index: 1, text: 'p1' },
    { file: 'USER.md', index: 2, text: 'p2' },
  ],
  mergedText: 'merged-multi',
  status: 'pending',
};
const multiOverlay = overlayEntriesWithPendingMerges(
  'USER.md',
  ['p0', 'p1', 'p2'],
  [multiIntent],
);
assert.deepEqual(multiOverlay.entries, ['merged-multi', 'p1', 'p2']);
assert.deepEqual([...multiOverlay.peerRemoves].sort(), [1, 2]);

const multiApplied = applyPendingMergeState(
  { 'USER.md': ['merged-multi', 'p1', 'p2'] },
  'USER.md',
  ['merged-multi', 'p1', 'p2'],
  [multiIntent],
);
assert.deepEqual(
  [...(multiApplied.pendingRemovesByFile['USER.md'] ?? [])].sort(),
  [1, 2],
);

const multiPatches = recallPatchesFromMergeIntents('USER.md', [multiIntent]);
assert.equal(multiPatches.deleteTextByIndex.get(1), 'p1');
assert.equal(multiPatches.deleteTextByIndex.get(2), 'p2');

const multiUndone = undoMergeIntentState(
  multiIntent,
  { 'USER.md': ['merged-multi', 'gone1', 'gone2'] },
  'USER.md',
  ['merged-multi', 'gone1', 'gone2'],
  new Set([1, 2]),
);
assert.deepEqual(multiUndone.entries, ['p0', 'p1', 'p2']);
assert.equal(multiUndone.pendingRemove.has(1), false);
assert.equal(multiUndone.pendingRemove.has(2), false);

console.log('hotMergeSave.test.ts: all assertions passed');
