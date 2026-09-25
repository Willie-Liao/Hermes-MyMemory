import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge.ts';

const MAX_ERROR_CHARS = 200;

/** Soft-append a UI activity line to the same file as weekly._log. */
export function appendWeeklyUiLog(message: string): void {
  try {
    const hermesHome = resolveHermesHome();
    const logPath = path.join(hermesHome, 'logs', 'memory-weekly.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    const ts = new Date().toISOString();
    const line = `${ts} ${message}\n`;
    fs.appendFileSync(logPath, line, 'utf8');
  } catch {
    // Never break HTTP / bridge callers on log IO failure.
  }
}

export function truncateUiLogDetail(detail: string, max = MAX_ERROR_CHARS): string {
  const cleaned = detail.replace(/\s+/g, ' ').trim();
  if (cleaned.length <= max) return cleaned;
  return `${cleaned.slice(0, max - 1)}…`;
}

/** Map bridge op → short ui verb for log lines. */
export function uiLogVerbForBridgeOp(op: string): string | null {
  switch (op) {
    case 'generate_week':
      return 'update';
    case 'close_week':
      return 'close';
    case 'reopen_week':
      return 'reopen';
    case 'request_weekly_reorganise':
      return 'digest-run';
    case 'request_resummarise':
      return 'digest-run';
    default:
      return null;
  }
}

export function logWeeklyBridgeMutation(
  op: string,
  args: Record<string, unknown>,
  bridged: { ok: boolean; result?: unknown; error?: string },
): void {
  const verb = uiLogVerbForBridgeOp(op);
  if (!verb) return;

  const week =
    typeof args.week_key === 'string' && args.week_key
      ? args.week_key
      : bridged.result && typeof bridged.result === 'object' && typeof (bridged.result as { week?: unknown }).week === 'string'
        ? (bridged.result as { week: string }).week
        : 'unknown';

  if (!bridged.ok) {
    appendWeeklyUiLog(
      `ui ${verb} week=${week} error=${truncateUiLogDetail(bridged.error || 'bridge_error')}`,
    );
    return;
  }

  const outcome =
    bridged.result && typeof bridged.result === 'object' && typeof (bridged.result as { outcome?: unknown }).outcome === 'string'
      ? (bridged.result as { outcome: string }).outcome
      : 'ok';
  appendWeeklyUiLog(`ui ${verb} week=${week} outcome=${outcome}`);
}
