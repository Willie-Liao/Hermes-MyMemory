import { getISOWeekCode } from './isoWeek.ts';

export const AUTO_RESCAN_MS = 180_000;
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

/** Fire idle auto-rescan only when the 3-min origin elapsed and the user is not idle or composing. */
export function shouldFireAutoRescan(args: {
  now: number;
  idle: boolean;
  timerStartedAt: number;
  intervalMs?: number;
  /** True while an edit/tighten composer is open — freezes the auto-rescan countdown. */
  editing?: boolean;
}): boolean {
  const intervalMs = args.intervalMs ?? AUTO_RESCAN_MS;
  if (args.idle || args.editing) return false;
  return args.now - args.timerStartedAt >= intervalMs;
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

/** Re-scan spinner must not treat "not started yet" as finished — generate_in_flight is false until the kick lands. */
export function rescanPollJobFinished(args: {
  sawGenerateInFlight: boolean;
  generateInFlight: boolean;
}): boolean {
  return args.sawGenerateInFlight && !args.generateInFlight;
}

/** After a browser remount, resume the spinner if the Python job is still marked in flight. */
export function shouldAdoptInFlightJob(inFlight: boolean): boolean {
  return Boolean(inFlight);
}

/** Reorganise is date-scoped; only resume the spinner when that date sits in the open week. */
export function reorganiseInFlightForWeek(args: {
  outcome: string;
  jobDate: string;
  weekKey: string;
}): boolean {
  if (args.outcome !== 'in_flight' || !args.weekKey) return false;
  return getISOWeekCode(args.jobDate) === args.weekKey;
}

/**
 * While editing, hold remaining auto-rescan time by shifting the origin forward
 * each tick by `elapsedMs` (typically the interval tick size).
 */
export function freezeAutoRescanOrigin(
  timerStartedAt: number,
  elapsedMs: number,
): number {
  return timerStartedAt + Math.max(0, elapsedMs);
}

/** Clears idle and resets the auto-rescan timer origin to `now`. */
export function onMouseMove(state: IdleRescanState, now: number): IdleRescanState {
  return {
    lastMoveAt: now,
    timerStartedAt: now,
    idle: false,
  };
}
