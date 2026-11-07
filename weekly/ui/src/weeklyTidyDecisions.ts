import type { WeeklyProposal } from './types';

export const proposalKey = (proposal: WeeklyProposal): string =>
  proposal.record_id || proposal.block_id || proposal.label || '';

/** Approval Hub: only ``type: event`` proposals (defensive UI filter). */
export const isApprovalHubProposal = (proposal: WeeklyProposal): boolean =>
  String(proposal.type || '').trim().toLowerCase() === 'event';

export const filterApprovalHubProposals = (
  proposals: WeeklyProposal[],
): WeeklyProposal[] => proposals.filter(isApprovalHubProposal);
