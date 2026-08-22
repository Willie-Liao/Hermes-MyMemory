import fs from 'node:fs';
import path from 'node:path';
import { resolveHermesHome } from './pluginBridge';
import type { HotMemoryFile } from './types';
import {
  RECALL_MAX_BATCHES,
  pruneExpiredBatches,
  recallFileName,
  resolveRecallPath,
  type NestedRecallStore,
  type RecallAppend,
  type RecallBatch,
  type RecallDelete,
  type RecallEdit,
  type RecallStore,
} from './hotRecall';

function isRecallEdit(value: unknown): value is RecallEdit {
  if (!value || typeof value !== 'object') return false;
  const edit = value as Partial<RecallEdit>;
  return Number.isInteger(edit.index) && typeof edit.before === 'string';
}

function isRecallDelete(value: unknown): value is RecallDelete {
  if (!value || typeof value !== 'object') return false;
  const del = value as Partial<RecallDelete>;
  return Number.isInteger(del.index) && typeof del.text === 'string';
}

function isRecallAppend(value: unknown): value is RecallAppend {
  if (!value || typeof value !== 'object') return false;
  const append = value as Partial<RecallAppend>;
  return Number.isInteger(append.index) && typeof append.text === 'string';
}

function isRecallBatch(value: unknown): value is RecallBatch {
  if (!value || typeof value !== 'object') return false;
  const batch = value as Partial<RecallBatch>;
  if (
    typeof batch.savedAt !== 'string'
    || !Array.isArray(batch.deletes)
    || !batch.deletes.every(isRecallDelete)
    || !Array.isArray(batch.edits)
    || !batch.edits.every(isRecallEdit)
  ) {
    return false;
  }
  if (batch.appends !== undefined) {
    if (!Array.isArray(batch.appends) || !batch.appends.every(isRecallAppend)) {
      return false;
    }
  }
  if (batch.linkId !== undefined && typeof batch.linkId !== 'string') {
    return false;
  }
  return true;
}

function normalizeSourceBatches(raw: unknown): RecallBatch[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
  const batches = (raw as { batches?: unknown }).batches;
  if (!Array.isArray(batches)) return [];
  return batches.filter(isRecallBatch);
}

function emptyNested(file: HotMemoryFile): NestedRecallStore {
  return { file, ui: { batches: [] } };
}

function normalizeNestedStore(file: HotMemoryFile, parsed: unknown): NestedRecallStore {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return emptyNested(file);
  }
  const obj = parsed as Record<string, unknown>;
  if ('ui' in obj || 'chat' in obj) {
    return {
      file,
      ui: { batches: normalizeSourceBatches(obj.ui) },
    };
  }
  // Legacy flat { file, batches } — fresh start, ignore
  return emptyNested(file);
}

function isHotMemoryFileName(file: string): file is HotMemoryFile {
  return file === 'MEMORY.md' || file === 'USER.md' || file === 'HERMES.md';
}

export function loadRecallStore(file: HotMemoryFile): RecallStore {
  if (!recallFileName(file)) {
    return { file, batches: [] };
  }
  const recallPath = resolveRecallPath(resolveHermesHome(), file);
  let nested = emptyNested(file);
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(fs.readFileSync(recallPath, 'utf8'));
    nested = normalizeNestedStore(file, parsed);
  } catch {
    return { file, batches: [] };
  }

  const uiPruned = pruneExpiredBatches(nested.ui.batches);
  const uiChanged = uiPruned.length !== nested.ui.batches.length;
  const hadLegacyChat =
    parsed !== null
    && typeof parsed === 'object'
    && !Array.isArray(parsed)
    && 'chat' in (parsed as Record<string, unknown>);
  if (uiChanged || hadLegacyChat) {
    saveNestedStore({
      file,
      ui: { batches: uiPruned },
    });
    return { file, batches: uiPruned };
  }
  return { file, batches: nested.ui.batches };
}

function saveNestedStore(store: NestedRecallStore): void {
  if (!isHotMemoryFileName(store.file) || !recallFileName(store.file as HotMemoryFile)) {
    return;
  }
  const file = store.file as HotMemoryFile;
  const recallPath = resolveRecallPath(resolveHermesHome(), file);
  const uiBatches = pruneExpiredBatches(store.ui.batches).slice(-RECALL_MAX_BATCHES);

  if (uiBatches.length === 0) {
    try {
      fs.unlinkSync(recallPath);
    } catch (err: unknown) {
      const code = (err as NodeJS.ErrnoException)?.code;
      if (code !== 'ENOENT') throw err;
    }
    return;
  }

  fs.mkdirSync(path.dirname(recallPath), { recursive: true });
  fs.writeFileSync(
    recallPath,
    `${JSON.stringify({ file, ui: { batches: uiBatches } }, null, 2)}\n`,
  );
}

export function saveRecallStore(store: RecallStore): void {
  if (!isHotMemoryFileName(store.file) || !recallFileName(store.file as HotMemoryFile)) {
    return;
  }
  saveNestedStore({
    file: store.file as HotMemoryFile,
    ui: { batches: store.batches },
  });
}

export { recallStem } from './hotRecall';
