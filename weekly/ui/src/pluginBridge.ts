import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { WeekOverview } from './types';
import { logWeeklyBridgeMutation } from './weeklyUiLog.ts';
import { normalizeWeekStatus } from './weekStatus.ts';

export {
  normalizeWeekStatus,
  parseWeekStatusFromContent,
  weekStatusesEquivalent,
} from './weekStatus.ts';
export {
  blankPendingWeekOverview,
  emptyWeekSoftLoadPayload,
  isEmptyDigestGenerateOutcome,
  purgedWeekSoftLoadResult,
  resolveDefaultWeekSelection,
} from './softWeek.ts';

export interface WeeklyBridgeResponse {
  ok: boolean;
  result?: any;
  error?: string;
}

export interface BridgeRunResult {
  status: number;
  stdout: string;
  stderr: string;
}

export type BridgeRunner = (
  input: string,
) => BridgeRunResult | Promise<BridgeRunResult>;

/** Same budget as the weekly UI latency suite (300s). */
export const BRIDGE_CHILD_TIMEOUT_MS = 300_000;

export function parseBridgeStdout(stdout: string): WeeklyBridgeResponse {
  const trimmed = String(stdout ?? '').trim();
  const start = trimmed.indexOf('{');
  const end = trimmed.lastIndexOf('}');
  if (start < 0 || end <= start) {
    throw new SyntaxError('no JSON object in bridge stdout');
  }
  return JSON.parse(trimmed.slice(start, end + 1)) as WeeklyBridgeResponse;
}

export interface PluginOutcomeHttpError {
  status: number;
  error: string;
  outcome: string;
  details?: unknown;
}

interface PluginWeekRow {
  week: string;
  status: string;
  filename?: string;
  path?: string;
  tidy?: string;
  generate_in_flight?: string;
  reorganise_in_flight?: string;
}

interface PluginWeeksResult {
  outcome?: string;
  weeks?: PluginWeekRow[];
}

const WEEK_KEY_RE = /^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/;
const MONTH_KEY_RE = /^\d{4}-(0[1-9]|1[0-2])$/;

function looksLikeHermesHome(dir: string): boolean {
  return (
    fs.existsSync(path.join(dir, 'memories'))
    && fs.existsSync(path.join(dir, 'plugins', 'MyMemory', 'weekly'))
  );
}

/** Prefer HERMES_HOME; else walk up from cwd until memories/ + plugins/MyMemory/weekly/. */
export function resolveHermesHome(): string {
  if (process.env.HERMES_HOME) {
    return path.resolve(process.env.HERMES_HOME);
  }

  let dir = path.resolve(process.cwd());
  for (;;) {
    if (looksLikeHermesHome(dir)) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }

  const fallback = path.resolve(process.cwd(), 'hermes-home');
  return looksLikeHermesHome(fallback) ? fallback : path.resolve(process.cwd(), 'hermes-home');
}

export function tidyStateForWeeklyReport(
  reviewed: boolean,
  fileContent: string,
): WeekOverview['tidyState'] {
  if (!reviewed) return 'none';

  const ledger = fileContent
    .split(/^##\s*(?:§\s*)?8\.?\s*Action ledger\s*$/im)[1]
    ?.split(/^##\s/m)[0] ?? '';
  const tableRows = ledger
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|'));

  return tableRows.length > 2 ? 'tidy: done' : 'tidy: pending';
}

export function isValidWeekKey(value: unknown): value is string {
  return typeof value === 'string' && WEEK_KEY_RE.test(value);
}

export function isValidMonthKey(value: unknown): value is string {
  return typeof value === 'string' && MONTH_KEY_RE.test(value);
}

function mapWeekStatus(status: string): WeekOverview['status'] {
  return normalizeWeekStatus(status);
}

export function mapListWeeks(payload: PluginWeeksResult): WeekOverview[] {
  return (payload.weeks ?? []).map((row) => ({
    week: row.week,
    status: mapWeekStatus(row.status),
    tidyState: row.tidy === 'pending'
      ? 'tidy: pending'
      : row.tidy === 'done'
        ? 'tidy: done'
        : 'none',
    filePath: row.path ?? row.filename ?? `${row.week}.md`,
    fileContent: '',
    generateInFlight: row.generate_in_flight === 'true',
    reorganiseInFlight: row.reorganise_in_flight === 'true',
  }));
}

export function pluginOutcomeError(
  result: unknown,
  successOutcomes: readonly string[],
): PluginOutcomeHttpError | undefined {
  const payload = result && typeof result === 'object'
    ? result as Record<string, unknown>
    : {};
  const outcome = typeof payload.outcome === 'string' ? payload.outcome : '';
  if (successOutcomes.includes(outcome)) {
    return undefined;
  }

  const statuses: Record<string, number> = {
    bad_week: 400,
    bad_month: 400,
    no_draft: 400,
    empty_digests: 400,
    no_daily: 400, // generate empty-digest (no dailies)
    no_file: 404,
    no_md: 404,
    no_reviewed_file: 404,
    sunday_only: 403,
    already_closed: 409,
    nothing: 409,
    generation_pending: 409,
    invalid_decisions: 422,
    failed: 502,
  };
  const normalized = outcome || 'invalid_outcome';
  return {
    status: statuses[normalized] ?? 502,
    error: `Weekly plugin returned ${normalized}.`,
    outcome: normalized,
    ...(payload.errors === undefined ? {} : { details: payload.errors }),
  };
}

export function runSpawnedPython(
  args: string[],
  input: string,
  options?: { timeoutMs?: number; env?: NodeJS.ProcessEnv },
): Promise<BridgeRunResult> {
  const timeoutMs = options?.timeoutMs ?? BRIDGE_CHILD_TIMEOUT_MS;
  const env = options?.env ?? process.env;
  return new Promise((resolve, reject) => {
    const child = spawn('python3', args, {
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
      const killTimer = setTimeout(() => {
        child.kill('SIGKILL');
      }, 2_000);
      child.once('close', () => clearTimeout(killTimer));
    }, timeoutMs);

    const finish = (result: BridgeRunResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };

    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk: string) => {
      stderr += chunk;
    });
    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });
    child.on('close', (code) => {
      const timeoutNote = `bridge timed out after ${timeoutMs}ms`;
      finish({
        status: timedOut ? -1 : (code ?? -1),
        stdout,
        stderr: timedOut
          ? `${stderr}${stderr.trim() ? '\n' : ''}${timeoutNote}`
          : stderr,
      });
    });
    child.stdin.on('error', () => {
      /* Child may close stdin before we finish writing. */
    });
    child.stdin.write(input, 'utf8');
    child.stdin.end();
  });
}

async function runPluginBridge(
  pluginDir: string,
  bridgeFile: string,
  input: string,
): Promise<BridgeRunResult> {
  const hermesHome = resolveHermesHome();
  const bridgePath = path.join(hermesHome, 'plugins', pluginDir, bridgeFile);
  return runSpawnedPython([bridgePath], input, {
    env: { ...process.env, HERMES_HOME: hermesHome },
  });
}

export const runBridge: BridgeRunner = (input) =>
  runPluginBridge('MyMemory/weekly', 'bridge_cli.py', input);

export const runDigestBridge: BridgeRunner = (input) =>
  runPluginBridge('MyMemory/digest', 'bridge_cli.py', input);

async function callBridge(
  label: 'weekly' | 'digest',
  op: string,
  args: Record<string, unknown>,
  runner: BridgeRunner,
  logMutation: boolean,
): Promise<WeeklyBridgeResponse> {
  let child: BridgeRunResult;
  try {
    child = await Promise.resolve(runner(JSON.stringify({ op, args })));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const bridged = { ok: false as const, error: message };
    if (logMutation) logWeeklyBridgeMutation(op, args, bridged);
    return bridged;
  }
  if (child.status !== 0) {
    const stderr = child.stderr.trim();
    let error = stderr;
    if (!error) {
      error = child.status === 137
        ? `${label} bridge exit 137 SIGKILL`
        : child.status === -1
          ? `${label} bridge timed out after ${BRIDGE_CHILD_TIMEOUT_MS}ms`
          : `${label} bridge exited with status ${child.status}`;
    } else if (child.status === 137 && !/137/.test(error)) {
      error = `${error}; bridge exit 137 SIGKILL`;
    }
    const bridged = {
      ok: false as const,
      error,
    };
    if (logMutation) logWeeklyBridgeMutation(op, args, bridged);
    return bridged;
  }

  try {
    const parsed = parseBridgeStdout(child.stdout);
    if (!parsed || typeof parsed.ok !== 'boolean') {
      const bridged = { ok: false as const, error: `${label} bridge returned an invalid response` };
      if (logMutation) logWeeklyBridgeMutation(op, args, bridged);
      return bridged;
    }
    if (logMutation) logWeeklyBridgeMutation(op, args, parsed);
    return parsed;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const bridged = {
      ok: false as const,
      error: `failed to parse ${label} bridge response: ${message}`,
    };
    if (logMutation) logWeeklyBridgeMutation(op, args, bridged);
    return bridged;
  }
}

export async function callWeeklyBridge(
  op: string,
  args: Record<string, unknown> = {},
  runner: BridgeRunner = runBridge,
): Promise<WeeklyBridgeResponse> {
  return callBridge('weekly', op, args, runner, true);
}

export async function callDigestBridge(
  op: string,
  args: Record<string, unknown> = {},
  runner: BridgeRunner = runDigestBridge,
): Promise<WeeklyBridgeResponse> {
  return callBridge('digest', op, args, runner, false);
}
