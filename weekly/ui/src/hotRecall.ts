import type { HotMemoryFile } from './types';

export const RECALL_TTL_MS = 24 * 60 * 60 * 1000;
export const RECALL_MAX_BATCHES = 3;
export const RECALL_LIMIT_MESSAGE = 'you can only recall last 3 actions!';
export const RECALL_DIR_NAME = '.memory-3-step-recall';

export type RecallEdit = { index: number; before: string };
export type RecallDelete = { index: number; text: string };
export type RecallAppend = { index: number; text: string };
export type RecallBatch = {
  savedAt: string;
  deletes: RecallDelete[];
  edits: RecallEdit[];
  /** Entries added by this step; on recall, remove matching index/text. */
  appends?: RecallAppend[];
  /** Pairs MEMORY + USER batches from one Save (move). */
  linkId?: string;
};
/** In-memory UI view — only the ui stack. */
export type RecallStore = { file: string; batches: RecallBatch[] };
/** On-disk shape: ui batches only; legacy chat keys are ignored on load. */
export type NestedRecallStore = {
  file: string;
  ui: { batches: RecallBatch[] };
};

export function recallFileName(file: HotMemoryFile): 'memory' | 'user' | null {
  if (file === 'MEMORY.md') return 'memory';
  if (file === 'USER.md') return 'user';
  return null;
}

/** @deprecated Prefer recallFileName for disk paths; kept for callers that need MEMORY|USER|HERMES. */
export function recallStem(file: HotMemoryFile): 'MEMORY' | 'USER' | 'HERMES' {
  if (file === 'MEMORY.md') return 'MEMORY';
  if (file === 'USER.md') return 'USER';
  return 'HERMES';
}

export function resolveRecallPath(hermesHome: string, file: HotMemoryFile): string {
  const name = recallFileName(file);
  if (!name) {
    return `${hermesHome}/memories/staging/${RECALL_DIR_NAME}/unsupported.json`;
  }
  return `${hermesHome}/memories/staging/${RECALL_DIR_NAME}/${name}.json`;
}

export function pruneExpiredBatches<T extends { savedAt: string }>(
  batches: T[],
  now: Date = new Date(),
): T[] {
  const cutoff = now.getTime() - RECALL_TTL_MS;
  return batches.filter((batch) => {
    const savedAt = Date.parse(batch.savedAt);
    return Number.isFinite(savedAt) && savedAt >= cutoff;
  });
}

export function pushRecallBatch<T extends { savedAt: string }>(
  batches: T[],
  batch: T,
): T[] {
  return [...batches, batch].slice(-RECALL_MAX_BATCHES);
}

export function applyRecallBatch(entries: string[], batch: RecallBatch): string[] {
  const next = [...entries];

  for (const edit of batch.edits ?? []) {
    if (!Number.isInteger(edit.index) || edit.index < 0) continue;
    const index = Math.min(edit.index, next.length);
    if (index >= next.length) {
      next.push(edit.before);
    } else {
      next[index] = edit.before;
    }
  }

  // Undo appends before restoring deletes so indices stay coherent for moves.
  const appends = [...(batch.appends ?? [])].sort((a, b) => b.index - a.index);
  for (const append of appends) {
    if (!Number.isInteger(append.index) || append.index < 0) continue;
    if (append.index >= next.length) continue;
    if (next[append.index] === append.text) {
      next.splice(append.index, 1);
    }
  }

  const deletes = [...(batch.deletes ?? [])].sort((a, b) => b.index - a.index);
  for (const del of deletes) {
    if (!Number.isInteger(del.index) || del.index < 0) continue;
    const index = Math.min(del.index, next.length);
    next.splice(index, 0, del.text);
  }

  return next;
}

export function hotRecallSibling(file: HotMemoryFile): HotMemoryFile | null {
  if (file === 'MEMORY.md') return 'USER.md';
  if (file === 'USER.md') return 'MEMORY.md';
  return null;
}

/** Find and remove a batch with the same linkId (mutates copy of batches). */
export function popLinkedBatch(
  batches: RecallBatch[],
  linkId: string,
): { batch: RecallBatch | null; remaining: RecallBatch[] } {
  const index = batches.findIndex((b) => b.linkId === linkId);
  if (index < 0) return { batch: null, remaining: batches };
  const remaining = [...batches.slice(0, index), ...batches.slice(index + 1)];
  return { batch: batches[index] ?? null, remaining };
}
