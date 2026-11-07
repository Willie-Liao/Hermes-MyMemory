/**
 * Independent UI staging Save/Recall stack (read-by-date).
 * Max 3 batches / 24h — lives under `.memory-3-step-recall/` with hot recall stacks.
 */
import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge';
import { RECALL_DIR_NAME } from './hotRecall';
import type { MemoryBlock, StagingUiEditOp, StagingUiPendingOp } from './types';
import {
  deleteMemoryBlock,
  extractRawMDBlockById,
  insertRawMDBlockAt,
  listRawMDBlocks,
  writeMemoryBlock,
} from './serverHelpers';

export const STAGING_UI_RECALL_MAX = 3;
export const STAGING_UI_RECALL_TTL_MS = 24 * 60 * 60 * 1000;
export const STAGING_UI_RECALL_FILE = '.staging-ui-recall.json';

export type { StagingUiEditOp, StagingUiPendingOp };

export type StagingUiDeleteOp = {
  kind: 'delete';
  before: MemoryBlock;
  rawYaml: string;
  index: number;
};

export type StagingUiOp = StagingUiEditOp | StagingUiDeleteOp;

export type StagingUiRecallBatch = {
  savedAt: string;
  ops: StagingUiOp[];
};

type StagingUiRecallStore = { batches: StagingUiRecallBatch[] };

function stagingDir(): string {
  return path.join(resolveHermesHome(), 'memories', 'staging');
}

function storePath(): string {
  return path.join(stagingDir(), RECALL_DIR_NAME, STAGING_UI_RECALL_FILE);
}

/** Pre-move location: `staging/.staging-ui-recall.json`. */
function legacyStorePath(): string {
  return path.join(stagingDir(), STAGING_UI_RECALL_FILE);
}

/** One-shot migrate legacy flat file into `.memory-3-step-recall/`. */
function migrateLegacyStoreIfNeeded(): void {
  const modern = storePath();
  const legacy = legacyStorePath();
  if (fs.existsSync(modern) || !fs.existsSync(legacy)) return;
  fs.mkdirSync(path.dirname(modern), { recursive: true });
  fs.renameSync(legacy, modern);
}

function prune(batches: StagingUiRecallBatch[], now = Date.now()): StagingUiRecallBatch[] {
  const cutoff = now - STAGING_UI_RECALL_TTL_MS;
  return batches.filter((b) => {
    const t = Date.parse(b.savedAt);
    return Number.isFinite(t) && t >= cutoff;
  });
}

function loadStore(): StagingUiRecallStore {
  migrateLegacyStoreIfNeeded();
  try {
    const parsed = JSON.parse(fs.readFileSync(storePath(), 'utf8')) as StagingUiRecallStore;
    if (!parsed || !Array.isArray(parsed.batches)) return { batches: [] };
    return { batches: prune(parsed.batches) };
  } catch {
    return { batches: [] };
  }
}

function saveStore(store: StagingUiRecallStore): void {
  const batches = prune(store.batches).slice(-STAGING_UI_RECALL_MAX);
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

export function stagingUiRecallAvailable(): boolean {
  return loadStore().batches.length > 0;
}

/** Capture raw yaml + index for a block before delete (for recall restore). */
export function captureDeletePayload(block: MemoryBlock): StagingUiDeleteOp | null {
  const filePath = path.join(
    resolveHermesHome(),
    'memories',
    'staging',
    'daily',
    path.basename(block.filePath),
  );
  if (!fs.existsSync(filePath)) return null;
  const content = fs.readFileSync(filePath, 'utf8');
  const raws = listRawMDBlocks(content);
  const index = raws.findIndex((r) => {
    const m = r.match(/^id:\s*(\S+)\s*$/m);
    return m?.[1] === block.id;
  });
  if (index < 0) return null;
  const rawYaml = extractRawMDBlockById(content, block.id);
  if (!rawYaml) return null;
  return { kind: 'delete', before: block, rawYaml, index };
}

/** Client may send delete with only `before`; capture rawYaml+index at apply time. */
export function applyStagingUiSave(
  ops: StagingUiPendingOp[],
): { ok: true; count: number } | { ok: false; error: string } {
  if (!ops.length) return { ok: false, error: 'No pending staging actions.' };
  const recorded: StagingUiOp[] = [];
  for (const op of ops) {
    if (op.kind === 'edit') {
      writeMemoryBlock(op.after, op.before.id);
      recorded.push(op);
      continue;
    }
    const captured = captureDeletePayload(op.before);
    if (!captured) {
      return { ok: false, error: `Could not capture delete payload for ${op.before.id}` };
    }
    if (!deleteMemoryBlock(op.before.id)) {
      return { ok: false, error: `Failed to delete ${op.before.id}` };
    }
    recorded.push(captured);
  }
  const store = loadStore();
  store.batches = prune([
    ...store.batches,
    { savedAt: new Date().toISOString(), ops: recorded },
  ]).slice(-STAGING_UI_RECALL_MAX);
  saveStore(store);
  return { ok: true, count: recorded.length };
}

export function applyStagingUiRecall():
  | { ok: true; count: number }
  | { ok: false; error: string } {
  const store = loadStore();
  const batches = prune(store.batches);
  if (!batches.length) {
    return { ok: false, error: 'Nothing to recall — Save a staging batch first.' };
  }
  const batch = batches[batches.length - 1];
  // Reverse ops last→first
  for (let i = batch.ops.length - 1; i >= 0; i--) {
    const op = batch.ops[i];
    if (op.kind === 'edit') {
      writeMemoryBlock(op.before, op.after.id);
    } else {
      insertRawMDBlockAt(path.basename(op.before.filePath), op.rawYaml, op.index);
    }
  }
  store.batches = batches.slice(0, -1);
  saveStore(store);
  return { ok: true, count: batch.ops.length };
}
