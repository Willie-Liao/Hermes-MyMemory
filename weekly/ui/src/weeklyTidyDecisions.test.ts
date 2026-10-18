import assert from 'node:assert/strict';
import {
  filterApprovalHubProposals,
  isApprovalHubProposal,
  proposalKey,
} from './weeklyTidyDecisions.ts';
import type { WeeklyProposal } from './types.ts';

assert.equal(
  proposalKey({
    record_id: 'cite-1',
    block_id: 'mem-fact',
    label: 'Fact',
  }),
  'cite-1',
);
assert.equal(
  proposalKey({
    block_id: 'mem-only',
    label: 'Label',
  } as WeeklyProposal),
  'mem-only',
);
assert.equal(
  proposalKey({
    label: 'fallback',
  } as WeeklyProposal),
  'fallback',
);

const mixed: WeeklyProposal[] = [
  {
    record_id: 'cite-1',
    block_id: 'mem-event',
    type: 'event',
    proposed_text: 'Kickoff',
    tier: 'cited',
    cite_n: '1',
  },
  {
    record_id: 'cite-2',
    block_id: 'mem-fact',
    type: 'fact',
    proposed_text: 'Policy',
    tier: 'cited',
  },
  {
    record_id: 'cite-3',
    block_id: 'mem-proc',
    type: 'procedure',
    proposed_text: 'Log paths',
  },
  {
    record_id: 'cite-4',
    block_id: 'mem-dec',
    type: 'decision_constraint',
    proposed_text: 'Budget cap',
  },
  {
    record_id: 'cite-5',
    block_id: 'mem-hyp',
    type: 'hypothesis',
    proposed_text: 'Pivot',
  },
  {
    record_id: 'cite-6',
    block_id: 'mem-untyped',
    proposed_text: 'No type — drop',
  },
];

assert.equal(isApprovalHubProposal(mixed[0]!), true);
assert.equal(isApprovalHubProposal(mixed[1]!), false);

const hub = filterApprovalHubProposals(mixed);
assert.equal(hub.length, 1);
assert.equal(hub[0]!.type, 'event');
assert.equal(hub[0]!.block_id, 'mem-event');
assert.equal(hub[0]!.record_id, 'cite-1');
assert.ok(hub[0]!.proposed_text);
// Actionable shape for Add memory / user / Delete staging
assert.ok(proposalKey(hub[0]!));

console.log('weeklyTidyDecisions.test.ts: all assertions passed');
