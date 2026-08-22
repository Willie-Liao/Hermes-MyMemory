/** Snapshot of window + nested list scroll positions for post-refresh restore. */
export type ViewScrollSnapshot = {
  windowY: number;
  approvalListTop: number;
  stagingListTop: number;
};

export function captureViewScrollSnapshot(
  approvalEl: HTMLElement | null | undefined,
  stagingEl: HTMLElement | null | undefined,
  windowY: number = typeof window !== 'undefined' ? window.scrollY : 0,
): ViewScrollSnapshot {
  return {
    windowY,
    approvalListTop: approvalEl?.scrollTop ?? 0,
    stagingListTop: stagingEl?.scrollTop ?? 0,
  };
}

export function applyViewScrollSnapshot(
  snap: ViewScrollSnapshot,
  approvalEl: HTMLElement | null | undefined,
  stagingEl: HTMLElement | null | undefined,
  scrollWindow: (x: number, y: number) => void = (x, y) => window.scrollTo(x, y),
): void {
  scrollWindow(0, snap.windowY);
  if (approvalEl) approvalEl.scrollTop = snap.approvalListTop;
  if (stagingEl) stagingEl.scrollTop = snap.stagingListTop;
}
