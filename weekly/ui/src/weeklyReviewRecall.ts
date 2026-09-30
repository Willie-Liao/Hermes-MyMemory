/**
 * Unified Weekly Review Save/Recall stack (hypothesis + overdue span ops).
 * Max 3 batches / 24h — lives under `.memory-3-step-recall/.weekly-review-recall.json`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge';
import { RECALL_DIR_NAME } from './hotRecall';
import { mondayOfISOWeek } from './isoWeek';
import {
  CITE_MAP_SECTION_TITLE,
  HYPOTHESIS_SECTION_TITLE,
} from './fourPartBrief';
import type { MemoryBlock } from './types';
import {
  deleteMemoryBlock,
  extractRawMDBlockById,
  listRawMDBlocks,
  readAllMemoryBlocks,
  writeMemoryBlock,
} from './serverHelpers';
import type { PutOffInterval } from './overdueActions';
import type {
  WeeklyReviewHypConfirmPending,
  WeeklyReviewHypDeletePending,
  WeeklyReviewPendingOp,
  WeeklyReviewSpanConfirmPending,
  WeeklyReviewSpanPutOffPending,
  WeeklyReviewSpanSetDuePending,
} from './weeklyReviewOps';

export type {
  WeeklyReviewHypConfirmPending,
  WeeklyReviewHypDeletePending,
  WeeklyReviewPendingOp,
  WeeklyReviewSpanConfirmPending,
  WeeklyReviewSpanPutOffPending,
  WeeklyReviewSpanSetDuePending,
} from './weeklyReviewOps';
export {
  reviewOpMarkKey,
  reviewPendingButtonLabel,
  reviewRecallButtonLabel,
} from './weeklyReviewOps';

export const WEEKLY_REVIEW_RECALL_MAX = 3;
export const WEEKLY_REVIEW_RECALL_TTL_MS = 24 * 60 * 60 * 1000;
export const WEEKLY_REVIEW_RECALL_FILE = '.weekly-review-recall.json';

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const PUT_OFF_DAYS: Record<Exclude<PutOffInterval, '1mo'>, number> = {
  '1d': 1,
  '7d': 7,
  '2w': 14,
};

export type WeeklyReviewHypConfirmOp = WeeklyReviewHypConfirmPending & {
  weeklyBefore: string;
  weeklyPath: string;
  factId: string;
  dailyFile: string;
};

export type WeeklyReviewHypDeleteOp = WeeklyReviewHypDeletePending & {
  weeklyBefore: string;
  weeklyPath: string;
};

export type WeeklyReviewSpanOp = {
  kind: 'span_confirm' | 'span_put_off' | 'span_set_due_date';
  weekKey: string;
  blockId: string;
  previous_valid_to: string;
  valid_to: string;
  /** Daily basename for restore. */
  dailyFile: string;
};

export type WeeklyReviewOp =
  | WeeklyReviewHypConfirmOp
  | WeeklyReviewHypDeleteOp
  | WeeklyReviewSpanOp;

export type WeeklyReviewRecallBatch = {
  savedAt: string;
  ops: WeeklyReviewOp[];
};

type WeeklyReviewRecallStore = { batches: WeeklyReviewRecallBatch[] };

function stagingDir(): string {
  return path.join(resolveHermesHome(), 'memories', 'staging');
}

function storePath(): string {
  return path.join(stagingDir(), RECALL_DIR_NAME, WEEKLY_REVIEW_RECALL_FILE);
}

function prune(
  batches: WeeklyReviewRecallBatch[],
  now = Date.now(),
): WeeklyReviewRecallBatch[] {
  const cutoff = now - WEEKLY_REVIEW_RECALL_TTL_MS;
  return batches.filter((b) => {
    const t = Date.parse(b.savedAt);
    return Number.isFinite(t) && t >= cutoff;
  });
}

function loadStore(): WeeklyReviewRecallStore {
  try {
    const parsed = JSON.parse(fs.readFileSync(storePath(), 'utf8')) as WeeklyReviewRecallStore;
    if (!parsed || !Array.isArray(parsed.batches)) return { batches: [] };
    return { batches: prune(parsed.batches) };
  } catch {
    return { batches: [] };
  }
}

function saveStore(store: WeeklyReviewRecallStore): void {
  const batches = prune(store.batches).slice(-WEEKLY_REVIEW_RECALL_MAX);
  const p = storePath();
  if (batches.length === 0) {
    try {
      fs.unlinkSync(p);
    } catch (err: unknown) {
      const code = (err as NodeJS.ErrnoException)?.code;
      if (code !== 'ENOENT') throw err;
    }
    return;
  }
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `${JSON.stringify({ batches }, null, 2)}\n`);
}

export function weeklyReviewRecallAvailable(): boolean {
  return loadStore().batches.length > 0;
}

function isValidIsoDate(value: string): boolean {
  if (!ISO_DATE_RE.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

function formatUtcDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function mondayIsoOfWeek(weekKey: string): string | null {
  const monday = mondayOfISOWeek(weekKey);
  if (!monday) return null;
  return formatUtcDate(monday);
}

function resolveWeeklyPath(weekKey: string): string {
  return path.join(stagingDir(), 'weekly', `${weekKey}.md`);
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function extractDateFromToken(token: string): string | null {
  const dashed = token.match(/(\d{4}-\d{2}-\d{2})/);
  if (dashed && isValidIsoDate(dashed[1])) return dashed[1];
  const compact = token.match(/(\d{8})/);
  if (compact) {
    const raw = compact[1];
    const iso = `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
    if (isValidIsoDate(iso)) return iso;
  }
  return null;
}

/** Prefer citeDate → related mem dates → ISO Monday of weekKey. */
export function resolveHypothesisFactDate(
  weekKey: string,
  related: string[] | undefined,
  citeDate?: string,
): string {
  if (citeDate && isValidIsoDate(citeDate)) return citeDate;
  for (const token of related ?? []) {
    const fromToken = extractDateFromToken(String(token));
    if (fromToken) return fromToken;
  }
  const monday = mondayIsoOfWeek(weekKey);
  if (monday) return monday;
  throw new Error(`Invalid weekKey: ${weekKey}`);
}

function stripTrailingCite(text: string): string {
  return text.replace(/\s+\[\d+\]\s*$/, '').trim();
}

/**
 * Remove one Distill YAML block by id. Scope to ## Distill … ## Brief so the
 * Brief section is never treated as block body (weekly files often lack a
 * trailing --- after the last Distill block).
 */
function removeDistillBlockById(content: string, blockId: string): string {
  const distillRe = /^## Distill\s*$/m;
  const distillMatch = distillRe.exec(content);
  if (!distillMatch || distillMatch.index == null) {
    // No Distill header — best-effort whole-file remove only when a later --- exists
    const raw = extractRawMDBlockById(content, blockId);
    if (!raw) return content;
    const trimmed = raw.replace(/\s+$/, '');
    const idx = content.indexOf(trimmed);
    if (idx < 0) return content;
    return `${content.slice(0, idx)}${content.slice(idx + trimmed.length)}`.replace(
      /\n{3,}/g,
      '\n\n',
    );
  }

  const afterHeader = distillMatch.index + distillMatch[0].length;
  const briefRel = /^## Brief\s*$/m.exec(content.slice(afterHeader));
  const briefAbs =
    briefRel && briefRel.index != null
      ? afterHeader + briefRel.index
      : content.length;

  const headerAndTitle = content.slice(0, afterHeader);
  const region = content.slice(afterHeader, briefAbs);
  const after = content.slice(briefAbs);

  const blocks = listRawMDBlocks(region);
  const kept = blocks.filter((b) => {
    const m = b.match(/^id:\s*(\S+)\s*$/m);
    return m?.[1] !== blockId;
  });
  if (kept.length === blocks.length) return content;

  const rebuilt =
    kept.length === 0
      ? '\n\n'
      : `\n\n${kept.map((b) => `${b.replace(/\s+$/, '')}\n`).join('\n')}`;
  return `${headerAndTitle}${rebuilt}${after}`.replace(/\n{3,}/g, '\n\n');
}

function nextBriefSectionIndex(lines: string[], from: number): number {
  for (let i = from + 1; i < lines.length; i++) {
    const t = lines[i].trim();
    if (
      t === 'Conflict'
      || t === HYPOTHESIS_SECTION_TITLE
      || t === 'Possible overdue report'
      || t === CITE_MAP_SECTION_TITLE
      || /^[A-Za-z]+ — \d{4}-\d{2}-\d{2} · Events/.test(t)
      || t === '## Action ledger'
      || t.startsWith('## ')
    ) {
      return i;
    }
  }
  return lines.length;
}

function isMatchingHypBullet(
  body: string,
  opts: { text?: string; cite?: number | null },
): boolean {
  const cite = opts.cite ?? null;
  const wantText = opts.text ? stripTrailingCite(opts.text) : '';
  const citeMatch = /^(?<text>.*?)\s+\[(?<n>\d+)\]\s*$/.exec(body);
  const bulletCite = citeMatch?.groups ? Number(citeMatch.groups.n) : null;
  const bulletText = (citeMatch?.groups?.text ?? body).trim();
  if (cite != null && bulletCite === cite) return true;
  if (wantText && bulletText === wantText) return true;
  return false;
}

function stripHypothesisFromBriefLines(
  content: string,
  opts: { hypothesisId: string; text?: string; cite?: number | null },
): string {
  const lines = content.split('\n');
  const hypStart = lines.findIndex((l) => l.trim() === HYPOTHESIS_SECTION_TITLE);
  const cite = opts.cite ?? null;
  let next = [...lines];

  if (hypStart >= 0) {
    const sectionEnd = nextBriefSectionIndex(next, hypStart);
    const keptBullets: string[] = [];
    let removed = false;
    for (let i = hypStart + 1; i < sectionEnd; i++) {
      const trimmed = next[i].trim();
      if (!trimmed) continue;
      const bullet = /^- (?<body>.+)$/.exec(trimmed);
      if (!bullet?.groups) continue;
      const body = bullet.groups.body.trim();
      if (body.toLowerCase() === 'none.') continue;
      if (isMatchingHypBullet(body, opts)) {
        removed = true;
        continue;
      }
      keptBullets.push(`- ${body}`);
    }
    if (removed) {
      const replacement = keptBullets.length > 0 ? keptBullets : ['- None.'];
      next = [
        ...next.slice(0, hypStart + 1),
        ...replacement,
        '',
        ...next.slice(sectionEnd),
      ];
    }
  }

  const citeIdx = next.findIndex((l) => l.trim() === CITE_MAP_SECTION_TITLE);
  if (citeIdx >= 0) {
    const filtered: string[] = [];
    for (let i = citeIdx + 1; i < next.length; i++) {
      const trimmed = next[i].trim();
      if (!trimmed) {
        filtered.push(next[i]);
        continue;
      }
      if (trimmed.startsWith('## ') || trimmed === HYPOTHESIS_SECTION_TITLE) {
        filtered.push(...next.slice(i));
        break;
      }
      const entry = /^- \[(?<n>\d+)\]\s+hypothesis\s+(?<id>\S+)\s*$/i.exec(trimmed);
      if (entry?.groups) {
        const n = Number(entry.groups.n);
        const id = entry.groups.id;
        if (id === opts.hypothesisId || (cite != null && n === cite)) {
          continue;
        }
      }
      filtered.push(next[i]);
    }
    next = [...next.slice(0, citeIdx + 1), ...filtered];
  }

  return next.join('\n').replace(/\n{3,}/g, '\n\n');
}

function stripHypothesisFromWeekly(
  content: string,
  opts: { hypothesisId: string; text?: string; cite?: number | null },
): string {
  let next = removeDistillBlockById(content, opts.hypothesisId);
  next = stripHypothesisFromBriefLines(next, opts);
  return next;
}

function addMonthsUtc(base: Date, months: number): Date {
  const year = base.getUTCFullYear();
  const month = base.getUTCMonth() + months;
  const day = base.getUTCDate();
  const target = new Date(Date.UTC(year, month, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(day, lastDay));
  return target;
}

function computePutOffValidTo(
  currentValidTo: string,
  interval: PutOffInterval,
  proposedValidTo?: string,
  todayIso = formatUtcDate(new Date()),
): string {
  let baseIso: string;
  if (currentValidTo && isValidIsoDate(currentValidTo)) {
    baseIso = currentValidTo;
  } else if (proposedValidTo && isValidIsoDate(proposedValidTo)) {
    baseIso = proposedValidTo;
  } else {
    baseIso = todayIso;
  }
  const base = new Date(`${baseIso}T00:00:00Z`);
  if (interval === '1mo') {
    return formatUtcDate(addMonthsUtc(base, 1));
  }
  const days = PUT_OFF_DAYS[interval];
  if (days == null) {
    throw new Error(`Unknown put-off interval: ${interval}`);
  }
  const next = new Date(base);
  next.setUTCDate(next.getUTCDate() + days);
  return formatUtcDate(next);
}

function findDailyBlock(blockId: string): MemoryBlock | null {
  return readAllMemoryBlocks().find((b) => b.id === blockId) ?? null;
}

function patchBlockValidTo(
  block: MemoryBlock,
  validTo: string,
): void {
  writeMemoryBlock({ ...block, valid_to: validTo }, block.id);
}

function applyHypConfirm(
  op: WeeklyReviewHypConfirmPending,
): WeeklyReviewHypConfirmOp | { error: string } {
  const weeklyPath = resolveWeeklyPath(op.weekKey);
  if (!fs.existsSync(weeklyPath)) {
    return { error: `Weekly file missing for ${op.weekKey}` };
  }
  const weeklyBefore = fs.readFileSync(weeklyPath, 'utf8');
  let dailyDate: string;
  try {
    dailyDate = resolveHypothesisFactDate(op.weekKey, op.related, op.citeDate);
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
  const factId = op.factId?.trim() || `fact-from-${op.hypothesisId}`;
  const dailyFile = `${dailyDate}.md`;
  const body = stripTrailingCite(op.text);
  const fact: MemoryBlock = {
    id: factId,
    type: 'fact',
    confidence: op.confidence ?? 'high',
    importance: 3,
    status: 'candidate',
    sources: op.sources?.length
      ? op.sources
      : [`weekly-review:${op.weekKey}`, `hypothesis:${op.hypothesisId}`],
    related: [op.hypothesisId, ...(op.related ?? [])],
    body,
    filePath: dailyFile,
  };
  writeMemoryBlock(fact);
  const stripped = stripHypothesisFromWeekly(weeklyBefore, {
    hypothesisId: op.hypothesisId,
    text: op.text,
    cite: op.cite,
  });
  fs.mkdirSync(path.dirname(weeklyPath), { recursive: true });
  fs.writeFileSync(weeklyPath, stripped);
  return {
    ...op,
    weeklyBefore,
    weeklyPath,
    factId,
    dailyFile,
  };
}

function applyHypDelete(
  op: WeeklyReviewHypDeletePending,
): WeeklyReviewHypDeleteOp | { error: string } {
  const weeklyPath = resolveWeeklyPath(op.weekKey);
  if (!fs.existsSync(weeklyPath)) {
    return { error: `Weekly file missing for ${op.weekKey}` };
  }
  const weeklyBefore = fs.readFileSync(weeklyPath, 'utf8');
  const stripped = stripHypothesisFromWeekly(weeklyBefore, {
    hypothesisId: op.hypothesisId,
    text: op.text,
    cite: op.cite,
  });
  fs.writeFileSync(weeklyPath, stripped);
  return {
    ...op,
    weeklyBefore,
    weeklyPath,
  };
}

function applySpanOp(
  op:
    | WeeklyReviewSpanConfirmPending
    | WeeklyReviewSpanPutOffPending
    | WeeklyReviewSpanSetDuePending,
): WeeklyReviewSpanOp | { error: string } {
  const block = findDailyBlock(op.blockId);
  if (!block) {
    return { error: `Block ${op.blockId} not found in daily staging` };
  }
  const previous = (block.valid_to || '').trim();
  let target: string;
  try {
    if (op.kind === 'span_confirm') {
      if (!isValidIsoDate(op.proposed_valid_to)) {
        return { error: 'span_confirm requires a valid proposed_valid_to (YYYY-MM-DD)' };
      }
      target = op.proposed_valid_to;
    } else if (op.kind === 'span_set_due_date') {
      if (!isValidIsoDate(op.due_date)) {
        return { error: 'span_set_due_date requires a valid due_date (YYYY-MM-DD)' };
      }
      target = op.due_date;
    } else {
      target = computePutOffValidTo(previous, op.interval, op.proposed_valid_to);
    }
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
  patchBlockValidTo(block, target);
  return {
    kind: op.kind,
    weekKey: op.weekKey,
    blockId: op.blockId,
    previous_valid_to: previous,
    valid_to: target,
    dailyFile: block.filePath,
  };
}

export function applyWeeklyReviewSave(
  ops: WeeklyReviewPendingOp[],
): { ok: true; count: number } | { ok: false; error: string } {
  if (!ops.length) {
    return { ok: false, error: 'No pending weekly review actions.' };
  }
  const recorded: WeeklyReviewOp[] = [];
  for (const op of ops) {
    if (op.kind === 'hyp_confirm') {
      const result = applyHypConfirm(op);
      if ('error' in result) return { ok: false, error: result.error };
      recorded.push(result);
      continue;
    }
    if (op.kind === 'hyp_delete') {
      const result = applyHypDelete(op);
      if ('error' in result) return { ok: false, error: result.error };
      recorded.push(result);
      continue;
    }
    const result = applySpanOp(op);
    if ('error' in result) return { ok: false, error: result.error };
    recorded.push(result);
  }
  const store = loadStore();
  store.batches = prune([
    ...store.batches,
    { savedAt: new Date().toISOString(), ops: recorded },
  ]).slice(-WEEKLY_REVIEW_RECALL_MAX);
  saveStore(store);
  return { ok: true, count: recorded.length };
}

function undoOp(op: WeeklyReviewOp): void {
  if (op.kind === 'hyp_confirm') {
    deleteMemoryBlock(op.factId);
    fs.writeFileSync(op.weeklyPath, op.weeklyBefore);
    return;
  }
  if (op.kind === 'hyp_delete') {
    fs.writeFileSync(op.weeklyPath, op.weeklyBefore);
    return;
  }
  const block = findDailyBlock(op.blockId);
  if (!block) {
    // Recreate minimal restore if missing — prefer existing path
    const restored: MemoryBlock = {
      id: op.blockId,
      type: 'fact',
      confidence: 'high',
      importance: 3,
      status: 'candidate',
      sources: ['weekly-review:recall'],
      valid_to: op.previous_valid_to || undefined,
      body: '',
      filePath: op.dailyFile,
    };
    writeMemoryBlock(restored);
    return;
  }
  patchBlockValidTo(block, op.previous_valid_to || 'open');
}

export function applyWeeklyReviewRecall():
  | { ok: true; count: number }
  | { ok: false; error: string } {
  const store = loadStore();
  const batches = prune(store.batches);
  if (!batches.length) {
    return { ok: false, error: 'Nothing to recall — Save a weekly review batch first.' };
  }
  const batch = batches[batches.length - 1];
  for (let i = batch.ops.length - 1; i >= 0; i--) {
    undoOp(batch.ops[i]);
  }
  store.batches = batches.slice(0, -1);
  saveStore(store);
  return { ok: true, count: batch.ops.length };
}
