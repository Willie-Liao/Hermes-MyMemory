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

    // Digest order: related before valid_from; clocks after sources.
    const relatedIdx = yaml.indexOf('related:');
    const validFromIdx = yaml.indexOf('valid_from:');
    const sourcesIdx = yaml.indexOf('sources:');
    assert.ok(relatedIdx > 0 && relatedIdx < validFromIdx);
    assert.ok(sourcesIdx > validFromIdx);
    assert.ok(STAGING_FRONTMATTER_KEY_ORDER.includes('supersedes'));
    assert.ok(STAGING_FRONTMATTER_KEY_ORDER.includes('entity_aliases'));
  });

  it('emits optional message clocks after sources and omits them when unset', () => {
    const withClocks: MemoryBlock = {
      id: 'mem-clock',
      type: 'fact',
      entity: 'Topic',
      confidence: 'high',
      importance: 3,
      status: 'candidate',
      sources: ['session s1#1-2'],
      user_message_at: '2026-08-22T16:01:12+08:00',
      assistant_response_at: '2026-08-22T17:10:44+08:00',
      generated_at: '2026-08-22T17:16:08+08:00',
      body: 'an observation',
      filePath: '2026-08-22.md',
    };
    const yaml = formatStagingFrontmatter(withClocks);
    assert.match(yaml, /user_message_at: ['"]2026-08-22T16:01:12\+08:00['"]/);
    assert.match(yaml, /assistant_response_at: ['"]2026-08-22T17:10:44\+08:00['"]/);
    assert.match(yaml, /generated_at: ['"]2026-08-22T17:16:08\+08:00['"]/);
    assert.ok(yaml.indexOf('sources:') < yaml.indexOf('user_message_at:'));
    assert.ok(STAGING_FRONTMATTER_KEY_ORDER.includes('generated_at'));

    const bare: MemoryBlock = {
      id: 'mem-clock-bare',
      type: 'fact',
      confidence: 'high',
      importance: 3,
      status: 'candidate',
      sources: ['session s1'],
      body: 'bare',
      filePath: '2026-08-22.md',
    };
    const bareYaml = formatStagingFrontmatter(bare);
    assert.equal(bareYaml.includes('user_message_at:'), false);
    assert.equal(bareYaml.includes('generated_at:'), false);
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

  it('parse + stringify preserves Unicode entity_aliases after entity', () => {
    const raw = `---
id: mem-2026-08-24-event-bbbbbbbbbbbb
type: event
entity: Memory Digest
entity_aliases: [记忆摘要]
predicate: user_requested_memory_recall
confidence: high
importance: 3
status: candidate
sources: [session s-example]
---
Beginning: asked; Course: traced; Outcome: recalled.
`;
    const parsed = parseMDBlock(raw, '2026-08-24.md');
    assert.deepEqual(parsed[0]?.entity_aliases, ['记忆摘要']);
    const out = stringifyMDBlock(parsed[0]!);
    assert.match(out, /entity: Memory Digest\nentity_aliases: \[记忆摘要\]/);
    const again = parseMDBlock(out, '2026-08-24.md');
    assert.deepEqual(again[0]?.entity_aliases, ['记忆摘要']);
    assert.equal(again[0]?.entity, 'Memory Digest');
  });
});
