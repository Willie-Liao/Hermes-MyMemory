export const IDLE_MS = 30_000;
/** Shared Weekly UI server inactivity timeout (desktop + phone). */
export const UI_IDLE_TIMEOUT_MS = 3_600_000;
/** Minimum gap between client activity heartbeats to the server. */
export const ACTIVITY_HEARTBEAT_MIN_MS = 5_000;

export type IdleRescanState = {
  lastMoveAt: number;
  timerStartedAt: number;
  idle: boolean;
};

export function isIdle(
  now: number,
  lastMoveAt: number,
  idleMs: number = IDLE_MS,
): boolean {
  return now - lastMoveAt >= idleMs;
}

/** True when the shared server idle deadline has elapsed. */
export function shouldIdleShutdown(args: {
  now: number;
  lastActivityAt: number;
  timeoutMs?: number;
}): boolean {
  const timeoutMs = args.timeoutMs ?? UI_IDLE_TIMEOUT_MS;
  return args.now - args.lastActivityAt >= timeoutMs;
}

/** Throttle client → server activity heartbeats. */
export function shouldSendActivityHeartbeat(args: {
  now: number;
  lastSentAt: number | null;
  minIntervalMs?: number;
}): boolean {
  if (args.lastSentAt == null) return true;
  const minIntervalMs = args.minIntervalMs ?? ACTIVITY_HEARTBEAT_MIN_MS;
  return args.now - args.lastSentAt >= minIntervalMs;
}

/** Hot Memory Editor entry edit, tighten guidance, or tighten draft review. */
export function isHotMemoryComposeActive(args: {
  editingIndex: number | null;
  tightenComposerIndex: number | null;
  tightenIndex: number | null;
}): boolean {
  return (
    args.editingIndex != null
    || args.tightenComposerIndex != null
    || args.tightenIndex != null
  );
}

/** Skip weeks list + selected-week reload after a background rescan while composing. */
export function shouldApplyPostRescanRefresh(editing: boolean): boolean {
  return !editing;
}

/** Clears idle and resets the last-move clock to `now`. */
export function onMouseMove(state: IdleRescanState, now: number): IdleRescanState {
  return {
    lastMoveAt: now,
    timerStartedAt: now,
    idle: false,
  };
}
