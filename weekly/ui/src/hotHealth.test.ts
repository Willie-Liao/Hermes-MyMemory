import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  buildMergePrompt,
  buildTightenBridgeArgs,
  canRunTightenGuidance,
  cardTintClass,
  clearAnnotationsForFiles,
  countAnnotationKinds,
  DEFAULT_TIGHTEN_GUIDANCE,
  entryPreviewWords,
  extractPeerRefs,
  flattenPeerGroups,
  loadHotHealthSidecar,
  mergeAnnotations,
  primaryAnnotationKind,
  resolvePeerRef,
  resolveTightenGuidance,
} from './hotHealth.ts';

const entries = [
  'old (valid_to: 2020-01-01)',
  'fresh (valid_to: 2099-01-01)',
];
const merged = mergeAnnotations(
  'MEMORY.md',
  entries,
  { 'MEMORY.md': [] },
  new Date('2026-07-12'),
);

assert.ok(merged.find((annotation) => annotation.index === 0)?.kinds.includes('outdated'));
assert.ok(!merged.find((annotation) => annotation.index === 1)?.kinds.includes('outdated'));
assert.deepEqual(countAnnotationKinds({
  'MEMORY.md': [
    { index: 0, kinds: ['outdated'], actions: ['purge'] },
    { index: 1, kinds: ['outdated', 'move_to_user'], actions: ['extend'] },
  ],
  'USER.md': [
    { index: 0, kinds: ['merge', 'rephrase'], actions: ['merge'] },
    { index: 1, kinds: ['purge'], actions: ['purge'] },
  ],
  'HERMES.md': [
    { index: 0, kinds: ['rephrase'], actions: ['rephrase'] },
    { index: 1, kinds: ['merge', 'outdated'], actions: ['merge'] },
  ],
}), {
  memoryOutdated: 2,
  userMerge: 1,
  userRephrase: 1,
  userPurge: 1,
  userMove: 1,
  hermesOutdated: 1,
  hermesMerge: 1,
  hermesRephrase: 1,
  hermesPurge: 0,
});

assert.equal(primaryAnnotationKind(['merge', 'outdated']), 'outdated');
assert.equal(primaryAnnotationKind(['merge', 'rephrase']), 'merge');
assert.equal(primaryAnnotationKind([]), null);

assert.equal(
  cardTintClass('outdated', true),
  'bg-amber-500/8',
);
assert.equal(
  cardTintClass('merge', false),
  'bg-slate-950/60',
);

assert.equal(
  resolvePeerRef(
    'USER.md',
    2,
    'MEMORY.md [2] is redundant — see USER.md [3].',
  ).file,
  'MEMORY.md',
);
assert.equal(
  resolvePeerRef('USER.md', 2, 'overlap with entry [2]').file,
  'USER.md',
);

assert.deepEqual(
  extractPeerRefs(
    'MEMORY.md',
    2,
    undefined,
    "Already covered by USER.md [3] decision + write-protocol",
    ['Merge into USER.md [3] or delete from MEMORY'],
  ),
  [{ file: 'USER.md', index: 3 }],
);
assert.deepEqual(
  extractPeerRefs('USER.md', 3, [2], 'MEMORY.md [2] duplicates this rule'),
  [{ file: 'MEMORY.md', index: 2 }],
);
assert.deepEqual(
  extractPeerRefs('MEMORY.md', 0, undefined, 'Long; tighten phrasing.', ['Drop SOUL L5 frame']),
  [],
);
assert.deepEqual(
  extractPeerRefs(
    'USER.md',
    0,
    undefined,
    'Overlap with HERMES.md [1] session rules',
  ),
  [{ file: 'HERMES.md', index: 1 }],
);

assert.deepEqual(
  flattenPeerGroups([[1, 2], [2, 4], [0]], 0),
  [1, 2, 4],
);
assert.deepEqual(flattenPeerGroups(undefined, undefined, [3, 1]), [3, 1]);
assert.deepEqual(
  extractPeerRefs('USER.md', 0, [9], '', [], [[1, 2], [4]]),
  [
    { file: 'USER.md', index: 1 },
    { file: 'USER.md', index: 2 },
    { file: 'USER.md', index: 4 },
  ],
);

assert.equal(
  entryPreviewWords('Alex teaches IB MYP PE all grades since 2022'),
  'Alex teaches IB MYP PE all…',
);

const prompt = buildMergePrompt({
  sourceRef: 'USER.md [3]',
  peerRef: 'MEMORY.md [2]',
  sourceText: 'write protocol rules',
  peerText: 'pointer to USER write rules',
  reason: 'duplicate pointer',
  actions: ['remove MEMORY [2]'],
});
assert.ok(prompt.includes('WHY MERGE'));
assert.ok(prompt.includes('duplicate pointer'));
assert.ok(prompt.includes('remove MEMORY [2]'));
assert.equal(prompt.includes('HERMES.md FORMAT'), false);

const multiPrompt = buildMergePrompt({
  sourceRef: 'USER.md [0]',
  sourceText: 'Parent',
  reason: 'scattered',
  actions: ['absorb'],
  peerEntries: [
    { ref: 'USER.md [1]', text: 'Peer one' },
    { ref: 'USER.md [2]', text: 'Peer two' },
  ],
});
assert.ok(multiPrompt.includes('PEER 1 — USER.md [1]:'));
assert.ok(multiPrompt.includes('Peer one'));
assert.ok(multiPrompt.includes('PEER 2 — USER.md [2]:'));
assert.ok(multiPrompt.includes('Peer two'));

const hermesPrompt = buildMergePrompt({
  sourceRef: 'HERMES.md [3]',
  peerRef: 'HERMES.md [4]',
  sourceText: '## Path Discipline\n\nKeep hot writes gated.',
  peerText: '## Hook Authority\n\nShell hook is the write-gate.',
  reason: 'overlap',
  actions: ['nest peer'],
});
assert.ok(hermesPrompt.includes('HERMES.md FORMAT'));
assert.ok(hermesPrompt.includes('ONE top-level ## heading'));
assert.ok(hermesPrompt.includes('Nest peer content under the parent using ###'));
assert.ok(hermesPrompt.includes('Never emit a second ## heading'));
assert.ok(hermesPrompt.includes('Regenerate a clean heading hierarchy'));

{
  const r = buildTightenBridgeArgs({ text: 'x', guidance: '  ' });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.deepEqual(r.args, {
      mode: 'tighten',
      text: 'x',
      guidance: DEFAULT_TIGHTEN_GUIDANCE,
    });
  }
}

{
  const r = buildTightenBridgeArgs({ text: 'long', guidance: 'half' });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.deepEqual(r.args, {
      mode: 'tighten',
      text: 'long',
      guidance: 'half',
    });
  }
}

{
  const r = buildTightenBridgeArgs({
    text: 'Beginning: a; Course: b; Outcome: c',
    guidance: 'polish',
    entryType: 'event',
  });
  assert.equal(r.ok, true);
  if (r.ok) {
    assert.deepEqual(r.args, {
      mode: 'tighten',
      text: 'Beginning: a; Course: b; Outcome: c',
      guidance: 'polish',
      entry_type: 'event',
    });
  }
}

{
  const r = buildTightenBridgeArgs({
    mode: 'merge',
    sourceText: 'A',
    peerText: 'B',
    reason: 'overlap',
    actions: ['keep dates'],
    sourceRef: 'USER.md [0]',
    peerRef: 'MEMORY.md [1]',
  });
  assert.equal(r.ok, true);
  if (r.ok && r.args.mode === 'merge') {
    assert.equal(r.args.source_text, 'A');
    assert.equal(r.args.peer_text, 'B');
    assert.equal(r.args.reason, 'overlap');
  }
}

{
  const r = buildTightenBridgeArgs({
    mode: 'merge',
    sourceText: 'Parent',
    peerEntries: [
      { ref: 'USER.md [1]', text: 'P1' },
      { ref: 'USER.md [2]', text: 'P2' },
    ],
    reason: 'multi',
    actions: ['absorb'],
    sourceRef: 'USER.md [0]',
  });
  assert.equal(r.ok, true);
  if (r.ok && r.args.mode === 'merge') {
    assert.equal(r.args.source_text, 'Parent');
    assert.equal(r.args.peer_text, 'P1');
    assert.equal(r.args.peer_ref, 'USER.md [1]');
    assert.deepEqual(r.args.peer_entries, [
      { ref: 'USER.md [1]', text: 'P1' },
      { ref: 'USER.md [2]', text: 'P2' },
    ]);
  }
}

assert.equal(canRunTightenGuidance(''), true);
assert.equal(canRunTightenGuidance('   '), true);
assert.equal(canRunTightenGuidance('half length'), true);
assert.equal(resolveTightenGuidance(''), DEFAULT_TIGHTEN_GUIDANCE);
assert.equal(resolveTightenGuidance('  '), DEFAULT_TIGHTEN_GUIDANCE);
assert.equal(resolveTightenGuidance('cut by half'), 'cut by half');

const originalHermesHome = process.env.HERMES_HOME;
const hermesHome = fs.mkdtempSync(path.join(os.tmpdir(), 'hot-health-'));
const stagingDir = path.join(hermesHome, 'memories', 'staging');
const sidecarPath = path.join(stagingDir, '.hot-health.json');

try {
  fs.mkdirSync(stagingDir, { recursive: true });
  process.env.HERMES_HOME = hermesHome;
  fs.writeFileSync(sidecarPath, JSON.stringify({
    'MEMORY.md': [{ index: 1, kinds: ['merge'], actions: ['merge'] }],
    'USER.md': [{ index: 0, kinds: ['rephrase'], actions: ['rephrase'] }],
  }));

  clearAnnotationsForFiles(['MEMORY.md']);
  assert.deepEqual(loadHotHealthSidecar(), {
    'MEMORY.md': [],
    'USER.md': [{ index: 0, kinds: ['rephrase'], actions: ['rephrase'] }],
    'HERMES.md': [],
  });

  clearAnnotationsForFiles(['MEMORY.md', 'USER.md']);
  assert.deepEqual(loadHotHealthSidecar(), {
    'MEMORY.md': [],
    'USER.md': [],
    'HERMES.md': [],
  });
} finally {
  if (originalHermesHome === undefined) {
    delete process.env.HERMES_HOME;
  } else {
    process.env.HERMES_HOME = originalHermesHome;
  }
  fs.rmSync(hermesHome, { recursive: true, force: true });
}

console.log('hotHealth.test.ts: all assertions passed');
