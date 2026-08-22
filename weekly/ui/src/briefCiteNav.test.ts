import assert from 'node:assert/strict';
import {
  briefCiteAnchorId,
  approvalCiteAnchorId,
  dailyBlockAnchorId,
  splitBriefCites,
  splitBriefDisplaySegments,
} from './briefCiteNav.ts';

assert.equal(briefCiteAnchorId(3), 'brief-cite-3');
assert.equal(approvalCiteAnchorId(3), 'approval-cite-3');
assert.equal(
  dailyBlockAnchorId('mem-2026-07-11-example'),
  'daily-block-mem-2026-07-11-example',
);

const segs = splitBriefCites('Hello [1] and [2].');
assert.deepEqual(segs, [
  { kind: 'text', value: 'Hello ' },
  { kind: 'cite', n: 1, value: '[1]' },
  { kind: 'text', value: ' and ' },
  { kind: 'cite', n: 2, value: '[2]' },
  { kind: 'text', value: '.' },
]);

const display = splitBriefDisplaySegments(
  '### Events\n- Shipped [1].\n### Hypothesis\n- None.\n',
);
assert.equal(display[0].kind, 'theme');
if (display[0].kind === 'theme') assert.equal(display[0].title, 'Events');
assert.ok(display.some((s) => s.kind === 'cite' && s.n === 1));
assert.ok(!display.some((s) => s.kind === 'text' && s.value.includes('###')));

const bare = splitBriefDisplaySegments('Events\n- X [2]\n');
assert.equal(bare[0].kind, 'theme');
if (bare[0].kind === 'theme') assert.equal(bare[0].title, 'Events');

console.log('briefCiteNav.test.ts: ok');
