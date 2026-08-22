import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  formatStagingFrontmatter,
  STAGING_FRONTMATTER_KEY_ORDER,
} from './stagingFrontmatter.ts';
import { parseMDBlock, stringifyMDBlock } from './serverHelpers.ts';
import type { MemoryBlock } from './types.ts';

describe('staging frontmatter display', () => {
  it('includes predicate, participants, related, involves, supersedes in digest order', () => {
    const block: MemoryBlock = {
      id: 'mem-20260726-alex-resume-v3-cocoindex-pushed',
      type: 'event',
      entity: 'Alex Chen',
      predicate: 'user_requested_resume_v3_cocoindex',
      participants: [
        { entity: 'User', role: 'requester' },
        { entity: 'Assistant', role: 'executor' },
      ],
      related: ['mem-20260726-alex-resume-delivered'],
      involves: undefined,
      supersedes: ['mem-old'],
      valid_from: '2026-07-26',
      valid_to: '2026-07-26',
      confidence: 'explicit',
      importance: 4,
      status: 'candidate',
      sources: [
        'session 20260722_172657_c54f77a8',
        'file:Grading/assistant/cocoindex_codebase.py',
      ],
      body: 'User asked to revise project.',
      filePath: '2026-07-26.md',
    };

    const yaml = formatStagingFrontmatter(block);
    assert.match(yaml, /^---\n/);
    assert.match(yaml, /\n---$/);
    assert.match(yaml, /predicate: user_requested_resume_v3_cocoindex/);
    assert.match(yaml, /participants:\n {2}- \{entity: User, role: requester\}/);
    assert.match(yaml, /related: \[mem-20260726-alex-resume-delivered\]/);
    assert.match(yaml, /supersedes: \[mem-old\]/);
    assert.match(
      yaml,
      /sources: \[session 20260722_172657_c54f77a8, file:Grading\/assistant\/cocoindex_codebase\.py\]/,
    );

    // Digest order: related before valid_from; sources last.
    const relatedIdx = yaml.indexOf('related:');
    const validFromIdx = yaml.indexOf('valid_from:');
    const sourcesIdx = yaml.indexOf('sources:');
    assert.ok(relatedIdx > 0 && relatedIdx < validFromIdx);
    assert.ok(sourcesIdx > validFromIdx);
    assert.ok(STAGING_FRONTMATTER_KEY_ORDER.includes('supersedes'));
  });

  it('parse + stringify round-trips supersedes and discarded_at', () => {
    const raw = `---
id: mem-helper
type: decision_constraint
entity: Elsa
confidence: explicit
importance: 1
status: rejected
discarded_at: 2026-07-26
supersedes: [mem-target]
sources: [session s1]
---
Spent helper.
`;
    const parsed = parseMDBlock(raw, '2026-07-26.md');
    assert.equal(parsed[0]?.supersedes?.[0], 'mem-target');
    assert.equal(parsed[0]?.discarded_at, '2026-07-26');
    const out = stringifyMDBlock(parsed[0]!);
    assert.match(out, /supersedes: \[mem-target\]/);
    assert.match(out, /discarded_at: 2026-07-26/);
    // sources last
    assert.ok(out.indexOf('discarded_at:') < out.indexOf('sources:'));
  });
});
