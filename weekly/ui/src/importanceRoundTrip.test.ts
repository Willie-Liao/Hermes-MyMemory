import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  normalizeImportance,
  parseMDBlock,
  stringifyMDBlock,
} from './serverHelpers.ts';
import type { MemoryBlock } from './types.ts';

describe('importance round-trip', () => {
  it('normalizeImportance clamps and defaults', () => {
    assert.equal(normalizeImportance(undefined), 3);
    assert.equal(normalizeImportance(''), 3);
    assert.equal(normalizeImportance(4), 4);
    assert.equal(normalizeImportance('0'), 0);
    assert.equal(normalizeImportance(9), 3);
    assert.equal(normalizeImportance(1.5), 3);
  });

  it('parseMDBlock reads importance; missing → 3', () => {
    const withImp = parseMDBlock(
      `---
id: a
type: fact
confidence: high
importance: 4
status: candidate
sources: [s1]
---
body a
`,
      '2026-07-23.md',
    );
    assert.equal(withImp[0]?.importance, 4);

    const missing = parseMDBlock(
      `---
id: b
type: fact
confidence: high
status: candidate
sources: [s1]
---
body b
`,
      '2026-07-23.md',
    );
    assert.equal(missing[0]?.importance, 3);
  });

  it('stringifyMDBlock writes importance after confidence', () => {
    const block: MemoryBlock = {
      id: 'c',
      type: 'fact',
      confidence: 'explicit',
      importance: 5,
      status: 'candidate',
      sources: ['s1'],
      body: 'hello',
      filePath: '2026-07-23.md',
    };
    const text = stringifyMDBlock(block);
    assert.match(text, /confidence: explicit\nimportance: 5\nstatus:/);
    const round = parseMDBlock(text, '2026-07-23.md');
    assert.equal(round[0]?.importance, 5);
  });
});
