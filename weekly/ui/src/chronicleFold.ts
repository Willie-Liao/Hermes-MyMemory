export type WeekViewMode = 'approve' | 'read' | 'hot';

/**
 * Chronicle fold when switching WeekReview tabs.
 * Approval Hub → expanded (folded=false). Read / Memory and User →
 * collapse if currently expanded; stay collapsed otherwise.
 */
export function nextChronicleFoldedOnTabChange(
  nextMode: WeekViewMode,
  currentlyFolded: boolean,
): boolean {
  if (nextMode === 'approve') {
    return false;
  }
  if (!currentlyFolded) {
    return true;
  }
  return currentlyFolded;
}
