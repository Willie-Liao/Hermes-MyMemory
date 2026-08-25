import assert from 'node:assert/strict';
import {
  isIdle,
  onMouseMove,
  isHotMemoryComposeActive,
  shouldApplyPostRescanRefresh,
  shouldIdleShutdown,
  shouldSendActivityHeartbeat,
  IDLE_MS,
  UI_IDLE_TIMEOUT_MS,
  ACTIVITY_HEARTBEAT_MIN_MS,
} from './idleRescan.ts';

assert.equal(isIdle(100_000, 100_000 - IDLE_MS - 1), true);
assert.equal(isIdle(100_000, 100_000 - 1000), false);

const s0 = { lastMoveAt: 0, timerStartedAt: 0, idle: true };
const s1 = onMouseMove(s0, 50_000);
assert.equal(s1.idle, false);
assert.equal(s1.timerStartedAt, 50_000);
assert.equal(s1.lastMoveAt, 50_000);

assert.equal(
  isHotMemoryComposeActive({
    editingIndex: null,
    tightenComposerIndex: null,
    tightenIndex: null,
  }),
  false,
);
assert.equal(
  isHotMemoryComposeActive({
    editingIndex: 0,
    tightenComposerIndex: null,
    tightenIndex: null,
  }),
  true,
);
assert.equal(
  isHotMemoryComposeActive({
    editingIndex: null,
    tightenComposerIndex: 2,
    tightenIndex: null,
  }),
  true,
);
assert.equal(
  isHotMemoryComposeActive({
    editingIndex: null,
    tightenComposerIndex: null,
    tightenIndex: 1,
  }),
  true,
);
assert.equal(shouldApplyPostRescanRefresh(false), true);
assert.equal(shouldApplyPostRescanRefresh(true), false);

assert.equal(UI_IDLE_TIMEOUT_MS, 3_600_000);
assert.equal(ACTIVITY_HEARTBEAT_MIN_MS, 5_000);
assert.equal(
  shouldIdleShutdown({
    now: 3_600_000,
    lastActivityAt: 0,
  }),
  true,
);
assert.equal(
  shouldIdleShutdown({
    now: 3_599_999,
    lastActivityAt: 0,
  }),
  false,
);
assert.equal(
  shouldIdleShutdown({
    now: 10_000,
    lastActivityAt: 0,
    timeoutMs: 5_000,
  }),
  true,
);
assert.equal(
  shouldSendActivityHeartbeat({
    now: 10_000,
    lastSentAt: 4_999,
  }),
  true,
);
assert.equal(
  shouldSendActivityHeartbeat({
    now: 9_999,
    lastSentAt: 5_000,
  }),
  false,
);
assert.equal(
  shouldSendActivityHeartbeat({
    now: 1,
    lastSentAt: null,
  }),
  true,
);

console.log('idleRescan.test.ts: ok');
