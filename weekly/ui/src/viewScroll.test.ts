import { describe, expect, it, vi } from 'vitest';
import {
  applyViewScrollSnapshot,
  captureViewScrollSnapshot,
} from './viewScroll';

describe('viewScroll', () => {
  it('captures window and nested list scrollTops', () => {
    const approval = { scrollTop: 120 } as HTMLElement;
    const staging = { scrollTop: 45 } as HTMLElement;
    expect(captureViewScrollSnapshot(approval, staging, 800)).toEqual({
      windowY: 800,
      approvalListTop: 120,
      stagingListTop: 45,
    });
  });

  it('treats missing list elements as scrollTop 0', () => {
    expect(captureViewScrollSnapshot(null, undefined, 10)).toEqual({
      windowY: 10,
      approvalListTop: 0,
      stagingListTop: 0,
    });
  });

  it('restores window and nested list positions', () => {
    const approval = { scrollTop: 0 } as HTMLElement;
    const staging = { scrollTop: 0 } as HTMLElement;
    const scrollWindow = vi.fn();
    applyViewScrollSnapshot(
      { windowY: 640, approvalListTop: 90, stagingListTop: 30 },
      approval,
      staging,
      scrollWindow,
    );
    expect(scrollWindow).toHaveBeenCalledWith(0, 640);
    expect(approval.scrollTop).toBe(90);
    expect(staging.scrollTop).toBe(30);
  });
});
