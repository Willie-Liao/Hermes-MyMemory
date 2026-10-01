import fs from 'node:fs';
import path from 'node:path';
import { UI_IDLE_TIMEOUT_MS, shouldIdleShutdown } from './idleRescan.ts';

export function parseTunnelPid(raw: string | null | undefined): number | null {
  if (raw == null) return null;
  const text = String(raw).trim();
  if (!/^\d+$/.test(text)) return null;
  const pid = Number(text);
  if (!Number.isInteger(pid) || pid <= 0) return null;
  return pid;
}

export function resolveTunnelPidPath(hermesHome: string): string {
  return path.join(hermesHome, 'cache', 'weekly-ui-tunnel.pid');
}

export type StopTrackedTunnelArgs = {
  pidPath: string;
  signal?: NodeJS.Signals;
  isAlive?: (pid: number) => boolean;
  kill?: (pid: number, signal?: NodeJS.Signals) => void;
  readFile?: (pidPath: string) => string | null;
  unlink?: (pidPath: string) => void;
};

export type StopTrackedTunnelResult = {
  stopped: boolean;
  pid: number | null;
};

function defaultIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Kill only the UI-owned cloudflared PID recorded on disk, then clear the record.
 * Never uses a global process-name match.
 */
export function stopTrackedTunnel(args: StopTrackedTunnelArgs): StopTrackedTunnelResult {
  const readFile =
    args.readFile
    ?? ((pidPath: string) => {
      try {
        return fs.readFileSync(pidPath, 'utf8');
      } catch {
        return null;
      }
    });
  const unlink =
    args.unlink
    ?? ((pidPath: string) => {
      try {
        fs.unlinkSync(pidPath);
      } catch {
        // ignore missing/stale
      }
    });
  const isAlive = args.isAlive ?? defaultIsAlive;
  const kill =
    args.kill
    ?? ((pid: number, signal?: NodeJS.Signals) => {
      process.kill(pid, signal);
    });
  const signal = args.signal ?? 'SIGTERM';

  const pid = parseTunnelPid(readFile(args.pidPath));
  if (pid == null) {
    unlink(args.pidPath);
    return { stopped: false, pid: null };
  }

  let stopped = false;
  if (isAlive(pid)) {
    try {
      kill(pid, signal);
      stopped = true;
    } catch {
      stopped = false;
    }
  }
  unlink(args.pidPath);
  return { stopped, pid };
}

export type UiLifecycle = {
  touch: () => number;
  checkIdle: () => boolean;
  shutdown: (reason: string) => boolean;
  getLastActivityAt: () => number;
  isShuttingDown: () => boolean;
};

export type CreateUiLifecycleArgs = {
  now?: () => number;
  idleTimeoutMs?: number;
  onShutdown: (reason: string) => void;
  stopTunnel: () => void;
};

/** Shared inactivity deadline + idempotent shutdown used by Close UI and idle expiry. */
export function createUiLifecycle(args: CreateUiLifecycleArgs): UiLifecycle {
  const nowFn = args.now ?? (() => Date.now());
  const idleTimeoutMs = args.idleTimeoutMs ?? UI_IDLE_TIMEOUT_MS;
  let lastActivityAt = nowFn();
  let shuttingDown = false;

  const shutdown = (reason: string): boolean => {
    if (shuttingDown) return false;
    shuttingDown = true;
    try {
      args.stopTunnel();
    } catch {
      // Best-effort tunnel teardown.
    }
    args.onShutdown(reason);
    return true;
  };

  return {
    touch: () => {
      lastActivityAt = nowFn();
      return lastActivityAt;
    },
    checkIdle: () => {
      if (shuttingDown) return false;
      if (
        !shouldIdleShutdown({
          now: nowFn(),
          lastActivityAt,
          timeoutMs: idleTimeoutMs,
        })
      ) {
        return false;
      }
      return shutdown('idle');
    },
    shutdown,
    getLastActivityAt: () => lastActivityAt,
    isShuttingDown: () => shuttingDown,
  };
}
