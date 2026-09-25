import assert from 'node:assert/strict';
import path from 'node:path';
import {
  getHotMemoryBudgets,
  splitHotEntries,
  joinHotEntries,
  resolveHotMemoryPath,
  stripSectionMarker,
} from './hotMemory.ts';
import { resolveHermesHome } from './pluginBridge.ts';

const hermesHome = resolveHermesHome();
assert.equal(
  resolveHotMemoryPath('MEMORY.md'),
  path.join(hermesHome, 'memories', 'MEMORY.md'),
);
// Prefer hermes-home/HERMES.md (cloud); fall back to sibling when only that exists.
assert.equal(
  resolveHotMemoryPath('HERMES.md'),
  path.join(hermesHome, 'HERMES.md'),
);

const budgets = getHotMemoryBudgets();
assert.equal(budgets['MEMORY.md'], 4000);
assert.equal(budgets['USER.md'], 3000);
assert.equal(budgets['HERMES.md'], null);

const section = splitHotEntries('MEMORY.md', 'alpha\n§\nbeta\n§\ngamma');
assert.equal(section.mode, 'section');
assert.deepEqual(section.entries, ['alpha', 'beta', 'gamma']);
assert.deepEqual(
  splitHotEntries('MEMORY.md', 'keep § inside').entries,
  ['keep  inside']
);
assert.deepEqual(
  splitHotEntries('MEMORY.md', 'note § here\n§\nsecond').entries,
  ['note  here', 'second']
);

const lineLeading = splitHotEntries('MEMORY.md', '§ alpha\n§ beta');
assert.equal(lineLeading.mode, 'section');
assert.deepEqual(lineLeading.entries, ['alpha', 'beta']);

const mixedBlank = splitHotEntries('MEMORY.md', 'intro\n\n§ alpha\n§ beta');
assert.equal(mixedBlank.mode, 'section');
assert.ok(mixedBlank.entries.includes('intro'));
assert.ok(mixedBlank.entries.includes('alpha'));
assert.ok(mixedBlank.entries.includes('beta'));

const rejoined = joinHotEntries('MEMORY.md', ['alpha', 'beta'], 'section');
assert.equal(rejoined, 'alpha\n§\nbeta');
assert.ok(!rejoined.split('\n§\n').some((p) => p.includes('§') && p !== '§'));

assert.equal(stripSectionMarker('hello § world'), 'hello  world');

const hermesHeading = splitHotEntries(
  'HERMES.md',
  '# Title\n\nintro\n\n## A\n\nbody a\n\n## B\n\nbody b'
);
assert.equal(hermesHeading.mode, 'heading');
assert.ok(hermesHeading.entries[0].startsWith('## A') || hermesHeading.entries.some((e) => e.includes('## A')));

const joinedHeading = joinHotEntries('HERMES.md', hermesHeading.entries, 'heading');
assert.ok(joinedHeading.includes('## A'));
assert.ok(!joinedHeading.includes('\n§\n'));

const hermesProseSection = splitHotEntries(
  'HERMES.md',
  '# Title\n\nUse § in prose for section refs.\n\n## A\n\nbody a § note\n\n## B\n\nbody b'
);
assert.equal(hermesProseSection.mode, 'heading');
assert.ok(hermesProseSection.entries.some((e) => e.startsWith('## ')));
assert.ok(hermesProseSection.entries.some((e) => e.includes('§')));

const joinedProseSection = joinHotEntries(
  'HERMES.md',
  hermesProseSection.entries,
  'heading'
);
assert.ok(joinedProseSection.includes('§'));
assert.ok(joinedProseSection.includes('## A'));
assert.ok(!joinedProseSection.includes('\n§\n'));

// Merge of two ## sections must survive join→split (Save must not resurrect the peer).
const mergedTwoHeadings =
  '## Path Discipline\n\nKeep hot writes gated.\n\n## Hook Authority\n\nShell hook is the write-gate.';
const afterMergeSave = joinHotEntries('HERMES.md', [mergedTwoHeadings], 'heading');
const reloadedMerge = splitHotEntries('HERMES.md', afterMergeSave);
assert.equal(
  reloadedMerge.entries.length,
  1,
  `expected 1 entry after merged HERMES save, got ${reloadedMerge.entries.length}`,
);
assert.match(reloadedMerge.entries[0] ?? '', /^## Path Discipline/m);
assert.match(reloadedMerge.entries[0] ?? '', /^### Hook Authority/m);

console.log('hotMemory.test.ts: all assertions passed');
